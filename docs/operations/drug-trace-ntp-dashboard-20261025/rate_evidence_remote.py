import collections
import json
import os
import re
import subprocess

out={}
for name,begin,end in [('before','2026-10-25 12:28:00','2026-10-25 13:31:11'),('after','2026-10-25 13:43:00',None)]:
    args=['journalctl','-u','blyb-dmz-forwarder.service','--since',begin,'-o','cat','--no-pager']
    if end:args+=['--until',end]
    p=subprocess.run(args,capture_output=True,text=True,timeout=20,env={**os.environ,'TZ':'UTC'})
    counts=collections.Counter()
    forwards=collections.Counter()
    for line in p.stdout.splitlines():
        if 'reject rid=' in line:
            m=re.search(r'status=(\d+).*reason=([\w-]+)',line)
            if m:counts[m.group(1)+':'+m.group(2)]+=1
        if 'forward rid=' in line:
            m=re.search(r'status=(\d+)',line)
            if m:forwards[m.group(1)]+=1
    out[name]={'exit_code':p.returncode,'rejects':dict(counts),'forwards':dict(forwards),'error':p.stderr[:200] if p.returncode else None}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
