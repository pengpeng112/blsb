"""Authorized source delivery through pinned SSH, with safe aggregate receipts.

No source writes or platform upload operation exists in this script.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
RID = "src-20261022T103000Z"
STAGED = f"<prod-path>-staging/{RID}"


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fb = load("source_fb", "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
deploy = fb.deploy
deploy.load_creds = fb.creds


def save(stage, result):
    ev = HERE / "evidence"
    ev.mkdir(exist_ok=True)
    name = stage + "-" + datetime.now(timezone.utc).strftime("%H%M%S") + ".json"
    (ev / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"stage": stage, "evidence": name, **result}, ensure_ascii=False))


def nested(jump, script, timeout=600):
    rc, tr = fb.nested(jump, script, timeout)
    # Known transient TCP refusal: one transport retry, no password retry.
    if rc == 255 and "Connection refused" in tr:
        save("transport-refused", {"exit_code": rc, "retry": "once; TCP only"})
        rc, tr = fb.nested(jump, script, timeout)
    return rc, tr


def command(stage, cmd, timeout=900):
    import shlex
    jump = deploy.connect_jump(fb.creds()["jump"])
    try:
        rc, tr = nested(jump, "bash -lc " + shlex.quote(cmd), timeout)
        # Commands and logs here contain no credentials or business records.
        out = tr.replace("\r", "")
        result = {"exit_code": rc, "output": out[-10000:]}
        save(stage, result)
        if rc:
            raise RuntimeError(f"{stage} failed; see receipt")
    finally:
        jump.close()


BIND_CODE = r'''
import json, os, re, shutil
args=json.load(open('/root/srcdeploy-reader.json'))
p='/etc/blyb/runtime.env'
shutil.copy2(p,'/root/srcdeploy-runtime-before-bind.env')
os.chmod('/root/srcdeploy-runtime-before-bind.env',0o600)
text=open(p).read()
value=args['user']+'/'+args['password']+'@<his-host>:1521/his'
assert '\n' not in value and '$' not in value and '#' not in value
line='BLYB_DRUGTRACE_ORACLE_DSN='+value
if re.search(r'^BLYB_DRUGTRACE_ORACLE_DSN=',text,re.M):
 text=re.sub(r'^BLYB_DRUGTRACE_ORACLE_DSN=.*$',lambda m:line,text,flags=re.M)
else:text=text.rstrip()+'\n'+line+'\n'
tmp=p+'.srcdeploy.tmp'
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as f:f.write(text)
os.replace(tmp,p);os.chmod(p,0o600)
os.remove('/root/srcdeploy-reader.json')
print('DSN_BOUND_WITHOUT_ECHO')
'''


def bind():
    script = "$c=Import-Clixml -LiteralPath '.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'; @{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress"
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, cwd=ROOT, check=True)
    data = json.loads(p.stdout)
    assert data["user"].upper() == "BLYB_DRUGTRACE"
    jump = deploy.connect_jump(fb.creds()["jump"])
    try:
        fb.feed_file(jump, "/root/srcdeploy-reader.json", json.dumps(data).encode())
        fb.feed_file(jump, "/root/srcdeploy-bind.py", BIND_CODE.encode())
        rc, tr = nested(jump, "python3 /root/srcdeploy-bind.py", 60)
        save("bind", {"exit_code": rc, "bound": "DSN_BOUND_WITHOUT_ECHO" in tr})
        if rc or "DSN_BOUND_WITHOUT_ECHO" not in tr:
            raise RuntimeError("bind failed; no secret output")
    finally:
        data.clear()
        jump.close()
    command("worker-recreate", "set -e; cd /opt/blyb/current/implementation; "
            "docker compose --project-name blyb --env-file /etc/blyb/runtime.env "
            "-f deploy/compose/docker-compose.yml -f deploy/compose/drug-trace.override.yml "
            "up -d worker >/root/srcdeploy-worker.log 2>&1; "
            "bash deploy/linux/status.sh", 600)


def worker_script(name):
    code = (HERE / (name + ".py")).read_bytes()
    jump = deploy.connect_jump(fb.creds()["jump"])
    try:
        fb.feed_file(jump, f"/root/srcdeploy-{name}.py", code)
        rc, tr = nested(jump, f"docker cp /root/srcdeploy-{name}.py <worker-container>:/tmp/srcdeploy-{name}.py "
                        f"&& docker exec -u 0 <worker-container> python /tmp/srcdeploy-{name}.py", 300)
        match = re.search(r"__SOURCE_RESULT__(\{.*\})__END__", tr, re.S)
        result = json.loads(match.group(1)) if match else {"result_marker": False}
        save(name, {"exit_code": rc, "result": result})
        if rc or not match or result.get("ok") is not True:
            raise RuntimeError(name + " failed (details suppressed; see safe receipt)")
    finally:
        jump.close()


def api(phase):
    profile = json.loads((ROOT / ".ai/private/alihealth-drugtrace-20261020/profile.json").read_text("utf-8"))
    args = {"phase": phase, "ref_ent_id": profile["ref_ent_id"]}
    jump = deploy.connect_jump(fb.creds()["jump"])
    try:
        fb.feed_file(jump, "/root/srcdeploy-api-args.json", (json.dumps(args) + "\n").encode())
        fb.feed_file(jump, "/root/srcdeploy-api.py", (HERE / "api_runner.py").read_bytes())
        cmd = ("set -e; umask 077; "
               "docker cp <worker-container>:/tmp/srcdeploy-candidate.json /root/srcdeploy-candidate.json; "
               "chmod 600 /root/srcdeploy-candidate.json; "
               "docker cp /root/srcdeploy-api.py blyb-core-1:/tmp/srcdeploy-api.py; "
               "sed -n 's/^BLYB_BOOTSTRAP_ADMIN_TOKEN=//p' /etc/blyb/runtime.env > /root/.srcdeploy-token; "
               "cat /root/.srcdeploy-token /root/srcdeploy-api-args.json /root/srcdeploy-candidate.json | "
               "docker exec -i -u 0 blyb-core-1 python /tmp/srcdeploy-api.py; "
               "rc=$?; rm -f /root/.srcdeploy-token; exit $rc")
        rc, tr = nested(jump, cmd, 300)
        # Even if set -e exits before cleanup, remove the temporary token.
        nested(jump, "rm -f /root/.srcdeploy-token", 60)
        match = re.search(r"__SOURCE_RESULT__(\{.*\})__END__", tr, re.S)
        result = json.loads(match.group(1)) if match else {"result_marker": False}
        save(phase, {"exit_code": rc, "result": result})
        if rc or not match or result.get("ok") is not True:
            raise RuntimeError(phase + " failed; inspect safe receipt")
    finally:
        jump.close()


def main():
    stage = sys.argv[1]
    if stage == "transfer":
        sys.argv = ["deploy_remote.py", "transfer", str(ROOT / "implementation/var" / RID)]
        raise SystemExit(deploy.main())
    if stage == "preflight":
        command(stage, "set -e; readlink -f /opt/blyb/current; "
                "cd /opt/blyb/current/implementation; bash deploy/linux/status.sh; "
                "df -h /opt /home; docker exec blyb-db-1 psql -U blyb -d blyb -At -c "
                "\"BEGIN READ ONLY; SELECT 'submissions',COUNT(*) FROM submission; ROLLBACK;\"")
    elif stage == "backup":
        command(stage, "set -e; umask 077; cd /opt/blyb/current/implementation; "
                "bash deploy/linux/backup.sh --with-objects >/root/srcdeploy-backup.log 2>&1; "
                "tail -25 /root/srcdeploy-backup.log; "
                "b=$(ls -1dt <prod-path>/backups/*/ | head -1); "
                "cd \"$b\"; sha256sum --quiet -c SHA256SUMS; echo BACKUP_HASH_OK", 3000)
    elif stage == "upgrade":
        command(stage, "set -e; umask 077; "
                f"bash {STAGED}/implementation/deploy/linux/install.sh {STAGED} "
                ">/root/srcdeploy-upgrade.log 2>&1; tail -40 /root/srcdeploy-upgrade.log; "
                "cd /opt/blyb/current/implementation; bash deploy/linux/status.sh", 3600)
    elif stage == "restore-check":
        command(stage, f"set -e; cd {STAGED}/implementation; "
                "b=$(ls -1dt <prod-path>/backups/*/ | head -1); "
                "bash deploy/linux/restore.sh \"$b\" --data-root <prod-path>-restore-check-20261022 "
                "--validate-only "
                ">/root/srcdeploy-restore.log 2>&1; tail -22 /root/srcdeploy-restore.log", 600)
    elif stage == "bind":
        bind()
    elif stage in ("verify_worker", "prepare_worker", "dryrun_worker"):
        worker_script(stage)
    elif stage in ("setup", "collect", "prepare"):
        api(stage)
    else:
        raise RuntimeError("unknown stage")


if __name__ == "__main__":
    main()
