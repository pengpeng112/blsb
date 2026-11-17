"""Host wrapper: token only in RAM/stdin; script code contains no secrets."""
import json
from pathlib import Path
import subprocess

token = next(line.split('=', 1)[1] for line in Path('/etc/blyb/runtime.env').read_text().splitlines()
             if line.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
p = subprocess.run(['docker', 'exec', '-i', '-u', '0', '<worker-container>', 'python', '-c', CONTAINER_CODE],
                   input=(token+'\n').encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=210)
token = None
print(p.stdout.decode('utf8', 'replace'))
if p.returncode:
    print('__SMOKE_WRAPPER_ERROR__' + json.dumps({'container_exit_code': p.returncode}))
