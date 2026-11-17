#!/usr/bin/env python3
"""INF-GO-20261028 · 注入平台 ODS 凭据到 <prod-host> inf.env（全程不回显）并跑全表 MOCK。

凭据来源：<relay-jump> data-asset-api 容器 /etc/data-asset/credentials/ods_internal（首行 user:password）。
用户授权（2026-10-28）：按平台现有用户即可，ODS 为数据中心非业务库。
通道：本机 → <jump-host>:<ssh-port> 跳板 → <prod-host>（stdin+stty -echo 注入，密码/凭据不进命令行、不打印）。
实测配方（探针验证）：host-key 应答只发一次（accepted 标志防双发）；双 -tt 下 EOF 用行首 Ctrl-D。
"""
import base64
import importlib.util
import io
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[3]
CRED_REMOTE = "/etc/data-asset/credentials/ods_internal"
ENV_REMOTE = "<prod-path>/exchange/inf-pusher/inf.env"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


probe = module("p", ROOT / "docs/deployment/reporting-20261015/probe_ssh.py")
deploy = module("d", ROOT / "docs/deployment/reporting-20261015/deploy_remote.py")


def fetch_ods_credential():
    """从 <relay-jump> 容器取 ods_internal 凭据（user:password），只返回不打印。"""
    cmd = ["ssh", "-i", r"C:\Users\Administrator\.ssh\id_ed25519_ai",
           "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
           "root@<frontend-host>3",
           "docker exec data-asset-api cat " + CRED_REMOTE]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    if r.returncode != 0:
        raise SystemExit("取凭据失败 rc=%d（不打印输出）" % r.returncode)
    line = r.stdout.decode("utf-8", "replace").strip().splitlines()[0]
    if ":" not in line:
        raise SystemExit("凭据格式异常（无冒号）")
    u, p = line.split(":", 1)
    return u.strip(), p.strip()


def feed_secret(jump, creds, remote_path, data: bytes, mode="600"):
    """stdin 注入到 <prod-host>：stty -echo + cat + 行首 Ctrl-D；凭据不进命令行、不打印。"""
    target = creds["target"]
    pin = json.loads((ROOT / ".ai/private/reporting-20261015"
                      "/target-nested-hostkey.json").read_text("utf-8"))
    b64 = base64.b64encode(data).decode("ascii")
    assert len(b64) < 4000 and "\n" not in b64
    marker = "__FEEDOK__"
    inner = ("echo STEP1-AUTHED; stty -echo; cat > {p}.b64; stty echo;"
             " base64 -d {p}.b64 > {p} && rm -f {p}.b64"
             " && chmod {m} {p} && echo {k}").format(p=remote_path, m=mode, k=marker)
    import shlex
    remote = ("LC_ALL=C ssh -tt -p 22 -o ConnectTimeout=10 -o NumberOfPasswordPrompts=1"
              " -o StrictHostKeyChecking=ask -o UserKnownHostsFile=/dev/null"
              " -o GlobalKnownHostsFile=/dev/null -o LogLevel=INFO "
              + shlex.quote("%s@%s" % (target["user"], target["host"]))
              + " " + shlex.quote(inner))
    chan = jump.get_transport().open_session()
    chan.get_pty(term="vt100", width=200, height=50)
    transcript, sent_pw, accepted, sent_data = "", False, False, False
    chan.exec_command(remote)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if chan.recv_ready():
            transcript += chan.recv(65536).decode("utf-8", "replace")
            if "continue connecting" in transcript and not accepted:
                m = re.search(r"fingerprint is (SHA256:[A-Za-z0-9+/=]+)", transcript)
                if not m or pin["sha256"] != m.group(1):
                    chan.close()
                    raise SystemExit("target fingerprint mismatch")
                chan.send("yes\n")
                accepted = True
            elif re.search(r"password:\s*$", transcript, re.I) and not sent_pw:
                chan.send(target["password"] + "\n")
                sent_pw = True
            elif "STEP1-AUTHED" in transcript and sent_pw and not sent_data:
                time.sleep(0.8)
                chan.send(b64 + "\n")
                time.sleep(0.5)
                chan.send("\x04")  # 行首 ^D：pty EOF（双 -tt 下 shutdown_write 无效）
                sent_data = True
            if marker in transcript:
                chan.close()
                return True
            if "Permission denied" in transcript:
                chan.close()
                raise SystemExit("目标密码被拒")
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        time.sleep(0.05)
    chan.close()
    raise SystemExit("注入失败（尾部=%r）"
                     % transcript[-80:].replace(target["password"], "***"))


def main():
    user, pwd = fetch_ods_credential()
    print("凭据已取得（user=%s***，长度=%d，不回显密码）" % (user[:2], len(pwd)))
    env_content = ("INF_ODS_USER=%s\nINF_ODS_PASSWORD=%s\n"
                   "INF_ODS_DSN=<ods-host>:1521/orcl\n" % (user, pwd)).encode()
    creds = probe.credentials()
    jump = deploy.connect_jump(creds["jump"])
    feed_secret(jump, creds, ENV_REMOTE, env_content)
    rc, out = deploy.nested_exec(
        jump, creds,
        "chown 10001:10001 %s && ls -l %s | awk '{print $1,$3,$4,$NF}'"
        % (ENV_REMOTE, ENV_REMOTE), 60)
    line = [l for l in out.splitlines() if "inf.env" in l]
    print("ENV-FILE:", line[0] if line else "?")

    # 全表 MOCK（真实读数，零发送；输出仅统计行）
    rc, out = deploy.nested_exec(
        jump, creds,
        "docker exec <worker-container> /data/exchange/inf-pusher/run.sh all mock 2>&1",
        timeout=600)
    for ln in out.splitlines():
        if ln.startswith("{") and '"table"' in ln:
            print("MOCK:", ln)
    errs = [l for l in out.splitlines() if "Error" in l or "Traceback" in l]
    if errs:
        print("ERRORS:", errs[:3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
