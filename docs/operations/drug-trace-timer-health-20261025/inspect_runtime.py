"""Read-only host/production DB snapshot; no source queries or API mutations."""
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
    spec = importlib.util.spec_from_file_location('existing_pinned_channel', ROOT / 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
    fb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fb)
    script_name = 'transport_remote.py' if '--transport' in sys.argv else 'snapshot_remote.py'
    script = (HERE / script_name).read_bytes()
    encoded = base64.b64encode(script).decode('ascii')
    command = 'python3 -c ' + shlex.quote('import base64;exec(compile(base64.b64decode(' + repr(encoded) + '),"readonly-health","exec"))')
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        rc, transcript = fb.nested(jump, command, timeout=150)
    finally:
        jump.close()
    match = re.search(r'__HEALTH_JSON__(.*?)__HEALTH_END__', transcript, re.S)
    result = {'ssh_exit_code': rc, 'captured_at': datetime.now(timezone.utc).isoformat(),
              'manual_uploads': 0, 'source_database_queries': 0, 'remote_changes': 0}
    if match:
        result['snapshot'] = json.loads(match.group(1))
    else:
        result['error'] = 'NO_JSON_MARKER'
        result['ssh_markers'] = [x for x in ['Connection refused', 'Connection timed out', 'Permission denied', 'Connection closed'] if x.lower() in transcript.lower()]
    out = HERE / 'evidence' / ('snapshot-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    out.parent.mkdir(exist_ok=True)
    with out.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if match and rc == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
