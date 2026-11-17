from pathlib import Path
import importlib.util,json

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('mapping_recovery_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
conn=fb.deploy.connect_jump(fb.creds()['jump'])
try:
    fb.feed_file(conn,'/opt/blyb/tools/blyb-mapping-recovery-20261026.py',(HERE/'batch_runner.py').read_bytes(),mode='700')
    rc,_=fb.nested(conn,'systemd-run --unit=blyb-mapping-recovery-20261026 --property=RuntimeMaxSec=28800 --property=UMask=0077 /usr/bin/python3 /opt/blyb/tools/blyb-mapping-recovery-20261026.py',timeout=30)
finally:conn.close()
out={'exit_code':rc,'launched':rc==0,'unit':'blyb-mapping-recovery-20261026.service','dates':['2026-10-25','2026-10-24'],'logs':'/var/log/blyb/mapping-recovery-20261026','new_his_collection_allowed':False,'driver_unchanged':True}
(HERE/'evidence/recovery-launch.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8')
print(json.dumps(out))
