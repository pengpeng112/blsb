"""Local planning-package checks only; never connects to HIS or a platform."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import re
import sqlite3
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[4]
BASE = Path(__file__).resolve().parents[1]
OUT = BASE / 'evidence'
report = {'at_utc': datetime.now(timezone.utc).isoformat(), 'mode': 'local_document_verification', 'checks': {}}
checks = report['checks']

baseline = json.loads((OUT / 'baseline-hashes.json').read_text(encoding='utf-8'))
changed = [name for name, expected in baseline.items()
           if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected]
checks['original_files'] = {'count': len(baseline), 'unchanged': not changed, 'changed_paths': changed}

docs = list(BASE.glob('*.md')) + [ROOT / p for p in (
    'docs/ai/STATE.md', 'docs/ai/HANDOFF.md', 'docs/ai/BACKLOG.md', 'docs/README.md',
    'docs/ai/sessions/REPORTING-DRUGTRACE-PLAN-20261016.md')]
bad_links = []
links = 0
for path in docs:
    for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
        target = target.strip('<>').split('#', 1)[0]
        if not target or re.match(r'^[a-z]+://', target):
            continue
        links += 1
        if not (path.parent / target).resolve().exists():
            bad_links.append({'file': str(path.relative_to(ROOT)), 'target': target})
checks['document_links'] = {'checked': links, 'valid': not bad_links, 'bad': bad_links}

credentials = json.loads((ROOT / '.ai/private/reporting-20261015/deployment-login.private.json').read_text(encoding='utf-8'))
passwords = [v['password'] for v in credentials.values() if isinstance(v, dict) and isinstance(v.get('password'), str) and v['password']]
scan_paths = set(docs) | {p for p in BASE.rglob('*') if p.is_file() and p.suffix in {'.md', '.json', '.py'}}
found = []
for path in sorted(scan_paths):
    body = path.read_text(encoding='utf-8')
    if any(secret in body for secret in passwords):
        found.append(str(path.relative_to(ROOT)))
checks['credential_scan'] = {'files': len(scan_paths), 'no_supplied_password_values': not found, 'matching_files': found}

for path in OUT.glob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
checks['evidence_script_syntax'] = {'valid': True}

# A synthetic counterexample for the MAX projection, not a query against any real data.
db = sqlite3.connect(':memory:')
db.execute('CREATE TABLE demo(record_id TEXT, created TEXT, detail_id TEXT, trace_code TEXT)')
db.executemany('INSERT INTO demo VALUES (?,?,?,?)', [
    ('R1', '2026-10-12 10:00:00', 'D1', '000123'),
    ('R1', '2026-10-12 10:00:00', 'D2', '000124')])
maximum = db.execute('SELECT MAX(trace_code) FROM demo GROUP BY record_id,created').fetchone()[0]
distinct_count = db.execute('SELECT COUNT(DISTINCT trace_code) FROM demo').fetchone()[0]
db.close()
checks['synthetic_sql_counterexample'] = {'input_distinct_codes': distinct_count, 'max_projection': maximum,
    'projected_codes': 1, 'loss_demonstrated': distinct_count == 2 and maximum == '000124',
    'scope': 'aggregation_semantics_only_not_actual_HIS_cardinality'}

tree = ET.parse(ROOT / 'implementation/var/deploy-r2-full-regression.xml')
suites = list(tree.getroot().iter('testsuite'))
totals = {k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')}
totals['passed'] = totals['tests'] - totals['failures'] - totals['errors'] - totals['skipped']
totals['rerun_this_task'] = False
checks['historical_regression_xml'] = totals

sql_text = (BASE / 'SQL_REVIEW.md').read_text(encoding='utf-8')
raw_sql = re.search(r'```sql\n(.*?)\n```', sql_text, re.S).group(1)
checks['user_sql_preserved'] = {'sha256_utf8_lf': hashlib.sha256(raw_sql.encode()).hexdigest(),
    'has_original_listagg_and_max': 'LISTAGG(PRESC_DETAILID' in raw_sql and 'MAX(T_TRACE_CODE)' in raw_sql}

result = subprocess.run(['git', 'diff', '--check'], cwd=ROOT, capture_output=True, text=True)
checks['git_diff_check'] = {'exit_code': result.returncode, 'output': result.stdout + result.stderr}
report['passed'] = (not changed and not bad_links and not found and
    checks['synthetic_sql_counterexample']['loss_demonstrated'] and
    checks['user_sql_preserved']['has_original_listagg_and_max'] and result.returncode == 0)
(OUT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(0 if report['passed'] else 1)
