"""Supervised pipeline validation (container-side): collect ONE small real
window (today 07:00-08:00 GMT+8), poll to DONE, report quality distribution.
NO prepare/send/query — collection only.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, time as dtime, timedelta, timezone

TOKEN = sys.stdin.readline().strip()
BASE = 'http://localhost:8000/api/v1'
CONNECTOR_ID = '7d51abd7-3f32-4c8e-b2fa-9b652a511808'
PROFILE = 'alihealth_yljg_retail_v1'
GMT8 = timezone(timedelta(hours=8))
out = {'calls': []}


def call(method, path, body=None, timeout=60):
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
        raise RuntimeError(f'API {path} -> {exc.code} {err.get("code")}: '
                           f'{str(err.get("message"))[:300]}')
    out['calls'].append({'method': method, 'path': path, 'status': st})
    return data


try:
    today = datetime.now(GMT8).date()
    wb = datetime.combine(today, dtime(7))  # naive GMT+8 (system convention)
    we = wb + timedelta(hours=1)
    out['window'] = [wb.isoformat(), we.isoformat()]
    # partition separate from daily stream and from the original first-run
    run = call('POST', '/drug-trace/collection-runs', {
        'connector_id': CONNECTOR_ID, 'window_begin': wb.isoformat(),
        'window_end': we.isoformat(), 'lookback_seconds': 600,
        'mode': 'oracle', 'profile': PROFILE,
        'checkpoint_partition': 'timed-validate-20261024'})
    run_id = run['collection_run_id']
    out['run_id'] = run_id
    deadline = time.monotonic() + 1500
    state = None
    while time.monotonic() < deadline:
        runs = call('GET', '/drug-trace/collection-runs?limit=10')
        cur = next((r for r in runs if r['id'] == run_id), None)
        state = cur['state'] if cur else None
        if state in ('DONE', 'FAILED', 'CANCELLED'):
            out['collection'] = cur
            break
        time.sleep(5)
    if state != 'DONE':
        raise RuntimeError(f'collection not DONE: {state} {out.get("collection")}')
    from core_db import make_engine, make_session_factory
    from core_db.models import Tenant
    from sqlalchemy import text
    engine = make_engine()
    with make_session_factory(engine)() as db:
        tenant = db.query(Tenant).filter_by(code='first-hospital').one()
        out['bills'] = [dict(r._mapping) for r in db.execute(text("""
            SELECT b.business_kind, r.quality_state, count(*) n
            FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
            WHERE b.tenant_id=:t AND b.connector_id=:cid AND b.bill_time>=:wb AND b.bill_time<:we
            GROUP BY 1,2 ORDER BY 3 DESC"""), {'t': tenant.id, 'cid': CONNECTOR_ID,
            'wb': wb, 'we': we})]
        out['issues'] = [dict(r._mapping) for r in db.execute(text("""
            SELECT i.rule, count(*) n FROM quality_issue i
            WHERE i.tenant_id=:t AND i.state='OPEN'
              AND i.created_at > now() - interval '30 minutes'
            GROUP BY 1 ORDER BY 2 DESC LIMIT 10"""), {'t': tenant.id})]
    engine.dispose()
    out['ok'] = True
except Exception as exc:
    out['ok'] = False
    out['error'] = str(exc)[:400]
print('__TIMED_RESULT__' + json.dumps(out, ensure_ascii=False, default=str) + '__END__')
