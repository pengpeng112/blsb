"""Expose the already synchronized DMZ chrony only to the reporting host."""
from pathlib import Path
from datetime import datetime, timezone
import json
import shutil
import subprocess


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=15)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()


tracking=run(['chronyc','tracking'])
if 'Normal' not in tracking or '<relay-net-host>' not in tracking:
    raise RuntimeError('DMZ_NOT_SYNCHRONIZED_TO_HOSPITAL_NTP')
p=Path('/etc/chrony.conf')
backup=Path('/root/blyb-ntp-backups')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-relay')
backup.mkdir(parents=True,mode=0o700)
shutil.copy2(p,backup/'chrony.conf')
text=p.read_text()
if 'allow <prod-host>/32' not in text:
    p.write_text(text.rstrip()+'\n# BLYB reporting host only; upstream remains hospital NTP.\nallow <prod-host>/32\n')
out={'backup':str(backup),'runtime_allow':run(['chronyc','allow','<prod-host>/32']),
     'access_check':run(['chronyc','accheck','<prod-host>']),
     'listeners':run(['ss','-lunp']),
     'enabled':run(['systemctl','enable','chronyd']),
     'clock_stepped':False,'upstream':'<relay-net-host>','allowed_client':'<prod-host>/32'}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
