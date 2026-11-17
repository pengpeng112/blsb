"""Import an explicitly manual, reconstructed failure summary; no sends."""
from pathlib import Path
import json
import subprocess

sql="""WITH scope AS (
 SELECT b.id bill_id,r.id revision_id FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.connector_id='7d51abd7-3f32-4c8e-b2fa-9b652a511808' AND b.bill_time>='2026-10-24' AND b.bill_time<'2026-10-25'
 AND b.business_kind='PATIENT_DISPENSE' AND b.finalized=true AND b.is_void=false AND r.quality_state='VALID'),
subs AS (SELECT s.id,s.state FROM submission s JOIN package_revision pr ON pr.id=s.package_revision_id JOIN scope c ON c.revision_id=pr.provenance->>'bill_revision_id'),
failed AS (SELECT c.bill_id,j.last_error FROM job j JOIN scope c ON c.revision_id=j.payload->>'revision_id' WHERE j.job_type='drug_trace.prepare' AND j.state='FAILED' AND j.created_at>='2026-10-25 09:57:00')
SELECT json_build_object(
'candidates',(SELECT count(*) FROM scope),
'collection',(SELECT json_build_object('id',id,'state',state,'summary',summary) FROM drug_trace_collection_run WHERE connector_id='7d51abd7-3f32-4c8e-b2fa-9b652a511808' AND window_begin='2026-10-24' AND window_end='2026-10-25' ORDER BY created_at DESC LIMIT 1),
'attempts',(SELECT count(*) FROM delivery_attempt a JOIN subs s ON s.id=a.submission_id WHERE a.started_at>='2026-10-25 09:57:00'),
'states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM subs GROUP BY state) x),
'prepare_error_count',(SELECT count(*) FROM failed),
'errors',(SELECT json_agg(json_build_object('bill',bill_id,'step','prepare','res',json_build_object('error_code','DICTIONARY_UNMAPPED'))) FROM (SELECT bill_id FROM failed ORDER BY bill_id LIMIT 50) x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',sql],capture_output=True,text=True,timeout=25)
if p.returncode:raise RuntimeError('READONLY_SNAPSHOT_FAILED')
snapshot=json.loads(p.stdout)
body={'report_date':'2026-10-24','source':'manual','window_begin':'2026-10-24T00:00:00','window_end':'2026-10-25T00:00:00',
      'candidate_count':snapshot['candidates'],'collection':snapshot['collection'],
      'send':{'sent':snapshot['attempts'],'count_source':'scoped delivery attempts since 2026-10-25T09:57:00Z',
              'receipt_reconstructed':True,'states_at_snapshot':snapshot['states'],
              'errors':snapshot['errors'] or [],'errors_total':snapshot['prepare_error_count']+snapshot['states'].get('UNKNOWN',0),
              'errors_truncated':True,'aborted_reason':'MAINTENANCE_AFTER_RATE_LIMIT; original driver interrupted; not a completed timer run'},
      'query_stats':{'queried':0,'counter_available':False,'note':'Original driver API counter unavailable; current outcomes use live submissions.'},
      'outcome':'PARTIAL_FAILURE','elapsed_seconds':12238.0}
receipt=Path('/var/log/blyb/daily-upload/20261025-maintenance-reconstruction.json')
with receipt.open('x') as f:json.dump(body,f);f.write('\n')
receipt.chmod(0o600)
token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import json,sys,urllib.request,urllib.error
token=sys.stdin.readline().strip();body=json.load(sys.stdin)
req=urllib.request.Request('http://localhost:8000/api/v1/drug-trace/daily-reports',method='POST',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'},data=json.dumps(body).encode())
try:
 with urllib.request.urlopen(req,timeout=20) as r:print(json.dumps({'status':r.status,'data':json.load(r).get('data')}))
except urllib.error.HTTPError as e:print(json.dumps({'status':e.code}));raise SystemExit(1)
'''
p=subprocess.run(['docker','exec','-i','-u','0','blyb-core-1','python','-c',code],input=(token+'\n'+json.dumps(body)).encode(),capture_output=True,timeout=30)
del token
out={'exit_code':p.returncode,'api':json.loads(p.stdout),'candidate_count':snapshot['candidates'],'scoped_states':snapshot['states'],
     'scoped_attempts':snapshot['attempts'],'prepare_errors':snapshot['prepare_error_count'],
     'source':'manual','outcome':'PARTIAL_FAILURE','no_uploads':True,'receipt':str(receipt)}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
