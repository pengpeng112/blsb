from pathlib import Path
import hashlib
import json
import re
import subprocess

def run(args, data=None):
    p = subprocess.run(args, input=data, capture_output=True, timeout=35)
    if p.returncode:
        raise RuntimeError(args[0] + '_EXIT_' + str(p.returncode))
    return p.stdout.decode().strip()

token = next(x.split('=', 1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code = '''import json,sys,urllib.request
t=sys.stdin.readline().strip()
def get(p):
 r=urllib.request.Request('http://localhost:8000/api/v1'+p,headers={'Authorization':'Bearer '+t})
 with urllib.request.urlopen(r,timeout=20) as x:return json.load(x)['data']
ds=get('/dictionaries')
print(json.dumps({'dictionaries':[d for d in ds if d['namespace']=='drugtrace.ref_ent_id'], 'scope':get('/drug-trace/send-scope')}))
'''
out = json.loads(run(['docker','exec','-i','blyb-core-1','python','-c',code],(token+'\n').encode()))
del token
text = Path('/etc/chrony.conf').read_text()
out['ntp'] = {'sha256':hashlib.sha256(text.encode()).hexdigest(),'sources':[x for x in text.splitlines() if re.match(r'^\s*(server|pool|peer|include|confdir|sourcedir|makestep)\b',x)],'version':run(['chronyd','-v']),'tracking':run(['chronyc','tracking'])}
out['units'] = run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','-p','SubState','-p','ExecMainStatus'])
out['recovery_final'] = json.loads(Path('/var/log/blyb/queue-recovery-20261026/final.json').read_text()) if Path('/var/log/blyb/queue-recovery-20261026/final.json').exists() else None
out['containers'] = run(['docker','ps','--format','{{.Names}} {{.Status}}'])
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
