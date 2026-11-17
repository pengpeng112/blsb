"""FB-04 只读诊断：两机时钟服务 + 证书元数据（DTR-FIRSTBILL-EXEC-20261021）。

零修改：只收集时钟服务现状（不猜 NTP 源、不改配置）与证书日期/SAN/EKU 元数据
（不打印任何私钥）。输出白名单字段到 evidence/fb04-clock-certs.json。
"""
from __future__ import annotations

import importlib.util
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


TARGET_CMDS = {
    # systemd219 兼容：status 而非 show；chrony/ntp 双探测
    "clock_status": "LC_ALL=C timedatectl status",
    "chrony_unit": "systemctl is-active chronyd 2>/dev/null; systemctl is-enabled chronyd 2>/dev/null; echo rc=$?",
    "chrony_sources": "command -v chronyc >/dev/null 2>&1 && chronyc sources 2>&1 | head -12 || echo NO_CHRONYC",
    "chrony_tracking": "command -v chronyc >/dev/null 2>&1 && chronyc tracking 2>&1 | head -14 || echo NO_CHRONYC",
    "ntp_unit": "systemctl is-active ntpd 2>/dev/null; systemctl is-enabled ntpd 2>/dev/null; echo rc=$?",
    "ntp_conf": "grep -h '^server\\|^pool' /etc/chrony.conf /etc/ntp.conf 2>/dev/null | head -6 || echo NO_CONF",
    "cert_meta": (
        "for c in /etc/blyb/certs/dmz-ca.crt /etc/blyb/certs/client.crt; do "
        "echo \"== $c\"; openssl x509 -in $c -noout -startdate -enddate -subject 2>/dev/null; "
        "openssl x509 -in $c -noout -text 2>/dev/null | grep -A1 'Subject Alternative Name' | tail -1; "
        "openssl x509 -in $c -noout -text 2>/dev/null | grep -A1 'Extended Key Usage' | tail -1; done"),
    "host_clock_epoch": "date -u '+%Y-%m-%dT%H:%M:%SZ'",
}

DMZ_CMDS = {
    "clock_status": "LC_ALL=C timedatectl status",
    "chrony_unit": "systemctl is-active chronyd 2>/dev/null; systemctl is-enabled chronyd 2>/dev/null; echo rc=$?",
    "chrony_sources": "command -v chronyc >/dev/null 2>&1 && chronyc sources 2>&1 | head -12 || echo NO_CHRONYC",
    "ntp_unit": "systemctl is-active ntpd 2>/dev/null; systemctl is-enabled ntpd 2>/dev/null; echo rc=$?",
    "ntp_conf": "grep -h '^server\\|^pool' /etc/chrony.conf /etc/ntp.conf 2>/dev/null | head -6 || echo NO_CONF",
    "cert_meta": (
        "for c in /etc/blyb-dmz/certs/forwarder.crt /etc/blyb-dmz/certs/clients-ca.crt; do "
        "echo \"== $c\"; openssl x509 -in $c -noout -startdate -enddate -subject 2>/dev/null; "
        "openssl x509 -in $c -noout -text 2>/dev/null | grep -A1 'Subject Alternative Name' | tail -1; done"),
    "host_clock_epoch": "date -u '+%Y-%m-%dT%H:%M:%SZ'",
}


def run_on_target():
    deploy = load("fb04_jump", "docs/deployment/reporting-20261015/deploy_remote.py")
    probe = load("fb04_credentials", "docs/deployment/reporting-20261015/probe_ssh.py")
    profiles = probe.credentials()
    jump = deploy.connect_jump(profiles["jump"])
    out = {}
    try:
        # 单次嵌套会话批量执行（避免连续多次嵌套连接触发 sshd 限流）
        sections = []
        for name, cmd in TARGET_CMDS.items():
            sections.append(f'echo "__FB04_{name}_BEGIN__"; {cmd}; '
                            f'echo "__FB04_{name}_END__=$?"')
        batch = "\n".join(sections)
        rc, transcript = deploy.nested_exec(jump, profiles, batch, timeout=120)
        text = transcript.replace("\r\n", "\n").replace("\r", "")
        import re
        for name in TARGET_CMDS:
            m = re.search(
                rf"__FB04_{name}_BEGIN__\n(.*?)\n__FB04_{name}_END__=(\d+)",
                text, re.S)
            if m:
                out[name] = {"rc": int(m.group(2)),
                             "out": m.group(1).strip()[:1200]}
            else:
                out[name] = {"rc": None, "out": "", "missing": True}
        out["_batch_rc"] = rc
    finally:
        jump.close()
    return {"host": "<prod-host>", "results": out}


def run_on_dmz():
    helper = load("fb04_dmz", "docs/deployment/drug-trace-install-20261021/deploy_dmz.py")
    client = helper.connect()
    out = {}
    try:
        for name, cmd in DMZ_CMDS.items():
            rc, stdout, _ = helper.run(client, cmd, timeout=60)
            out[name] = {"rc": rc, "out": stdout[-1200:]}
    finally:
        client.close()
    return {"host": "<relay-net-host>", "results": out}


def main() -> int:
    receipt = {"captured_at": datetime.now(timezone.utc).isoformat(),
               "read_only": True, "remote_config_changes": 0,
               "his_queries": 0, "upload_requests": 0}
    receipt["target_8156"] = run_on_target()
    receipt["dmz_1090"] = run_on_dmz()
    # 侧写时钟偏差（采集时刻两端 host_clock_epoch 差）
    def _epoch(node):
        t = receipt[node]["results"]["host_clock_epoch"]["out"].strip()
        return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp(), t
    try:
        e1, t1 = _epoch("target_8156")
        e2, t2 = _epoch("dmz_1090")
        receipt["clock_skew_seconds_dmz_minus_8156"] = round(e2 - e1, 1)
    except Exception as exc:  # noqa: BLE001
        receipt["clock_skew_error"] = type(exc).__name__
    path = HERE / "evidence" / "fb04-clock-certs.json"
    if path.exists():
        print("已存在，不覆盖", file=__import__("sys").stderr)
        return 2
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print("captured:", path.name,
          "skew:", receipt.get("clock_skew_seconds_dmz_minus_8156"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
