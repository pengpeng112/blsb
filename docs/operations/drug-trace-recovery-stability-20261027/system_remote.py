"""Operational health and finite send authorization; no HIS/platform calls."""
from pathlib import Path
from datetime import datetime,timezone
import json,subprocess

def run(args,timeout=25,data=None):
    p=subprocess.run(args,input=data,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(args[0]+'_FAILED')
    return p.stdout.strip()

out={'utc':datetime.now(timezone.utc).isoformat(),'release':Path('/opt/blyb/current').resolve().name}
token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import json,sys,urllib.request,time,os
from pathlib import Path
t=sys.stdin.readline().strip()
r=urllib.request.Request('http://localhost:8000/api/v1/drug-trace/send-scope',headers={'Authorization':'Bearer '+t})
with urllib.request.urlopen(r,timeout=15) as response:
 d=json.load(response)['data'];ev=d.get('evidence') or {};s=d.get('scope') or {}
 assert all(s.get(k) is not None for k in ('window_begin','window_end','pharmacies','max_bills')),'SCOPE_UNAVAILABLE'
 print(json.dumps({'http_status':response.status,'status':ev.get('status'),'valid_until':ev.get('valid_until'),
  'scope':{k:s.get(k) for k in ('window_begin','window_end','pharmacies','max_bills')},'quota':d.get('quota')}))
'''
out['send_authorization']=json.loads(run(['docker','exec','-i','blyb-core-1','python','-c',code],data=token+'\n'));del token
for label,args in {
 'daily_timer':['systemctl','show','blyb-daily-upload.timer','-p','ActiveState','-p','SubState','-p','NextElapseUSecRealtime'],
 'original_timer_run':['systemctl','show','blyb-daily-upload.service','-p','ActiveState','-p','SubState','-p','ExecMainStatus','-p','Result'],
 'containers':['docker','ps','--format','{{.Names}} {{.Status}}'],
 'resources':['docker','stats','--no-stream','--format','{{.Name}} CPU={{.CPUPerc}} MEM={{.MemUsage}}'],
 'disk':['df','-hP','/','<prod-path>','/opt/blyb'],
 'ntp_tracking':['chronyc','tracking'],
}.items():out[label]=run(args)
sql="""SELECT json_build_object(
'overdue_leases',(SELECT count(*) FROM job WHERE state='LEASED' AND lease_until<now()),
'window_attempts',(SELECT json_agg(row_to_json(x)) FROM(SELECT http_status,outcome,count(*) n FROM delivery_attempt WHERE started_at>='2026-10-26 16:00:00' AND ended_at IS NOT NULL GROUP BY 1,2)x),
'new_failed_jobs',(SELECT json_agg(row_to_json(x)) FROM(SELECT job_type,state,count(*) n FROM job WHERE created_at>='2026-10-26 16:00:00' AND state IN('FAILED','DEAD') GROUP BY 1,2)x),
'unpublished_outbox',(SELECT count(*) FROM outbox_event WHERE published_at IS NULL))"""
out['queue']=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
