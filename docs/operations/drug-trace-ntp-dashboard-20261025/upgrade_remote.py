"""Launch ordinary backup/upgrade independently of SSH lifetime."""
from pathlib import Path
import json
import os
import subprocess

rid='ui-20261025T140000Z'
release=Path('<prod-path>-staging')/rid
if not (release/'manifest.json').is_file():raise RuntimeError('RELEASE_NOT_STAGED')
if Path('/opt/blyb/current').resolve().name!='src-20261022T103000Z':raise RuntimeError('CURRENT_RELEASE_DRIFT')
for script in (release/'implementation/deploy/linux').glob('*.sh'):
    script.chmod(0o755)
folder=Path('/root/blyb-maintenance-backups/upgrade-'+rid+'-r2')
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
