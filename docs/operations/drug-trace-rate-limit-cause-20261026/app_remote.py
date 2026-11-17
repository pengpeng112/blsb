"""Application-only SELECT aggregates, plus actual running pacing source."""
from pathlib import Path
import hashlib,json,subprocess
sql="""WITH target AS(
 SELECT s.id FROM submission s JOIN package_revision p ON p.id=s.package_revision_id JOIN data_package d ON d.id=p.package_id
 WHERE d.domain='drug_trace' AND s.state IN('UNKNOWN','ACCEPTED'))
SELECT json_build_object(
 'query_jobs_for_old247',(SELECT count(*) FROM job j JOIN target t ON t.id=j.payload->>'submission_id' WHERE j.job_type='drug_trace.query_status'),
 'old_query_classification',(SELECT json_agg(row_to_json(x)) FROM(SELECT parsed_result->>'state' classification,parsed_result->>'reason' reason,count(*) n FROM receipt_event r JOIN target t ON t.id=r.submission_ref WHERE r.external_receipt_id LIKE 'query:%' GROUP BY 1,2)x),
 'upload429',(SELECT json_agg(row_to_json(x)) FROM(SELECT http_status,outcome,min(started_at) first_utc,max(started_at) last_utc,count(*) n FROM delivery_attempt a JOIN target t ON t.id=a.submission_id GROUP BY 1,2)x),
 'active_jobs',(SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT')),
 'scope_quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=30)
assert p.returncode==0,'LEDGER_AGGREGATE_FAILED'
out=json.loads(p.stdout)
code="""import hashlib,json
from pathlib import Path
from destinations.alihealth_drugtrace import transport,protocol
t=Path(transport.__file__).read_text();p=Path(protocol.__file__).read_text()
print(json.dumps({'transport_sha256':hashlib.sha256(t.encode()).hexdigest(),'interval_1_5_seconds':'interval=1.5' in t,
'http429_cooldown':'if status == 429:' in t,'transport_inspects_top_code7':'limited-by-api-access-count' in t or 'App Call Limited' in t,
'query_parser_keeps_subcode':'sub_code' in p[p.index('def parse_query_response'):]}))
"""
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=20)
assert p.returncode==0,'RUNNING_SOURCE_CHECK_FAILED'
out['running_transport']=json.loads(p.stdout);out['release']=Path('/opt/blyb/current').resolve().name
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
