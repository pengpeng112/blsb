"""Keep persistent hospital NTP polling; remove unusable relay experiment."""
from pathlib import Path
from datetime import datetime, timezone
import json
import shutil
import subprocess


def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()


is_dmz=Path('/etc/blyb-dmz/forwarder.json').exists()
p=Path('/etc/chrony.conf');text=p.read_text()
backup=Path('/root/blyb-ntp-backups')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-finalize')
backup.mkdir(parents=True,mode=0o700);shutil.copy2(p,backup/'chrony.conf')
out={'host':'dmz' if is_dmz else 'app','backup':str(backup),'manual_clock_set':False}
if is_dmz:
    text=text.replace('# BLYB reporting host only; upstream remains hospital NTP.\nallow <prod-host>/32\n','')
    p.write_text(text)
    out['relay_removed']=run(['chronyc','deny','<prod-host>/32'])
else:
    text=text.replace('server <relay-net-host> iburst','server <relay-net-host> iburst minpoll 6 maxpoll 8')
    p.write_text(text)
    run(['chronyd','-p','-f',str(p)])
    run(['systemctl','restart','chronyd'])
out['enabled']=run(['systemctl','enable','chronyd'])
out['sources']=run(['chronyc','-n','sources'])
out['tracking']=run(['chronyc','tracking'])
out['synchronized']='Leap status     : Normal' in out['tracking']
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
