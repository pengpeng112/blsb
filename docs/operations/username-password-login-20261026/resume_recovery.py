"""Resume the same approved windows after authenticated upgrade verification."""
from pathlib import Path
import importlib.util,json,shlex,sys

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
source=(HERE.parent/'drug-trace-mapping-ntp-20261026/batch_runner.py').read_text('utf8')
source=source.replace('/var/log/blyb/mapping-recovery-20261026','/var/log/blyb/mapping-recovery-auth-20261026')
source=source.replace('queue-20261026T010000Z-r2','auth-20261026T043500Z')
source=source.replace('blyb-mapping-recovery-20261026.lock','blyb-mapping-recovery-auth-20261026.lock')
(HERE/'batch_runner_resumed.py').write_text(source,encoding='utf8')
spec=importlib.util.spec_from_file_location('password_resume_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
conn=fb.deploy.connect_jump(fb.creds()['jump'])
try:
    fb.feed_file(conn,'/opt/blyb/tools/blyb-mapping-recovery-auth-20261026.py',source.encode(),mode='700')
    rc,_=fb.nested(conn,'test -f /root/blyb-maintenance-backups/auth-20261026/login-verified.json && systemctl enable --now blyb-daily-upload.timer && systemd-run --unit=blyb-mapping-recovery-auth-20261026 --property=RuntimeMaxSec=28800 --property=UMask=0077 /usr/bin/python3 /opt/blyb/tools/blyb-mapping-recovery-auth-20261026.py',timeout=30)
finally:conn.close()
out={'exit_code':rc,'launched':rc==0,'unit':'blyb-mapping-recovery-auth-20261026.service','logs':'/var/log/blyb/mapping-recovery-auth-20261026','dates':['2026-10-25','2026-10-24'],'driver_unchanged':True,'new_source_collection':False}
(HERE/'evidence/resume-launch.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8')
print(json.dumps(out))
raise SystemExit(rc)
