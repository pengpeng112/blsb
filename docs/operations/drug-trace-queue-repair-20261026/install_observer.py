from pathlib import Path
import importlib.util
import json
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('queue_observer_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
conn=fb.deploy.connect_jump(fb.creds()['jump'])
try:
    fb.feed_file(conn,'/opt/blyb/tools/blyb-queue-observe-20261026.py',(HERE/'observe_remote.py').read_bytes(),mode='700')
    command='systemctl restart blyb-queue-observe-20261026.service' if '--refresh' in sys.argv else 'systemd-run --unit=blyb-queue-observe-20261026 --property=RuntimeMaxSec=14500 /usr/bin/python3 /opt/blyb/tools/blyb-queue-observe-20261026.py --watch'
    rc,raw=fb.nested(conn,command,timeout=30)
    result={'exit_code':rc,'launched':rc==0,'unit':'blyb-queue-observe-20261026.service',
            'logs':'/var/log/blyb/queue-recovery-20261026','read_only':True}
finally:conn.close()
(HERE/('evidence/observer-refresh.json' if '--refresh' in sys.argv else 'evidence/observer-install.json')).write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result))
