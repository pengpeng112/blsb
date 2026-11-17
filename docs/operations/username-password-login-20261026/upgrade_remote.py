"""Run the existing consistent backup/upgrade after the drained pause."""
from pathlib import Path
import json,os,subprocess

RID='auth-20261026T043500Z'
release=Path('<prod-path>-staging')/RID
if not (release/'manifest.json').exists():raise RuntimeError('RELEASE_NOT_STAGED')
if Path('/opt/blyb/current').resolve().name!='queue-20261026T010000Z-r2':raise RuntimeError('RELEASE_DRIFT')
if not Path('/root/blyb-maintenance-backups/auth-20261026/pause.json').exists():raise RuntimeError('PAUSE_EVIDENCE_REQUIRED')
for unit in ['blyb-daily-upload.service','blyb-mapping-recovery-20261026.service']:
    p=subprocess.run(['systemctl','show',unit,'-p','ActiveState','--value'],capture_output=True,text=True,timeout=10)
    if p.stdout.strip() in ('active','activating','deactivating'):raise RuntimeError('DRIVER_STILL_ACTIVE')
check=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',
    "SELECT (SELECT count(*) FROM submission WHERE state='SENDING')+(SELECT count(*) FROM job WHERE job_type IN ('drug_trace.send','drug_trace.collect') AND state IN('PENDING','LEASED','RETRY_WAIT'))"],capture_output=True,text=True,timeout=20)
if check.returncode or check.stdout.strip()!='0':raise RuntimeError('ACTIVE_DELIVERY_OR_COLLECTION')
for f in (release/'implementation/deploy/linux').glob('*.sh'):f.chmod(0o755)
folder=Path('/root/blyb-maintenance-backups/upgrade-'+RID)
folder.mkdir(mode=0o700,exist_ok=False)
wrapper='''from pathlib import Path
from datetime import datetime,timezone
import json,subprocess
p=Path('''+repr(str(folder))+''')
with (p/'upgrade.log').open('wb') as f:
 r=subprocess.run(['bash','''+repr(str(release/'implementation/deploy/linux/upgrade.sh'))+','+repr(str(release))+'''],stdout=f,stderr=subprocess.STDOUT)
(p/'result.json').write_text(json.dumps({'exit_code':r.returncode,'finished_utc':datetime.now(timezone.utc).isoformat()})+'\\n')
'''
(folder/'run.py').write_text(wrapper);os.chmod(str(folder/'run.py'),0o700)
with (folder/'launcher.log').open('wb') as f:
    child=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':child.pid,'release':RID,'logs':str(folder)})+'__END__')
