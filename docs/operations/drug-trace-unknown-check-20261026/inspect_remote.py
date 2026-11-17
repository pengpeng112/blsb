"""Read-only application ledger aggregates; no HIS or external calls."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
sql="""WITH targets AS (
 SELECT s.id,s.state,s.created_at,p.payload->>'bill_time' bill_time
 FROM submission s JOIN package_revision p ON p.id=s.package_revision_id JOIN data_package d ON d.id=p.package_id
 WHERE s.state IN ('UNKNOWN','ACCEPTED') AND d.domain='drug_trace'
), attempts AS (
 SELECT a.*,row_number() OVER(PARTITION BY a.submission_id ORDER BY a.started_at DESC,a.id DESC) rn
 FROM delivery_attempt a JOIN targets t ON t.id=a.submission_id
), receipts AS (
 SELECT r.*,row_number() OVER(PARTITION BY r.submission_ref ORDER BY r.received_at DESC,r.id DESC) rn
 FROM receipt_event r JOIN targets t ON t.id=r.submission_ref WHERE external_receipt_id LIKE 'query:%'
)
SELECT json_build_object(
 'release',NULL,
 'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
 'targets',(SELECT json_agg(row_to_json(x)) FROM(SELECT state,count(*) n,min(created_at) first_created_utc,max(created_at) last_created_utc,min(substr(bill_time,1,10)) first_business_day,max(substr(bill_time,1,10)) last_business_day FROM targets GROUP BY state)x),
 'latest_uploads',(SELECT json_agg(row_to_json(x)) FROM(SELECT t.state,a.http_status,a.outcome,a.response_snapshot->'error'->>'reason' reason,count(*) n FROM targets t JOIN attempts a ON a.submission_id=t.id AND a.rn=1 GROUP BY 1,2,3,4)x),
 'latest_platform_queries',(SELECT json_agg(row_to_json(x)) FROM(SELECT t.state,r.parsed_result->>'state' classification,r.parsed_result->>'reason' reason,r.parsed_result->'error'->>'code' error_code,r.parsed_result->'error'->>'msg_code' msg_code,r.raw_payload->>'query_status' query_status,r.raw_payload->>'total_num' total_num,min(r.received_at) earliest_utc,max(r.received_at) latest_utc,count(*) n FROM targets t LEFT JOIN receipts r ON r.submission_ref=t.id AND r.rn=1 GROUP BY 1,2,3,4,5,6,7)x),
 'poll_counts',(SELECT json_agg(row_to_json(x)) FROM(SELECT t.state,min(q.n) minimum,max(q.n) maximum FROM targets t LEFT JOIN (SELECT payload->>'submission_id' sid,count(*) n FROM job WHERE job_type='drug_trace.query_status' GROUP BY 1)q ON q.sid=t.id GROUP BY 1)x),
 'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
 'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=30)
if p.returncode:raise RuntimeError('READONLY_LEDGER_QUERY_FAILED')
out=json.loads(p.stdout);out.update(release=Path('/opt/blyb/current').resolve().name,utc=datetime.now(timezone.utc).isoformat(),his_queries=0,platform_calls=0)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
