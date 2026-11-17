"""Query-only recovery of the pre-transport verification failure; never send."""
import json
import sys
import urllib.request
from sqlalchemy import text
from core_db import make_engine, make_session_factory
from workflow_core.jobs import claim_job, finish_job
from domains.drug_trace.plugin import _j_query
from domains.drug_trace import service

token = sys.stdin.readline().strip()
out = {'uploads': 0, 'source_reads': 0, 'action': 'query_only'}
engine = make_engine()
sf = make_session_factory(engine)
try:
    with sf() as db:
        row = db.execute(text("""
          SELECT s.id,s.tenant_id,s.destination_id,p.provenance->>'config_release_id'
          FROM submission s JOIN package_revision p ON p.id=s.package_revision_id
          WHERE s.state='UNKNOWN' AND EXISTS(SELECT 1 FROM delivery_attempt a
            WHERE a.submission_id=s.id AND a.started_at>='2026-10-25 10:12:00'
              AND a.http_status IS NULL AND a.response_snapshot->'error'->>'reason'='send-failure: FileNotFoundError')
          LIMIT 2
        """)).fetchall()
        if len(row) != 1:raise RuntimeError('TARGET_NOT_UNIQUE')
        sid, tenant, dest, config = row[0]
        from core_db.models import Destination
        service.build_client(db, tenant, db.get(Destination, dest), config_release_id=config,
                             profile='alihealth_yljg_retail_v1')
        out['worker_client_preflight'] = True
    req = urllib.request.Request('http://core:8000/api/v1/drug-trace/submissions/'+sid+'/query',
        data=b'{}', headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}, method='POST')
    with urllib.request.urlopen(req,timeout=90) as response:
        out['query_api_status']=response.status
    with sf() as db:
        lease=claim_job(db,owner='timer-repair-query-only',job_types=['drug_trace.query_status'])
        if lease is None or (lease.job.payload or {}).get('submission_id')!=sid:
            raise RuntimeError('QUERY_TARGET_NOT_READY')
        result=_j_query(db,lease.job)
        finish_job(db,lease,success=True)
        out['classification']=result.get('classification')
        out['final_state']=db.execute(text('SELECT state FROM submission WHERE id=:i'),{'i':sid}).scalar()
        out['attempt_count']=db.execute(text('SELECT count(*) FROM delivery_attempt WHERE submission_id=:i'),{'i':sid}).scalar()
    out['ok']=True
except Exception as exc:
    out['ok']=False;out['error_type']=type(exc).__name__
    if isinstance(exc,RuntimeError):out['error_code']=str(exc)
finally:
    engine.dispose()
print('__ONE_BILL__'+json.dumps(out,ensure_ascii=True)+'__ONE_BILL_END__')
