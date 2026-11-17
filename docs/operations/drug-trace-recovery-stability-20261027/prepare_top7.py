"""Bind the existing one-POST tool to this day's single known TOP7 failure.

This is an operations exception within the already authorized 1995-bill batch.
It cannot operate on any of the 205 historical UNKNOWN submissions.
"""
from pathlib import Path
import hashlib, json
HERE=Path(__file__).resolve().parent
source=(HERE.parent/'drug-trace-history-retry-20261026/trial_container.py').read_text(encoding='utf8')

def replace(old,new):
    global source
    assert source.count(old)==1,old[:80]
    source=source.replace(old,new)

replace("TARGET='9b19170e-5ceb-4da4-99bb-7478c77d9695'", "TARGET='8528553c-c9db-4548-bffd-d610ce876e41'")
replace("BILL_HASH='c59321aefc1d98e5246f1d622e1af4f3e86af2e85246d0d79e26162c167d618c'", "BILL_HASH='30b7d74a981b934b040d949aeade7b7039c0e9136c5d33b3f6ee49b270db28bb'")
replace("TASK='DTR-HISTORY-RETRY-20261026'", "TASK='DTR-RECOVERY-STABILITY-20261027-TOP7-ONCE'")
replace("ROOT=Path('/data/exchange/history-retry-20261026-one-r2')", "ROOT=Path('/data/exchange/recovery-top7-20261027-one')")
replace("'last_http_status':429", "'last_http_status':200,'top_code':'7','sub_code':'accesscontrol.limited-by-api-access-count'")
replace("'last_http_status':attempts[-1].http_status if len(attempts)==1 else None,", "'last_http_status':attempts[-1].http_status if len(attempts)==1 else None,\n                'top_code':(attempts[0].response_snapshot.get('error') or {}).get('code') if len(attempts)==1 else None,\n                'sub_code':(attempts[0].response_snapshot.get('error') or {}).get('sub_code') if len(attempts)==1 else None,")
replace("'active_jobs':db.query(Job).filter(Job.state.in_(['PENDING','LEASED','RETRY_WAIT'])).count(),", "'active_jobs':db.query(Job).filter(Job.state.in_(['PENDING','LEASED','RETRY_WAIT']),\n                    ~((Job.job_type==S.C.DomainJobType.QUERY)&(Job.payload['submission_id'].as_string()==TARGET)&(Job.state.in_(['PENDING','RETRY_WAIT']))&(Job.available_at>utcnow()+__import__('datetime').timedelta(seconds=120)))).count(),")
replace("assert before_jobs==after_jobs==12,'HISTORICAL_QUERY_LEDGER_CHANGED'", "assert after_jobs>=before_jobs,'QUERY_LEDGER_REMOVED'")
replace("'snapshot_digest':pr.digest,'query_generations':12,'bill_hash':BILL_HASH", "'snapshot_digest':pr.digest,'query_generations':query_count(db),'bill_hash':BILL_HASH")
replace("assert query_count(db)==12,'HISTORICAL_QUERY_LEDGER_CHANGED'", "assert 1<=query_count(db)<=11,'CURRENT_QUERY_BUDGET_UNEXPECTED'")
replace("reason='User explicitly authorized historical resend test in this turn; one bill/one POST only after fresh full NOT_FOUND. '+TASK", "reason='User authorized recovery of existing DONE 10-26 batch and asked to continue; this exact one TOP7 failure only, after fresh complete NOT_FOUND. No historical UNKNOWN batch resend. '+TASK")
replace("rev=db.get(DrugTraceBillRevision,pr.provenance['bill_revision_id']);bill=db.get(DrugTraceBill,rev.bill_id)", "rev=db.get(DrugTraceBillRevision,pr.provenance['bill_revision_id']);bill=db.get(DrugTraceBill,rev.bill_id)\n            assert bill.bill_time.date().isoformat()=='2026-10-26','OUTSIDE_AUTHORIZED_BATCH'\n            assert sub.created_at>=datetime(2026,10,26,16),'HISTORICAL_TARGET_BLOCKED'\n            assert datetime.now(timezone.utc).date().isoformat()=='2026-10-27','DATE_GUARD'")
source=source.replace('history-retry:','recovery-top7:').replace('HISTORY_RETRY_ONCE','RECOVERY_TOP7_RETRY_ONCE').replace('drug_trace.history_retry.authorize','drug_trace.recovery_top7.authorize')
source=source.replace('One explicitly authorized historical retry. Never run for a batch.', 'One bounded retry of the authorized daily batch TOP7 failure. Never historical batch retry.')
compile(source,'top7-container','exec');(HERE/'top7_container.py').write_text(source,encoding='utf8')

folder='/root/blyb-maintenance-backups/recovery-top7-20261027-one'
wrapper='''from pathlib import Path
import json,subprocess,re,os
os.umask(0o077)
folder=Path(FOLDER)
try:
 with (folder/'stdout.log').open('xb') as output,(folder/'stderr.log').open('xb') as error:
  p=subprocess.run(['docker','exec','-i','<worker-container>','python','-','send'],input=(folder/'trial.py').read_bytes(),stdout=output,stderr=error,timeout=300)
 raw=(folder/'stdout.log').read_text();m=re.search(r'__TRIAL__(.*?)__END__',raw,re.S)
 out={'exit_code':p.returncode,'result':json.loads(m.group(1)) if m else {'marker_missing':True}}
except BaseException as exc:out={'exit_code':99,'error_type':type(exc).__name__}
(folder/'final.json').write_text(json.dumps(out,indent=2)+'\\n')
'''.replace('FOLDER',repr(folder))
launch='''from pathlib import Path
import json,subprocess
assert Path('/opt/blyb/current').resolve().name=='stability-20261027T030000Z-r2'
verified=json.loads(Path('/root/blyb-maintenance-backups/upgrade-stability-20261027T030000Z-r2/ui-verified.json').read_text())
assert verified['old_246_unchanged'] and verified['quota_unchanged'] and verified['checkpoint_unchanged']
assert not Path('/var/log/blyb/recovery-stability-20261027-resumed').exists(),'RUN_RECOVERY_AFTER_SINGLE_RETRY'
folder=Path(FOLDER);folder.mkdir(mode=0o700)
(folder/'trial.py').write_text(SOURCE);(folder/'run.py').write_text(WRAPPER)
with (folder/'launcher.log').open('xb') as log:
 p=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':p.pid,'max_uploads':1,'target':'8528553c-c9db-4548-bffd-d610ce876e41'})+'__END__')
'''.replace('FOLDER',repr(folder)).replace('SOURCE',repr(source)).replace('WRAPPER',repr(wrapper))
compile(wrapper,'top7-wrapper','exec');compile(launch,'top7-launch','exec')
(HERE/'top7_send_remote.py').write_text(launch,encoding='utf8')
for phase in ('status','query'):
    code='''from pathlib import Path
import json,subprocess,re
folder=Path(FOLDER)
out={'runner':json.loads((folder/'final.json').read_text()) if (folder/'final.json').exists() else None}
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-',MODE],input=(folder/'trial.py').read_bytes(),capture_output=True,timeout=150)
m=re.search(r'__TRIAL__(.*?)__END__',p.stdout.decode(),re.S)
out.update(container_exit=p.returncode,progress=json.loads(m.group(1)) if m else {'marker_missing':True})
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
'''.replace('FOLDER',repr(folder)).replace('MODE',repr(phase))
    compile(code,'top7-'+phase,'exec');(HERE/('top7_'+phase+'_remote.py')).write_text(code,encoding='utf8')
(HERE/'evidence/top7-binding.json').write_text(json.dumps({'target':'8528553c-c9db-4548-bffd-d610ce876e41','max_uploads':1,'source_sha256':hashlib.sha256(source.encode()).hexdigest()},indent=2),encoding='utf8')
tests=(HERE.parents[2]/'implementation/tests/integration/test_drug_trace_history_retry.py').read_text(encoding='utf8')
tests=tests.replace('docs/operations/drug-trace-history-retry-20261026/trial_container.py','docs/operations/drug-trace-recovery-stability-20261027/top7_container.py')
tests=tests.replace("last_http_status=429,", "last_http_status=200,top_code='7',sub_code='accesscontrol.limited-by-api-access-count',")
tests=tests.replace("{'last_http_status':200}","{'last_http_status':429},{'top_code':'5'},{'sub_code':'other'}")
(HERE.parents[2]/'implementation/tests/integration/test_drug_trace_recovery_top7.py').write_text(tests,encoding='utf8')
print('Bound current-day TOP7 exception; no network or business writes')
