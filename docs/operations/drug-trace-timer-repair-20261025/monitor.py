"""Capture sanitized progress without triggering any pipeline action."""
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


def main():
    script = (HERE / ('details_remote.py' if '--details' in sys.argv else 'monitor_remote.py')).read_bytes()
    command = 'python3 -c ' + shlex.quote('import base64;exec(compile(base64.b64decode(' + repr(base64.b64encode(script).decode()) + '),"monitor","exec"))')
    spec = importlib.util.spec_from_file_location('monitor_channel', ROOT / 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
    fb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fb)
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        rc, transcript = fb.nested(jump, command, timeout=60)
    finally:
        jump.close()
    match = re.search(r'__PROGRESS__(.*?)__PROGRESS_END__', transcript, re.S)
    out = {'captured_at': datetime.now(timezone.utc).isoformat(), 'ssh_exit_code': rc,
           'progress': json.loads(match.group(1)) if match else {'capture_failed': True}}
    target = HERE / 'evidence' / ('progress-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    with target.open('x', encoding='utf8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=True))
    return 0 if match and rc == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
