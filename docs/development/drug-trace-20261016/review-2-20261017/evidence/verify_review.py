"""Verify review deliverables locally without reading credentials."""
from pathlib import Path
from datetime import datetime, timezone
import ast
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[5]
BASE = Path(__file__).resolve().parents[1]
OUT = BASE / 'evidence'
target = OUT / 'verification.json'
if not target.exists():
    target.write_text('{}\n', encoding='utf-8')
baseline = json.loads((OUT / 'baseline-hashes.json').read_text(encoding='utf-8'))
allowed = {'docs/ai/STATE.md', 'docs/ai/BACKLOG.md', 'docs/ai/HANDOFF.md', 'docs/README.md'}
changed = []
for name, expected in baseline.items():
    p = ROOT / name
    if name not in allowed and (not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected):
        changed.append(name)
docs = list(BASE.glob('*.md')) + [ROOT / name for name in allowed] + [ROOT / 'docs/ai/sessions/DTR-REVIEW2-20261017.md']
bad, links = [], 0
for p in docs:
    body = p.read_text(encoding='utf-8')
    for link in re.findall(r'\[[^\]]*\]\(([^)]+)\)', body):
        link = link.strip('<>').split('#', 1)[0]
        if not link or re.match(r'^[a-z]+://', link):
            continue
        links += 1
        if not (p.parent / link).resolve().exists():
            bad.append({'file': p.relative_to(ROOT).as_posix(), 'target': link})
syntax = []
for p in OUT.glob('*.py'):
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
    syntax.append(p.name)
def xml_totals(path):
    ss = list(ET.parse(path).getroot().iter('testsuite'))
    d = {k: sum(int(s.get(k, 0)) for s in ss) for k in ('tests', 'failures', 'errors', 'skipped')}
    d['passed'] = d['tests'] - d['failures'] - d['errors'] - d['skipped']
    return d
local = xml_totals(OUT / 'local-regression.xml')
initial = xml_totals(OUT / 'initial-environment-regression.xml')
history = xml_totals(ROOT / 'implementation/var/drugtrace-regression-20261016.xml')
probes = json.loads((OUT / 'probe-results.json').read_text(encoding='utf-8'))['observations']
diff = subprocess.run(['git', 'diff', '--check'], cwd=ROOT, capture_output=True, text=True)
# Generic accidental private-key/credential markers only, no credential-file access.
markers = []
for p in [*docs, *OUT.glob('*.py'), *OUT.glob('*.log')]:
    body = p.read_text(encoding='utf-8')
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', body):
        markers.append(p.relative_to(ROOT).as_posix())
result = {'at_utc': datetime.now(timezone.utc).isoformat(),
    'protected_baseline_files': len(baseline) - len(allowed),
    'unexpected_original_changes': changed, 'local_links_checked': links, 'broken_links': bad,
    'script_syntax': syntax, 'local_original_tests_rerun': local, 'initial_environment_run': initial,
    'historical_full_regression_not_rerun': history,
    'offline_observations': len(probes), 'issues_reproduced': sum(x['issue_reproduced'] for x in probes),
    'diff_check_exit_code': diff.returncode, 'private_key_markers_in_deliverables': markers,
    'credentials_read_this_task': False, 'remote_access_this_task': False}
result['passed'] = (not changed and not bad and not markers and diff.returncode == 0 and
                    local['passed'] == 47 and local['failures'] == 0 and local['skipped'] == 0 and
                    len(probes) == 16 and all(x['issue_reproduced'] for x in probes))
target.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result['passed'] else 1)
