from pathlib import Path
import json
import subprocess

token=next(line.split('=',1)[1].strip() for line in Path('/etc/blyb/runtime.env').read_text().splitlines() if line.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import json,sys,urllib.request,urllib.error
token=sys.stdin.readline().strip();out={}
for path in ['/drug-trace/daily-reports','/drug-trace/daily-reports/2026-10-25','/drug-trace/upload-stats?days=2','/drug-trace/send-scope']:
 try:
  req=urllib.request.Request('http://localhost:8000/api/v1'+path,headers={'Authorization':'Bearer '+token})
  with urllib.request.urlopen(req,timeout=20) as response:
   data=json.load(response).get('data');item={'status':response.status}
   if path.endswith('daily-reports'):item['data']=data
   elif path.endswith('/2026-10-25'):
    item['reports']=[{'report_date':r.get('report_date'),'source':r.get('source'),'outcome':r.get('outcome'),'candidate_count':r.get('candidate_count'),
      'send_error_count':(r.get('send') or {}).get('errors_total',len((r.get('send') or {}).get('errors') or [])),
      'query_error_count':(r.get('query_stats') or {}).get('errors_total',len((r.get('query_stats') or {}).get('errors') or [])),
      'query_error_codes':sorted(set(str((e.get('res') or {}).get('error_code')) for e in (r.get('query_stats') or {}).get('errors',[]))),
      'aborted_reason':(r.get('send') or {}).get('aborted_reason')} for r in data]
   elif path.endswith('send-scope'):item['quota']=data.get('quota');item['evidence_status']=(data.get('evidence') or {}).get('status')
   else:item['shape']=type(data).__name__
   out[path]=item
 except urllib.error.HTTPError as e:out[path]={'status':e.code}
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','-i','-u','0','blyb-core-1','python','-c',code],input=(token+'\n').encode(),capture_output=True,timeout=90)
del token
out={'exit_code':p.returncode,'api':json.loads(p.stdout) if p.returncode==0 else None}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
