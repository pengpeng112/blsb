"""Bind maintenance scripts to reviewed frozen code and exact server baselines."""
from pathlib import Path
import json,hashlib,ast
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
RID='flow-20261026T132000Z';folder='/root/blyb-maintenance-backups/upgrade-'+RID
driver=(ROOT/'docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py').read_text('utf8')
old_sha='1d1862d93ca36449fa8658b7295b0b9a857d8b902059057ee25f9a68a1cd8c46'
wrapper='''from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess
p=Path(FOLDER)
driver=Path('/opt/blyb/tools/blyb-daily-upload.py')
code=99;details={}
try:
 assert hashlib.sha256(driver.read_bytes()).hexdigest()==OLD_SHA,'DRIVER_DRIFT'
 shutil.copy2(driver,p/'daily-upload.before.py')
 with (p/'upgrade.log').open('wb') as log:
  code=subprocess.run(['bash','<prod-path>-staging/RELEASE/implementation/deploy/linux/upgrade.sh','<prod-path>-staging/RELEASE'],stdout=log,stderr=subprocess.STDOUT).returncode
 if code==0:
  source=DRIVER_SOURCE
  compile(source,'daily-upload-new','exec')
  temporary=driver.with_name('blyb-daily-upload.RELEASE.next.py')
  assert not temporary.exists()
  temporary.write_text(source);temporary.chmod(0o700);os.replace(temporary,driver)
  details['driver_sha256']=hashlib.sha256(driver.read_bytes()).hexdigest()
except Exception as exc:
 code=99;details['error_type']=type(exc).__name__
finally:
 timer=subprocess.run(['systemctl','start','blyb-daily-upload.timer']).returncode
 (p/'result.json').write_text(json.dumps({'exit_code':code,'timer_restart_exit':timer,'finished_utc':datetime.now(timezone.utc).isoformat(),**details})+'\\n')
'''
wrapper=wrapper.replace('FOLDER',repr(folder)).replace('OLD_SHA',repr(old_sha)).replace('DRIVER_SOURCE',repr(driver)).replace('RELEASE',RID)
compile(wrapper,'detached-flow-upgrade','exec')
source=(HERE/'upgrade_remote.py').read_text('utf8')
a=source.index('    wrapper=');b=source.index('    compile(wrapper,',a)
source=source[:a]+'    wrapper='+repr(wrapper)+'\n'+source[b:]
compile(source,'flow-upgrade','exec');(HERE/'upgrade_remote.py').write_text(source,encoding='utf8')
forwarder=(ROOT/'implementation/var'/RID/'implementation/deploy/dmz/forwarder.py').read_text('utf8')
baseline=hashlib.sha256((HERE/'baselines/implementation/deploy/dmz/forwarder.py').read_text('utf8').encode()).hexdigest()
dmz='''from pathlib import Path
import hashlib,json,os,shutil,subprocess
current=Path('/opt/blyb-dmz/current');old=current.resolve();new=old.parent/'RELEASE'
assert old.parent==Path('/opt/blyb-dmz/releases'),'DMZ_LAYOUT_DRIFT'
assert hashlib.sha256((old/'forwarder.py').read_text().encode()).hexdigest()==BASELINE,'DMZ_SOURCE_DRIFT'
assert not new.exists(),'DMZ_RELEASE_EXISTS'
folder=Path('/root/blyb-maintenance-backups/dmz-RELEASE');folder.mkdir(mode=0o700)
(folder/'previous-release.txt').write_text(str(old))
shutil.copytree(old,new)
(new/'forwarder.py').write_text(FORWARDER_SOURCE)
compile((new/'forwarder.py').read_text(),'forwarder-new','exec')
check=subprocess.run(['/opt/blyb-dmz/runtime/bin/python3.11',str(new/'forwarder.py'),'--config','/etc/blyb-dmz/forwarder.json','--check-config'],capture_output=True,timeout=20)
assert check.returncode==0,'DMZ_CONFIG_CHECK_FAILED'
link=current.with_name('current.RELEASE.next');assert not link.exists()
link.symlink_to(new);os.replace(link,current)
try:
 subprocess.run(['systemctl','restart','blyb-dmz-forwarder.service'],check=True,timeout=30)
 state=subprocess.run(['systemctl','is-active','blyb-dmz-forwarder.service'],capture_output=True,text=True,timeout=10)
 assert state.stdout.strip()=='active'
except BaseException:
 link.symlink_to(old);os.replace(link,current)
 subprocess.run(['systemctl','restart','blyb-dmz-forwarder.service'],check=True,timeout=30)
 raise
out={'release':new.name,'previous':old.name,'active':True,'config_check':0,'forwarder_sha256':hashlib.sha256((new/'forwarder.py').read_bytes()).hexdigest(),'upstream_calls':0}
(folder/'result.json').write_text(json.dumps(out,indent=2))
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
'''
dmz=dmz.replace('RELEASE',RID).replace('BASELINE',repr(baseline)).replace('FORWARDER_SOURCE',repr(forwarder))
compile(dmz,'dmz-flow-upgrade','exec');(HERE/'dmz_upgrade_remote.py').write_text(dmz,encoding='utf8')
(HERE/'evidence/deployment-bindings.json').write_text(json.dumps({'release':RID,'driver_expected_before':old_sha,'driver_after':hashlib.sha256(driver.encode()).hexdigest(),'dmz_expected_before':baseline,'dmz_after':hashlib.sha256(forwarder.encode()).hexdigest()},indent=2)+'\n',encoding='utf8')
print('Prepared compiled upgrade wrappers with driver baseline and immutable DMZ release')
