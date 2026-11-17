"""Read only app DB; return aggregate progress and bounded nonclinical errors."""
from pathlib import Path
from datetime import datetime, timezone
import json, subprocess

sql = """SELECT json_build_object(
'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
'window_states',(SELECT json_object_agg(state,n) FROM(SELECT s.state,count(*) n FROM submission s JOIN package_revision p ON p.id=s.package_revision_id JOIN drug_trace_bill_revision r ON r.id=p.provenance->>'bill_revision_id' JOIN drug_trace_bill b ON b.id=r.bill_id WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' GROUP BY 1)x),
'window_quality',(SELECT json_object_agg(quality_state,n) FROM(SELECT quality_state,count(*) n FROM drug_trace_bill_revision r JOIN drug_trace_bill b ON b.id=r.bill_id WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' GROUP BY 1)x),
'new_unknowns',(SELECT json_agg(row_to_json(x)) FROM(SELECT s.id,s.state,s.submission_revision,encode(sha256(convert_to(p.payload->>'bill_code','UTF8')),'hex') bill_hash,json_array_length(p.payload->'codes') codes_count,a.started_at,a.http_status,a.outcome,a.response_snapshot->'error'->>'code' top_code,a.response_snapshot->'error'->>'sub_code' sub_code,a.response_snapshot->'error'->>'reason' reason,a.response_snapshot->'rate_limit' rate_limit,(SELECT json_agg(json_build_object('state',e.parsed_result->>'state','result_type',e.parsed_result->>'result_type','sub_process_flag',e.parsed_result->>'sub_process_flag','reason',e.parsed_result->>'reason','received_at',e.received_at)) FROM receipt_event e WHERE e.submission_ref=s.id) receipts,(SELECT json_agg(json_build_object('state',j.state,'available_at',j.available_at)) FROM job j WHERE j.job_type='drug_trace.query_status' AND j.payload->>'submission_id'=s.id AND j.state IN('PENDING','LEASED','RETRY_WAIT')) query_jobs FROM submission s JOIN package_revision p ON p.id=s.package_revision_id JOIN delivery_attempt a ON a.submission_id=s.id WHERE a.started_at>='2026-10-26 16:00:00' AND a.outcome='UNKNOWN' ORDER BY a.started_at LIMIT 10)x),
'quota',(SELECT json_agg(json_build_object('used',used,'max_bills',max_bills)) FROM drug_trace_send_quota),
'checkpoint',(SELECT json_agg(json_build_object('version',row_version,'since_time',cursor_json->>'since_time')) FROM source_checkpoint WHERE partition='r10-batch-daily'),
'pending',(SELECT json_agg(row_to_json(x)) FROM(SELECT job_type,state,count(*) n FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT') GROUP BY 1,2)x),
'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
'old_205',(SELECT json_build_object('n',count(*),'hash',md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id))) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state='UNKNOWN'))
"""
p = subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql], capture_output=True, text=True, timeout=25)
if p.returncode: raise RuntimeError(p.stderr[:400])
out = json.loads(p.stdout)
out.update(release=Path('/opt/blyb/current').resolve().name, utc=datetime.now(timezone.utc).isoformat())
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
