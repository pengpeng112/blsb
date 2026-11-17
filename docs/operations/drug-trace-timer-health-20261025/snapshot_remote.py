"""Executed in memory on app host. Whitelisted aggregates only leave the host."""
import collections
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess


def run(args, timeout=30):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode('utf8', 'replace'), p.stderr.decode('utf8', 'replace')


out = {'host_time': datetime.datetime.now(datetime.timezone.utc).isoformat()}
for unit in ('blyb-daily-upload.timer', 'blyb-daily-upload.service'):
    props = ['ActiveState', 'SubState', 'Result', 'ExecMainStatus', 'ExecMainStartTimestamp',
             'ExecMainExitTimestamp', 'NextElapseUSecRealtime', 'LastTriggerUSec', 'UnitFileState', 'TimersCalendar']
    rc, stdout, stderr = run(['systemctl', 'show', unit] + ['--property=' + k for k in props])
    out[unit] = {'exit_code': rc, 'properties': dict(line.split('=', 1) for line in stdout.splitlines() if '=' in line)}
rc, stdout, stderr = run(['docker', 'ps', '--format', '{{json .}}'])
out['containers'] = [{'name': d.get('Names'), 'status': d.get('Status')} for d in [json.loads(x) for x in stdout.splitlines()] ] if rc == 0 else {'exit_code': rc}
driver = Path('/opt/blyb/tools/blyb-daily-upload.py')
if driver.exists():
    content = driver.read_bytes()
    out['deployed_driver'] = {'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content),
                              'has_v2_report_push': b'push_daily_report' in content}
    out['deployed_driver']['line_65_context'] = [line for i, line in enumerate(content.decode().splitlines(), 1) if 61 <= i <= 68]
out['current_release'] = str(Path('/opt/blyb/current').resolve())
rcu, exec_start, _ = run(['systemctl', 'show', 'blyb-daily-upload.service', '--property=ExecStart', '--value'])
formats = re.findall(r'printf\s+"([%s\\n]+)"', exec_start)
out['unit_token_format'] = {'show_exit_code': rcu, 'printf_formats': formats}
if len(formats) == 1:
    # Simulate ONLY the printf clause with a public dummy, never actual secrets.
    fake = subprocess.run(['/bin/bash', '-c', 'printf "' + formats[0] + '" "PUBLIC_DUMMY"'], stdout=subprocess.PIPE)
    out['unit_token_format']['dummy_ends_newline'] = fake.stdout.endswith(b'\n')
    out['unit_token_format']['dummy_ends_literal_backslash_n'] = fake.stdout.endswith(b'\\n')
    out['unit_token_format']['dummy_stripped_equals_input'] = fake.stdout.decode().strip() == 'PUBLIC_DUMMY'
receipts = []
for path in sorted(Path('/var/log/blyb/daily-upload').glob('*.json'))[-8:]:
    row = {'file': path.name, 'bytes': path.stat().st_size,
           'mtime_utc': datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc).isoformat()}
    try:
        d = json.loads(path.read_text())
        row.update({k: d[k] for k in ['window', 'candidate_count', 'elapsed_seconds', 'outcome'] if k in d})
        if 'fatal' in d: row['fatal_kind'] = re.sub('[^a-zA-Z_]', '', str(d['fatal']))[:80]
        for key in ['send', 'query', 'collection', 'report_push']:
            v = d.get(key) or {}
            allowed = {'sent', 'skipped_terminal', 'resumed', 'prepared_new', 'queried', 'state', 'ok', 'status'}
            row[key] = {k: val for k, val in v.items() if k in allowed and isinstance(val, (int, bool, str, type(None)))}
            if key == 'collection':
                row[key]['summary_counts'] = {k: val for k, val in (v.get('summary') or {}).items() if isinstance(val, (int, float, bool))}
            row[key]['error_count'] = len(v.get('errors') or [])
            row[key]['error_codes'] = dict(collections.Counter(str((x.get('res') or {}).get('error_code') or 'UNSPECIFIED') for x in (v.get('errors') or [])))
            if key == 'send': row[key]['aborted'] = bool(v.get('aborted_reason'))
    except Exception as exc:
        row['parse_error'] = type(exc).__name__
    receipts.append(row)
out['receipts'] = receipts
log = Path('/var/log/blyb/daily-upload/last-error.log')
if log.exists():
    raw = log.read_text(errors='replace')[-100000:]
    out['last_error_log'] = {'bytes': log.stat().st_size, 'traceback_count': raw.count('Traceback'),
                            'error_types': dict(collections.Counter(re.findall(r'\b(?:[A-Za-z]+Error|[A-Za-z]+Exception)\b', raw))),
                            'ora_codes': sorted(set(re.findall(r'\bORA-\d+', raw)))}
    out['last_error_log']['frames'] = [{'file': Path(f).name, 'line': int(n), 'function': fun}
                                      for f, n, fun in re.findall(r'File "([^"]+)", line (\d+), in ([\w<>]+)', raw)]
    out['last_error_log']['attribute_errors'] = [{'object_type': t, 'attribute': a}
                                                for t, a in re.findall(r"AttributeError: '([A-Za-z]+)' object has no attribute '([A-Za-z_]+)'", raw)]
sql = """
SELECT json_build_object(
 'database_time',now()::text,
 'database_timezone',current_setting('TimeZone'),
 'daily_report_table',to_regclass('public.drug_trace_daily_report')::text,
 'schema',(SELECT json_agg(row_to_json(x)) FROM (SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('submission','delivery_attempt','job','drug_trace_collection_run','drug_trace_daily_report','reconcile_run','reconcile_item') ORDER BY table_name,ordinal_position) x),
 'submissions',(SELECT json_agg(row_to_json(x)) FROM (SELECT s.state,count(*) n,min(s.created_at)::text first_created,max(s.created_at)::text last_created FROM submission s JOIN destination d ON d.id=s.destination_id WHERE d.code='alihealth-drugtrace-retail' GROUP BY s.state) x),
 'collections',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,window_begin::text,window_end::text,created_at::text,finished_at::text,json_build_object('error_count',summary->'error_count','bills',summary->'bills','ingested',summary->'ingested') summary FROM drug_trace_collection_run ORDER BY created_at DESC LIMIT 5) x),
 'quality_current',(SELECT json_agg(row_to_json(x)) FROM (SELECT b.business_kind,r.quality_state,count(*) n FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id WHERE b.bill_time>='2026-10-24' AND b.bill_time<'2026-10-25' GROUP BY b.business_kind,r.quality_state) x),
 'jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,count(*) n FROM job GROUP BY job_type,state) x),
 'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills,updated_at::text FROM drug_trace_send_quota) x),
 'evidence',(SELECT json_agg(row_to_json(x)) FROM (SELECT capability,status,valid_until::text FROM drug_trace_production_evidence ORDER BY capability) x)
)
"""
rc, stdout, stderr = run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000', 'blyb-db-1',
                         'psql', '-X', '-U', 'blyb', '-d', 'blyb', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql], 40)
out['database'] = {'exit_code': rc}
if rc == 0:
    out['database']['data'] = json.loads(stdout)
else:
    out['database']['error_kind'] = 'QUERY_FAILED'
    out['database']['missing_columns'] = re.findall(r'column [\w." ]+ does not exist', stderr)
if rc == 0:
    detail_sql = """
    SELECT json_build_object(
      'today_collections',(SELECT count(*) FROM drug_trace_collection_run WHERE created_at>='2026-10-24 22:10:00'),
      'today_submissions',(SELECT count(*) FROM submission WHERE created_at>='2026-10-24 22:10:00'),
      'today_attempts',(SELECT count(*) FROM delivery_attempt WHERE started_at>='2026-10-24 22:10:00'),
      'attempts',(SELECT json_agg(row_to_json(x)) FROM (SELECT a.http_status,a.outcome,count(*) n,max(a.started_at)::text latest_started FROM delivery_attempt a JOIN submission s ON s.id=a.submission_id JOIN destination d ON d.id=s.destination_id WHERE d.code='alihealth-drugtrace-retail' GROUP BY a.http_status,a.outcome) x),
      'today_jobs',(SELECT count(*) FROM job WHERE created_at>='2026-10-24 22:10:00'),
      'confirm_last_updated',(SELECT max(updated_at)::text FROM submission WHERE state='CONFIRMED'),
      'pending_items',(SELECT count(*) FROM submission_item i JOIN submission s ON s.id=i.submission_id WHERE s.state='CONFIRMED' AND i.state='PENDING')
    )
    """
    rc2, stdout2, stderr2 = run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000', 'blyb-db-1', 'psql', '-X', '-U', 'blyb', '-d', 'blyb', '-At', '-v', 'ON_ERROR_STOP=1', '-c', detail_sql], 40)
    out['activity_since_timer'] = {'exit_code': rc2, 'data': json.loads(stdout2) if rc2 == 0 else None}
# GET only. Keep host bootstrap token in RAM and stdin; no files, argv or output.
api_code = '''import sys,json,urllib.request,urllib.error
token=sys.stdin.readline().strip()
out=[]
for limit in (200,10):
 r={"limit":limit}
 req=urllib.request.Request("http://localhost:8000/api/v1/drug-trace/collection-runs?limit="+str(limit),headers={"Authorization":"Bearer "+token})
 try:
  with urllib.request.urlopen(req,timeout=20) as response:
   r["http_status"]=response.status;body=json.load(response)
 except urllib.error.HTTPError as exc:
  r["http_status"]=exc.code
  try:body=json.load(exc)
  except Exception:body=None
 r["root_type"]=type(body).__name__
 if isinstance(body,dict):
  r["root_keys"]=sorted(body.keys());data=body.get("data");r["data_type"]=type(data).__name__
  if isinstance(data,dict):r["data_keys"]=sorted(data.keys())
  elif isinstance(data,list):r["row_count"]=len(data)
  err=body.get("error")
  if isinstance(err,dict):r["error_code"]=err.get("code")
 out.append(r)
print(json.dumps(out))
'''
try:
    token = next(line.split('=', 1)[1] for line in Path('/etc/blyb/runtime.env').read_text().splitlines() if line.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
    p = subprocess.run(['docker', 'exec', '-i', '-u', '0', 'blyb-core-1', 'python', '-c', api_code], input=(token+'\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=50)
    out['readonly_api_shape'] = {'exit_code': p.returncode, 'responses': json.loads(p.stdout) if p.returncode == 0 else None}
    token = None
except Exception as exc:
    out['readonly_api_shape'] = {'error_type': type(exc).__name__}
print('__HEALTH_JSON__' + json.dumps(out, ensure_ascii=True) + '__HEALTH_END__')
