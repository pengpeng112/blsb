#!/usr/bin/env python3
"""INF-WATCH-IMPL-20261103 · inf_watchdog 部署到 <prod-host>（经 <jump-host>:<ssh-port> 跳板）。

复用 reporting-20261015 的 probe_ssh/deploy_remote（同指纹与密码通道口径，Secret 不入
命令行/不回显）；上传走 base64 分块+sha256 校验（inf_pusher 部署已验证的模式）。
顺序铁律（§0.4 红线 4）：selftest 全绿 → 手动演练 → 才挂 timer；因此分三个子命令分阶段执行：
  upload    Ph3：环境探测 → 上传 9 文件到 <watchdog-path>（中转）→ 落位
            <prod-path>/exchange/inf-watchdog/（700 root）→ 写真实 config（600 root，
            Ph0 为 file-only：smtp/webhook enabled=false）→ 宿主 selftest 全绿
  units     Ph5：4 个 unit 安装 /etc/systemd/system → daemon-reload → enable --now
            两 timer → list-timers 留证（在 Ph4 手动演练通过后才执行）
  uninstall 回退（§6）：disable 两 timer + 删 unit + 删 <prod-path>/exchange/inf-watchdog
            与中转 <watchdog-path>；推送主链路零影响
用法：python deploy_8156_infwatchdog.py {upload,units,uninstall,upgrade}
  upgrade   PT-02 受控升级（20261118）：备份生产 watchdog.py/selftest.py
            （.bak-YYYYMMDD-pt02）→ 上传新件到中转 → 落位**仅覆盖代码与模板
            （绝不触碰生产 config.json 与 state/）** → 宿主 selftest 全绿 →
            安装新 unit + daemon-reload → timers 保持启用。config.template.json
            仅作参考件落位，真实 config.json 由生产保留（relay.enabled 等不变）。
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROBE = ROOT / "docs/deployment/reporting-20261015/probe_ssh.py"
DEPLOY = ROOT / "docs/deployment/reporting-20261015/deploy_remote.py"
STAGE = "<watchdog-path>"                      # 中转（§1）
HOME_DIR = "<prod-path>/exchange/inf-watchdog"    # 落位（与推送器同一持久挂载，700 root）
# 落位权限：脚本 700（root 执行）；template/README 644 只读参考；真实 config 600 root
FILES = ["watchdog.py", "selftest.py", "config.template.json", "README.md",
         "inf-watchdog.service", "inf-watchdog.timer",
         "inf-watchdog-daily.service", "inf-watchdog-daily.timer"]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def feed_file(deploy, jump, creds, remote_path, data, mode="644"):
    """base64 分块经双层 pty 上传 + sha256 校验（deploy_8156_infpusher 同款）。"""
    b64 = base64.b64encode(data).decode("ascii")
    chunks = [b64[i:i + 3500] for i in range(0, len(b64), 3500)]
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
        "base64 -d %s.b64 > %s && rm -f %s.b64 && chmod %s %s && sha256sum %s | cut -d' ' -f1"
        % (remote_path, remote_path, remote_path, mode, remote_path, remote_path), timeout=30)
    sha_line = next((ln for ln in reversed(out.splitlines())
                     if re.fullmatch(r"[0-9a-f]{64}", ln.strip())), "")
    ok = sha_line.strip() == local_sha
    print("UPLOADED %-28s sha256 %s" % (remote_path.split("/")[-1],
                                        "OK" if ok else "MISMATCH:" + sha_line.strip()))
    return ok


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("upload", "units", "uninstall",
                                                 "upgrade"):
        print(__doc__)
        return 2
    action = sys.argv[1]
    probe = module("p", PROBE)
    deploy = module("d", DEPLOY)
    creds = probe.credentials()
    jump = deploy.connect_jump(creds["jump"])
    try:
        if action == "upgrade":
            # PT-02 受控升级：生产 config.json / state/ 零触碰；先备份后落位；
            # 任何一步失败立即中止（不留半升级状态——备份可一键回退）
            tag = "bak-" + __import__("datetime").date.today().strftime("%Y%m%d") + "-pt02"
            backup_cmd = (
                "set -e; cd %s; "
                "ls watchdog.py selftest.py >/dev/null; "
                "cp -p watchdog.py watchdog.py.%s; "
                "cp -p selftest.py selftest.py.%s; "
                "cp -p config.json config.json.pre-pt02 2>/dev/null || true; "
                "ls -la *.%s config.json.pre-pt02 2>/dev/null; echo __BACKED_UP__"
                % (HOME_DIR, tag, tag, tag))
            rc, out = deploy.nested_exec(jump, creds, backup_cmd, timeout=60)
            print(out)
            if "__BACKED_UP__" not in out:
                print("备份失败，中止升级（生产未做任何变更）")
                return 1
            # 上传新件到中转
            deploy.nested_exec(jump, creds, "mkdir -p %s" % STAGE, timeout=30)
            upgrade_files = ["watchdog.py", "selftest.py", "config.template.json",
                             "inf-watchdog.service", "inf-watchdog-daily.service"]
            for f in upgrade_files:
                if not feed_file(deploy, jump, creds, "%s/%s" % (STAGE, f),
                                 (HERE / f).read_bytes()):
                    print("upload failed:", f, "（生产代码未动，备份仍在）")
                    return 1
            # 落位：仅覆盖代码/模板/unit；config.json 与 state/ 绝不触碰
            place_cmd = (
                "set -e; "
                "install -m 700 %s/watchdog.py %s/watchdog.py; "
                "install -m 700 %s/selftest.py %s/selftest.py; "
                "install -m 644 %s/config.template.json %s/config.template.json; "
                "install -m 644 %s/inf-watchdog.service /etc/systemd/system/inf-watchdog.service; "
                "install -m 644 %s/inf-watchdog-daily.service "
                "/etc/systemd/system/inf-watchdog-daily.service; "
                "systemctl daemon-reload; "
                "systemctl list-timers 'inf-watchdog*' --no-pager | head -4; "
                "sha256sum %s/watchdog.py %s/selftest.py; "
                "echo __PLACED__"
                % (STAGE, HOME_DIR, STAGE, HOME_DIR, STAGE, HOME_DIR,
                   STAGE, STAGE, HOME_DIR, HOME_DIR))
            rc, out = deploy.nested_exec(jump, creds, place_cmd, timeout=90)
            print(out)
            if "__PLACED__" not in out:
                print("落位失败：", out[-500:], "—— 回退：cp *.bak-%s 回原名" % tag)
                return 1
            # 宿主 selftest（3.9 兼容 + 全绿门槛）
            rc, out = deploy.nested_exec(
                jump, creds,
                "cd %s && python3 selftest.py > /tmp/wd-selftest-pt02.log 2>&1; "
                "echo SELFTEST_RC=$?; tail -3 /tmp/wd-selftest-pt02.log; "
                "rm -f /tmp/wd-selftest-pt02.log"
                % HOME_DIR, timeout=300)
            print(out)
            if "SELFTEST_RC=0" not in out:
                print("宿主 selftest 未全绿——立即回退：cd %s && "
                      "cp -p watchdog.py.%s watchdog.py && cp -p selftest.py.%s selftest.py"
                      % (HOME_DIR, tag, tag))
                return 1
            print("UPGRADE DONE——生产 config.json/state 未触碰；回退件 *.%s 保留" % tag)
            return 0

        if action == "upload":
            # 1) 环境探测（只读）：python3 / 推送器 state 在位 / 未见既有 watchdog
            script = r"""
set -e
echo "== env =="; python3 --version 2>&1
ls -d <prod-path>/exchange/inf-pusher/state || echo "PUSHER_STATE_MISSING"
ls -d %s 2>/dev/null && echo "HOME_DIR_EXISTS(will merge)" || echo "HOME_DIR_ABSENT(fresh)"
ls /etc/systemd/system/inf-watchdog* 2>/dev/null || echo "NO_UNITS_YET"
""" % HOME_DIR
            rc, out = deploy.nested_exec(jump, creds, script, timeout=60)
            print(out)
            if "Python 3" not in out:
                print("python3 不可用，中止")
                return 1
            # 2) 上传到中转 <watchdog-path>
            deploy.nested_exec(jump, creds, "mkdir -p %s" % STAGE, timeout=30)
            for f in FILES:
                if not feed_file(deploy, jump, creds, "%s/%s" % (STAGE, f),
                                 (HERE / f).read_bytes()):
                    print("upload failed:", f)
                    return 1
            # 3) 落位 <prod-path>/exchange/inf-watchdog（700 root:root）+ 真实 config（600）
            #    真实 config 与 template 同值（Ph0=file-only；通道后补时只改宿主此文件）
            install_cmd = (
                "set -e; "
                "install -d -m 700 -o root -g root %s; "
                "install -m 700 %s/watchdog.py %s/watchdog.py; "
                "install -m 700 %s/selftest.py %s/selftest.py; "
                "install -m 644 %s/config.template.json %s/config.template.json; "
                "install -m 644 %s/README.md %s/README.md; "
                "cp %s/config.template.json %s/config.json && chmod 600 %s/config.json; "
                "mkdir -p %s/state && chmod 700 %s/state; "
                "chown -R root:root %s; "
                "ls -la %s; echo __INSTALLED__"
                % (HOME_DIR,
                   STAGE, HOME_DIR,
                   STAGE, HOME_DIR,
                   STAGE, HOME_DIR,
                   STAGE, HOME_DIR,
                   STAGE, HOME_DIR, HOME_DIR,
                   HOME_DIR, HOME_DIR,
                   HOME_DIR,
                   HOME_DIR))
            rc, out = deploy.nested_exec(jump, creds, install_cmd, timeout=60)
            if "__INSTALLED__" not in out:
                print("落位失败：", out[-500:])
                return 1
            print(out)
            # 4) 宿主 selftest（红线 4 第一关）；不用管道取退出码（sh 兼容）
            rc, out = deploy.nested_exec(
                jump, creds,
                "cd %s && python3 selftest.py > /tmp/wd-selftest.log 2>&1; "
                "echo SELFTEST_RC=$?; tail -4 /tmp/wd-selftest.log; rm -f /tmp/wd-selftest.log"
                % HOME_DIR, timeout=300)
            print(out)
            if "SELFTEST_RC=0" not in out or "PASS ====" not in out:
                print("宿主 selftest 未全绿，不得进入下一阶段")
                return 1
            return 0

        if action == "units":
            # §0.4 红线 4：必须在 Ph4 手动演练（check+test-alert）通过后执行
            script = (
                "set -e; "
                "install -m 644 %s/inf-watchdog.service %s/inf-watchdog.timer "
                "%s/inf-watchdog-daily.service %s/inf-watchdog-daily.timer "
                "/etc/systemd/system/; "
                "systemctl daemon-reload; "
                "systemctl enable --now inf-watchdog.timer inf-watchdog-daily.timer; "
                "echo __LIST__$(systemctl list-timers 'inf-watchdog*' --no-pager | head -5)__END__"
                % (STAGE, STAGE, STAGE, STAGE))
            rc, out = deploy.nested_exec(jump, creds, script, timeout=120)
            print(out)
            if "__LIST__" not in out or "inf-watchdog.timer" not in out:
                print("unit 安装/启用验证失败")
                return 1
            return 0

        if action == "uninstall":
            script = (
                "systemctl disable --now inf-watchdog.timer inf-watchdog-daily.timer 2>/dev/null; "
                "rm -f /etc/systemd/system/inf-watchdog.service "
                "/etc/systemd/system/inf-watchdog.timer "
                "/etc/systemd/system/inf-watchdog-daily.service "
                "/etc/systemd/system/inf-watchdog-daily.timer; "
                "systemctl daemon-reload; "
                "rm -rf %s %s; "
                "echo __ROLLED_BACK__; systemctl list-timers --no-pager | grep -c inf-watchdog || true"
                % (HOME_DIR, STAGE))
            rc, out = deploy.nested_exec(jump, creds, script, timeout=120)
            print(out)
            return 0 if "__ROLLED_BACK__" in out else 1
    finally:
        jump.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
