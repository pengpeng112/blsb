"""Deploy the user-authorized timer hotfix with backups and pinned SSH."""
from datetime import datetime, timezone
from pathlib import Path
import base64
import importlib.util
import json
import re
import shlex
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    if '--authorized' not in sys.argv:
        raise SystemExit('Repair authorization required')
    origin = ROOT / 'docs/development/drug-trace-timed-report-20261024'
    files = {name: base64.b64encode((origin / name).read_bytes()).decode()
             for name in ['daily_upload_driver.py', 'blyb-daily-upload.service']}
    upgrade = '--extend-wait' in sys.argv
    expected = None
    if upgrade:
        receipts = [json.loads(p.read_text('utf8')) for p in sorted((HERE / 'evidence').glob('deploy-*.json'))]
        expected = next(d['result']['hashes'] for d in reversed(receipts) if d['result'].get('installed'))
    script = 'PAYLOAD=' + repr(files) + '\nSTART=' + repr('--start' in sys.argv) + '\n'
    script += 'EXTEND_WAIT=' + repr(upgrade) + '\nEXPECTED=' + repr(expected) + '\n'
    script += (HERE / 'deploy_remote.py').read_text('utf8')
    encoded = base64.b64encode(script.encode()).decode()
    cmd = 'python3 -c ' + shlex.quote('import base64;exec(compile(base64.b64decode(' + repr(encoded) + '),"timer-repair","exec"))')
    spec = importlib.util.spec_from_file_location('pinned_existing', ROOT / 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
    fb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fb)
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        rc, transcript = fb.nested(jump, cmd, timeout=120)
    finally:
        jump.close()
    marker = re.search(r'__REPAIR_JSON__(.*?)__REPAIR_END__', transcript, re.S)
    data = json.loads(marker.group(1)) if marker else {'error': 'NO_RESULT_MARKER'}
    result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'ssh_exit_code': rc, 'result': data}
    target = HERE / 'evidence' / ('deploy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    target.parent.mkdir(exist_ok=True)
    with target.open('x', encoding='utf8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=True))
    return 0 if rc == 0 and data.get('installed') else 1


if __name__ == '__main__':
    sys.exit(main())
