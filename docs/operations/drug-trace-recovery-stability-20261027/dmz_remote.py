from datetime import datetime,time,timedelta,timezone
import collections
import json
import os
import re
import subprocess

now=datetime.now(timezone(timedelta(hours=8)))
since=datetime.combine(now.date(),time(),tzinfo=now.tzinfo).astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
out={'captured_gmt8':now.isoformat(),'since_utc':since}
p=subprocess.run(['journalctl','-u','blyb-dmz-forwarder.service','--since',since,'-o','cat','--no-pager'],capture_output=True,text=True,timeout=20,env={**os.environ,'TZ':'UTC'})
forwards=collections.Counter();rejects=collections.Counter()
for line in p.stdout.splitlines():
    match=re.search(r'status=(\d+)',line)
    if match and 'forward rid=' in line:forwards[match.group(1)]+=1
    if match and 'reject rid=' in line:rejects[match.group(1)]+=1
out['traffic']={'exit_code':p.returncode,'forward_statuses':dict(forwards),'reject_statuses':dict(rejects)}
for name,args in {'sources':['chronyc','-n','sources'],'tracking':['chronyc','tracking'],
                 'forwarder':['systemctl','show','blyb-dmz-forwarder.service','-p','ActiveState','-p','MainPID'],
                 'ntp':['systemctl','show','chronyd','-p','ActiveState','-p','UnitFileState']}.items():
    p=subprocess.run(args,capture_output=True,text=True,timeout=10)
    out[name]={'exit_code':p.returncode,'status':p.stdout.strip()}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
