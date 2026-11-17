"""rootcause-r2: bounded two-view re-verification (Q00/Q02/Q03 + Q01 view-side).

Reuses the r1 execution mechanism (<relay-jump> controlled OracleConnector with the
restricted BLYB_DRUGTRACE reader credential). Raw source tables are NOT
accessed here: SQL01/02/03 stay for DBA / approved audit identity execution.
No r1 artifact is overwritten; shared evidence carries aggregates only.
"""
from pathlib import Path
from datetime import datetime, timezone
import base64
import gzip
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
R1 = ROOT / 'docs/development/drug-trace-history-live-compare-20261023'
PRIVATE_BASE = Path(os.environ['LOCALAPPDATA']) / 'BLYB/history-rootcause-r2'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_samples(annotated, db):
    """Q01.1 sample selection: S01 code-visible-elsewhere, S02 code-absent,
    S03 same-visibility class as S01 but from a different original file.
    Deterministic: first by (original file, original row number)."""
    def locate(item):
        row = db.execute(
            'SELECT file,sheet,row_no,bill,bill_time,code FROM history '
            'WHERE bill=? AND code=? ORDER BY file,row_no LIMIT 1',
            (item['bill'], item['code'])).fetchone()
        return row

    located = []
    for item in annotated:
        row = locate(item)
        if row is not None:
            located.append((row, item))
    located.sort(key=lambda pair: (pair[0][0], pair[0][2]))

    s01 = next(p for p in located if p[1]['current_code_exists'])
    s02 = next(p for p in located if not p[1]['current_code_exists'])
    used_files = {s01[0][0], s02[0][0]}
    s03 = next(p for p in located if p[1]['current_code_exists'] and p[0][0] not in used_files)
    return [
        {'id': 'S01', 'rule': 'first code-visible-under-other-line by (file,row)',
         'file': s01[0][0], 'sheet': s01[0][1], 'row_no': s01[0][2],
         'bill': s01[0][3], 'bill_time': s01[0][4], 'code': s01[0][5]},
        {'id': 'S02', 'rule': 'first code-absent-from-view by (file,row)',
         'file': s02[0][0], 'sheet': s02[0][1], 'row_no': s02[0][2],
         'bill': s02[0][3], 'bill_time': s02[0][4], 'code': s02[0][5]},
        {'id': 'S03', 'rule': 'first code-visible-under-other-line from a file '
                              'different than S01/S02, by (file,row)',
         'file': s03[0][0], 'sheet': s03[0][1], 'row_no': s03[0][2],
         'bill': s03[0][3], 'bill_time': s03[0][4], 'code': s03[0][5]},
    ]


def main():
    if '--approved-daytime' not in sys.argv:
        raise SystemExit('Current daytime approval required')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    private = PRIVATE_BASE / ('live-' + run_id)
    private.mkdir(parents=True, exist_ok=False)

    # ---- local inputs (read-only; original artifacts untouched) ----
    full = json.loads((R1 / 'evidence/full-compare-r1.json').read_text('utf8'))
    diag = json.loads((R1 / 'evidence/diagnose-r1.json').read_text('utf8'))
    r1_full_detail = json.loads(Path(full['private_details_path']).read_text('utf8'))
    r1_diag_detail = json.loads(Path(diag['private_details_path']).read_text('utf8'))
    db = sqlite3.connect((Path(os.environ['LOCALAPPDATA']) /
                          'BLYB/history-compare-20261023/history.sqlite3').as_uri() + '?mode=ro', uri=True)
    dup_pairs = [[b, c] for b, c, n in db.execute(
        'SELECT bill,code,COUNT(*) FROM history GROUP BY bill,code HAVING COUNT(*)>1')]
    samples = select_samples(r1_diag_detail['missing_annotated'], db)
    db.close()
    excluded_pairs = [[r[0], r[1]] for r in r1_full_detail['currently_excluded_pairs']]
    missing_codes = sorted({item['code'] for item in r1_diag_detail['missing_annotated']})
    missing_pairs = [[r[0], r[1]] for r in r1_full_detail['missing_pairs']]

    # DBA sample package (restricted location only; never printed)
    (private / 'dba-sample-package.json').write_text(json.dumps({
        'run_id': run_id,
        'purpose': 'SQL01/02/03 bind values for DBA raw-table small-sample check',
        'samples': samples,
        'all_missing_codes_count': len(missing_codes),
        'all_missing_legacy_bills_count': len({p[0] for p in missing_pairs}),
        'sql_sources': ['docs/development/drug-trace-history-live-compare-20261023/sql/'
                        '01-dba-legacy-raw-check.sql',
                        'docs/development/drug-trace-history-live-compare-20261023/sql/'
                        '02-dba-legacy-master-link.sql',
                        'docs/development/drug-trace-history-live-compare-20261023/sql/'
                        '03-dba-code-context.sql'],
        'bind_mapping': {'code_0': 'S01.code', 'line_0': 'S01.bill',
                         'code_1': 'S02.code', 'line_1': 'S02.bill',
                         'code_2': 'S03.code', 'line_2': 'S03.bill'},
    }, ensure_ascii=False, indent=2), encoding='utf8')

    payload = {'excluded_pairs': excluded_pairs, 'dup_pairs': dup_pairs,
               'missing_codes': missing_codes}
    compressed = base64.b64encode(gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())).decode()
    del payload

    # ---- remote script (single bounded session) ----
    remote_code = (HERE / 'r2_remote.py').read_text('utf8')
    remote = 'HISTORY_GZ=' + repr(compressed) + '\n' + remote_code

    base = load('live_base', ROOT / 'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    prefix = base.REMOTE.split('try:\n    auth=query')[0].replace("'timeout_ms':45000", "'timeout_ms':30000")
    remote = prefix + remote
    for line in list(remote.splitlines()):
        if line.startswith('from app.core.database ') or line.startswith('from app.models.asset '):
            remote = remote.replace(line, '')

    credential = ROOT / '.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'
    ps = ("$c=Import-Clixml -LiteralPath '" + str(credential).replace("'", "''") +
          "';@{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps],
                            capture_output=True, text=True, timeout=15, check=True)
    credentials = json.loads(result.stdout)
    sjzc = load('live_sjzc', Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    imports = '\n'.join(line for line in sjzc.LIVE_CODE.splitlines() if line.startswith('from app.'))
    remote = imports + '\nCREDS=' + repr(credentials) + '\n' + remote
    credentials.clear()

    out = HERE / 'evidence' / ('r2-two-views-' + run_id + '.json')
    with out.open('x', encoding='utf8') as f:
        json.dump({'status': 'RESERVED'}, f)
    proc = subprocess.run(['ssh', '-i', sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
                           'docker exec -i -e PYTHONPATH=/app '
                           '-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
                          input=remote.encode(), capture_output=True, timeout=720)
    try:
        data = json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data = {'status': 'REMOTE_ERROR', 'error_type': type(exc).__name__,
                'ssh_exit_code': proc.returncode,
                'oracle_codes': sorted(set(re.findall(
                    r'(?:ORA|DPY|DPI)-\d+', proc.stderr.decode(errors='replace'))))}
    blob = data.pop('private_detail_gzip_base64', None)
    if blob is not None:
        target = private / 'r2-details.json'
        target.write_bytes(gzip.decompress(base64.b64decode(blob)))
        data['private_details_path'] = str(target)
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    data['run_id'] = run_id
    data['private_dir'] = str(private)
    data['samples_selected'] = [{'id': s['id'], 'rule': s['rule'], 'file': s['file'],
                                 'row_no': s['row_no']} for s in samples]
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    printable = {k: v for k, v in data.items()
                 if k not in ('queries', 'batches', 'registration', 'view_definitions')}
    print(json.dumps(printable, ensure_ascii=False))
    return 0 if data.get('run_completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
