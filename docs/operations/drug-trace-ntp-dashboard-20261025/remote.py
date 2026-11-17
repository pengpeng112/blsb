"""Pinned SSH code runner; only sanitized marker output is persisted."""
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


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def main():
    host, phase = sys.argv[1:3]
    code = (HERE / (phase + '_remote.py')).read_bytes()
    command = ('python3' if host == 'app' else '/opt/blyb-dmz/runtime/bin/python3.11') + ' -c ' + shlex.quote(
        'import base64;exec(compile(base64.b64decode(' + repr(base64.b64encode(code).decode()) + "),'maintenance','exec'))")
    if host == 'app':
        helper = load('maintenance_app', 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
        conn = helper.deploy.connect_jump(helper.creds()['jump'])
    elif host == 'dmz':
        helper = load('maintenance_dmz', 'docs/deployment/drug-trace-install-20261021/deploy_dmz.py')
        conn = helper.connect()
    else:
        raise ValueError('app|dmz')
    try:
        if host == 'app':
            rc, raw = helper.nested(conn, command, timeout=180)
        else:
            rc, raw, _ = helper.run(conn, command, timeout=180)
    finally:
        conn.close()
    match = re.search(r'__MAINTENANCE__(.*?)__END__', raw, re.S)
    out = {'host': host, 'phase': phase, 'exit_code': rc,
           'captured_at': datetime.now(timezone.utc).isoformat(),
           'result': json.loads(match.group(1)) if match else {'marker_missing': True}}
    target = HERE / 'evidence' / (host + '-' + phase + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps(out, ensure_ascii=True))
    return 0 if rc == 0 and match else 1


if __name__ == '__main__':
    raise SystemExit(main())
