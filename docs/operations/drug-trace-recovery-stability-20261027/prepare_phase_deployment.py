"""Prepare the UI successor deployment, gated on all recovery work finishing."""
from pathlib import Path
import hashlib,json,re
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OLD='stability-20261027T030000Z-r2';NEW='stability-20261027T064000Z-r3'
FOLDER='/root/blyb-maintenance-backups/upgrade-'+NEW
driver_source=(ROOT/'docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py').read_text(encoding='utf8')
driver_after=hashlib.sha256(driver_source.encode()).hexdigest()
assert driver_after!='470b4bcca8dc28fee9d990a0d77a28e070baf8f156d5fae38b65a2014863213d'
compile(driver_source,'final-budget-driver','exec')

sql="""SELECT json_build_object(
'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
'ledger_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text||':'||snapshot_digest,',' ORDER BY id)) FROM submission),
'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
'active_jobs',(SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT')),
'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL))"""
pause='''from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=30)
 assert p.returncode==0,args[0]+'_FAILED'
 return p.stdout.strip()
assert Path('/opt/blyb/current').resolve().name==OLD,'RELEASE_DRIFT'
assert json.loads(Path('/var/log/blyb/recovery-stability-20261027-resumed/final.json').read_text())['outcome']=='ALL_CONFIRMED','RECOVERY_NOT_COMPLETE'
old=json.loads(Path('/root/blyb-maintenance-backups/old41-confirmations-20261027/result.json').read_text())
assert old['exit_code']==0 and old['result']['outcome']=='ALL_41_CONFIRMED','OLD41_NOT_COMPLETE'
assert run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value']) not in ('active','activating','deactivating')
folder=Path(FOLDER);folder.mkdir(mode=0o700)
run(['systemctl','stop','blyb-daily-upload.timer'])
try:
 baseline=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',SQL]))
 assert baseline['states']=={'CONFIRMED':4613,'UNKNOWN':205}
 assert baseline['active_jobs']==baseline['inflight']==0,'ACTIVE_WORK_REQUIRES_DRAIN'
 assert baseline['protected_old_hash']=='7a691905901917167cf999c3d8f4c202'
 (folder/'baseline.json').write_text(json.dumps(baseline))
 print('__MAINTENANCE__'+json.dumps({'outcome':'DRAINED_AFTER_RECOVERY','baseline':baseline,'utc':datetime.now(timezone.utc).isoformat()})+'__END__')
except BaseException:
 subprocess.run(['systemctl','start','blyb-daily-upload.timer'],capture_output=True);raise
'''
pause=re.sub(r'\bOLD\b',lambda _:repr(OLD),pause).replace('FOLDER',repr(FOLDER)).replace('SQL',repr(sql))
compile(pause,'phase-pause','exec');(HERE/'phase_pause_remote.py').write_text(pause,encoding='utf8')

wrapper='''from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess
folder=Path(FOLDER);driver=Path('/opt/blyb/tools/blyb-daily-upload.py')
code=99;details={}
try:
 before=hashlib.sha256(driver.read_bytes()).hexdigest()
 assert before=='470b4bcca8dc28fee9d990a0d77a28e070baf8f156d5fae38b65a2014863213d','DRIVER_DRIFT'
 shutil.copy2(driver,folder/'daily-upload.before.py')
 with (folder/'upgrade.log').open('xb') as log:
  code=subprocess.run(['bash','<prod-path>-staging/NEW/implementation/deploy/linux/upgrade.sh','<prod-path>-staging/NEW'],stdout=log,stderr=subprocess.STDOUT).returncode
 assert hashlib.sha256(driver.read_bytes()).hexdigest()==before,'UNEXPECTED_DRIVER_CHANGE'
 if code==0:
  source=DRIVER_SOURCE;compile(source,'new-budget-driver','exec')
  temp=driver.with_name('blyb-daily-upload.budget.next.py');assert not temp.exists()
  temp.write_text(source);temp.chmod(0o700);os.replace(temp,driver)
  assert hashlib.sha256(driver.read_bytes()).hexdigest()==DRIVER_AFTER
  details.update(driver_before_sha256=before,driver_after_sha256=DRIVER_AFTER,overall_budget_includes_queue=True)
except BaseException as exc:
 code=99;details['error_type']=type(exc).__name__
finally:
 timer=subprocess.run(['systemctl','start','blyb-daily-upload.timer']).returncode
 (folder/'result.json').write_text(json.dumps({'exit_code':code,'timer_restart_exit':timer,'finished_utc':datetime.now(timezone.utc).isoformat(),**details})+'\\n')
'''.replace('FOLDER',repr(FOLDER)).replace('NEW',NEW)
wrapper=wrapper.replace('DRIVER_SOURCE',repr(driver_source)).replace('DRIVER_AFTER',repr(driver_after))
compile(wrapper,'phase-upgrade-wrapper','exec')
launch='''from pathlib import Path
import hashlib,json,subprocess
assert Path('/opt/blyb/current').resolve().name==OLD,'RELEASE_DRIFT'
folder=Path(FOLDER);assert (folder/'baseline.json').is_file()
assert not (folder/'run.py').exists(),'UPGRADE_ALREADY_USED'
release=Path('<prod-path>-staging/NEW')
for line in (release/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1);assert hashlib.sha256((release/name).read_bytes()).hexdigest()==digest,'RELEASE_HASH_DRIFT'
for f in (release/'implementation/deploy/linux').glob('*.sh'):f.chmod(0o755)
code=WRAPPER;(folder/'run.py').write_text(code)
with (folder/'launcher.log').open('xb') as log:
 p=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':p.pid,'release':'NEW','no_business_work_started':True})+'__END__')
'''
launch=re.sub(r'\bOLD\b',lambda _:repr(OLD),launch).replace('FOLDER',repr(FOLDER)).replace('NEW',NEW)
launch=launch.replace('WRAPPER',repr(wrapper))
compile(launch,'phase-upgrade-launcher','exec');(HERE/'phase_upgrade_remote.py').write_text(launch,encoding='utf8')

status=(HERE/'upgrade_status_remote.py').read_text(encoding='utf8').replace(OLD,NEW)
(HERE/'phase_upgrade_status_remote.py').write_text(status,encoding='utf8')
verify=(HERE/'verify_remote.py').read_text(encoding='utf8').replace(OLD,NEW).replace('old_246_unchanged','old_205_unchanged')
needle="'auth_migration',(SELECT count(*) FROM schema_migrations"
assert verify.count(needle)==1
verify=verify.replace(needle,"'ledger_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text||':'||snapshot_digest,',' ORDER BY id)) FROM submission),\n"+needle)
needle="out['nginx_config']=run("
assert verify.count(needle)==1
verify=verify.replace(needle,"out['all_4818_submissions_unchanged']=after['ledger_hash']==baseline['ledger_hash'];assert out['all_4818_submissions_unchanged']\n"+needle)
needle="out['containers']=run("
assert verify.count(needle)==1
verify=verify.replace(needle,"out['ui']['collection_failure_present']=any('等待采集完成超时'.encode() in get(path)[1] and '未统计'.encode() in get(path)[1] for path in assets if path.endswith('.js'));assert out['ui']['collection_failure_present']\n"+needle)
compile(verify,'phase-verify','exec');(HERE/'phase_verify_remote.py').write_text(verify,encoding='utf8')

runtime='''from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess
release=Path('/opt/blyb/current').resolve();assert release.name==NEW
base=Path('/opt/blyb/releases/OLD')
manifest=json.loads((release/'manifest.json').read_text())
assert manifest['delta']['base_release']==OLD and not manifest['delta']['dependencies_changed']
core=['apps/worker/engine.py','packages/workflow_core/jobs.py','packages/workflow_core/lease_guard.py','plugins/domains/drug_trace/ingest.py','plugins/domains/drug_trace/plugin.py','plugins/domains/drug_trace/service.py','plugins/sources/db_view/drug_trace.py']
front=['apps/admin_web/src/views/DrugTrace.vue','apps/admin_web/src/views/drugTraceDisplay.ts','apps/admin_web/tests/drugTraceDisplay.test.mjs']
assert set(manifest['delta']['files'])==set(front)
for name in core:assert (base/'implementation'/name).read_bytes()==(release/'implementation'/name).read_bytes()
expected={n:hashlib.sha256((release/'implementation'/n).read_bytes()).hexdigest() for n in core+front}
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=25);assert p.returncode==0;return p.stdout.strip()
code="from pathlib import Path;import hashlib,json,sys;print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in json.loads(sys.argv[1])}))"
out={'release':NEW,'core_runtime_unchanged':True,'containers':{}}
for name in ('blyb-core-1','<worker-container>'):
 assert json.loads(run(['docker','exec',name,'python','-c',code,json.dumps(core+front)]))==expected
 assert run(['docker','inspect','--format','{{.Image}}',name])==manifest['images']['backend']
 out['containers'][name]={'ten_files_match':True,'image':manifest['images']['backend']}
out['driver_sha256']=hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()
assert out['driver_sha256']==DRIVER_AFTER
out['utc']=datetime.now(timezone.utc).isoformat()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
'''.replace('NEW',repr(NEW)).replace("'/opt/blyb/releases/OLD'",repr('/opt/blyb/releases/'+OLD)).replace('OLD',repr(OLD)).replace('DRIVER_AFTER',repr(driver_after))
compile(runtime,'phase-runtime','exec');(HERE/'phase_runtime_remote.py').write_text(runtime,encoding='utf8')
(HERE/'evidence/phase-deployment-bindings.json').write_text(json.dumps({'release':NEW,'driver_before':'470b4bcca8dc28fee9d990a0d77a28e070baf8f156d5fae38b65a2014863213d','driver_after':driver_after,'core_runtime_unchanged':True,'frontend_delta_files':3},indent=2),encoding='utf8')
print('Prepared UI upgrade gated on all1995 confirmed and old41 completed; no remote actions')
