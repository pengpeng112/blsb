from datetime import datetime,time,timedelta,timezone
from pathlib import Path
import collections
import json
import re
import subprocess

now=datetime.now(timezone(timedelta(hours=8)))
since=datetime.combine(now.date(),time(),tzinfo=now.tzinfo).astimezone(timezone.utc).isoformat()
out={'captured_gmt8':now.isoformat()}
for container in ['blyb-core-1','<worker-container>']:
    p=subprocess.run(['docker','logs','--since',since,'--tail','2500',container],capture_output=True,text=True,timeout=20)
    raw=p.stdout+p.stderr
    counts=collections.Counter()
    for line in raw.splitlines():
        m=re.search(r'"(GET|POST) ([^ ]+) HTTP/[^"]+" (\d+)',line)
        if not m:continue
        method,path,status=m.groups()
        category='other'
        if re.search(r'/drug-trace/submissions/[^/]+/query',path):category='query'
        elif re.search(r'/drug-trace/submissions/[^/]+/send',path):category='send'
        elif re.search(r'/drug-trace/bills/[^/]+/prepare',path):category='prepare'
        elif 'daily-reports' in path:category='daily_report'
        if category!='other':counts[method+':'+category+':'+status]+=1
    out[container]={'exit_code':p.returncode,'tail_lines':len(raw.splitlines()),'http_counts':dict(counts),
                    'error_types':dict(collections.Counter(re.findall(r'\b[A-Za-z]+(?:Error|Exception)\b',raw))),
                    'oracle_errors':dict(collections.Counter(re.findall(r'\b(?:ORA|DPY|DPI)-\d+',raw)))}
sql="""SELECT json_build_object(
'send_jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,created_at::text,finished_at::text,available_at::text,last_error->>'code' code FROM job WHERE job_type='drug_trace.send' AND created_at>='2026-10-25 16:00:00' ORDER BY created_at) x),
'today_reports',(SELECT json_agg(row_to_json(x)) FROM (SELECT report_date::text,source,outcome,candidate_count,send->>'sent' sent,send->>'aborted_reason' aborted_reason,send->>'errors_total' errors_total,elapsed_seconds,updated_at::text FROM drug_trace_daily_report ORDER BY updated_at DESC LIMIT 3) x),
'today_attempt_times',(SELECT json_agg(row_to_json(x)) FROM (SELECT started_at::text,ended_at::text,http_status,outcome FROM delivery_attempt WHERE started_at>='2026-10-25 16:00:00' ORDER BY started_at) x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=25)
out['database_exit_code']=p.returncode
if p.returncode==0:out['database']=json.loads(p.stdout)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
