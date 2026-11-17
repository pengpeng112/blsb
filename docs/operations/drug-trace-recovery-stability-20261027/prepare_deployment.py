"""Prepare exact-hash deployment scripts; never deploys by itself."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
RID='stability-20261027T030000Z-r2'
OLD='flow-20261026T132000Z'
driver=(ROOT/'docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py').read_text(encoding='utf8')
old_sha='cf967102e68c4978cc17ba4739af6860de60344404727f6cc8eedf1b4a851c39'
new_sha=hashlib.sha256(driver.encode()).hexdigest()
assert new_sha!=old_sha
pause=(HERE.parent/'username-password-login-20261026/pause_remote.py').read_text(encoding='utf8')
pause=pause.replace('queue-20261026T010000Z-r2',OLD)
pause=pause.replace("/root/blyb-maintenance-backups/auth-20261026","/root/blyb-maintenance-backups/stability-20261027")
pause=pause.replace('blyb-mapping-recovery-20261026','blyb-recovery-stability-20261027')
pause=pause.replace('/var/log/blyb/mapping-recovery-20261026','/var/log/blyb/recovery-stability-20261027')
pause=pause.replace('/tmp/mapping-recovery-driver.py','/tmp/recovery-stability-20261027.py')
pause=pause.replace('INTERRUPTED_FOR_LOGIN_UPGRADE','INTERRUPTED_FOR_STABILITY_UPGRADE')
pause=pause.replace('Planned upgrade; completed sends retained','Planned collection/lease stability upgrade; completed sends retained')
pause=pause.replace('old_247_unchanged','old_246_unchanged')
compile(pause,'pause-stability','exec');(HERE/'pause_remote.py').write_text(pause,encoding='utf8')

# The fixed-date runner resumes guarded PREPARED states and keeps source=manual.
resumed=(HERE/'recover_runner.py').read_text(encoding='utf8')
resumed=resumed.replace("Path('/var/log/blyb/recovery-stability-20261027')","Path('/var/log/blyb/recovery-stability-20261027-resumed')")
resumed=resumed.replace("EXPECTED='"+old_sha+"'","EXPECTED='"+new_sha+"'")
resumed=resumed.replace("name=='"+OLD+"'","name=='"+RID+"'")
# Explicitly observe terminal state after the driver completes; no extra network requests.
needle="    save('final.json',state)\n    return 0 if child.returncode==0 and state['old_246_unchanged'] and state['checkpoint_unchanged'] else 1"
replacement="""    save('driver-final.json',state)
    if child.returncode==0:
        deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            current=snapshot();counts=current.get('window_states') or {}
            state.update(outcome='DRIVER_COMPLETED_AWAITING_PLATFORM',snapshot=current,updated_utc=datetime.now(timezone.utc).isoformat())
            save('progress.json',state)
            if counts.get('CONFIRMED',0)==1995:
                state['outcome']='ALL_CONFIRMED';break
            if counts.get('UNKNOWN',0) or counts.get('REJECTED',0) or counts.get('PARTIAL',0):
                state['outcome']='BUSINESS_REVIEW_REQUIRED';break
            time.sleep(30)
        else:state['outcome']='PENDING_PLATFORM'
    save('final.json',state)
    return 0 if state['outcome']=='ALL_CONFIRMED' and state['old_246_unchanged'] and state['checkpoint_unchanged'] else 1"""
assert needle in resumed;resumed=resumed.replace(needle,replacement)
compile(resumed,'resumed-runner','exec');(HERE/'recover_runner_resumed.py').write_text(resumed,encoding='utf8')

wrapper='''from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess
folder=Path(FOLDER)
driver=Path('/opt/blyb/tools/blyb-daily-upload.py')
code=99;details={}
try:
 assert hashlib.sha256(driver.read_bytes()).hexdigest()==OLD_SHA,'DRIVER_DRIFT'
 shutil.copy2(driver,folder/'daily-upload.before.py')
 with (folder/'upgrade.log').open('xb') as log:
  code=subprocess.run(['bash','<prod-path>-staging/RELEASE/implementation/deploy/linux/upgrade.sh','<prod-path>-staging/RELEASE'],stdout=log,stderr=subprocess.STDOUT).returncode
 if code==0:
  source=DRIVER_SOURCE;compile(source,'new-driver','exec')
  temporary=driver.with_name('blyb-daily-upload.stability.next.py')
  assert not temporary.exists();temporary.write_text(source);temporary.chmod(0o700);os.replace(temporary,driver)
  assert hashlib.sha256(driver.read_bytes()).hexdigest()==NEW_SHA
  details['driver_sha256']=NEW_SHA
  runner=Path('/opt/blyb/tools/blyb-recovery-stability-20261027-resumed.py')
  assert not runner.exists();runner.write_text(RUNNER_SOURCE);runner.chmod(0o700)
  details['recovery_resume']='STAGED_AWAITING_VERIFICATION'
except BaseException as exc:
 code=99;details['error_type']=type(exc).__name__
finally:
 timer=subprocess.run(['systemctl','start','blyb-daily-upload.timer']).returncode
 (folder/'result.json').write_text(json.dumps({'exit_code':code,'timer_restart_exit':timer,'finished_utc':datetime.now(timezone.utc).isoformat(),**details})+'\\n')
'''
folder='/root/blyb-maintenance-backups/upgrade-'+RID
wrapper=wrapper.replace('FOLDER',repr(folder)).replace('OLD_SHA',repr(old_sha)).replace('NEW_SHA',repr(new_sha)).replace('RELEASE',RID)
wrapper=wrapper.replace('DRIVER_SOURCE',repr(driver)).replace('RUNNER_SOURCE',repr(resumed))
compile(wrapper,'detached-upgrade','exec')
launch='''from pathlib import Path
import hashlib,json,os,subprocess
assert Path('/opt/blyb/current').resolve().name==OLD,'RELEASE_DRIFT'
pause=json.loads(Path('/root/blyb-maintenance-backups/stability-20261027/pause.json').read_text())
assert not any(pause['after'][k] for k in ['sending','inflight','pending_sends'])
folder=Path(FOLDER);folder.mkdir(mode=0o700)
(folder/'baseline.json').write_text(json.dumps(pause['after']))
release=Path('<prod-path>-staging/RELEASE')
assert release.is_dir()
for line in (release/'SHA256SUMS').read_text().splitlines():
 digest,name=line.split('  ',1)
 assert hashlib.sha256((release/name).read_bytes()).hexdigest()==digest,'RELEASE_HASH_DRIFT'
for f in (release/'implementation/deploy/linux').glob('*.sh'):f.chmod(0o755)
code=WRAPPER;compile(code,'upgrade-wrapper','exec');(folder/'run.py').write_text(code)
with (folder/'launcher.log').open('xb') as log:
 p=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':p.pid,'release':'RELEASE'})+'__END__')
'''.replace('FOLDER',repr(folder)).replace('OLD',repr(OLD)).replace('RELEASE',RID)
launch=launch.replace('WRAPPER',repr(wrapper))
compile(launch,'upgrade-launcher','exec');(HERE/'upgrade_remote.py').write_text(launch,encoding='utf8')
for name in ['upgrade_status_remote.py','verify_remote.py']:
    text=(HERE.parent/'drug-trace-upload-flow-20261026'/name).read_text(encoding='utf8').replace('flow-20261026T132000Z',RID).replace('old_247_unchanged','old_246_unchanged')
    (HERE/name).write_text(text,encoding='utf8')
status=(HERE/'recovery_status_remote.py').read_text(encoding='utf8')
status=status.replace("Path('/var/log/blyb/recovery-stability-20261027')","Path('/var/log/blyb/recovery-stability-20261027-resumed')")
status=status.replace("'blyb-recovery-stability-20261027.service'","'blyb-recovery-stability-20261027-resumed.service'")
(HERE/'resumed_status_remote.py').write_text(status,encoding='utf8')
(HERE/'evidence/deployment-bindings.json').write_text(json.dumps({'release':RID,'old_driver':old_sha,'new_driver':new_sha,'runner_resumed_sha256_lf':hashlib.sha256(resumed.encode()).hexdigest()},indent=2),encoding='utf8')
resume='''from pathlib import Path
import hashlib,json,subprocess
folder=Path(FOLDER)
verified=json.loads((folder/'ui-verified.json').read_text())
assert all(verified[k] for k in ('old_246_unchanged','checkpoint_unchanged','quota_unchanged'))
assert Path('/opt/blyb/current').resolve().name==RELEASE
runner=Path('/opt/blyb/tools/blyb-recovery-stability-20261027-resumed.py')
assert hashlib.sha256(runner.read_bytes()).hexdigest()==RUNNER_SHA
assert not Path('/var/log/blyb/recovery-stability-20261027-resumed').exists(),'ONE_OFF_ALREADY_USED'
p=subprocess.run(['systemd-run','--unit=blyb-recovery-stability-20261027-resumed','--property=RuntimeMaxSec=28800','--property=UMask=0077','/usr/bin/python3',str(runner)],capture_output=True)
assert p.returncode==0
print('__MAINTENANCE__'+json.dumps({'launched':True,'release':RELEASE,'verified_before_resume':True})+'__END__')
'''.replace('FOLDER',repr(folder)).replace('RELEASE',repr(RID)).replace('RUNNER_SHA',repr(hashlib.sha256(resumed.encode()).hexdigest()))
compile(resume,'resume-launcher','exec');(HERE/'resume_remote.py').write_text(resume,encoding='utf8')
print('Prepared guarded pause/upgrade/resume scripts; no remote action')
