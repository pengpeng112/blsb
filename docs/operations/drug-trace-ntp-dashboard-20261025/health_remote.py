from pathlib import Path
from datetime import datetime, timezone
import base64
import hashlib
import json
import re
import ssl
import socket
import subprocess
import urllib.request


def run(args, stdin=None):
    p=subprocess.run(args,input=stdin,capture_output=True,timeout=30)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.decode().strip()


token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import collections,json,sys,urllib.request,urllib.error
token=sys.stdin.readline().strip();out={}
for path in ['/drug-trace/daily-reports','/drug-trace/daily-reports/2026-10-24','/drug-trace/upload-stats?days=7','/drug-trace/send-scope','/drug-trace/submission-outcomes?days=7&only=problem']:
 try:
  req=urllib.request.Request('http://localhost:8000/api/v1'+path,headers={'Authorization':'Bearer '+token})
  with urllib.request.urlopen(req,timeout=20) as r:
   data=json.load(r).get('data');entry={'status':r.status,'shape':type(data).__name__}
   if isinstance(data,list):
    entry['count']=len(data)
    if path.endswith('daily-reports'):entry['reports']=data
   elif path.endswith('send-scope'):
    entry['quota']=data.get('quota');entry['evidence_status']=(data.get('evidence') or {}).get('status')
   out[path]=entry
 except urllib.error.HTTPError as e:out[path]={'status':e.code}
 except Exception as e:out[path]={'error_type':type(e).__name__}
print(json.dumps(out))
'''
out={'api':json.loads(run(['docker','exec','-i','-u','0','blyb-core-1','python','-c',code],(token+'\n').encode()))}
del token
context=ssl.create_default_context(cafile='/etc/blyb/certs/tls.crt')
def trusted_local_get(path):
    # Existing certificate has only CN. Verify that CN plus its pinned CA,
    # connecting to the local nginx IP; this does not validate an IP SAN.
    import http.client
    with socket.create_connection(('<prod-host>',443),timeout=10) as tcp:
        with context.wrap_socket(tcp,server_hostname='blyb-<prod-host>') as tls:
            tls.sendall(('GET '+path+' HTTP/1.1\r\nHost: <prod-host>\r\nConnection: close\r\n\r\n').encode())
            response=http.client.HTTPResponse(tls);response.begin()
            return response.status,response.read()
try:
    st,html=trusted_local_get('/ui/')
    assets=re.findall(r'(?:src|href)="([^"]+assets/[^"]+)"',html.decode())
    out['ui_server_check']={'status':st,'assets':assets,'asset_statuses':[trusted_local_get(x)[0] for x in assets],
                            'tls_check':'existing CN and explicit trust anchor; IP SAN remains missing'}
except Exception as exc:out['ui_server_check']={'error_type':type(exc).__name__}
try:
    with urllib.request.urlopen('https://<prod-host>/ui/',context=context,timeout=10) as r:
        html=r.read().decode();out['ui']={'status':r.status,'assets':re.findall(r'(?:src|href)="([^"]+assets/[^"]+)"',html)}
    out['ui']['asset_statuses']=[]
    for asset in out['ui']['assets']:
        with urllib.request.urlopen('https://<prod-host>'+asset,context=context,timeout=10) as r:
            out['ui']['asset_statuses'].append(r.status)
except Exception as e:
    reason=getattr(e,'reason',None)
    out['ui']={'error_type':type(e).__name__,'reason_type':type(reason).__name__,
               'verify_message':getattr(reason,'verify_message',None)}
out['ui']['certificate_subject']=ssl._ssl._test_decode_cert('/etc/blyb/certs/tls.crt').get('subject')
out['ui']['certificate_san']=ssl._ssl._test_decode_cert('/etc/blyb/certs/tls.crt').get('subjectAltName')
sql="""SELECT json_build_object(
'states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission GROUP BY state) x),
'new_attempts',(SELECT count(*) FROM delivery_attempt WHERE started_at>='2026-10-25 13:43:00'),
'new_429',(SELECT count(*) FROM delivery_attempt WHERE started_at>='2026-10-25 13:43:00' AND http_status=429),
'new_query_receipts',(SELECT count(*) FROM receipt_event WHERE received_at>='2026-10-25 13:43:00'),
'poll_budget',(SELECT json_agg(row_to_json(x)) FROM (SELECT s.state,count(*) n,min(coalesce(q.n,0)) min_polls,max(coalesce(q.n,0)) max_polls FROM submission s LEFT JOIN (SELECT payload->>'submission_id' sid,count(*) n FROM job WHERE job_type='drug_trace.query_status' GROUP BY 1) q ON q.sid=s.id WHERE s.state IN ('UNKNOWN','ACCEPTED') GROUP BY s.state) x),
'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills FROM drug_trace_send_quota) x),
'query_jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,count(*) n FROM job WHERE job_type='drug_trace.query_status' GROUP BY state) x))"""
out['db']=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c',sql]))
out['utc']=datetime.now(timezone.utc).isoformat()
out['containers']=run(['docker','ps','--format','{{.Names}} {{.Status}}'])
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
