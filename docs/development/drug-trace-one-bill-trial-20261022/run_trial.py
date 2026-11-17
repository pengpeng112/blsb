"""One explicitly authorized real submission, using existing audited APIs.

No bypass transport, direct status edits, source writes or upload retries.
Production deferrals are recorded as user exceptions, not verified facts.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import re
import sys
import time
from email.utils import parsedate_to_datetime

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


d = load("trial_source_helpers", "docs/development/drug-trace-source-deploy-20261022/deploy_source.py")
d.HERE = HERE
dmz = load("trial_dmz_helpers", "docs/deployment/drug-trace-install-20261021/deploy_dmz.py")


def dmz_action(enable=False):
    client = dmz.connect()
    try:
        rc, out, err = dmz.run(client,
            "curl -4 --noproxy '*' -sI --connect-timeout 5 --max-time 15 https://eco.taobao.com/router/rest", 25)
        lines = out.splitlines()
        dates = [line.split(":", 1)[1].strip() for line in lines if line.lower().startswith("date:")]
        assert rc == 0 and any("200" in line for line in lines[:1]) and len(dates) == 1
        ref = {"reference_epoch": parsedate_to_datetime(dates[0]).timestamp(),
               "observed_local_epoch": time.time(), "platform_http_date": dates[0]}
        d.save("platform-clock", {"exit_code": rc, "result": ref})
        if enable:
            code = r'''
import json,os,shutil,subprocess
p='/etc/blyb-dmz/forwarder.json'
c=json.load(open(p))
method='alibaba.alihealth.drugtrace.top.yljg.uploadretail'
before=list(c['allowed_top_methods'])
assert not c.get('enable_inout_upload')
assert set(before).issubset({method,'alibaba.alihealth.drugtrace.top.yljg.query.billstatus'})
backup='/root/forwarder-before-single-trial-20261022.json'
if not os.path.exists(backup):shutil.copy2(p,backup);os.chmod(backup,0o600)
if method not in before:
 c['allowed_top_methods']=before+[method]
 tmp=p+'.single-trial.tmp'
 with open(tmp,'w') as f:json.dump(c,f,indent=2)
 os.chmod(tmp,0o600)
 os.chown(tmp,0,__import__('grp').getgrnam('blyb-dmz').gr_gid)
 os.chmod(tmp,0o640)
 subprocess.run(['/opt/blyb-dmz/runtime/bin/python3.11','/opt/blyb-dmz/current/forwarder.py','--config',tmp,'--check-config'],check=True,stdout=subprocess.DEVNULL)
 os.replace(tmp,p)
 subprocess.run(['systemctl','restart','blyb-dmz-forwarder'],check=True)
subprocess.run(['systemctl','is-active','--quiet','blyb-dmz-forwarder'],check=True)
print(json.dumps({'allowed_top_methods':c['allowed_top_methods'],'inventory_enabled':c.get('enable_inout_upload'),'retained_per_user':True}))
'''
            import base64, shlex
            cmd = "/opt/blyb-dmz/runtime/bin/python3.11 -c " + shlex.quote(
                "import base64;exec(base64.b64decode('" + base64.b64encode(code.encode()).decode() + "'))")
            rc, out, err = dmz.run(client, cmd, 45)
            result = json.loads(out) if rc == 0 else {"error_type": "DMZ_POLICY_FAILED"}
            d.save("dmz-policy", {"exit_code": rc, "result": result})
            assert rc == 0, "DMZ policy failed; no upload performed"
        return ref
    finally:
        client.close()


def preflight():
    ref = dmz_action(False)
    jump = d.deploy.connect_jump(d.fb.creds()["jump"])
    try:
        d.fb.feed_file(jump, "/root/trial-clock.json", json.dumps(ref).encode())
        rc, tr = d.nested(jump, "docker cp /root/trial-clock.json <worker-container>:/tmp/trial-clock.json", 60)
        assert rc == 0
    finally:
        jump.close()
    d.worker_script("preflight_worker")


def main():
    phase = sys.argv[1]
    if phase == "preflight":
        preflight()
    elif phase == "dmz-enable":
        dmz_action(True)
    elif phase in ("approve", "send", "query"):
        if phase == "approve":
            jump=d.deploy.connect_jump(d.fb.creds()["jump"])
            try:
                rc,tr=d.nested(jump,
                    "docker cp <worker-container>:/tmp/trial-preflight.json /root/trial-preflight.json "
                    "&& chmod 600 /root/trial-preflight.json "
                    "&& docker cp /root/trial-preflight.json blyb-core-1:/tmp/trial-preflight.json",60)
                assert rc == 0
            finally:jump.close()
        d.api(phase)
    elif phase == "status":
        d.worker_script("status_worker")
    else:
        raise RuntimeError("unknown stage")


if __name__ == "__main__":
    main()
