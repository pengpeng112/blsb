"""One approved PREPARED bill; standard API/leased handlers, no collect handler."""
import json
import sys
import time
import urllib.request
import urllib.error

TOKEN = sys.stdin.readline().strip()
out = {'new_upload_limit': 1, 'source_reads': 0, 'collect_jobs_claimed': 0}


def call(path, body):
    request = urllib.request.Request('http://core:8000/api/v1' + path,
        data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + TOKEN,
                                               'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.load(response)
            return response.status, data.get('data')
    except urllib.error.HTTPError as exc:
        data = json.load(exc)
        error = data.get('error') if isinstance(data, dict) else None
        return exc.code, {'error_code': error.get('code') if isinstance(error, dict) else 'HTTP_ERROR'}


try:
    from core_db import make_engine, make_session_factory
    from sqlalchemy import text
    from workflow_core.jobs import claim_job, finish_job
    from domains.drug_trace.plugin import _j_send, _j_query
    engine = make_engine()
    sf = make_session_factory(engine)
    with sf() as db:
        from core_db.models import Destination, Tenant
        from domains.drug_trace import service
        tenant = db.query(Tenant).filter_by(code='first-hospital').one()
        dest = db.query(Destination).filter_by(tenant_id=tenant.id,code='alihealth-drugtrace-retail').one()
        service.build_client(db,tenant.id,dest,config_release_id=dest.config_revision,
                             profile='alihealth_yljg_retail_v1')
        out['worker_client_preflight'] = True
        row = db.execute(text("""
        SELECT s.id FROM submission s JOIN destination d ON d.id=s.destination_id
        JOIN tenant t ON t.id=s.tenant_id JOIN package_revision p ON p.id=s.package_revision_id
        JOIN drug_trace_bill_revision r ON r.id=p.provenance->>'bill_revision_id'
        JOIN drug_trace_bill b ON b.id=r.bill_id
        WHERE t.code='first-hospital' AND d.code='alihealth-drugtrace-retail'
          AND d.is_mock=false AND d.status='ENABLED' AND s.state='PREPARED'
          AND b.current_revision_id=r.id AND r.quality_state='VALID'
          AND b.business_kind='PATIENT_DISPENSE' AND b.finalized=true AND b.is_void=false
          AND b.bill_time>='2026-10-24' AND b.bill_time<'2026-10-25'
        ORDER BY s.created_at LIMIT 1
        """)).fetchone()
        if row is None:
            candidate = db.execute(text("""
             SELECT b.id FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
             WHERE b.tenant_id=:t AND b.business_kind='PATIENT_DISPENSE' AND b.finalized=true
               AND b.is_void=false AND r.quality_state='VALID' AND b.bill_time>='2026-10-24' AND b.bill_time<'2026-10-25'
               AND NOT EXISTS(SELECT 1 FROM submission s JOIN package_revision p ON p.id=s.package_revision_id
                 WHERE s.destination_id=:d AND p.provenance->>'bill_revision_id'=r.id)
             ORDER BY b.bill_time LIMIT 1
            """),{'t':tenant.id,'d':dest.id}).fetchone()
            if candidate is None:raise RuntimeError('NO_ELIGIBLE_CANDIDATE')
            bill_id=candidate[0];dest_id=dest.id
    if row is None:
        status,prep=call('/drug-trace/bills/'+bill_id+'/prepare',
             {'profile':'alihealth_yljg_retail_v1','destination_id':dest_id})
        if status not in (200,201):raise RuntimeError('PREPARE_REJECTED')
        sid=prep['submission_id']
    else:
        sid = row[0]
    status, response = call('/drug-trace/submissions/' + sid + '/send',
        {'confirm': True, 'reason': 'User-authorized timer repair verification 2026-10-25; one existing approved PREPARED bill'})
    out['send_api_status'] = status
    if status not in (200, 201):
        out['error_code'] = response.get('error_code')
        raise RuntimeError('SEND_API_REJECTED')
    out['jobs_finished'] = []
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        with sf() as db:
            current = db.execute(text('SELECT state FROM submission WHERE id=:i'), {'i': sid}).scalar()
            if current in ('CONFIRMED', 'REJECTED', 'PARTIAL'):
                break
            # Exclude collect/prepare explicitly: never reclaim the long HIS job.
            lease = claim_job(db, owner='timer-repair-one-bill',
                              job_types=['drug_trace.send', 'drug_trace.query_status'])
            if lease:
                if (lease.job.payload or {}).get('submission_id') != sid:
                    finish_job(db, lease, success=False,
                        error={'code': 'REPAIR_NOT_TARGET'}, retry_after_seconds=1)
                    raise RuntimeError('ANOTHER_BILL_JOB_FOUND')
                handler = _j_send if lease.job.job_type == 'drug_trace.send' else _j_query
                try:
                    handler(db, lease.job)
                    finish_job(db, lease, success=True)
                    out['jobs_finished'].append(lease.job.job_type)
                except Exception as exc:
                    db.rollback()
                    finish_job(db, lease, success=False, error={'type': type(exc).__name__})
                    raise
        time.sleep(2.2)
    with sf() as db:
        out['final_state'] = db.execute(text('SELECT state FROM submission WHERE id=:i'), {'i': sid}).scalar()
        attempts = db.execute(text('SELECT http_status,outcome FROM delivery_attempt WHERE submission_id=:i ORDER BY attempt_no'), {'i': sid}).fetchall()
        out['attempts'] = [{'http_status': r[0], 'outcome': r[1]} for r in attempts]
        out['quota'] = [dict(r._mapping) for r in db.execute(text('SELECT used,max_bills FROM drug_trace_send_quota'))]
        out['collect_running_count'] = db.execute(text("SELECT count(*) FROM drug_trace_collection_run WHERE state='RUNNING' AND window_begin='2026-10-24' AND window_end='2026-10-25'")).scalar()
    engine.dispose()
    out['ok'] = out['final_state'] == 'CONFIRMED' and len(attempts) == 1
except Exception as exc:
    out['ok'] = False
    out['error_type'] = type(exc).__name__
    if isinstance(exc, RuntimeError) and str(exc).isupper(): out['error_code'] = str(exc)
print('__ONE_BILL__' + json.dumps(out, ensure_ascii=True) + '__ONE_BILL_END__')
