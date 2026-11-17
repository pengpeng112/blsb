import collections
import datetime
import json
from pathlib import Path
import re
import subprocess


def run(args, timeout=25):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode('utf8', 'replace'), p.stderr.decode('utf8', 'replace')


out = {'host_time': datetime.datetime.now(datetime.timezone.utc).isoformat()}
for name in ['blyb-daily-upload.service', 'blyb-daily-upload.timer', 'blyb-repair-observe-20261025.service']:
    rc, text, _ = run(['systemctl', 'show', name, '-p', 'ActiveState', '-p', 'SubState', '-p', 'ExecMainStatus', '-p', 'ExecMainStartTimestamp', '-p', 'ExecMainExitTimestamp', '-p', 'NextElapseUSecRealtime'])
    out[name] = dict(x.split('=', 1) for x in text.splitlines() if '=' in x)
receipts = []
for path in sorted(Path('/var/log/blyb/daily-upload').glob('*.json'))[-3:]:
    r = {'file': path.name, 'bytes': path.stat().st_size}
    if r['bytes']:
        try:
            d = json.loads(path.read_text())
            for key in ['window', 'candidate_count', 'outcome', 'fatal', 'elapsed_seconds']:
                if key in d: r[key] = d[key]
            r['send'] = {k: v for k, v in (d.get('send') or {}).items() if isinstance(v, (int, bool))}
            r['send_error_count'] = len((d.get('send') or {}).get('errors') or [])
            r['send_error_codes'] = dict(collections.Counter(str((x.get('res') or {}).get('error_code') or 'UNSPECIFIED') for x in (d.get('send') or {}).get('errors') or []))
            r['query_error_count'] = len((d.get('query') or {}).get('errors') or [])
            r['query_count'] = (d.get('query') or {}).get('queried', 0)
            r['report_push'] = d.get('report_push')
        except Exception as exc:
            r['parse_error'] = type(exc).__name__
    receipts.append(r)
out['receipts'] = receipts
observed = Path('/var/log/blyb/repair-observe-20261025/latest.json')
if observed.exists():
    out['background_observer'] = json.loads(observed.read_text())
err = Path('/var/log/blyb/daily-upload/last-error.log')
if err.exists():
    raw = err.read_text(errors='replace')[-5000:]
    out['driver_error'] = {'bytes': err.stat().st_size,
                           'types': sorted(set(re.findall(r'\b[A-Za-z]+(?:Error|Exception)\b', raw)))}
sql = """
SELECT json_build_object(
 'collections',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,window_begin::text,window_end::text,created_at::text,finished_at::text,summary FROM drug_trace_collection_run ORDER BY created_at DESC LIMIT 2) x),
 'quality',(SELECT json_agg(row_to_json(x)) FROM (SELECT b.business_kind,r.quality_state,count(*) n FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id WHERE b.bill_time>='2026-10-24' AND b.bill_time<'2026-10-25' GROUP BY b.business_kind,r.quality_state) x),
 'submissions',(SELECT json_agg(row_to_json(x)) FROM (SELECT s.state,count(*) n FROM submission s JOIN destination d ON d.id=s.destination_id WHERE d.code='alihealth-drugtrace-retail' GROUP BY s.state) x),
 'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills FROM drug_trace_send_quota) x),
 'jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,count(*) n FROM job WHERE created_at>='2026-10-25 09:57:00' GROUP BY job_type,state) x),
 'attempts',(SELECT json_agg(row_to_json(x)) FROM (SELECT http_status,outcome,count(*) n FROM delivery_attempt WHERE started_at>='2026-10-25 09:57:00' GROUP BY http_status,outcome) x),
 'query_receipt_count',(SELECT count(*) FROM receipt_event WHERE received_at>='2026-10-25 09:57:00'),
 'query_results',(SELECT json_agg(row_to_json(x)) FROM (SELECT parsed_result->>'state' state,parsed_result->>'result_type' result_type,parsed_result->>'sub_process_flag' sub_process_flag,count(*) n FROM receipt_event WHERE received_at>='2026-10-25 09:57:00' GROUP BY 1,2,3) x)
)
"""
rc, text, error = run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000', 'blyb-db-1', 'psql', '-X', '-U', 'blyb', '-d', 'blyb', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql])
out['database_exit_code'] = rc
if rc == 0:
    data = json.loads(text)
    for row in data.get('collections') or []:
        # Only numeric counters/known state strings leave the host.
        row['summary'] = {k: v for k, v in (row.get('summary') or {}).items() if isinstance(v, (int, float, bool))}
    out['database'] = data
else:
    out['database_error'] = re.findall(r'column [\w." ]+ does not exist', error)
print('__PROGRESS__' + json.dumps(out, ensure_ascii=True) + '__PROGRESS_END__')
