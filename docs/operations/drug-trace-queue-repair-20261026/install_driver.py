from pathlib import Path
import base64
import hashlib
import importlib.util
import json
import re
import shlex

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
spec=importlib.util.spec_from_file_location('driver_upgrade',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
payload={name:base64.b64encode((ROOT/'docs/development/drug-trace-timed-report-20261024'/name).read_bytes()).decode() for name in ['daily_upload_driver.py','blyb-daily-upload.service']}
code='PAYLOAD='+repr(payload)+'\n'+(HERE/'install_driver_remote.py').read_text('utf8')
conn=fb.deploy.connect_jump(fb.creds()['jump'])
try:
    # The uploaded file contains reviewed code and unit only, no credentials.
    fb.feed_file(conn,'/root/blyb-driver-queue-install.py',code.encode())
    rc,raw=fb.nested(conn,'python3 /root/blyb-driver-queue-install.py',timeout=100)
finally:conn.close()
match=re.search(r'__MAINTENANCE__(.*?)__END__',raw,re.S)
out={'exit_code':rc,'result':json.loads(match.group(1)) if match else {'marker_missing':True}}
(HERE/'evidence/driver-install.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8')
print(json.dumps(out))
if not match or rc:raise SystemExit(1)
