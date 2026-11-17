"""Safe diagnostics for slow batch; no source database requests or mutations."""
import collections
import json
import re
import subprocess


def run(args, timeout=25):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode('utf8', 'replace'), p.stderr.decode('utf8', 'replace')


out = {}
rc, raw, error = run(['docker', 'logs', '--since', '2026-10-25T09:57:00Z', '--tail', '300', '<worker-container>'])
text = raw + error
out['worker_logs'] = {'exit_code': rc, 'line_count': len(text.splitlines()),
    'oracle_errors': dict(collections.Counter(re.findall(r'\b(?:ORA|DPY|DPI)-\d+', text))),
    'exception_types': dict(collections.Counter(re.findall(r'\b[A-Za-z]+(?:Error|Exception)\b', text))),
    'known_markers': {k: text.count(k) for k in ['Traceback', 'LEASE_LOST', 'PROFILE_UNAPPROVED', 'VERSION_CONFLICT', 'SOURCE_LOOP_SUSPECTED']}}
rc, raw, _ = run(['docker', 'stats', '--no-stream', '--format', '{{json .}}'], 30)
out['resources'] = [{'name': d.get('Name'), 'cpu': d.get('CPUPerc'), 'memory': d.get('MemUsage')} for d in [json.loads(x) for x in raw.splitlines()]] if rc == 0 else {'exit_code': rc}
rc, raw, _ = run(['chronyc', 'tracking'])
out['ntp'] = {'exit_code': rc, 'leap_normal': bool(re.search(r'Leap status\s*:\s*Normal', raw)),
              'system_offset_seconds': re.findall(r'System time\s*:\s*([\d.]+) seconds', raw)}
sql = """
SELECT json_build_object(
 'job_leases',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,attempts,lease_until::text,available_at::text,extract(epoch from now()-created_at) age_seconds,last_error->>'code' error_code FROM job WHERE created_at>='2026-10-25 09:57:00') x),
 'db_connections',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,wait_event_type,wait_event,count(*) n,max(extract(epoch from now()-xact_start)) max_transaction_age FROM pg_stat_activity WHERE datname='blyb' GROUP BY state,wait_event_type,wait_event) x),
 'collection_checkpoint',(SELECT json_agg(row_to_json(x)) FROM (SELECT stream,partition,row_version,cursor_json->>'since_time' since_time,cursor_json->>'completed_window' completed_window FROM source_checkpoint WHERE partition='r10-batch-daily') x)
)
"""
rc, raw, error = run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000', 'blyb-db-1', 'psql', '-X', '-U', 'blyb', '-d', 'blyb', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql])
out['database_exit_code'] = rc
if rc == 0: out['data'] = json.loads(raw)
else: out['database_error'] = re.findall(r'column [\w." ]+ does not exist', error)
print('__PROGRESS__' + json.dumps(out, ensure_ascii=True) + '__PROGRESS_END__')
