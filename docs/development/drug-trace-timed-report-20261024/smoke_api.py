"""Supervised single-bill send smoke under the standing scope (container-side).

Picks ONE VALID finalized PATIENT_DISPENSE bill from today's validated 07:00-08:00
window, prepares, sends (confirm), polls to a post-send state, then queries.
Proves the standing EXT-12 scope/quota path end-to-end before the timer's
first full-day run.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

TOKEN = sys.stdin.readline().strip()
BASE = 'http://localhost:8000/api/v1'
PROFILE = 'alihealth_yljg_retail_v1'
out = {'calls': []}


def call(method, path, body=None, timeout=90):
    req = urllib.request.Request(BASE + path, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            st, data = r.status, json.load(r).get('data')
    except urllib.error.HTTPError as exc:
        try:
            data = json.load(exc)
        except Exception:
            data = {}
        err = data.get('error') or {}
        out['calls'].append({'method': method, 'path': path, 'status': exc.code,
                             'error': f"{err.get('code')}: {str(err.get('message'))[:200]}"})
        raise RuntimeError(f'API {path} -> {exc.code} {err.get("code")}')
    out['calls'].append({'method': method, 'path': path, 'status': st})
    return data


try:
    from core_db import make_engine, make_session_factory
    from core_db.models import Tenant
    from sqlalchemy import text
    engine = make_engine()
    with make_session_factory(engine)() as db:
        tenant = db.query(Tenant).filter_by(code='first-hospital').one()
        dest_id = db.execute(text("SELECT id FROM destination WHERE tenant_id=:t "
            "AND code='alihealth-drugtrace-retail'"), {'t': tenant.id}).scalar()
        row = db.execute(text("""
            SELECT b.id, r.id FROM drug_trace_bill b
            JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
            WHERE b.tenant_id=:t AND b.business_kind='PATIENT_DISPENSE'
              AND b.finalized=true AND b.is_void=false AND r.quality_state='VALID'
              AND b.bill_time>='2026-10-24 07:00:00' AND b.bill_time<'2026-10-24 08:00:00'
            ORDER BY b.bill_time LIMIT 1"""), {'t': tenant.id}).fetchone()
        assert row, 'no VALID candidate in validated window'
        bill_id, rev_id = row
        ex = db.execute(text("""
            SELECT s.id, s.state FROM submission s
            JOIN package_revision pr ON pr.id=s.package_revision_id
            WHERE s.tenant_id=:t AND pr.provenance->>'bill_revision_id'=:r
            ORDER BY s.created_at DESC LIMIT 1"""),
            {'t': tenant.id, 'r': str(rev_id)}).fetchone()
    engine.dispose()
    out['bill'] = bill_id
    if ex is not None and ex[1] in ('CONFIRMED', 'REJECTED', 'PARTIAL'):
        out['skip'] = f'candidate already terminal {ex[1]}'
        print('__TIMED_RESULT__' + json.dumps(out, ensure_ascii=False) + '__END__')
        sys.exit(0)
    if ex is not None:
        sid = ex[0]
        out['resumed_prepared'] = True
    else:
        prep = call('POST', f'/drug-trace/bills/{bill_id}/prepare',
                    {'profile': PROFILE, 'destination_id': dest_id})
        sid = prep['submission_id']
    out['submission_id'] = sid
    out['send'] = call('POST', f'/drug-trace/submissions/{sid}/send', {
        'confirm': True, 'reason': 'supervised smoke under standing timed-report '
        'scope 2026-10-24; user instruction 2026-10-24'})
    deadline = time.monotonic() + 240
    state = None
    while time.monotonic() < deadline:
        engine2 = make_engine()
        with make_session_factory(engine2)() as db2:
            state = db2.execute(text('SELECT state FROM submission WHERE id=:i'),
                                {'i': sid}).scalar()
        engine2.dispose()
        if state not in ('PREPARED', 'SENDING'):
            break
        time.sleep(5)
    out['post_send_state'] = state
    if state in ('ACCEPTED', 'UNKNOWN'):
        time.sleep(8)
        out['query'] = call('POST', f'/drug-trace/submissions/{sid}/query', {})
        engine3 = make_engine()
        with make_session_factory(engine3)() as db3:
            out['final_state'] = db3.execute(text(
                'SELECT state FROM submission WHERE id=:i'), {'i': sid}).scalar()
            out['attempt'] = dict(db3.execute(text(
                'SELECT outcome, http_status FROM delivery_attempt '
                'WHERE submission_id=:i ORDER BY attempt_no DESC LIMIT 1'),
                {'i': sid}).fetchone()._mapping)
        engine3.dispose()
    out['ok'] = True
except Exception as exc:
    out['ok'] = False
    out['error'] = str(exc)[:400]
print('__TIMED_RESULT__' + json.dumps(out, ensure_ascii=False, default=str) + '__END__')
