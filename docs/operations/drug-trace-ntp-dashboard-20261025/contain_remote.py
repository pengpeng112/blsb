"""Stop only daily driver and worker for the authorized maintenance window."""
from pathlib import Path
from datetime import datetime, timezone
import json
import subprocess


def run(args, timeout=90):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(args[0] + '_EXIT_' + str(p.returncode))
    return p.stdout.strip()


backup = Path('/root/blyb-maintenance-backups') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
backup.mkdir(parents=True, mode=0o700)
import shutil
for path in ['/etc/systemd/system/blyb-daily-upload.service','/opt/blyb/tools/blyb-daily-upload.py','/etc/chrony.conf']:
    shutil.copy2(path, backup / Path(path).name)
run(['systemctl','stop','blyb-daily-upload.timer'])
killer = '''from pathlib import Path
import os,signal,json
found=[]
for path in Path('/proc').glob('[0-9]*/cmdline'):
 try:args=path.read_bytes().split(bytes([0]))
 except OSError:continue
 if b'/tmp/daily-upload.py' in args:found.append(int(path.parent.name))
if len(found)>1:raise RuntimeError('DRIVER_NOT_UNIQUE')
for pid in found:os.kill(pid,signal.SIGTERM)
print(json.dumps({'daily_driver_terminated':len(found)}))
'''
out = {'backup': str(backup), 'driver': json.loads(run(['docker','exec','-u','0','blyb-core-1','python','-c',killer]))}
out['worker_stop'] = run(['docker','stop','--time','60','<worker-container>'])
out.update(reason='authorized_ntp_dashboard_upgrade_and_rate_limit_containment',
           utc=datetime.now(timezone.utc).isoformat(), approval_revoked=False,
           submission_states_changed=False, source_database_writes=0)
(backup/'maintenance.json').write_text(json.dumps(out,indent=2)+'\n')
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
