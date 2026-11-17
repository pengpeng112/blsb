#!/usr/bin/env python3
"""INF-PUSHER-DEPLOY-20261028 · inf_pusher 部署到 <prod-host>（经 <jump-host>:<ssh-port> 跳板）。

复用 reporting-20261015 的 probe_ssh/deploy_remote（同指纹与密码通道口径，Secret 不入命令行/不回显）。
动作：上传 5 文件 → 主机环境探测（python3/oracledb/docker worker 容器）→ 双可达探测
（ODS 8.216:1521、前置机 <frontend-host>）→ 远端 selftest。
用法：python deploy_8156_infpusher.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = ROOT / "docs/deployment/reporting-20261015/probe_ssh.py"
DEPLOY = ROOT / "docs/deployment/reporting-20261015/deploy_remote.py"
REMOTE_DIR = "<deploy-path>"
# T100-20261107：tables.py（exam_item overlap 2→5）随 pusher/selftest/run.sh 一并上传
FILES = ["pusher.py", "tables.py", "selftest.py", "run.sh"]


def remote_mode_for(name: str) -> str:
    """PT-01/R5：远端权限映射——shell 脚本必须保留可执行位（run.sh 丢 +x 事故修复：
    此前统一 644 上传导致 OCI permission denied，cron 无法拉起）。
    离线单测锁定本规则（tests/unit/test_pt01_inf_pusher_reliability.py）。"""
    return "755" if name.endswith(".sh") else "644"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    probe = module("p", PROBE)
    deploy = module("d", DEPLOY)
    creds = probe.credentials()
    jump = deploy.connect_jump(creds["jump"])

    # 1) 环境与连通性探测（嵌套 exec 在 <prod-host> 上执行）
    script = r"""
set -e
echo "== host =="; uname -m; python3 --version 2>&1 || true
python3 -c 'import oracledb; print("oracledb", oracledb.__version__)' 2>&1 || echo "oracledb: absent(host)"
ls /opt/oracle/instantclient* 2>/dev/null | head -1 || echo "instantclient(host): absent"
docker ps --format '{{.Names}}' 2>/dev/null | grep -E 'worker|core' | head -4 || true
docker exec <worker-container> python -c 'import oracledb; print("worker oracledb", oracledb.__version__)' 2>&1 | head -1 || true
echo "== reach ODS <ods-host>:1521 =="
(exec 3<>/dev/tcp/<ods-host>/1521) 2>/dev/null && echo "ODS: OPEN" || echo "ODS: CLOSED"
echo "== reach front <frontend-host> =="
(exec 3<>/dev/tcp/<frontend-host>/8881) 2>/dev/null && echo "FRONT: OPEN" || echo "FRONT: CLOSED"
"""
    rc, out = deploy.nested_exec(jump, creds, script, timeout=90)
    print(out)
    if rc != 0:
        return 1

    # 2) 上传：base64 分块经 pty（双层 pty 二进制安全，同 DPL 现场结论），sha256 校验
    import base64
    import hashlib

    def feed_file(remote_path: str, data: bytes, mode: str = "644") -> bool:
        b64 = base64.b64encode(data).decode("ascii")
        chunks = [b64[i:i + 3500] for i in range(0, len(b64), 3500)]
        deploy.nested_exec(jump, creds,
                           "mkdir -p %s && rm -f %s.b64" % (REMOTE_DIR, remote_path),
                           timeout=30)
        for i, ch in enumerate(chunks):
            op = ">" if i == 0 else ">>"
            rc, out = deploy.nested_exec(
                jump, creds,
                "stty -echo; printf '%%s' '%s' %s %s.b64; stty echo; echo __CHUNK%d__"
                % (ch, op, remote_path, i), timeout=30)
            if "__CHUNK%d__" % i not in out:
                print("chunk %d marker missing for %s" % (i, remote_path))
                return False
        local_sha = hashlib.sha256(data).hexdigest()
        rc, out = deploy.nested_exec(
            jump, creds,
            "base64 -d %s.b64 > %s && rm -f %s.b64 && chmod %s %s "
            "&& sha256sum %s | cut -d' ' -f1" % (remote_path, remote_path,
                                                 remote_path, mode, remote_path,
                                                 remote_path), timeout=30)
        import re
        sha_line = next((ln for ln in reversed(out.splitlines())
                         if re.fullmatch(r"[0-9a-f]{64}", ln.strip())), "")
        remote_sha = sha_line.strip()
        ok = remote_sha == local_sha
        print("UPLOADED %-24s sha256 %s" % (remote_path.split("/")[-1],
                                            "OK" if ok else "MISMATCH:" + remote_sha))
        return ok

    deploy.nested_exec(jump, creds, "mkdir -p %s" % REMOTE_DIR, timeout=30)
    for f in FILES:
        if not feed_file("%s/%s" % (REMOTE_DIR, f), (HERE / f).read_bytes(),
                         mode=remote_mode_for(f)):
            print("upload failed:", f)
            return 1

    # 3) 远端自测（无 DB/网络依赖）
    rc, out = deploy.nested_exec(
        jump, creds, "cd %s && python3 selftest.py 2>&1 | tail -6" % REMOTE_DIR, timeout=120)
    print(out)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
