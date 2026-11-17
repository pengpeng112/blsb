"""Resume the standing-approved completed day through the normal driver."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json
import subprocess


def run(args,stdin=None):
    p=subprocess.run(args,input=stdin,capture_output=True,timeout=35)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.decode().strip()


if datetime.now(timezone(timedelta(hours=8))).date().isoformat()!='2026-10-26':raise RuntimeError('RECOVERY_DATE_GUARD')
if Path('/opt/blyb/current').resolve().name!='queue-20261026T010000Z-r2':raise RuntimeError('NEW_RELEASE_NOT_ACTIVE')
if run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value']) in ('active','activating','deactivating'):
    raise RuntimeError('DRIVER_ALREADY_ACTIVE')
probe='''import importlib.util,io,json,sys,inspect
from core_db import make_engine,make_session_factory
from core_db.models import Tenant,Destination
from apps.worker.engine import WorkerEngine
sys.stdin=io.StringIO('public-readonly-preflight\\n')
spec=importlib.util.spec_from_file_location('driver_preflight','/tmp/queue-recovery-driver.py')
D=importlib.util.module_from_spec(spec);spec.loader.exec_module(D)
engine=make_engine();db=make_session_factory(engine)()
try:
 tenant=db.query(Tenant).filter_by(code='first-hospital').one()
 destination=db.query(Destination).filter_by(tenant_id=tenant.id,code=D.DEST_CODE).one()
 eligible=D.eligible_queries(db,tenant.id,destination.id)
 print(json.dumps({'eligible_query_count':len(eligible),'query':D.receipt['query'],
  'queue_wait_seconds':D.SEND_QUEUE_WAIT_SECONDS,'execution_wait_seconds':D.SEND_EXECUTION_WAIT_SECONDS,
  'fair_background_slots':'n % 5 == 4' in inspect.getsource(WorkerEngine._run_due_jobs)}))
finally:db.close();engine.dispose()
'''
run(['docker','cp','/opt/blyb/tools/blyb-daily-upload.py','blyb-core-1:/tmp/queue-recovery-driver.py'])
preflight=json.loads(run(['docker','exec','-i','-u','0','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-core-1','python','-c',probe]))
if not preflight['fair_background_slots']:raise RuntimeError('WORKER_FAIRNESS_NOT_PRESENT')
sql="""SELECT json_build_object(
'states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission GROUP BY state) x),
'protected_old_rows',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN ('UNKNOWN','ACCEPTED')),
'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills FROM drug_trace_send_quota) x),
'checkpoint',(SELECT json_agg(row_to_json(x)) FROM (SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily') x))"""
baseline=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',sql]))
folder=Path('/root/blyb-maintenance-backups/queue-recovery-20261026')
folder.mkdir(mode=0o700,exist_ok=False)
(folder/'baseline.json').write_text(json.dumps({'preflight':preflight,'baseline':baseline})+'\n')
run(['systemctl','enable','--now','blyb-daily-upload.timer'])
run(['systemctl','start','--no-block','blyb-daily-upload.service'])
out={'started':True,'business_date':'2026-10-25','normal_unit':True,'baseline_file':str(folder/'baseline.json'),
     'preflight':preflight,'baseline':baseline,'approvals_or_quota_reset':False,'unknowns_reuploaded':False}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
