"""Launch ordinary backup/upgrade independently of SSH lifetime."""
from pathlib import Path
import json
import os
import subprocess

rid='queue-20261026T010000Z-r2'
release=Path('<prod-path>-staging')/rid
if not (release/'manifest.json').is_file():raise RuntimeError('RELEASE_NOT_STAGED')
if Path('/opt/blyb/current').resolve().name!='ui-20261025T140000Z':raise RuntimeError('CURRENT_RELEASE_DRIFT')
check=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000',
    'blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',
    "SELECT (SELECT count(*) FROM submission WHERE state='SENDING')+(SELECT count(*) FROM job WHERE job_type IN ('drug_trace.send','drug_trace.collect') AND state IN ('PENDING','LEASED','RETRY_WAIT'))"],capture_output=True,text=True,timeout=20)
if check.returncode or check.stdout.strip()!='0':raise RuntimeError('ACTIVE_DELIVERY_OR_COLLECTION')
active=subprocess.run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value'],capture_output=True,text=True,timeout=10)
if active.stdout.strip() in ('active','activating','deactivating'):raise RuntimeError('DRIVER_STILL_RUNNING')
subprocess.run(['systemctl','stop','blyb-daily-upload.timer'],check=True,timeout=10)
for script in (release/'implementation/deploy/linux').glob('*.sh'):
    script.chmod(0o755)
folder=Path('/root/blyb-maintenance-backups/upgrade-'+rid+'-r1')
folder.mkdir(mode=0o700,exist_ok=False)
wrapper='''from pathlib import Path
import json,subprocess
from datetime import datetime,timezone
p=Path('''+repr(str(folder))+''')
with (p/'upgrade.log').open('wb') as log:
 result=subprocess.run(['bash','''+repr(str(release/'implementation/deploy/linux/upgrade.sh'))+','+repr(str(release))+'''],stdout=log,stderr=subprocess.STDOUT)
(p/'result.json').write_text(json.dumps({'exit_code':result.returncode,'finished_at':datetime.now(timezone.utc).isoformat()})+'\\n')
'''
(folder/'run.py').write_text(wrapper)
os.chmod(folder/'run.py',0o700)
with (folder/'launcher.log').open('wb') as log:
    child=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
out={'launched':True,'pid':child.pid,'private_logs':str(folder),'release':rid}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
