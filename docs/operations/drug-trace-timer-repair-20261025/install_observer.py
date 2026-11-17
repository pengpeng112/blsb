"""Install this run's bounded read-only observer; no business actions."""
from pathlib import Path
from datetime import datetime, timezone
import base64
import importlib.util
import json
import re
import shlex

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
code=(HERE/'observer.py').read_bytes()
remote='''from pathlib import Path
import base64,json,os,subprocess
p=Path('/opt/blyb/tools/blyb-repair-observe-20261025.py')
if p.exists():raise RuntimeError('OBSERVER_ALREADY_EXISTS')
p.write_bytes(base64.b64decode(PAYLOAD));os.chmod(p,0o700)
r=subprocess.run(['systemd-run','--unit=blyb-repair-observe-20261025','--property=RuntimeMaxSec=21700','/usr/bin/python3',str(p)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
print('__OBSERVER__'+json.dumps({'exit_code':r.returncode,'installed':r.returncode==0,'business_mutations':0,'source_queries':0,'directory':'/var/log/blyb/repair-observe-20261025'})+'__OBSERVER_END__')
'''
script='PAYLOAD='+repr(base64.b64encode(code).decode())+'\n'+remote
cmd='python3 -c '+shlex.quote('import base64;exec(base64.b64decode('+repr(base64.b64encode(script.encode()).decode())+'))')
spec=importlib.util.spec_from_file_location('observer_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
jump=fb.deploy.connect_jump(fb.creds()['jump'])
try:rc,tr=fb.nested(jump,cmd,timeout=60)
finally:jump.close()
m=re.search(r'__OBSERVER__(.*?)__OBSERVER_END__',tr,re.S)
out={'ssh_exit_code':rc,'result':json.loads(m.group(1)) if m else {'capture_failed':True}}
p=HERE/'evidence'/('observer-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps(out,ensure_ascii=True))
raise SystemExit(0 if out['result'].get('installed') else 1)
