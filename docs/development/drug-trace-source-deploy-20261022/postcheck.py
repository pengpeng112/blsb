"""Final safe runtime/DMZ inventory. No business operations."""
import importlib.util
import json
from pathlib import Path
from datetime import datetime, timezone

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    d = load("source_deploy_final", HERE / "deploy_source.py")
    d.command("final-health", "set -e; readlink -f /opt/blyb/current; "
        "docker ps --filter name=blyb- --format '{{.Names}} {{.Status}} {{.Ports}}'; "
        "stat -c '%U:%G %a' /etc/blyb/runtime.env; "
        "docker exec blyb-core-1 python -c \"import os,json; "
        "print(json.dumps({'core_has_oracle_dsn':bool(os.getenv('BLYB_DRUGTRACE_ORACLE_DSN')),"
        "'core_has_app_secret':bool(os.getenv('BLYB_ALIHEALTH_APP_SECRET'))}))\"; "
        "sha256sum /opt/blyb/current/implementation/deploy/linux/restore.sh", 90)
    dmz = load("source_dmz_final", ROOT / "docs/deployment/drug-trace-install-20261021/deploy_dmz.py")
    client = dmz.connect()
    commands = {
        "service": "systemctl is-active blyb-dmz-forwarder; systemctl show blyb-dmz-forwarder -p User -p SubState",
        "time_sync": "chronyc tracking; chronyc sources -n",
        "platform": "date -u '+%Y-%m-%dT%H:%M:%SZ'; curl -4 --noproxy '*' -sI --connect-timeout 5 --max-time 15 https://eco.taobao.com/router/rest | head -10",
        "policy": "/opt/blyb-dmz/runtime/bin/python3.11 -c \"import json; c=json.load(open('/etc/blyb-dmz/forwarder.json')); print(json.dumps({k:c.get(k) for k in ('allowed_top_methods','enable_inout_upload','allow_ips')}))\"",
        "certificate": "openssl x509 -in /etc/blyb-dmz/certs/forwarder.crt -noout -subject -issuer -dates",
    }
    result = {"captured_at": datetime.now(timezone.utc).isoformat(), "dmz_mutations": 0, "uploads": 0, "checks": {}}
    try:
        for name, cmd in commands.items():
            rc, out, err = dmz.run(client, cmd, 40)
            result["checks"][name] = {"exit_code": rc, "stdout": out.strip(), "stderr": err.strip()}
    finally:
        client.close()
    with (HERE / "evidence/dmz-final-r2.json").open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
