"""Upgrade frontend-only release only after the existing recovery exits naturally."""
from pathlib import Path
import json, os, subprocess

RID='ui-clarity-20261026T063000Z'
release=Path('<prod-path>-staging')/RID
folder=Path('/root/blyb-maintenance-backups/upgrade-'+RID)
def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if p.returncode: raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()
def idle():
    for unit in ['blyb-daily-upload.service','blyb-mapping-recovery-20261026.service','blyb-mapping-recovery-auth-20261026.service']:
        state=run(['systemctl','show',unit,'-p','ActiveState','--value'])
        if state in ('active','activating','deactivating'): raise RuntimeError('DRIVER_STILL_ACTIVE:'+unit)
    sql="SELECT (SELECT count(*) FROM submission WHERE state='SENDING')+(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL)+(SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT'))"
    if run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql])!='0': raise RuntimeError('BUSINESS_JOBS_ACTIVE')

assert Path('/opt/blyb/current').resolve().name=='auth-20261026T043500Z','RELEASE_DRIFT'
manifest=json.loads((release/'manifest.json').read_text())
assert manifest['release_id']==RID and all(x.startswith('apps/admin_web/') for x in manifest['delta']['files'])
final=json.loads(Path('/var/log/blyb/mapping-recovery-auth-20261026/final.json').read_text())
assert final['outcome']=='DRIVER_WINDOWS_COMPLETED' and all(x['exit_code']==0 and x['outcome']=='OK' for x in final['dates']),'RECOVERY_NOT_COMPLETE'
idle()
timer=run(['systemctl','show','blyb-daily-upload.timer','-p','ActiveState','--value'])
assert timer=='active','TIMER_WAS_NOT_ACTIVE'
subprocess.run(['systemctl','stop','blyb-daily-upload.timer'],check=True)
try:
    idle()
    folder.mkdir(mode=0o700,exist_ok=False)
    sql="""SELECT json_build_object(
      'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
      'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
      'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
      'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x))"""
    baseline=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))
    (folder/'baseline.json').write_text(json.dumps(baseline,indent=2)+'\n')
    for f in (release/'implementation/deploy/linux').glob('*.sh'): f.chmod(0o755)
    wrapper='''from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
p=Path('''+repr(str(folder))+''')
code=99
try:
 with (p/'upgrade.log').open('wb') as f:
  code=subprocess.run(['bash','''+repr(str(release/'implementation/deploy/linux/upgrade.sh'))+','+repr(str(release))+'''],stdout=f,stderr=subprocess.STDOUT).returncode
finally:
 timer=subprocess.run(['systemctl','start','blyb-daily-upload.timer']).returncode
 (p/'result.json').write_text(json.dumps({'exit_code':code,'timer_restart_exit':timer,'finished_utc':datetime.now(timezone.utc).isoformat()})+'\\n')
'''
    compile(wrapper,'ui-upgrade-wrapper','exec')
    (folder/'run.py').write_text(wrapper);os.chmod(str(folder/'run.py'),0o700)
    with (folder/'launcher.log').open('wb') as f:
        child=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
except BaseException:
    subprocess.run(['systemctl','start','blyb-daily-upload.timer'],check=True)
    raise
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':child.pid,'release':RID,'logs':str(folder),'recovery_finished_naturally':True})+'__END__')
