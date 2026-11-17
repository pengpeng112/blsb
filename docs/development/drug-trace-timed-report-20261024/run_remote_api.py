"""Local runner: execute a container-side API script on <prod-host> via jump host.

Pattern follows deploy_source.api(): token read on host from
/etc/blyb/runtime.env, piped via stdin into docker exec blyb-core-1.
Result parsed from __TIMED_RESULT__ marker; saved to evidence/.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fb = load("timed_fb2", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")


def run_remote(script_path: Path, name: str, timeout: int = 300) -> dict:
    import shlex
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        fb.feed_file(jump, f"/root/timed-{name}.py", script_path.read_bytes())
        cmd = ("set -e; umask 077; "
               f"docker cp /root/timed-{name}.py blyb-core-1:/tmp/timed-{name}.py; "
               "sed -n 's/^BLYB_BOOTSTRAP_ADMIN_TOKEN=//p' /etc/blyb/runtime.env > /root/.timed-token; "
               f"cat /root/.timed-token | docker exec -i -u 0 blyb-core-1 python /tmp/timed-{name}.py; "
               "rc=$?; rm -f /root/.timed-token; exit $rc")
        rc, tr = fb.nested(jump, "bash -lc " + shlex.quote(cmd), timeout)
    finally:
        jump.close()
    m = re.search(r'__TIMED_RESULT__(\{.*\})__END__', tr, re.S)
    result = json.loads(m.group(1)) if m else {"marker": False, "transcript_tail": tr[-1200:]}
    ev = HERE / 'evidence'
    ev.mkdir(exist_ok=True)
    name2 = f"{name}-" + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'
    (ev / name2).write_text(json.dumps({'exit_code': rc, 'result': result},
                                       ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    return {'exit_code': rc, 'file': name2, 'result': result}


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else 'evidence'
    if which == 'evidence':
        r = run_remote(HERE / 'evidence_api.py', 'evidence')
    elif which == 'pipeline':
        r = run_remote(HERE / 'pipeline_api.py', 'pipeline', timeout=900)
    elif which == 'install':
        r = run_remote(HERE / 'install_api.py', 'install', timeout=300)
    else:
        raise SystemExit('usage: run_remote_api.py evidence|pipeline|install')
    res = r['result']
    print(json.dumps({'exit_code': r['exit_code'], 'ok': res.get('ok'),
                      'file': r['file'],
                      'calls': res.get('calls')}, ensure_ascii=False))
    if r['exit_code'] != 0 or not res.get('ok'):
        print(json.dumps(res, ensure_ascii=False)[:1200])
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
