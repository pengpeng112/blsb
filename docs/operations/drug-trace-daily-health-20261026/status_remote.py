from pathlib import Path
from datetime import datetime,date,time,timedelta,timezone
import collections
import json
import re
import subprocess


def run(args,timeout=25,stdin=None):
    p=subprocess.run(args,input=stdin,capture_output=True,timeout=timeout)
    return p.returncode,p.stdout.decode('utf8','replace'),p.stderr.decode('utf8','replace')


now=datetime.now(timezone(timedelta(hours=8)))
today=now.date();business=today-timedelta(days=1)
start=datetime.combine(today,time(),tzinfo=now.tzinfo).astimezone(timezone.utc).replace(tzinfo=None).isoformat()
begin=business.isoformat();end=today.isoformat()
out={'captured_gmt8':now.isoformat(),'business_window':[begin,end],
     'current_release':str(Path('/opt/blyb/current').resolve())}
for unit in ['blyb-daily-upload.service','blyb-daily-upload.timer']:
    rc,raw,_=run(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStatus',
                 '-p','ExecMainStartTimestamp','-p','ExecMainExitTimestamp','-p','NextElapseUSecRealtime','-p','TimeoutStartUSec'])
    out[unit]={'exit_code':rc,'properties':dict(line.split('=',1) for line in raw.splitlines() if '=' in line)}
rc,raw,_=run(['docker','ps','--format','{{.Names}} {{.Status}}'])
out['containers']={'exit_code':rc,'status':raw.strip()}
out['receipts']=[]
for p in sorted(Path('/var/log/blyb/daily-upload').glob(today.strftime('%Y%m%d')+'*.json')):
    entry={'file':p.name,'bytes':p.stat().st_size}
    if p.stat().st_size:
        try:
            d=json.loads(p.read_text())
            for k in ['window','candidate_count','outcome','fatal','elapsed_seconds','report_push']:
                if k in d:entry[k]=d[k]
            entry['send_counters']={k:v for k,v in (d.get('send') or {}).items() if type(v) in (int,bool)}
            entry['send_error_codes']=dict(collections.Counter((x.get('res') or {}).get('error_code','UNSPECIFIED') for x in (d.get('send') or {}).get('errors',[])))
            entry['aborted_reason']=(d.get('send') or {}).get('aborted_reason')
            entry['query_counters']={k:v for k,v in (d.get('query') or {}).items() if type(v) in (int,bool)}
            entry['query_errors']=len((d.get('query') or {}).get('errors') or [])
        except Exception as exc:entry['parse_error']=type(exc).__name__
    out['receipts'].append(entry)
p=Path('/var/log/blyb/daily-upload/last-error.log')
if p.exists():
    raw=p.read_text(errors='replace')[-8000:]
    out['driver_stderr']={'bytes':p.stat().st_size,'exception_types':sorted(set(re.findall(r'\b[A-Za-z]+(?:Error|Exception)\b',raw))),
                          'oracle_codes':sorted(set(re.findall(r'\b(?:ORA|DPY|DPI)-\d+',raw)))}
sql="""SELECT json_build_object(
'collections',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,window_begin::text,window_end::text,created_at::text,finished_at::text,summary FROM drug_trace_collection_run ORDER BY created_at DESC LIMIT 3) x),
'quality',(SELECT json_agg(row_to_json(x)) FROM (SELECT b.business_kind,r.quality_state,count(*) n FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id WHERE b.bill_time>='%s' AND b.bill_time<'%s' GROUP BY 1,2) x),
'all_states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission GROUP BY state) x),
'today_created_states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission WHERE created_at>='%s' GROUP BY state) x),
'today_jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,count(*) n,min(created_at)::text oldest,max(lease_until)::text lease_until FROM job WHERE created_at>='%s' GROUP BY 1,2) x),
'today_job_errors',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,left(last_error->>'message',160) error,count(*) n FROM job WHERE created_at>='%s' AND state='FAILED' GROUP BY 1,2) x),
'today_attempts',(SELECT json_agg(row_to_json(x)) FROM (SELECT http_status,outcome,count(*) n FROM delivery_attempt WHERE started_at>='%s' GROUP BY 1,2) x),
'today_queries',(SELECT json_agg(row_to_json(x)) FROM (SELECT parsed_result->>'state' state,count(*) n FROM receipt_event WHERE received_at>='%s' GROUP BY 1) x),
'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills FROM drug_trace_send_quota) x),
'checkpoint',(SELECT json_agg(row_to_json(x)) FROM (SELECT partition,row_version,cursor_json->>'since_time' since_time,cursor_json->>'completed_window' completed_window FROM source_checkpoint WHERE partition='r10-batch-daily') x)
)"""%(begin,end,start,start,start,start,start)
rc,raw,err=run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql])
out['database_exit_code']=rc
if rc==0:
    out['database']=json.loads(raw)
    for row in out['database'].get('collections') or []:
        row['summary']={k:v for k,v in (row.get('summary') or {}).items() if type(v) in (int,float,bool)}
else:out['database_error_types']=re.findall(r'column [\w." ]+ does not exist',err)
for name,args in {'ntp_sources':['chronyc','-n','sources'],'ntp_tracking':['chronyc','tracking'],
                 'ntp_unit':['systemctl','show','chronyd','-p','ActiveState','-p','UnitFileState']}.items():
    rc,raw,_=run(args);out[name]={'exit_code':rc,'status':raw.strip()}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
