"""Readonly ledger proof for the one historical retry; no platform/HIS calls."""
from pathlib import Path
import hashlib,json,subprocess
baseline=json.loads(Path('/root/blyb-maintenance-backups/upgrade-flow-20261026T132000Z/baseline.json').read_text())
sql="""SELECT json_build_object(
 'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
 'submissions',(SELECT count(*) FROM submission),
 'attempts',(SELECT count(*) FROM delivery_attempt),
 'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
 'active_jobs',(SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT')),
 'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
 'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
 'old_247_projected_hash',(SELECT md5(string_agg(id||':'||CASE WHEN id='9b19170e-5ceb-4da4-99bb-7478c77d9695' THEN 'UNKNOWN' ELSE state END||':'||CASE WHEN id='9b19170e-5ceb-4da4-99bb-7478c77d9695' THEN '3' ELSE submission_revision::text END,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND (state IN('UNKNOWN','ACCEPTED') OR id='9b19170e-5ceb-4da4-99bb-7478c77d9695')),
 'decision',(SELECT json_build_object('action',action,'actor',actor,'max_uploads',after_ref->>'max_uploads') FROM manual_decision WHERE id='94184837-4fc8-50e8-b9c6-c499c3415e33'),
 'target_attempts',(SELECT json_agg(row_to_json(x)) FROM(SELECT attempt_no,http_status,outcome FROM delivery_attempt WHERE submission_id='9b19170e-5ceb-4da4-99bb-7478c77d9695' ORDER BY attempt_no)x),
 'historical_query_generations',(SELECT count(*) FROM job WHERE job_type='drug_trace.query_status' AND dedup_key LIKE 'q:9b19170e-5ceb-4da4-99bb-7478c77d9695:%'),
 'target_state',(SELECT state FROM submission WHERE id='9b19170e-5ceb-4da4-99bb-7478c77d9695'),
 'confirmed_query_receipts',(SELECT count(*) FROM receipt_event WHERE submission_ref='9b19170e-5ceb-4da4-99bb-7478c77d9695' AND parsed_result->>'state'='CONFIRMED' AND parsed_result->>'result_type'='3' AND parsed_result->>'sub_process_flag'='51'),
 'target_digest',(SELECT snapshot_digest FROM submission WHERE id='9b19170e-5ceb-4da4-99bb-7478c77d9695'))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=25)
assert p.returncode==0,'LEDGER_QUERY_FAILED'
out=json.loads(p.stdout)
out['other_246_unchanged']=out.pop('old_247_projected_hash')==baseline['protected_old_hash']
out['checkpoint_unchanged']=out['checkpoint']==baseline['checkpoint']
out['target_digest_unchanged']=out.pop('target_digest')=='6c9c68a54439d062129ab32d66759c5f684763d311176524239865a3da9a2c4a'
out['quota_increased_one']=sum(x['used'] for x in out['quota'])==sum(x['used'] for x in baseline['quota'])+1
out['release']=Path('/opt/blyb/current').resolve().name
for key,args in {
 'containers':['docker','ps','--format','{{.Names}} {{.Status}}'],
 'units':['systemctl','show','blyb-daily-upload.timer','-p','ActiveState','-p','SubState'],
}.items():
 p=subprocess.run(args,capture_output=True,text=True,timeout=15);assert p.returncode==0
 out[key]=p.stdout.strip()
assert out['other_246_unchanged'] and out['checkpoint_unchanged'] and out['target_digest_unchanged'] and out['quota_increased_one']
assert out['historical_query_generations']==12 and out['inflight']==0 and out['submissions']==2823
assert len(out['target_attempts'])==2 and out['target_attempts'][0]['http_status']==429 and out['target_attempts'][1]['http_status']==200
assert out['target_state']=='CONFIRMED' and out['confirmed_query_receipts']==1
assert sum('healthy' in line for line in out['containers'].splitlines())==6
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
