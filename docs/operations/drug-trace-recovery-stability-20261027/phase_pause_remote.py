from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=30)
 assert p.returncode==0,args[0]+'_FAILED'
 return p.stdout.strip()
assert Path('/opt/blyb/current').resolve().name=='stability-20261027T030000Z-r2','RELEASE_DRIFT'
assert json.loads(Path('/var/log/blyb/recovery-stability-20261027-resumed/final.json').read_text())['outcome']=='ALL_CONFIRMED','RECOVERY_NOT_COMPLETE'
old=json.loads(Path('/root/blyb-maintenance-backups/old41-confirmations-20261027/result.json').read_text())
assert old['exit_code']==0 and old['result']['outcome']=='ALL_41_CONFIRMED','OLD41_NOT_COMPLETE'
assert run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value']) not in ('active','activating','deactivating')
folder=Path('/root/blyb-maintenance-backups/upgrade-stability-20261027T064000Z-r3');folder.mkdir(mode=0o700)
run(['systemctl','stop','blyb-daily-upload.timer'])
try:
 baseline=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',"SELECT json_build_object(\n'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),\n'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),\n'ledger_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text||':'||snapshot_digest,',' ORDER BY id)) FROM submission),\n'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),\n'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),\n'active_jobs',(SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT')),\n'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL))"]))
 assert baseline['states']=={'CONFIRMED':4613,'UNKNOWN':205}
 assert baseline['active_jobs']==baseline['inflight']==0,'ACTIVE_WORK_REQUIRES_DRAIN'
 assert baseline['protected_old_hash']=='7a691905901917167cf999c3d8f4c202'
 (folder/'baseline.json').write_text(json.dumps(baseline))
 print('__MAINTENANCE__'+json.dumps({'outcome':'DRAINED_AFTER_RECOVERY','baseline':baseline,'utc':datetime.now(timezone.utc).isoformat()})+'__END__')
except BaseException:
 subprocess.run(['systemctl','start','blyb-daily-upload.timer'],capture_output=True);raise
