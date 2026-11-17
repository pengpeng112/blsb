"""Install the daily-upload systemd timer on <prod-host> (user-authorized).

Feeds driver + units via jump, daemon-reload, ENABLES the timer (no immediate
start; first fire next 06:10 Asia/Shanghai). Verifies with list-timers.
Rollback: systemctl disable --now blyb-daily-upload.timer &&
          rm /etc/systemd/system/blyb-daily-upload.{service,timer} /opt/blyb/tools/blyb-daily-upload.py
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import shlex

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

spec = importlib.util.spec_from_file_location(
    'fb', ROOT / 'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fb)


def main():
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        fb.feed_file(jump, '/root/blyb-daily-upload.py',
                     (HERE / 'daily_upload_driver.py').read_bytes())
        fb.feed_file(jump, '/root/blyb-daily-upload.service',
                     (HERE / 'blyb-daily-upload.service').read_bytes())
        fb.feed_file(jump, '/root/blyb-daily-upload.timer',
                     (HERE / 'blyb-daily-upload.timer').read_bytes())
        cmd = ("set -e; "
               "mkdir -p /opt/blyb/tools /var/log/blyb/daily-upload; "
               "install -m 700 /root/blyb-daily-upload.py /opt/blyb/tools/blyb-daily-upload.py; "
               "install -m 644 /root/blyb-daily-upload.service /etc/systemd/system/; "
               "install -m 644 /root/blyb-daily-upload.timer /etc/systemd/system/; "
               "systemctl daemon-reload; "
               "systemctl enable blyb-daily-upload.timer; "
               "rm -f /root/blyb-daily-upload.py /root/blyb-daily-upload.service /root/blyb-daily-upload.timer; "
               "echo __LIST__$(systemctl list-timers blyb-daily-upload.timer --no-pager | head -3)__END__")
        rc, tr = fb.nested(jump, 'bash -lc ' + shlex.quote(cmd), 120)
    finally:
        jump.close()
    result = {'exit_code': rc, 'transcript_tail': tr[-800:]}
    ev = HERE / 'evidence'
    ev.mkdir(exist_ok=True)
    name = 'install-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'
    (ev / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                           encoding='utf8')
    ok = rc == 0 and 'blyb-daily-upload.timer' in tr
    print(json.dumps({'installed': ok, 'file': name,
                      'timer_line': next((l for l in tr.splitlines()
                                          if 'blyb-daily-upload' in l and 'n/a' not in l), '')},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
