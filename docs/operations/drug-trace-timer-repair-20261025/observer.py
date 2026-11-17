"""Bounded read-only observer for this repair run. No sends/retries/source reads."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path('/var/log/blyb/repair-observe-20261025')


def command(args):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    return p.returncode, p.stdout.decode('utf8', 'replace')


def snapshot():
    out = {'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    rc, text = command(['systemctl', 'show', 'blyb-daily-upload.service', '-p', 'ActiveState', '-p', 'SubState', '-p', 'ExecMainStatus'])
    out['service'] = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
    sql = """SELECT json_build_object(
       'collections',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,window_begin::text,window_end::text,finished_at::text FROM drug_trace_collection_run WHERE created_at>='2026-10-25 09:57:00' ORDER BY created_at DESC LIMIT 2) x),
       'submissions',(SELECT json_agg(row_to_json(x)) FROM (SELECT state,count(*) n FROM submission GROUP BY state) x),
       'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills FROM drug_trace_send_quota) x),
       'persisted_bills',(SELECT count(*) FROM drug_trace_bill WHERE bill_time>='2026-10-24' AND bill_time<'2026-10-25'))"""
    rc, text = command(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000',
                       'blyb-db-1', 'psql', '-X', '-U', 'blyb', '-d', 'blyb', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql])
    out['database_exit_code'] = rc
    if rc == 0:
        out['database'] = json.loads(text)
    receipt = Path('/var/log/blyb/daily-upload/20261025-180611.json')
    out['receipt_bytes'] = receipt.stat().st_size if receipt.exists() else None
    if out['receipt_bytes']:
        try:
            d = json.loads(receipt.read_text())
            out['receipt'] = {k: d[k] for k in ('window', 'outcome', 'fatal', 'candidate_count', 'elapsed_seconds') if k in d}
            for key in ('send', 'query'):
                out['receipt'][key] = {k: v for k, v in (d.get(key) or {}).items() if isinstance(v, (int, bool))}
                out['receipt'][key]['error_count'] = len((d.get(key) or {}).get('errors') or [])
        except (ValueError, OSError):
            out['receipt_parse_error'] = True
    return out


def main():
    ROOT.mkdir(mode=0o700, exist_ok=True)
    os.chmod(ROOT, 0o700)
    deadline = time.monotonic() + 6 * 3600
    while time.monotonic() < deadline:
        try:
            row = snapshot()
        except Exception as exc:
            row = {'observed_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'error_type': type(exc).__name__}
        with (ROOT / 'progress.jsonl').open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=True) + '\n')
        tmp = ROOT / 'latest.json.tmp'
        tmp.write_text(json.dumps(row, ensure_ascii=True, indent=2))
        tmp.replace(ROOT / 'latest.json')
        if (row.get('service') or {}).get('ActiveState') in ('inactive', 'failed'):
            row['observation_complete'] = True
            (ROOT / 'final.json').write_text(json.dumps(row, ensure_ascii=True, indent=2))
            return 0
        time.sleep(60)
    (ROOT / 'observer-timeout.json').write_text(json.dumps({'observer_budget_exhausted': True}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
