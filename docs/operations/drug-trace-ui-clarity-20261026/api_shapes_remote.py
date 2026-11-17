"""Validate production GET response shapes without logging bill contents."""
from pathlib import Path
import json,subprocess
token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import json,sys,urllib.request
t=sys.stdin.readline().strip()
paths=['readiness','send-scope?profile=alihealth_yljg_retail_v1','daily-reports?limit=30','upload-stats?days=14','submission-outcomes?days=14','submission-outcomes?days=14&only=problem']
out={}
for path in paths:
 r=urllib.request.Request('http://localhost:8000/api/v1/drug-trace/'+path,headers={'Authorization':'Bearer '+t})
 with urllib.request.urlopen(r,timeout=20) as s: data=json.load(s)['data'];status=s.status
 assert status==200
 if path.startswith('upload-stats'):
  assert isinstance(data['days'],list)
  assert all(all(k in row for k in ['date','by_state','total']) for row in data['days'])
 elif path.startswith('send-scope'): assert all(k in data for k in ['evidence','quota','scope'])
 elif path=='readiness': assert isinstance(data,list) or isinstance(data.get('profiles'),list)
 else:
  assert isinstance(data,list)
  if path.startswith('submission-outcomes'): assert all(all(k in row for k in ['id','state','updated_at']) for row in data)
  if path.startswith('daily-reports'): assert all(all(k in row for k in ['report_date','source','outcome']) for row in data)
 out[path]={'http':status,'type':type(data).__name__,'rows':len(data) if isinstance(data,list) else None}
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','-i','blyb-core-1','python','-c',code],input=(token+'\n').encode(),capture_output=True,timeout=150)
del token
if p.returncode: raise RuntimeError('GET_SHAPE_CHECK_FAILED')
print('__MAINTENANCE__'+json.dumps({'checks':json.loads(p.stdout),'methods':['GET'],'real_platform_requests':0,'his_requests':0})+'__END__')
