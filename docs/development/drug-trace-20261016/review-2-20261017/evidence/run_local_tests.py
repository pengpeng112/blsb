"""Use existing Git Bash/OpenSSL explicitly; no installs or real systems."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
initial = OUT / 'local-regression.xml'
if initial.exists() and not (OUT / 'initial-environment-regression.xml').exists():
    initial.rename(OUT / 'initial-environment-regression.xml')
env = os.environ.copy()
env['PATH'] = r'D:\Program Files\Git\bin;D:\Program Files\Git\usr\bin;' + env.get('PATH', '')
command = [sys.executable, '-m', 'pytest',
    'tests/integration/test_drug_trace_domain.py', 'tests/integration/test_drug_trace_api.py',
    'tests/integration/test_drug_trace_ops.py', 'tests/integration/test_dmz_forwarder.py',
    '-q', '-ra', '--junitxml=' + str(OUT / 'local-regression.xml')]
result = subprocess.run(command, cwd=ROOT / 'implementation', env=env, capture_output=True, text=True)
(OUT / 'local-regression.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf-8')
(OUT / 'local-test-command.json').write_text(json.dumps({'command': command,
    'cwd': str(ROOT / 'implementation'), 'bash': shutil.which('bash', path=env['PATH']),
    'openssl': shutil.which('openssl', path=env['PATH']), 'exit_code': result.returncode}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(result.stdout[-1700:])
print('exit_code=' + str(result.returncode))
raise SystemExit(result.returncode)
