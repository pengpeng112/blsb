#!/usr/bin/env python3
"""inf_watchdog 自测（计划 §3）：fixture 驱动、无网络、不触碰真实 state。

本地与 <prod-host> 宿主都要全绿（禁真实网络：smtp/webhook 一律打桩；禁真实 state：全部检查跑在
临时目录 fixture 上，并用前后哈希比对证明推送器目录零写入）。
用法：python3 selftest.py   （退出码 0=全绿 1=有失败）
"""
import importlib.util
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time as _time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("wd", HERE / "watchdog.py")
wd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wd)

RESULTS = []
FIXED_NOW = datetime(2026, 11, 3, 12, 0, 0)          # 供可注入 now 的检查用
PATIENT_ID = "01024777_1"                             # fixture 回执 id（外发绝不许出现）


def check(name, cond, note=""):
    RESULTS.append((name, bool(cond), note))
    print("%s %-46s %s" % ("PASS" if cond else "FAIL", name, note if not cond else ""))


def cron_line(mon, day, hh, mm, ss, kind):
    return ("%3s %2d %02d:%02d:%02d localhost CROND[123456]: (root) %s (timeout -k 60 1200 "
            "flock -n <prod-path>/exchange/inf-pusher/state/push.lock /usr/local/bin/docker "
            "exec <worker-container> /data/exchange/inf-pusher/run.sh all real "
            ">> <prod-path>/exchange/inf-pusher/state/cron.log 2>&1)"
            % ({"11": "Nov"}[str(mon)], day, hh, mm, ss, kind))


def recv_line(ts, table="exam", result=True, stage="ok", rid=PATIENT_ID):
    rec = {"id": rid, "result": result, "stage": stage, "table": table, "ts": ts,
           "mode": "", "attempt": 1, "desc": "\u64cd\u4f5c\u6210\u529f"}
    if not result:
        rec["desc"] = "network error"
    return json.dumps(rec, ensure_ascii=False)


def snapshot_tree(root):
    """目录内容快照（相对路径→sha256），用于只读纪律断言。"""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = str(p.relative_to(root)).replace("\\", "/")
        if p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        else:
            out[rel] = "<dir>"
    return out


class Fixture:
    """临时目录伪造推送器 state + watchdog 配置。"""

    def __init__(self):
        self.root = Path(tempfile.mkdtemp(prefix="infwd-selftest-"))
        self.pusher_state = self.root / "pusher-state"
        self.state_dir = self.root / "wd-state"
        self.pusher_state.mkdir(parents=True)
        self.cron_log = self.root / "cron.log"
        self.cron_log.write_text("", encoding="utf-8")
        self.cfg = {
            "enabled": True,
            "host_label": "selftest-host",
            "pusher_state": str(self.pusher_state),
            "cron_log": str(self.cron_log),
            "docker_bin": "/usr/local/bin/docker",
            "state_dir": str(self.state_dir),
            "thresholds": dict(wd.DEFAULTS["thresholds"]),
            "smtp": {"enabled": False, "host": "", "port": 25, "sender": "", "to": [],
                     "starttls": False},
            "webhook": {"enabled": False, "type": "wecom", "url": ""},
        }

    def write(self, name, text, mtime=None):
        p = self.pusher_state / name
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            os.utime(str(p), (mtime, mtime))
        return p

    def receipts(self, name, lines):
        (self.pusher_state / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def patch(name, fn):
    orig = getattr(wd, name)
    setattr(wd, name, fn)
    return orig


# ---------------------------------------------------------------- C1 窗函数
def t_c1():
    fx = Fixture()
    try:
        # 正常：10 分钟前完成
        p = fx.write("last-run-real.json", "[]", mtime=(FIXED_NOW - timedelta(minutes=10)).timestamp())
        check("T01 c1 新鲜度正常", wd.check_c1(fx.cfg, FIXED_NOW) == [])
        # 40min 阈值边界
        fx.write("last-run-real.json", "[]", mtime=(FIXED_NOW - timedelta(minutes=39)).timestamp())
        check("T02 c1 39min 不告警", wd.check_c1(fx.cfg, FIXED_NOW) == [])
        fx.write("last-run-real.json", "[]", mtime=(FIXED_NOW - timedelta(minutes=41)).timestamp())
        r = wd.check_c1(fx.cfg, FIXED_NOW)
        check("T03 c1 41min→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
        # 维护窗边界（坑2）：05:45 班后直到 8:30 迟班无班；宽限到 08:34（见 watchdog.check_c1 注释）
        stale_mt = datetime(2026, 11, 3, 5, 46, 0).timestamp()
        for hhmm, expect_ok in [(datetime(2026, 11, 3, 5, 59), None),   # 窗前按阈值判
                                (datetime(2026, 11, 3, 6, 0), True),
                                (datetime(2026, 11, 3, 8, 29), True),
                                (datetime(2026, 11, 3, 8, 34), True),
                                (datetime(2026, 11, 3, 8, 35), False)]:
            fx.write("last-run-real.json", "[]", mtime=stale_mt)
            r = wd.check_c1(fx.cfg, hhmm)
            if expect_ok is True:
                check("T04 c1 维护窗 %s 不告警" % hhmm.strftime("%H:%M"), r == [], str(r))
            elif expect_ok is False:
                check("T05 c1 窗后 %s 告警" % hhmm.strftime("%H:%M"),
                      len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
        # 05:59 时 44min 陈旧（窗外）应告警
        fx.write("last-run-real.json", "[]",
                 mtime=(datetime(2026, 11, 3, 5, 15, 0)).timestamp())
        r = wd.check_c1(fx.cfg, datetime(2026, 11, 3, 5, 59, 0))
        check("T06 c1 05:59 窗外陈旧→CRITICAL",
              len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
        # 文件缺失
        os.remove(str(fx.pusher_state / "last-run-real.json"))
        r = wd.check_c1(fx.cfg, FIXED_NOW)
        check("T07 c1 文件缺失→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL")
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C2
def t_c2():
    fx = Fixture()
    try:
        normal = json.dumps([{"table": t, "sent": 1, "failed": 0} for t in
                             ("admission", "exam", "exam_item", "first_course", "daily_course", "death")])
        fx.write("last-run-real.json", normal)
        check("T08 c2 正常班→OK", wd.check_c2(fx.cfg, FIXED_NOW) == [])
        bad = json.dumps([{"table": "exam", "error": "x" * 350, "failed": 1},
                          {"table": "death", "sent": 0}])
        fx.write("last-run-real.json", bad)
        r = wd.check_c2(fx.cfg, FIXED_NOW)
        check("T09 c2 error 键→CRITICAL 且截断 200",
              len(r) == 1 and r[0]["level"] == "CRITICAL" and "exam" in r[0]["detail"]
              and len(r[0]["detail"]) <= 200 + 80, str(r)[:120])
        fx.write("last-run-real.json", "{not json")
        r = wd.check_c2(fx.cfg, FIXED_NOW)
        check("T10 c2 损坏 JSON→ERROR", len(r) == 1 and r[0]["level"] == "ERROR")
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C3
def t_c3():
    fx = Fixture()
    try:
        def log(pairs):
            fx.cron_log.write_text("\n".join(pairs) + "\n", encoding="utf-8")

        normal = [cron_line(11, 3, 10, 0, 1, "CMD"), cron_line(11, 3, 10, 0, 19, "CMDEND"),
                  cron_line(11, 3, 10, 15, 1, "CMD"), cron_line(11, 3, 10, 15, 25, "CMDEND")]
        log(normal)
        check("T11 c3 正常班（15-25s）→OK", wd.check_c3(fx.cfg, FIXED_NOW) == [])
        same = [cron_line(11, 3, 10, 0, 1, "CMD"), cron_line(11, 3, 10, 0, 1, "CMDEND")] * 3
        log(normal[:2] + same)
        r = wd.check_c3(fx.cfg, FIXED_NOW)
        check("T12 c3 同秒连击 3→WARN", len(r) == 1 and r[0]["level"] == "WARN", str(r))
        same8 = [cron_line(11, 3, 10, 0, 1, "CMD"), cron_line(11, 3, 10, 0, 1, "CMDEND")] * 8
        log(normal[:2] + same8)
        r = wd.check_c3(fx.cfg, FIXED_NOW)
        check("T13 c3 同秒连击 8→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
        # 连击中断后重新累计：2 连击+正常+1 连击 → 不告警（max=2 < 3）
        log(normal[:2] + [cron_line(11, 3, 10, 0, 1, "CMD"),
                          cron_line(11, 3, 10, 0, 1, "CMDEND")] * 2 + normal[2:] +
            [cron_line(11, 3, 11, 0, 1, "CMD"), cron_line(11, 3, 11, 0, 1, "CMDEND")])
        check("T14 c3 连击中断重新累计→OK", wd.check_c3(fx.cfg, FIXED_NOW) == [])
        # 历史事故已恢复：中部 63 次同秒连击 + 末尾正常班 → 当前连击 0，不得告警
        # （2026-11-03 宿主演练实测暴露：按历史最大值告警会在轮转前永久假 CRITICAL）
        incident = [cron_line(11, 2, 10, 0, 1, "CMD"),
                    cron_line(11, 2, 10, 0, 1, "CMDEND")] * 63
        log(normal[:2] + incident + normal[2:])
        check("T14b c3 历史连击已恢复→OK（只看当前连击）", wd.check_c3(fx.cfg, FIXED_NOW) == [])
        # 日志缺失（轮转后暂无内容）不炸
        fx.cron_log.unlink()
        check("T15 c3 日志缺失→OK", wd.check_c3(fx.cfg, FIXED_NOW) == [])
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C4
def t_c4():
    fx = Fixture()
    lock = str(fx.pusher_state / "push.lock")
    try:
        fx.write("push.lock", "")
        o1 = patch("probe_flock", lambda p: False)
        o2 = patch("lock_holders_etime", lambda p: [])
        try:
            check("T16 c4 锁空闲→OK", wd.check_c4(fx.cfg, FIXED_NOW) == [])
            wd.probe_flock = lambda p: True
            wd.lock_holders_etime = lambda p: [600.0]          # 10 分钟：正常班进行中
            check("T17 c4 持锁 10min→OK", wd.check_c4(fx.cfg, FIXED_NOW) == [])
            wd.lock_holders_etime = lambda p: [1560.0]         # 26 分钟 > 25 阈值
            r = wd.check_c4(fx.cfg, FIXED_NOW)
            check("T18 c4 持锁 26min→CRITICAL",
                  len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
            wd.lock_holders_etime = lambda p: []               # 持锁但找不到持有进程
            r = wd.check_c4(fx.cfg, FIXED_NOW)
            check("T19 c4 持锁无持有进程→WARN 需人工", len(r) == 1 and r[0]["level"] == "WARN")
            wd.probe_flock = lambda p: None                    # fcntl 不可用（自身能力缺失）
            r = wd.check_c4(fx.cfg, FIXED_NOW)
            check("T20 c4 探测原语缺失→ERROR", len(r) == 1 and r[0]["level"] == "ERROR")
        finally:
            patch("probe_flock", o1)
            patch("lock_holders_etime", o2)
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C5
def t_c5():
    fx = Fixture()
    try:
        now_utc = wd.utcnow_naive()
        today = now_utc.strftime("%Y%m%d")
        lines = [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT))]
        for i in range(11):
            lines.append(recv_line((now_utc - timedelta(hours=2)).strftime(wd.DATE_FMT),
                                   result=False, stage="network"))
        for i in range(5):
            lines.append(recv_line((now_utc - timedelta(hours=2)).strftime(wd.DATE_FMT),
                                   result=False, stage="client-validate"))
        fx.receipts("receipts-%s.jsonl" % today, lines)
        r = wd.check_c5(fx.cfg, FIXED_NOW)
        check("T21 c5 11 network 失败→WARN 且 client-validate 不计",
              len(r) == 1 and r[0]["level"] == "WARN" and "network=11" in r[0]["detail"]
              and "client-validate" not in r[0]["detail"], str(r))
        # 51 条 → CRITICAL
        big = [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT))]
        for i in range(51):
            big.append(recv_line((now_utc - timedelta(minutes=30)).strftime(wd.DATE_FMT),
                                 result=False, stage="network"))
        fx.receipts("receipts-%s.jsonl" % today, big)
        r = wd.check_c5(fx.cfg, FIXED_NOW)
        check("T22 c5 51 失败→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
        # 全成功
        fx.receipts("receipts-%s.jsonl" % today,
                    [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT))])
        check("T23 c5 全成功→OK", wd.check_c5(fx.cfg, FIXED_NOW) == [])
        # 坑1：文件名日期不可信——旧文件名（2 天前）里的新 ts 仍要计入
        old_name = (now_utc - timedelta(days=1)).strftime("%Y%m%d")
        fx.receipts("receipts-%s.jsonl" % old_name,
                    [recv_line((now_utc - timedelta(hours=3)).strftime(wd.DATE_FMT),
                               result=False, stage="network")] * 12)
        fx.receipts("receipts-%s.jsonl" % today, [])
        r = wd.check_c5(fx.cfg, FIXED_NOW)
        check("T24 c5 按行内 ts 计（不信文件名）",
              len(r) == 1 and r[0]["level"] == "WARN" and "network=12" in r[0]["detail"], str(r))
        # 24h 之外的老失败不计
        old2 = (now_utc - timedelta(days=1)).strftime("%Y%m%d")
        fx.receipts("receipts-%s.jsonl" % old2,
                    [recv_line((now_utc - timedelta(hours=30)).strftime(wd.DATE_FMT),
                               result=False, stage="network")] * 40)
        fx.receipts("receipts-%s.jsonl" % today, [])
        check("T25 c5 超 24h 失败不计", wd.check_c5(fx.cfg, FIXED_NOW) == [])
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C6
def t_c6():
    fx = Fixture()
    try:
        y = (FIXED_NOW - timedelta(days=1)).date()
        ymd = y.strftime("%Y%m%d")
        # 昨日计算 + 00:40 边界（坑6：宿主本地-1）
        t0039 = datetime.combine(FIXED_NOW.date(), datetime.strptime("00:39", "%H:%M").time())
        t0040 = t0039.replace(minute=40)
        check("T26 c6 00:39 缺失→OK", wd.check_c6(fx.cfg, t0039) == [])
        r = wd.check_c6(fx.cfg, t0040)
        check("T27 c6 00:40 缺失→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL"
              and ymd in r[0]["detail"], str(r))
        fx.write("reconcile-%s.json" % ymd,
                 json.dumps({"total_repushed": 100, "total_failed": 0}))
        check("T28 c6 正常对账→OK", wd.check_c6(fx.cfg, t0040) == [])
        fx.write("reconcile-%s.json" % ymd,
                 json.dumps({"total_repushed": 501, "total_failed": 0}))
        r = wd.check_c6(fx.cfg, t0040)
        check("T29 c6 repushed>500→WARN", len(r) == 1 and r[0]["level"] == "WARN", str(r))
        fx.write("reconcile-%s.json" % ymd,
                 json.dumps({"total_repushed": 10, "total_failed": 2}))
        r = wd.check_c6(fx.cfg, t0040)
        check("T30 c6 failed>0→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL", str(r))
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C7
def t_c7():
    fx = Fixture()
    try:
        today = FIXED_NOW.strftime("%Y%m%d")
        old = "20261010"
        fx.write("ALERT-%s" % today,
                 "2026-11-03 00:07:01 GAP-RECOVERED table=exam day=20261102 count=951\n"
                 "2026-11-03 00:07:01 RECONCILE-FAILED table=death day=20261102 count=1\n")
        r = wd.check_c7(fx.cfg, FIXED_NOW)
        check("T31 c7 当日 ALERT→WARN 且逐行转发",
              len(r) == 1 and r[0]["level"] == "WARN" and r[0]["key"] == "c7:ALERT-%s" % today
              and any("GAP-RECOVERED table=检查报告(exam)" in l for l in r[0]["lines"]),
              str(r)[:150])
        os.remove(str(fx.pusher_state / ("ALERT-%s" % today)))
        fx.write("ALERT-%s" % old, "old stuff\n")
        check("T32 c7 旧 ALERT 不转发→OK", wd.check_c7(fx.cfg, FIXED_NOW) == [])
        os.remove(str(fx.pusher_state / ("ALERT-%s" % old)))
        ymd = (FIXED_NOW - timedelta(days=1)).strftime("%Y%m%d")
        fx.write("ALERT-%s" % ymd, "2026-11-02 16:05:24 GAP-RECOVERED table=admission day=20261102 count=194\n")
        r = wd.check_c7(fx.cfg, FIXED_NOW)
        check("T33 c7 昨日 ALERT 也转发", len(r) == 1 and r[0]["key"] == "c7:ALERT-%s" % ymd)
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- C8–C10
def t_c8_c10():
    fx = Fixture()
    try:
        tables = ("admission", "exam", "exam_item", "first_course", "daily_course", "death")
        fx.write("watermark.json", json.dumps(
            {t: (FIXED_NOW - timedelta(hours=27)).strftime(wd.DATE_FMT) for t in tables}))
        r = wd.check_c8(fx.cfg, FIXED_NOW)
        check("T34 c8 六表最大 opTime 27h→WARN", len(r) == 1 and r[0]["level"] == "WARN", str(r))
        # death 稀疏（8 天）但 exam 新鲜 → 不告警（按六表最大值，防 death 天天误报）
        wm = {t: (FIXED_NOW - timedelta(hours=25)).strftime(wd.DATE_FMT) for t in tables}
        wm["death"] = (FIXED_NOW - timedelta(days=8)).strftime(wd.DATE_FMT)
        wm["exam"] = (FIXED_NOW - timedelta(hours=1)).strftime(wd.DATE_FMT)
        fx.write("watermark.json", json.dumps(wm))
        check("T35 c8 death 稀疏不误报（取最大）", wd.check_c8(fx.cfg, FIXED_NOW) == [])
        o = patch("fs_usage_pct", lambda p: 85.3)
        try:
            r = wd.check_c9(fx.cfg, FIXED_NOW)
            check("T36 c9 85%→WARN", len(r) == 1 and r[0]["level"] == "WARN", str(r))
            wd.fs_usage_pct = lambda p: 50.0
            check("T37 c9 50%→OK", wd.check_c9(fx.cfg, FIXED_NOW) == [])
            wd.fs_usage_pct = lambda p: None
            r = wd.check_c9(fx.cfg, FIXED_NOW)
            check("T38 c9 探测不可用→ERROR", len(r) == 1 and r[0]["level"] == "ERROR")
        finally:
            patch("fs_usage_pct", o)
        o = patch("docker_state", lambda b, name="<worker-container>", timeout=10: "running")
        try:
            check("T39 c10 running（docker 小写输出）→OK", wd.check_c10(fx.cfg, FIXED_NOW) == [])
            wd.docker_state = lambda b, name="<worker-container>", timeout=10: "exited"
            r = wd.check_c10(fx.cfg, FIXED_NOW)
            check("T40 c10 exited→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL")
            wd.docker_state = lambda b, name="<worker-container>", timeout=10: "Restarting"
            r = wd.check_c10(fx.cfg, FIXED_NOW)
            check("T40b c10 大写混合状态非 running→CRITICAL", len(r) == 1
                  and r[0]["level"] == "CRITICAL")
            wd.docker_state = lambda b, name="<worker-container>", timeout=10: ""
            r = wd.check_c10(fx.cfg, FIXED_NOW)
            check("T41 c10 inspect 失败→CRITICAL", len(r) == 1 and r[0]["level"] == "CRITICAL")
        finally:
            patch("docker_state", o)
    finally:
        fx.cleanup()


# ------------------------------------------------- 驱动/白名单/防轰/恢复
class Capture:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail              # True=模拟外部驱动异常（隧道闪断）

    def __call__(self, subject, lines, level, meta=None):
        if self.fail:
            raise OSError("tunnel down (selftest stub)")
        self.calls.append((subject, list(lines), level))
        return True


def t_drivers():
    fx = Fixture()
    try:
        os.makedirs(str(fx.state_dir), exist_ok=True)
        send = wd.driver_file(str(fx.state_dir))
        send("[WARN] selftest-host c5_push_failures", ["第 1 行", "第 2 行"], "WARN")
        day = datetime.now().strftime("%Y%m%d")
        ops = fx.state_dir / ("OPS-ALERT-%s" % day)
        js = fx.state_dir / ("alerts-%s.jsonl" % day)
        check("T42 file 驱动人读文件落盘",
              ops.exists() and "c5_push_failures" in ops.read_text(encoding="utf-8"))
        check("T43 file 驱动结构化文件落盘",
              js.exists() and json.loads(js.read_text(encoding="utf-8").splitlines()[0])
              ["subject"].startswith("[WARN]"))
        # webhook payload（纯函数，无网络）
        pl = wd._webhook_payload("[TEST] s", ["l1", "l2"])
        check("T44 webhook payload text 格式",
              pl["msgtype"] == "text" and pl["text"]["content"].startswith("[TEST] s\nl1"))
        # 防轰：同键二次不外发；file 不受限
        cap = Capture()
        filecap = Capture()
        dd = {"sent": {}, "levels": {}}
        f = wd._f("WARN", "内容X")
        drv = [("file", filecap, False), ("smtp", cap, True)]
        wd.dispatch(fx.cfg, drv, f, "c5_push_failures", dd)
        wd.dispatch(fx.cfg, drv, f, "c5_push_failures", dd)
        check("T45 防轰同键二次不外发", len(cap.calls) == 1, str(cap.calls))
        check("T46 file 记录不限次", len(filecap.calls) == 2)
        # C7 永久去重（ttl=None）
        dd2 = {"sent": {}, "levels": {}}
        f7 = wd._f("WARN", "x", key="c7:ALERT-20261103", ttl=None)
        wd.dispatch(fx.cfg, drv, f7, "c7_pusher_alerts", dd2)
        wd.dispatch(fx.cfg, drv, f7, "c7_pusher_alerts", dd2)
        check("T47 c7 文件名键永久去重", len(cap.calls) == 2)   # T45 消耗 1 次，此处再 +1
        check("T48 白名单截断 clip", len(wd.clip("x" * 350)) == 200 and
              wd.clip("a\x00b\x1fc") == "a b c")
    finally:
        fx.cleanup()


def healthy_fixture(fx):
    now = datetime.now()
    now_utc = wd.utcnow_naive()
    tables = ("admission", "exam", "exam_item", "first_course", "daily_course", "death")
    fx.write("last-run-real.json",
             json.dumps([{"table": t, "sent": 1, "failed": 0} for t in tables]),
             mtime=(now - timedelta(minutes=10)).timestamp())
    fx.write("watermark.json", json.dumps(
        {t: (now - timedelta(hours=1)).strftime(wd.DATE_FMT) for t in tables}))
    ymd = (now - timedelta(days=1)).strftime("%Y%m%d")
    fx.write("reconcile-%s.json" % ymd, json.dumps({"total_repushed": 100, "total_failed": 0}))
    fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"),
                [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT))])
    fx.cron_log.write_text("\n".join([
        cron_line(now.month, now.day, now.hour - 1 if now.hour else 12, 0, 1, "CMD"),
        cron_line(now.month, now.day, now.hour - 1 if now.hour else 12, 0, 19, "CMDEND")]) + "\n",
        encoding="utf-8")
    return now


def t_exit_codes_and_readonly():
    fx = Fixture()
    try:
        healthy_fixture(fx)
        before = snapshot_tree(fx.pusher_state)
        o = {"probe_flock": patch("probe_flock", lambda p: False),
             "lock_holders_etime": patch("lock_holders_etime", lambda p: []),
             "docker_state": patch("docker_state", lambda b, name="x", timeout=10: "Running"),
             "fs_usage_pct": patch("fs_usage_pct", lambda p: 50.0),
             "build_drivers": patch("build_drivers",
                                    lambda cfg: [("file", wd.driver_file(cfg["state_dir"]), False),
                                                 ("smtp", Capture(), True)])}
        try:
            rc = wd.cmd_check(fx.cfg)
            check("T49 全绿 fixture→exit 0（HEALTHY）", rc == 0,
                  "rc=%s" % rc + json.dumps({n: r["level"] for n, r in
                                             wd.run_checks(fx.cfg)[0].items()}))
            check("T50 status.json 产出", (fx.state_dir / "status.json").exists())
            # WARN 场景：11 条失败（fixture 改写不算 watchdog 写入，只读断言见 T53）
            now_utc = wd.utcnow_naive()
            fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"),
                        [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT))] +
                        [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT),
                                   result=False, stage="network") for _ in range(11)])
            rc = wd.cmd_check(fx.cfg)
            check("T51 WARN 场景→exit 1", rc == 1, "rc=%s" % rc)
            # 恢复：清掉失败 → RECOVERED 外发（捕获在 smtp 桩里）
            fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"),
                        [recv_line((now_utc - timedelta(minutes=30)).strftime(wd.DATE_FMT))])
            before2 = snapshot_tree(fx.pusher_state)   # 紧贴本次运行取证（排除 fixture 自身改写）
            rc = wd.cmd_check(fx.cfg)
            ops = (fx.state_dir / ("OPS-ALERT-%s" % datetime.now().strftime("%Y%m%d"))) \
                .read_text(encoding="utf-8")
            check("T52 恢复通知 RECOVERED 落盘", rc == 0 and "【已恢复】" in ops
                  and "推送失败突增" in ops and "恢复时间" in ops)
            # 只读纪律：单次完整 check 前后推送器目录零变化
            after2 = snapshot_tree(fx.pusher_state)
            check("T53 推送器 state/ 零写入（哈希一致）", before2 == after2,
                  str([k for k in set(before2) | set(after2)
                       if before2.get(k) != after2.get(k)]))
        finally:
            for k, orig in o.items():
                patch(k, orig)
        # 配置错误 → main 返回 2（自身错误也落盘 file 告警；落盘目录=config 同级 state/）
        bad = fx.root / "bad-config.json"
        bad.write_text(json.dumps({"pusher_state": str(fx.root / "no-such-dir")}),
                       encoding="utf-8")
        rc = wd.main(["watchdog.py", "--config", str(bad), "check"])
        st2 = fx.root / "state"
        filed = st2.exists() and any(p.name.startswith("OPS-ALERT-")
                                     for p in st2.iterdir())
        check("T54 配置错误→exit 2 且落盘告警", rc == 2 and filed, "rc=%s filed=%s" % (rc, filed))
    finally:
        fx.cleanup()


def t_whitelist_outbound():
    """D5：外发正文不含 id/patientName/idCard——含 receipts 造的患者样字段也不外泄。"""
    fx = Fixture()
    try:
        healthy_fixture(fx)
        now_utc = wd.utcnow_naive()
        lines = [json.dumps({"id": PATIENT_ID, "patientName": "张三", "idCard": "370104199001011234",
                             "result": False, "stage": "network", "table": "exam",
                             "ts": (now_utc - timedelta(minutes=30)).strftime(wd.DATE_FMT)},
                            ensure_ascii=False) for _ in range(12)]
        fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"), lines)
        cap = Capture()
        o = {"probe_flock": patch("probe_flock", lambda p: False),
             "lock_holders_etime": patch("lock_holders_etime", lambda p: []),
             "docker_state": patch("docker_state", lambda b, name="x", timeout=10: "Running"),
             "fs_usage_pct": patch("fs_usage_pct", lambda p: 50.0),
             "build_drivers": patch("build_drivers",
                                    lambda cfg: [("file", wd.driver_file(cfg["state_dir"]), False),
                                                 ("smtp", cap, True)])}
        try:
            wd.cmd_check(fx.cfg)
        finally:
            for k, orig in o.items():
                patch(k, orig)
        blob = json.dumps(cap.calls, ensure_ascii=False) + \
            "\n".join(p.read_text(encoding="utf-8") for p in fx.state_dir.iterdir()
                      if p.name.startswith(("OPS-ALERT-", "alerts-")))
        check("T55 外发不含回执 id", PATIENT_ID not in blob)
        check("T56 外发不含患者姓名/证件字段", "patientName" not in blob and "idCard" not in blob
              and "张三" not in blob and "370104" not in blob)
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- daily
def t_daily():
    fx = Fixture()
    try:
        now = FIXED_NOW                                   # 2026-11-03 12:00 CST
        y = datetime(2026, 11, 2)                         # 昨日 CST
        # cron：昨日 4 CMD / 3 CMDEND + 今日 1 条（不计）
        fx.cron_log.write_text("\n".join([
            cron_line(11, 2, 0, 5, 1, "CMD"), cron_line(11, 2, 0, 5, 40, "CMDEND"),
            cron_line(11, 2, 0, 15, 1, "CMD"), cron_line(11, 2, 0, 15, 20, "CMDEND"),
            cron_line(11, 2, 0, 30, 1, "CMD"), cron_line(11, 2, 0, 30, 25, "CMDEND"),
            cron_line(11, 2, 0, 45, 1, "CMD"),
            cron_line(11, 3, 11, 0, 1, "CMD"), cron_line(11, 3, 11, 0, 19, "CMDEND"),
        ]) + "\n", encoding="utf-8")
        # receipts：行 ts 为容器 UTC（坑1）——昨日 CST 窗=11-01 16:00Z~11-02 16:00Z
        in_win = ["2026-11-01 17:00:00", "2026-11-02 01:30:00", "2026-11-02 15:59:59"]
        out_win = ["2026-11-01 15:59:59", "2026-11-02 16:00:01", "2026-11-03 02:00:00"]
        lines = []
        for ts in in_win:
            lines.append(recv_line(ts, table="exam", result=True))
            lines.append(recv_line(ts, table="admission", result=False, stage="network"))
        lines.append(recv_line(in_win[0], result=False, stage="client-validate"))
        for ts in out_win:
            lines.append(recv_line(ts, result=False, stage="network"))
            lines.append(recv_line(ts, result=True))
        fx.receipts("receipts-20261101.jsonl", lines[:3])
        fx.receipts("receipts-20261102.jsonl", lines[3:])
        fx.write("reconcile-20261102.json",
                 json.dumps({"total_repushed": 2419, "total_failed": 0}))
        fx.write("watermark.json", json.dumps(
            {"exam": "2026-11-03 11:11:07", "death": "2026-10-26 23:39:38"}))
        os.makedirs(str(fx.state_dir), exist_ok=True)
        (fx.state_dir / "alerts-20261102.jsonl").write_text(
            json.dumps({"ts": "x", "level": "WARN", "subject": "[WARN] selftest-host c5_push_failures",
                        "body": []}, ensure_ascii=False) + "\n", encoding="utf-8")
        cap = Capture()
        o = patch("build_drivers",
                  lambda cfg: [("file", wd.driver_file(cfg["state_dir"]), False),
                               ("smtp", cap, True)])
        try:
            ymd, report = wd.daily_report(fx.cfg, now=now)
            txt = "\n".join(report)
            check("T57 daily 昨日日期", ymd == "20261102")
            check("T58 daily 班次计数", "CMD 4 班 / CMDEND 3 完成" in txt, txt[:200])
            check("T59 daily 各表聚合正确（四类计数）",
                  re.search(r"检查报告\(exam\)\s+sent=3\s+真实失败=0\s+等待父受理=0\s+必填尾差=1", txt) is not None
                  and re.search(r"住院诊疗\(admission\)\s+sent=0\s+真实失败=3", txt) is not None, txt)
            check("T60 daily client-validate 尾差去重单列", "必填尾差=1" in txt
                  and "13 表完整度" in txt)
            check("T61 daily 对账与水位", "total_repushed=2419" in txt and "11:11:07" in txt)
            check("T62 daily 摘要无回执 id", PATIENT_ID not in txt and "patientName" not in txt)
            # daily 落盘 + 外发一封
            fx.state_dir.mkdir(parents=True, exist_ok=True)
            rc = wd.cmd_daily(fx.cfg, now=now)
        finally:
            patch("build_drivers", o)
        day_file = fx.state_dir / "daily-20261102.txt"
        check("T63 daily 落盘且外发 1 封（晨报标题）",
              rc == 0 and day_file.exists() and len(cap.calls) == 1
              and cap.calls[0][0].startswith("【晨报】传染病上报·昨日运行总览（2026-11-02）"),
              str(cap.calls)[:120])
    finally:
        fx.cleanup()


def t_test_alert():
    fx = Fixture()
    try:
        cap = Capture()
        o = patch("build_drivers",
                  lambda cfg: [("file", wd.driver_file(cfg["state_dir"]), False),
                               ("smtp", cap, True)])
        try:
            rc = wd.cmd_test_alert(fx.cfg)
        finally:
            patch("build_drivers", o)
        day = datetime.now().strftime("%Y%m%d")
        ops = fx.state_dir / ("OPS-ALERT-%s" % day)
        check("T64 test-alert 全通道送达",
              rc == 0 and len(cap.calls) == 1 and "【测试】" in cap.calls[0][0]
              and ops.exists() and "【测试】" in ops.read_text(encoding="utf-8"))
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- PT-02 新增
def t_pt02_min_ymd_and_big_scan():
    """min_ymd 必须真正传入 _receipt_files（终结全盘扫描）+ 大文件快速分流（fail_only
    预筛 + 晨报子串分流不走全量 json.loads）。30 万行量级模拟 855 万行形态。"""
    fx = Fixture()
    try:
        now_utc = wd.utcnow_naive()
        old_ymd = "20260101"
        # 历史大文件（文件名日期 << min_ymd）：内含 5 万行历史失败行——不得进入扫描
        fx.receipts("receipts-%s.jsonl" % old_ymd,
                    [recv_line("2026-01-01 10:00:00", result=False, stage="network",
                               rid="OLD-%05d" % i) for i in range(50000)])
        recent = [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT),
                            result=False, stage="network", rid="NEW-1")]
        fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"), recent)
        files_all = wd._receipt_files(fx.pusher_state)
        files_bounded = wd._receipt_files(fx.pusher_state,
                                          min_ymd=(now_utc - timedelta(days=2)).strftime("%Y%m%d"))
        check("P01 _receipt_files min_ymd 过滤历史大文件",
              len(files_all) == 2 and len(files_bounded) == 1
              and old_ymd not in files_bounded[0], str(files_bounded))
        # c5 用例：min_ymd 传入后历史失败行不计入（否则 c5 会扫 5 万历史行）；
        # 注入 11 条近期失败触发 WARN（阈值 10）
        fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"),
                    [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT),
                               result=False, stage="network", rid="NEW-%d" % i)
                     for i in range(11)])
        t0 = _time.monotonic()
        r = wd.check_c5(fx.cfg, datetime.now())
        dt = _time.monotonic() - t0
        n_fail = -1
        for f in r:
            if "真实失败" in f["detail"]:
                import re as _re
                m = _re.search(r"真实失败 (\d+) 行", f["detail"])
                n_fail = int(m.group(1)) if m else -1
        check("P02 c5 只扫 min_ymd 后文件（历史失败不计数）", n_fail == 11, str(r)[:150])
        check("P03 c5 扫描耗时可接受（<5s，30 万行形态）", dt < 5.0, "%.2fs" % dt)
        # 晨报四类扫描：等待/尾差去重不计入真实失败
        y = (datetime.now() - timedelta(days=1)).date()
        win_start = datetime(y.year, y.month, y.day) - timedelta(hours=8)
        win_end = win_start + timedelta(days=1)
        min_ymd2 = (y - timedelta(days=2)).strftime("%Y%m%d")
        rows = []
        # 300 parent-wait 行同 (table,id)（每班重复写的形态）→ 去重后=1
        for i in range(300):
            rows.append(recv_line((win_start + timedelta(hours=1)).strftime(wd.DATE_FMT),
                                  table="ex_lab_item", result=False, stage="parent-wait"))
        for i in range(3):     # 3 个不同 id 的真实失败
            rows.append(recv_line((win_start + timedelta(hours=2)).strftime(wd.DATE_FMT),
                                  table="order", result=False, stage="network",
                                  rid="FAIL-%d" % i))
        for i in range(2):     # 2 行 mock（不计 sent）
            rows.append(recv_line((win_start + timedelta(hours=3)).strftime(wd.DATE_FMT),
                                  table="exam", result=True, stage="mock").replace(
                                  '"mode": ""', '"mode": "mock"'))
        rows.append(recv_line((win_start + timedelta(hours=4)).strftime(wd.DATE_FMT),
                              table="exam", result=True))
        fx.receipts("receipts-%s.jsonl" % win_end.strftime("%Y%m%d"), rows)
        sent, fail, wait, cv = wd._scan_daily_receipts(
            fx.pusher_state, win_start, win_end, min_ymd2)
        check("P04 晨报 sent 排除 mock", sent.get("exam") == 1 and sent.get("ex_lab_item", 0) == 0,
              str(sent))
        check("P05 晨报真实失败=3（等待/尾差不混入）", fail.get("order") == 3
              and "ex_lab_item" not in fail, str(fail))
        check("P06 晨报等待父受理去重=1（96 倍虚高根除）", len(wait) == 1, str(len(wait)))
        # 30 万行快速分流性能（fail_only 形态模拟：子串预筛 + 仅失败行解析）
        big = [recv_line("2026-01-01 10:00:00", result=True, rid="BULK-%06d" % i)
               for i in range(300000)]
        fx.receipts("receipts-20260102.jsonl", big)
        t0 = _time.monotonic()
        wd._scan_daily_receipts(fx.pusher_state,
                                datetime(2026, 1, 1) - timedelta(hours=8),
                                datetime(2026, 1, 1) + timedelta(hours=16), "20251230")
        dt2 = _time.monotonic() - t0
        check("P07 30 万行晨报分流 <5s（855 万行形态等比外推 ~2.5 分钟内）", dt2 < 5.0,
              "%.2fs" % dt2)
    finally:
        fx.cleanup()


def t_pt02_dispatch_and_recovery():
    """隧道闪断不吞告警（外部全失败不写防轰账→下轮补发）+ 子键独立恢复 + 升级穿透
    + 夜间免打扰（WARN 静默不写账、CRITICAL 照发、恢复期自动补发）。"""
    fx = Fixture()
    try:
        os.makedirs(str(fx.state_dir), exist_ok=True)
        f = wd._f("CRITICAL", "推送失败率：最近 24h 真实失败 60 行（阈值 50）；"
                  "按文书 检验报告(ex_lab)=60", key=None)
        # ① 隧道闪断：外部驱动全失败 → dedupe 不写账，下轮立即重试
        dd = {"sent": {}, "levels": {}}
        bad_cap = Capture(fail=True)
        filecap = Capture()
        drv_down = [("file", filecap, False), ("relay", bad_cap, True)]
        wd.dispatch(fx.cfg, drv_down, f, "c5_push_failures", dd)
        check("P08 隧道闪断 file 照常落盘", len(filecap.calls) == 1)
        check("P09 隧道闪断不写防轰账", "c5_push_failures:CRITICAL" not in dd["sent"],
              str(dd["sent"]))
        ok_cap = Capture()
        drv_up = [("file", Capture(), False), ("relay", ok_cap, True)]
        wd.dispatch(fx.cfg, drv_up, f, "c5_push_failures", dd)
        check("P10 下一轮自动补发（吞告警根除）", len(ok_cap.calls) == 1)
        check("P11 补发成功后写防轰账", "c5_push_failures:CRITICAL" in dd["sent"])
        # ② 结构化正文四段
        body = ok_cap.calls[0][1]
        check("P12 正文含主机/时间/异常现象/涉及文书/处置建议五段",
              body[0].startswith("主机：<prod-host>") and "时间：" in body[1]
              and body[2].startswith("【异常现象】")
              and any(l.startswith("【涉及文书】") for l in body)
              and any(l.startswith("【处置建议】") for l in body), str(body)[:220])
        # ③ 升级穿透：prev WARN → CRITICAL dry_all 立即外发（防轰窗内也发）
        dd2 = {"sent": {"c5_push_failures:CRITICAL": _time.time() - 60}, "levels": {
            "c5_push_failures|c5_push_failures": {"level": "WARN", "first_bad_ts": _time.time() - 600}}}
        cap2 = Capture()
        wd.dispatch(fx.cfg, [("file", Capture(), False), ("smtp", cap2, True)],
                    wd._f("CRITICAL", "真实失败 60 行"), "c5_push_failures", dd2)
        check("P13 防轰窗内常规路径被拦", len(cap2.calls) == 0, str(cap2.calls))
        # ④ relay 幂等键稳定：同键/级别/小时桶 → source_event_id 不随正文漂移
        rc = {"source_id": "inf-pusher-watchdog", "category": "inf-pusher"}
        p1 = wd._relay_payload(rc, "【严重】传染病上报·推送失败突增", ["l=1"], "CRITICAL", "2026111710",
                               dedupe_key="c5_push_failures:CRITICAL")
        p2 = wd._relay_payload(rc, "【严重】传染病上报·推送失败突增", ["l=999（计数变了）"],
                               "CRITICAL", "2026111710", dedupe_key="c5_push_failures:CRITICAL")
        check("P14 relay 幂等键稳定（计数文本不参与）",
              p1["source_event_id"] == p2["source_event_id"] and len(p1["source_event_id"]) == 24,
              "%s vs %s" % (p1["source_event_id"], p2["source_event_id"]))
        p3 = wd._relay_payload(rc, "s", ["l"], "CRITICAL", "2026111711",
                               dedupe_key="c5_push_failures:CRITICAL")
        check("P15 跨小时桶幂等键不同（可再投递）", p3["source_event_id"] != p1["source_event_id"])
        # ⑤ 夜间免打扰：WARN 静默不写账；CRITICAL 照发
        cfg_night = dict(fx.cfg)
        cfg_night["quiet_hours"] = {"enabled": True, "start": "00:00", "end": "23:59",
                                    "suppress_levels": ["WARN"]}
        dd3 = {"sent": {}, "levels": {}}
        night_cap = Capture()
        fw = wd._f("WARN", "积压 400 行（去重后）", key="c5_wait")
        wd.dispatch(cfg_night, [("file", Capture(), False), ("smtp", night_cap, True)],
                    fw, "c5_push_failures", dd3)
        check("P16 夜间 WARN 静默不推企微", len(night_cap.calls) == 0)
        check("P17 夜间静默不写防轰账（次日自动补发）", "c5_wait:WARN" not in dd3["sent"])
        fc = wd._f("CRITICAL", "积压 1200 行（去重后）", key="c5_wait")
        wd.dispatch(cfg_night, [("file", Capture(), False), ("smtp", Capture(), True)],
                    fc, "c5_push_failures", dd3)
        check("P18 夜间 CRITICAL 照常外发（绝不静默）",
              "c5_wait" in dd3["sent"])
        # ⑥ 静默解除后 WARN 自动补发（独立防轰账，避免跨级共享键干扰）
        dd4 = {"sent": {}, "levels": {}}
        day_cap = Capture()
        wd.dispatch(fx.cfg, [("file", Capture(), False), ("smtp", day_cap, True)],
                    fw, "c5_push_failures", dd4)
        check("P19 静默解除后 WARN 自动补发", len(day_cap.calls) == 1)
    finally:
        fx.cleanup()


def t_pt02_subkey_recovery_and_escalation():
    """子键独立恢复（真实失败恢复时等待积压仍 WARN 照样发恢复）+ 升级穿透 + 中文恢复文案含时长。"""
    fx = Fixture()
    try:
        healthy_fixture(fx)
        now_utc = wd.utcnow_naive()
        # 11 条真实失败 + 300 个不同 id 的 parent-wait（≥c5_wait_warn=300 触发独立键）
        rows = [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT))]
        rows += [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT),
                           result=False, stage="network", rid="F-%d" % i) for i in range(11)]
        rows += [recv_line((now_utc - timedelta(hours=1)).strftime(wd.DATE_FMT),
                           result=False, stage="parent-wait", rid="W-%04d" % i)
                 for i in range(300)]
        fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"), rows)
        cap = Capture()
        o = {"probe_flock": patch("probe_flock", lambda p: False),
             "lock_holders_etime": patch("lock_holders_etime", lambda p: []),
             "docker_state": patch("docker_state", lambda b, name="x", timeout=10: "Running"),
             "fs_usage_pct": patch("fs_usage_pct", lambda p: 50.0),
             "build_drivers": patch("build_drivers",
                                    lambda cfg: [("file", wd.driver_file(cfg["state_dir"]), False),
                                                 ("smtp", cap, True)])}
        try:
            rc1 = wd.cmd_check(fx.cfg)     # 两个子键都恶化
            check("P20 双子键恶化 exit 1", rc1 == 1, "rc=%s" % rc1)
            # 真实失败清零，等待积压仍在（300 个 id 不变）
            fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"),
                        [recv_line((now_utc - timedelta(minutes=30)).strftime(wd.DATE_FMT))]
                        + [recv_line((now_utc - timedelta(minutes=30)).strftime(wd.DATE_FMT),
                                     result=False, stage="parent-wait", rid="W-%04d" % i)
                           for i in range(300)])
            rc2 = wd.cmd_check(fx.cfg)
            ops = (fx.state_dir / ("OPS-ALERT-%s" % datetime.now().strftime("%Y%m%d"))) \
                .read_text(encoding="utf-8")
            check("P21 子键恢复：失败清零→【已恢复】推送失败突增（等待仍 WARN 不阻挡）",
                  "【已恢复】传染病上报·推送失败突增" in ops and "恢复时间" in ops, ops[-400:])
            check("P22 等待积压子键独立：未恢复仍跟踪",
                  "【已恢复】传染病上报·子项等待父受理积压" not in ops)
            dd = json.loads((fx.state_dir / "dedupe.json").read_text(encoding="utf-8"))
            check("P23 c5_wait 级别仍在跟踪（未恢复）",
                  any("c5_wait" in k and isinstance(v, dict) and v.get("level") == "WARN"
                      for k, v in dd["levels"].items()), str(dd["levels"]))
            check("P24 等待积压恢复仅当其子键消失", rc2 == 1, "rc=%s" % rc2)
            # 升级穿透：c5_wait WARN→CRITICAL（防轰窗内也立即发）
            rows2 = [recv_line((now_utc - timedelta(minutes=10)).strftime(wd.DATE_FMT))]
            rows2 += [recv_line((now_utc - timedelta(minutes=10)).strftime(wd.DATE_FMT),
                                result=False, stage="parent-wait", rid="W2-%04d" % i)
                      for i in range(1200)]      # ≥c5_wait_crit → CRITICAL
            fx.receipts("receipts-%s.jsonl" % now_utc.strftime("%Y%m%d"), rows2)
            wd.cmd_check(fx.cfg)
            ops2 = (fx.state_dir / ("OPS-ALERT-%s" % datetime.now().strftime("%Y%m%d"))) \
                .read_text(encoding="utf-8")
            check("P25 WARN→CRITICAL 升级穿透【升级·严重】", "【升级·严重】传染病上报·子项等待父受理积压" in ops2,
                  ops2[-300:])
        finally:
            for k, orig in o.items():
                patch(k, orig)
    finally:
        fx.cleanup()


class _ProbeHandler(BaseHTTPRequestHandler):
    """401 探针 / 退避重试 / 幂等键 字节验证用本地 Mock Relay（零真实网络）。"""
    protocol_version = "HTTP/1.1"
    mode = "401"           # 401 | ok | 502_then_ok | 429_then_ok | record
    seen = []
    bodies = []

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(ln) if ln else b""
        _ProbeHandler.seen.append(dict(self.headers))
        _ProbeHandler.bodies.append(raw)
        m = _ProbeHandler.mode
        n = len(_ProbeHandler.seen)
        if m == "401":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "34")
            self.end_headers()
            self.wfile.write(b'{"detail":"Missing X-Source-Id"}')
        elif m == "ok":
            self.send_response(202)
            self.send_header("Content-Length", "14")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"id":1}')
        elif m == "502_then_ok" and n == 1:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif m == "429_then_ok" and n == 1:
            self.send_response(429)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(202)
            self.send_header("Content-Length", "14")
            self.end_headers()
            self.wfile.write(b'{"ok":true,"id":1}')

    def log_message(self, *a):
        pass


def t_pt02_relay_probe_and_retry():
    """C11 401 真达探针 + relay 502/429 原字节重试（本地 Mock，零真实网络）。"""
    import threading
    from http.server import ThreadingHTTPServer
    fx = Fixture()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d/api/v2/events" % srv.server_address[1]
    rcfg = {"enabled": True, "url": url, "source_id": "inf-pusher-watchdog",
            "category": "inf-pusher"}
    try:
        _ProbeHandler.mode = "401"
        ok, err = wd.relay_probe_check(rcfg)
        check("P26 401=应用层真达判据", ok is True and err == "", str(err))
        _ProbeHandler.mode = "ok"
        ok2, err2 = wd.relay_probe_check(rcfg)
        check("P27 网关异常放行无签名请求→判不可达", ok2 is False and err2 != "", str(err2))
        rcfg_bad = {"enabled": True, "url": "http://127.0.0.1:1/api/v2/events",
                    "source_id": "x", "category": "c"}
        ok3, _ = wd.relay_probe_check(rcfg_bad)
        check("P28 连接拒绝→不可达", ok3 is False)
        # 429 退避重试：第一次 429(Retry-After:0) → 第二次 202 成功
        os.environ["INF_WATCHDOG_RELAY_SECRET"] = "selftest-secret"
        _ProbeHandler.mode = "429_then_ok"
        _ProbeHandler.seen.clear()
        drv = wd.driver_relay(rcfg, "selftest-host")
        rc = drv("【警告】传染病上报·测试", ["l1"], "WARN")
        check("P29 429 退避重试后送达", rc is True and len(_ProbeHandler.seen) == 2,
              str(len(_ProbeHandler.seen)))
        # 502 原字节重试：两次请求体一致（原签名原样重试语义）
        _ProbeHandler.mode = "502_then_ok"
        _ProbeHandler.seen.clear()
        _ProbeHandler.bodies.clear()
        drv("【警告】传染病上报·测试", ["l1"], "WARN")
        check("P30 502 原字节重试一致", len(_ProbeHandler.bodies) == 2
              and _ProbeHandler.bodies[0] == _ProbeHandler.bodies[1])
        # HMAC 签名格式：X-Signature 64 位小写 hex，签名输入=source_id.ts.raw
        import hashlib as _h
        import hmac as _hm
        hdr = _ProbeHandler.seen[-1]
        ts = hdr.get("X-Timestamp", "")
        sig = hdr.get("X-Signature", "")
        expect = _hm.new(b"selftest-secret",
                         ("inf-pusher-watchdog.%s." % ts).encode() + _ProbeHandler.bodies[-1],
                         _h.sha256).hexdigest()
        check("P31 HMAC-SHA256 签名可复算（协议一致）", sig == expect and len(sig) == 64, sig)
        check("P32 不携带 receivers 字段", b"receivers" not in _ProbeHandler.bodies[-1])
        os.environ.pop("INF_WATCHDOG_RELAY_SECRET", None)
    finally:
        srv.shutdown()
        fx.cleanup()


def main():
    print("inf_watchdog selftest —— fixture 驱动，无网络，无真实 state\n")
    for fn in (t_c1, t_c2, t_c3, t_c4, t_c5, t_c6, t_c7, t_c8_c10, t_drivers,
               t_exit_codes_and_readonly, t_whitelist_outbound, t_daily, t_test_alert,
               t_pt02_min_ymd_and_big_scan, t_pt02_dispatch_and_recovery,
               t_pt02_subkey_recovery_and_escalation, t_pt02_relay_probe_and_retry):
        fn()
    npass = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n==== %d/%d PASS ====" % (npass, len(RESULTS)))
    for name, ok, note in RESULTS:
        if not ok:
            print("FAIL %s — %s" % (name, note))
    return 0 if npass == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
