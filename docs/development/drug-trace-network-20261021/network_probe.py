"""One-shot read-only check of the configured intranet/DMZ route; no business POST.

Reuse locally pinned SSH host identities and existing DPAPI credentials. Do not
call deployment main functions or change remote configuration/files.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from datetime import datetime, timezone

import paramiko

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = ROOT / "docs/deployment/reporting-20261015"
PRIVATE = ROOT / ".ai/private/reporting-20261015"
OUT = HERE / "evidence/network-probe.json"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main():
    # Fail before SSH if this revision was already run, including interrupted runs.
    with OUT.open("x", encoding="utf-8") as f:
        json.dump({"status": "RESERVED_OUTCOME_UNCONFIRMED"}, f)
    result = {"captured_at": datetime.now(timezone.utc).isoformat(),
              "read_only": True, "remote_changes": 0, "real_uploads": 0,
              "business_requests": 0, "credentials_in_output": False}
    old_probe = module("old_probe", BASE / "probe_ssh.py")
    deploy = module("deploy_helpers", BASE / "deploy_remote.py")
    old_dmz = module("dmz_helpers", BASE / "revision-7/probe_dmz.py")
    profiles = old_probe.credentials()
    clients = []
    try:
        try:
            jump = deploy.connect_jump(profiles["jump"])
            clients.append(jump)
            # Fixed read-only inventory; never cat runtime.env or compose config.
            script = """printf '__RELEASE__\\n'
readlink -f /opt/blyb/current
printf '__SERVICES__\\n'
docker ps --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'
printf '__SOURCE_TCP__\\n'
python3 -c 'import socket,time,json
for host,port in (("<relay-net-host>",443),("<relay-net-host>",8443),("<his-host>",1521)):
 s=socket.socket();s.settimeout(4);s.bind(("<prod-host>",0));t=time.monotonic();r={"source":s.getsockname(),"destination":[host,port]}
 try: s.connect((host,port));r["connected"]=True
 except OSError as e: r.update(connected=False,error_type=type(e).__name__)
 finally: r["elapsed_seconds"]=round(time.monotonic()-t,3);s.close()
 print(json.dumps(r))'
printf '__ACTIVE_PLUGINS__\\n'
docker exec blyb-core-1 python -c 'import os,json;print(json.dumps({"process":"core","plugins":os.environ.get("BLYB_PLUGINS","")}))'
docker exec <worker-container> python -c 'import os,json;print(json.dumps({"process":"worker","plugins":os.environ.get("BLYB_PLUGINS",""),"alihealth_secret_present":bool(os.environ.get("BLYB_ALIHEALTH_APP_SECRET"))}))'
printf '__UTC_CLOCK__\\n'
date -u '+%Y-%m-%dT%H:%M:%SZ'
printf '__END__\\n'
"""
            code, transcript = deploy.nested_exec(jump, profiles, script, timeout=35)
            # Omit SSH banner/prompt; only fixed command output is evidence.
            output = transcript.split("__RELEASE__", 1)[-1] if "__RELEASE__" in transcript else ""
            result["intranet"] = {"ssh_exit_code": code, "inventory_complete": "__END__" in output,
                                    "output": output.replace("\r", "")}
        except Exception as exc:
            result["intranet"] = {"error_type": type(exc).__name__}

        # Decrypt through a captured local pipe; never print or pass on command line.
        ps = """$ErrorActionPreference='Stop'
$c=Import-Clixml -LiteralPath '.ai/private/reporting-20261015/dmz-credential.clixml'
@{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress
"""
        capture = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                                 cwd=ROOT, capture_output=True, text=True, timeout=12, check=True)
        dmz_credentials = json.loads(capture.stdout)
        prior = json.loads((BASE / "revision-6/followup-evidence-20261016T085216Z.json").read_text("utf-8"))[
            "local_to_dmz_ssh_handshake"]["fingerprint"]

        class MatchPin(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                actual = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
                if actual != prior:
                    raise paramiko.SSHException("Previously pinned DMZ identity changed")

        dmz = paramiko.SSHClient()
        dmz.set_missing_host_key_policy(MatchPin())
        clients.append(dmz)
        try:
            dmz.connect("<relay-net-host>", port=22, username=dmz_credentials["user"],
                        password=dmz_credentials["password"], look_for_keys=False,
                        allow_agent=False, timeout=7, banner_timeout=10, auth_timeout=12)
            checks = {
                "listeners": "ss -lntH",
                "clock": "date -u '+%Y-%m-%dT%H:%M:%SZ'; LC_ALL=C timedatectl status",
                "forwarder_units": "systemctl show blyb-dmz-forwarder.service blyb-dmz-forwarder.socket -p LoadState -p ActiveState -p SubState -p FragmentPath",
                "forwarder_directories": "for p in /opt/blyb-dmz /opt/blyb-dmz/current /etc/blyb-dmz/forwarder.json; do if [ -e \"$p\" ]; then printf \"PRESENT %s\\n\" \"$p\"; else printf \"ABSENT %s\\n\" \"$p\"; fi; done",
                "official_dns": "timeout 8 getent ahostsv4 eco.taobao.com",
                "official_https": old_dmz.curl("https://eco.taobao.com/router/rest").replace("curl ", "curl -4 ", 1),
                "official_http": old_dmz.curl("http://gw.api.taobao.com/router/rest").replace("curl ", "curl -4 ", 1),
            }
            result["dmz"] = {"authenticated": True, "checks": {}}
            for name, command in checks.items():
                _, value = old_dmz.command(dmz, name, command)
                result["dmz"]["checks"][name] = value
        except Exception as exc:
            result["dmz"] = {"error_type": type(exc).__name__}
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        for secret in [profiles["jump"]["password"], profiles["target"]["password"], dmz_credentials["password"]]:
            if secret:
                encoded = encoded.replace(secret, "[REDACTED]")
        OUT.write_text(encoded + "\n", encoding="utf-8")
        print(encoded)
        return 0
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
