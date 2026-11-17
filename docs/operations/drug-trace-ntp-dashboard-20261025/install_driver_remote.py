from pathlib import Path
from datetime import datetime,timezone
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()


if Path('/opt/blyb/current').resolve().name!='ui-20261025T140000Z':raise RuntimeError('DASHBOARD_RELEASE_NOT_ACTIVE')
step=run(['docker','exec','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',"SELECT count(*) FROM schema_migrations WHERE step=12"])
if step!='1':raise RuntimeError('MIGRATION_012_MISSING')
files={'daily_upload_driver.py':Path('/opt/blyb/tools/blyb-daily-upload.py'),
       'blyb-daily-upload.service':Path('/etc/systemd/system/blyb-daily-upload.service')}
expected={'daily_upload_driver.py':'1f275f0b20fb58ddc8319347d65981d3f253e85b1005ff2853ea7392e2726df3',
          'blyb-daily-upload.service':'f89c0c81f5448818d4d05b58145d016621838f45710e4cf00b5ea76a7a5d33f0'}
for key,p in files.items():
    if hashlib.sha256(p.read_bytes()).hexdigest()!=expected[key]:raise RuntimeError('DRIVER_BASELINE_DRIFT')
backup=Path('/root/blyb-maintenance-backups')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-driver')
backup.mkdir(mode=0o700)
for key,p in files.items():shutil.copy2(p,backup/key)
for key,p in files.items():
    data=base64.b64decode(PAYLOAD[key])
    if key.endswith('.py'):compile(data,'new-driver','exec')
    p.write_bytes(data);p.chmod(0o700 if key.endswith('.py') else 0o644)
run(['systemd-analyze','verify',str(files['blyb-daily-upload.service'])])
run(['systemctl','daemon-reload'])
effective=run(['systemctl','show','blyb-daily-upload.service','-p','ExecStart','--value'])
m=re.search(r'printf\s+"([^"\r\n]+)"',effective)
if not m or not re.fullmatch(r'%s(?:\\+n)',m.group(1)):raise RuntimeError('PRINTF_SPECIFIER_INVALID')
dummy=subprocess.run(['/bin/bash','-c','printf "'+m.group(1)+'" "PUBLIC_DUMMY"'],capture_output=True)
if dummy.stdout!=b'PUBLIC_DUMMY\n':raise RuntimeError('DUMMY_FAILED')
run(['systemctl','enable','--now','blyb-daily-upload.timer'])
out={'backup':str(backup),'report_enabled':True,'dummy_verified':True,
     'hashes':{key:hashlib.sha256(p.read_bytes()).hexdigest() for key,p in files.items()},
     'timer':run(['systemctl','list-timers','blyb-daily-upload.timer','--no-pager']),
     'manual_batch_started':False}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
