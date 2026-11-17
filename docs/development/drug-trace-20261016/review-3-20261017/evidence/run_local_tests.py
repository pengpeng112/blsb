"""Run only changed FIX2/local TLS tests with existing tools; no installations."""
from pathlib import Path
import json, os, subprocess, sys
ROOT = Path(__file__).resolve().parents[5]
OUT = Path(__file__).resolve().parent
env = os.environ.copy()
env['PATH'] = r'D:\Program Files\Git\bin;D:\Program Files\Git\usr\bin;' + env.get('PATH', '')
cmd = [sys.executable, '-m', 'pytest', 'tests/integration/test_drug_trace_fix2.py',
       'tests/integration/test_dmz_forwarder.py', '-q', '-ra', '--junitxml='+str(OUT/'local-tests.xml')]
r = subprocess.run(cmd, cwd=ROOT/'implementation', env=env, capture_output=True, text=True)
(OUT/'local-tests.log').write_text(r.stdout+'\n'+r.stderr, encoding='utf-8')
(OUT/'test-command.json').write_text(json.dumps({'command':cmd,'exit_code':r.returncode},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(r.stdout[-1300:]); print('exit_code='+str(r.returncode)); raise SystemExit(r.returncode)
