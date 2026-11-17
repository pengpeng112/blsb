"""User-authorized bounded one-bill recovery verification."""
from pathlib import Path
from datetime import datetime, timezone
import base64
import importlib.util
import json
import re
import shlex
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
if '--authorized-one-bill' not in sys.argv:
    raise SystemExit('One-bill send authorization required')
container = (HERE / ('resolve_container.py' if '--query-only' in sys.argv else 'smoke_container.py')).read_text('utf8')
script = 'CONTAINER_CODE=' + repr(container) + '\n' + (HERE / 'smoke_remote.py').read_text('utf8')
cmd = 'python3 -c ' + shlex.quote('import base64;exec(compile(base64.b64decode(' + repr(base64.b64encode(script.encode()).decode()) + '),"smoke-host","exec"))')
spec = importlib.util.spec_from_file_location('smoke_existing', ROOT / 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fb)
jump = fb.deploy.connect_jump(fb.creds()['jump'])
try:
    rc, transcript = fb.nested(jump, cmd, timeout=240)
finally:
    jump.close()
match = re.search(r'__ONE_BILL__(.*?)__ONE_BILL_END__', transcript, re.S)
out = {'ssh_exit_code': rc, 'captured_at': datetime.now(timezone.utc).isoformat(),
       'result': json.loads(match.group(1)) if match else {'error': 'NO_SAFE_RESULT_MARKER'}}
p = HERE / 'evidence' / ('one-bill-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
with p.open('x', encoding='utf8') as f:json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=True))
raise SystemExit(0 if out['result'].get('ok') else 1)
