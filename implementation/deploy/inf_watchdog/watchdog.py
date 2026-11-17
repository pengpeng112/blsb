#!/usr/bin/env python3
"""传染病推送链路守护与告警 inf_watchdog —— 主程序（计划：plans/20261103-守护告警执行计划.md §2）。

职责边界（§0.3 D2 / §0.4 红线，不得放宽）：
- 纯只读检测（C1–C10）+ 告警外发，零自愈动作：不 kill 进程、不触发推送、不改 crontab；
- 推送器 state/ 只读（对账唯一依据）；自身状态只写本目录 state/；
- 严禁 import 推送器代码或复用其 state 写路径（故障域隔离）；
- 外发内容过 D5 白名单：计数/表名/时间戳/since/check 名/级别/错误摘要（截断 200 字符）；
  禁止任何患者级字段（id/患者姓名/证件/文书内容）——正文一律由白名单字段构造，不透传原始 dict；
- stdlib only，Python 3.9（宿主 /usr/bin/python3）。systemd unit 内一律绝对路径。

时间口径（§2.5 已知坑 1/6）：
- 宿主为 CST：C1/C6/C7/C8/daily/告警文件名均用宿主本地时间；
- 容器为 UTC：receipts 行内 ts 与文件名日期都是容器 UTC（naive）——C5/daily 按"行内 ts 与
  utcnow() 比较"过滤，不信文件名；CST↔UTC 换算只出现在 daily 的 receipts 汇总窗口。

子命令：
  check       跑 C1–C10 → 写 state/status.json → 告警外发（防轰+恢复通知）。
              退出码 0=HEALTHY / 1=degraded / 2=配置或自身错误（自身错误也落盘+file 告警）。
  daily       汇总昨日 CST（班次/各表回执/对账/告警/水位）→ state/daily-YYYYMMDD.txt + 外发晨报。
  test-alert  向每个已启用驱动发一条 [TEST]，验证通道；不跑检查。

用法：python3 watchdog.py [--config CONFIG] {check,daily,test-alert}
"""
import errno
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time as _time
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from pathlib import Path

DATE_FMT = "%Y-%m-%d %H:%M:%S"
SEV = {"OK": 0, "RECOVERED": 0, "WARN": 1, "CRITICAL": 2, "ERROR": 2}
# 防轰窗口（§2.3）：同一 check+级别 1 小时内最多外发 1 次；file 驱动不限次
DEDUPE_TTL_SEC = 3600
# 恢复通知宽限：级别记录比外发去重宽松，每次 check 都刷新
MONTHS = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
          "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
# 推送器班表（crontab）里与 watchdog 相关的两个特殊时刻：8:30 迟班、23:58 末班（§2.2 C1）
MAINT_START = time(6, 0)          # 预期无班窗起点（06:00）
MAINT_END_GRACE = time(8, 35)     # 8:30 迟班完成宽限后仍无新班才算不新鲜（见 check_c1 注释）

DEFAULTS = {
    "enabled": True,
    "host_label": "<host-label>",
    "pusher_state": "<prod-path>/exchange/inf-pusher/state",
    "cron_log": "/var/log/cron",
    "docker_bin": "/usr/local/bin/docker",
    "state_dir": "",              # 空 = 脚本目录/state（真实部署即 <prod-path>/exchange/inf-watchdog/state）
    "thresholds": {"c1_stale_min": 40, "c3_warn": 3, "c3_crit": 8, "c4_etime_min": 25,
                   "c5_warn": 10, "c5_crit": 50, "c5_wait_warn": 300, "c5_wait_crit": 1000,
                   "c6_repush_warn": 500, "c8_stale_h": 26,
                   "c9_disk_pct": 80},
    "smtp": {"enabled": False, "host": "", "port": 25, "sender": "", "to": [],
             "starttls": False},
    "webhook": {"enabled": False, "type": "wecom", "url": ""},
    # wechat-relay 统一消息平台（qywx docs/04+08）：HMAC-SHA256 签名 POST /api/v2/events，
    # 收件人由 relay 订阅路由管理（联调期仅 003966）；secret 从环境变量
    # INF_WATCHDOG_RELAY_SECRET 读（宿主 env 文件 600，不进 config 不进仓库——红线 6）。
    "relay": {"enabled": False, "url": "http://<relay-host>:3000/api/v2/events",
              "source_id": "inf-pusher-watchdog", "category": "inf-pusher"},
    # PT-02：可选夜间免打扰——窗口内 WARN 只落本地文件不推企微；次日首个非静默
    # 周期若仍 WARN 自动补发（实现=静默期不写 dedupe["sent"]，下轮自然重发）；
    # CRITICAL/ERROR 全天候 24h 立即推送绝不静默。
    "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:30",
                    "suppress_levels": ["WARN"]},
}


def now_str():
    return datetime.now().strftime(DATE_FMT)


def utcnow_naive():
    """容器 receipts 行内 ts 是 UTC naive——统一入口取当前 UTC naive
    （datetime.utcnow() 在新版本 Python 弃用，宿主 3.9 兼容此写法）。"""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


def clip(s, n=200):
    """D5 白名单：错误/详情摘要统一截断 200 字符并压掉控制字符。"""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(s)).strip()
    return s if len(s) <= n else s[:n - 3] + "..."


# 13 在推表的中文名（口径与数据上报 interfaces 档案一致）——告警/晨报里表名
# 显示为"中文(英文)"，收件人无需记英文表名。未知表原样显示（新表注册前也不丢信息）。
TABLE_CN = {
    "admission": "住院诊疗", "admission_info": "入院记录", "discharge_info": "出院记录",
    "death": "死亡记录", "exam": "检查报告", "exam_item": "检查项目",
    "ex_lab": "检验报告", "ex_lab_item": "检验明细", "first_course": "首次病程",
    "daily_course": "日常病程", "order": "医嘱", "order_item": "医嘱明细",
    "vital_signs": "生命体征",
}
_TABLE_RE = re.compile(r"\btable=(\S+)")     # ALERT 行 "table=ex_lab_item ..." 的表名段


def tbl_cn(name):
    """表名白名单渲染：中文名(英文名)；未知表原样。仅表名显示形式，不含患者级信息。"""
    cn = TABLE_CN.get(str(name))
    return "%s(%s)" % (cn, name) if cn else str(name)


def render_alert_line(line):
    """ALERT 原行中的 table=<英文> 段替换为 table=<中文(英文)>，其余原样（D5 白名单内）。"""
    return _TABLE_RE.sub(lambda m: "table=%s" % tbl_cn(m.group(1)), str(line))


# 检查项/级别中文名——企微标题显示"【级别】传染病上报·检查项"，收件人不需记 c5 等内部编号。
CHECK_CN = {
    "c1_freshness": "班次停滞", "c2_lastrun_error": "班次含异常表",
    "c3_skip_streak": "班次被跳过", "c4_lock_hang": "推送锁悬挂",
    "c5_push_failures": "推送失败突增", "c5_wait": "子项等待父受理积压",
    "c6_reconcile": "日终对账异常",
    "c7_pusher_alerts": "对账缺口告警", "c8_watermark": "水位停滞",
    "c9_disk": "磁盘空间", "c10_container": "推送容器异常",
    "c11_relay_tunnel": "隧道真达",
}
SEV_CN = {"CRITICAL": "严重", "ERROR": "错误", "WARN": "警告",
          "RECOVERED": "已恢复", "OK": "正常", "TEST": "测试",
          "DAILY": "晨报"}

# PT-02：C1–C11 每项 1 句人话处置建议（企微卡片第四段；只含运维动作，无患者字段）
ADVICE_CN = {
    "c1_freshness": "先看 <prod-host> 的 state/last-run-real.json 与 cron.log；若班次被跳过见「班次被跳过」，容器停了见「推送容器异常」；必要时手动补推一班。",
    "c2_lastrun_error": "读 state/last-run-real.json 中带 error 的表项定位失败表；01/99 会自动重试，02/03/04 按数据上报接口档案核对字段口径。",
    "c3_skip_streak": "多为此前班次占锁或超时引发；结合「推送锁悬挂」与容器状态判断，等 timeout 20 分钟放锁或人工处理持有链。",
    "c4_lock_hang": "SSH <prod-host> 查 push.lock 持有进程；timeout 层失效需人工 kill 持有链，释放后下一班自动恢复。",
    "c5_push_failures": "按回执 errorCode 分布处置：01/99 自动重试；02/03/04 需对照数据上报接口档案修正字段口径或源数据。",
    "c5_wait": "等待父受理的子项会在父受理后自动补发；对照数据问题清单核对缺必填的父单，交科室补录后等待消化。",
    "c6_reconcile": "查看 state/reconcile-<昨日>.json 失败表项；幂等补推仍失败多为数据本身问题，人工核对源数据后定向重放。",
    "c7_pusher_alerts": "GAP-RECOVERED 行=已自动补推无需处理；RECONCILE-FAILED/PARENT-WAIT 行需人工复核对应文书。",
    "c8_watermark": "疑似 ODS 取数停滞；检查 ODS 连通性与容器日志，必要时手动跑一班 --since 显式窗补齐。",
    "c9_disk": "归档后清理 state/ 旧 receipts 或扩容卷；注意保留近期对账依据（sent/receipts/水位）。",
    "c10_container": "docker start <worker-container> 拉起容器；频繁退出查 docker logs 与宿主内存。",
    "c11_relay_tunnel": "企微隧道闪断：systemctl status inf-relay-tunnel 并看 relay-tunnel.log；恢复后下一班自动补发被吞告警。",
    "daily": "",
}

# 涉及文书提取：正文里的「中文名(英文表名)」段（D5 白名单内的表名显示形式）
_DOC_RE = re.compile(r"[\u4e00-\u9fff]{2,10}\([a-z_0-9]+\)")


def alert_subject(level, check_name):
    """企微告警标题：【中文级别】传染病上报·中文检查项；未知级别/检查项回退原值。"""
    return "【%s】传染病上报·%s" % (SEV_CN.get(level, level),
                                   CHECK_CN.get(check_name, check_name))


# ---------------------------------------------------------------- config ----
def load_config(path):
    """读配置并与 DEFAULTS 合并；pusher_state 目录必须存在（防笔误造成假 CRITICAL）。"""
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in cfg.items()
                   if k not in ("thresholds", "smtp", "webhook", "relay", "quiet_hours")})
    for sec in ("thresholds", "smtp", "webhook", "relay", "quiet_hours"):
        merged[sec] = dict(DEFAULTS[sec])
        merged[sec].update(cfg.get(sec) or {})
    if not os.path.isdir(merged["pusher_state"]):
        raise ValueError("pusher_state 目录不存在: %r（检查 config）" % merged["pusher_state"])
    if not merged["state_dir"]:
        merged["state_dir"] = str(Path(path).resolve().parent / "state")
    return merged


def atomic_write(path, text):
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_line(path, line):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ------------------------------------------------- 可注入的探测原语 ----------
# （selftest 在 Windows/无特权环境打桩替换；宿主为真实现）
def probe_flock(lock_path):
    """探测 flock 锁是否被持有。只以 O_RDONLY 打开既有文件再加锁探测——绝不创建/改写
    state/ 下任何文件（红线 2）。未持锁时立即释放探测锁。fcntl 仅 Linux 存在。"""
    try:
        import fcntl
    except ImportError:
        return None  # 非 Linux（本地 selftest 场景）：由调用方打桩，不猜
    if not os.path.exists(lock_path):
        return False
    fd = os.open(lock_path, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return True
        raise
    finally:
        os.close(fd)


def _proc_uptime():
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def _proc_starttime(pid):
    with open("/proc/%d/stat" % pid) as f:
        s = f.read()
    data = s[s.rindex(")") + 2:].split()      # 跳过 comm（可含空格）
    return int(data[19]) / os.sysconf("SC_CLK_TCK")   # field 22（1-based）


def lock_holders_etime(lock_path):
    """返回持有 lock_path 的宿主进程 etime 秒列表（/proc/*/fd readlink + starttime）。
    持有链=flock→timeout→docker 等，任一进程打开该文件即计入。"""
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fd_dir = "/proc/%s/fd" % pid
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == lock_path:
                    out.append(max(0.0, _proc_uptime() - _proc_starttime(int(pid))))
                    break
            except OSError:
                continue
    return out


def docker_state(docker_bin, name="<worker-container>", timeout=10):
    """/usr/local/bin/docker inspect 取容器状态；返回 "Running"/其他状态串，失败返回 ""。"""
    try:
        p = subprocess.run([docker_bin, "inspect", "--format", "{{.State.Status}}", name],
                           capture_output=True, text=True, timeout=timeout)
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    return p.stdout.strip()


def fs_usage_pct(path):
    """state/ 所在卷使用率（os.statvfs；Windows 无此调用返回 None 由 selftest 打桩）。"""
    try:
        st = os.statvfs(path)
    except (AttributeError, OSError):
        return None
    if st.f_blocks <= 0:
        return None
    return (st.f_blocks - st.f_bfree) * 100.0 / st.f_blocks


# --------------------------------------------------------- cron 日志解析 ----
CRON_TS_RE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2})")


def parse_cron_ts(line):
    """'Nov  3 17:45:01 localhost CROND[..]: (root) CMD (...' → (month, day, h, m, s)。
    不带年份（同秒/同日比较用不到；坑5：日志会轮转，只看当前文件）。"""
    m = CRON_TS_RE.match(line)
    if not m or m.group(1) not in MONTHS:
        return None
    mon, day, hh, mm, ss = m.groups()
    return (MONTHS[mon], int(day), int(hh), int(mm), int(ss))


def read_pusher_cron_lines(cron_log):
    """取 /var/log/cron 中 inf-pusher 相关行（含 timeout 包装新格式）。文件缺失返回 []。"""
    try:
        with open(cron_log, "r", encoding="utf-8", errors="replace") as f:
            return [l.rstrip("\n") for l in f if "inf-pusher" in l]
    except OSError:
        return []


# ------------------------------------------------------------- 检查项 C1–C10
def _f(level, detail, key=None, ttl=DEDUPE_TTL_SEC, lines=None, subject=None):
    return {"level": level, "detail": clip(detail), "key": key, "ttl": ttl,
            "lines": lines or [], "subject": subject}


def check_c1(cfg, now):
    """C1 班次新鲜度：last-run-real.json mtime 年龄>40min→CRITICAL。
    预期无班窗 06:00 起（坑2：*/15 班表为 0-5,9-23 点，05:45 后直到 8:30 迟班无班）。
    计划文字写"06:00–08:29"；实现到 08:34:59——8:30 迟班正常完成需 15–25 秒，而本
    watchdog timer 恰在 08:30:00 整点触发，按字面截到 08:29 会每天 08:30 产生一次假
    CRITICAL（与 D6 防轰初衷矛盾），故补 5 分钟完成宽限，8:30 班若真缺席最迟 08:35 告警。"""
    th = cfg["thresholds"]
    p = os.path.join(cfg["pusher_state"], "last-run-real.json")
    if not os.path.exists(p):
        return [_f("CRITICAL", "last-run-real.json 不存在（推送器从未完成过班次？）")]
    mtime = datetime.fromtimestamp(os.path.getmtime(p))
    age_min = (now - mtime).total_seconds() / 60.0
    if age_min <= th["c1_stale_min"]:
        return []
    t = now.time()
    if MAINT_START <= t < MAINT_END_GRACE:
        return []   # 维护窗/8:30 迟班宽限内，预期无新班
    return [_f("CRITICAL", "班次不新鲜：last-run-real.json 最后完成于 %s，距今 %d 分钟（阈值 %d 分钟）"
               % (mtime.strftime(DATE_FMT), int(age_min), th["c1_stale_min"]))]


def check_c2(cfg, now):
    """C2 班统计异常：last-run-real.json（数组）任一表对象含 "error" 键→CRITICAL。
    这是 2026-11-03 单表异常隔离加固的产物——单表取数失败被记录而非中断整班。"""
    p = os.path.join(cfg["pusher_state"], "last-run-real.json")
    if not os.path.exists(p):
        return []   # 文件缺失由 C1 判，这里不重复告警
    try:
        arr = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as e:
        return [_f("ERROR", "last-run-real.json 解析失败: %s" % e)]
    if not isinstance(arr, list):
        return [_f("ERROR", "last-run-real.json 不是数组（格式变化？）")]
    errs = [t for t in arr if isinstance(t, dict) and "error" in t]
    if errs:
        parts = ["%s: %s" % (tbl_cn(t.get("table", "?")), t.get("error", "")) for t in errs]
        return [_f("CRITICAL", "上次班次 %d/%d 个表异常——%s" % (len(errs), len(arr), "; ".join(parts)))]
    return []


def check_c3(cfg, now):
    """C3 跳班连击：/var/log/cron 中 inf-pusher CMD 与 CMDEND 同秒= flock 秒退（跳班）。
    连续同秒对 ≥3→WARN，≥8→CRITICAL。坑5：日志轮转后从当前文件重新累计，可接受。
    PID 配对不可靠（实测 CMD/CMDEND 的 CROND pid 不同），按时间顺序配对最近的 CMD。"""
    th = cfg["thresholds"]
    lines = read_pusher_cron_lines(cfg["cron_log"])
    streak = max_streak = 0
    pending = None
    for l in lines:
        ts = parse_cron_ts(l)
        if ts is None:
            continue
        sec = ts[2:]
        if " CMD (" in l:                    # " CMDEND (" 不含 " CMD ("，互不误匹配
            pending = sec
        elif " CMDEND (" in l and pending is not None:
            if sec == pending:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0                   # 正常班完成，连击中断
            pending = None
    # 只按"当前连续"跳班告警（进行中的连击）；历史最大连击仅作背景信息——否则一次事故
    # 会让日志轮转前每次检查都假 CRITICAL（2026-11-03 宿主演练实测教训）
    if streak >= th["c3_crit"]:
        return [_f("CRITICAL", "跳班连击：当前连续同秒跳班 %d 次（阈值 %d）；本文件历史最大连击 %d（参考）"
                   % (streak, th["c3_crit"], max_streak))]
    if streak >= th["c3_warn"]:
        return [_f("WARN", "跳班连击：当前连续同秒跳班 %d 次（阈值 %d）；本文件历史最大连击 %d（参考）"
                   % (streak, th["c3_warn"], max_streak))]
    return []


def check_c4(cfg, now):
    """C4 锁悬挂：push.lock 被持有且持有链 etime>25min→CRITICAL。
    timeout 层 20min 上限 + 缓冲；超 25min = timeout 失效或旁路，需人工（02 手册"跳班识别"）。"""
    th = cfg["thresholds"]
    lock = os.path.join(cfg["pusher_state"], "push.lock")
    held = probe_flock(lock)
    if held is None:
        return [_f("ERROR", "锁探测原语不可用（fcntl 缺失？）")]
    if not held:
        return []
    etimes = lock_holders_etime(lock)
    if not etimes:
        return [_f("WARN", "锁被持有但未找到持有进程（fd 已被继承或权限不足），etime 未知，需人工确认")]
    emax = max(etimes) / 60.0
    if emax > th["c4_etime_min"]:
        return [_f("CRITICAL", "锁悬挂：push.lock 被持有 %d 分钟（阈值 %d 分钟，持有进程 %d 个）——"
                   "timeout 层应 20 分钟放锁，超时说明 kill 失效，需人工处理"
                   % (int(emax), th["c4_etime_min"], len(etimes)))]
    return []   # 正常班进行中（*/15 班 15–25 秒），不告警


def _receipt_files(pusher_state, min_ymd=None):
    """坑1：receipts 文件名=容器 UTC 日期（CST 0–8 点写入前一 CST 日文件）。
    为省 IO 只扫文件名日期 ≥ min_ymd 的文件；名字解析不了的保守纳入。"""
    out = []
    try:
        names = os.listdir(pusher_state)
    except OSError:
        return out
    for n in sorted(names):
        m = re.fullmatch(r"receipts-(\d{8})\.jsonl", n)
        if m and min_ymd and m.group(1) < min_ymd:
            continue
        if m or n.startswith("receipts-"):
            out.append(os.path.join(pusher_state, n))
    return out


def iter_receipts(pusher_state, min_utc=None, max_utc=None, fail_only=False,
                  min_ymd=None):
    """逐行读 receipts（行内 ts 为容器 UTC naive，坑1），yield 落在 [min_utc,max_utc) 的行。
    fail_only=True：先按 '"result": false' 子串预筛再 json.loads——11-14 放量日回执单日
    855 万行，全量解析曾把 check 拖过 TimeoutStartSec 致守护停摆两天（2026-11-16 定位），
    失败行占比极小，预筛后扫描降为秒级。
    PT-02：min_ymd 透传 _receipt_files 按文件名日期过滤历史大文件——调用方算出的
    下界日期必须真正生效，终结"每次 check 从第 1 天扫起"的全盘扫描。"""
    for path in _receipt_files(pusher_state, min_ymd=min_ymd):
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if fail_only and '"result": false' not in line and '"result":false' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                ts = rec.get("ts", "")
                try:
                    t = datetime.strptime(ts, DATE_FMT)
                except ValueError:
                    continue
                if min_utc and t < min_utc:
                    continue
                if max_utc and t >= max_utc:
                    continue
                yield rec


def check_c5(cfg, now):
    """C5 推送失败率：最近 24h（行内 UTC ts，坑1）result:false 拆两类——
    ①真失败（stage≠client-validate/parent-wait，>10→WARN >50→CRITICAL）；
    ②parent-wait 等待（子项等父受理，回执每班重复写故按 (table,id) 去重计数，
    ≥c5_wait_warn→WARN ≥c5_wait_crit→CRITICAL，独立防轰键，父受理后自动消化）。
    两类正文均按文书类型（中文表名）列出。"""
    th = cfg["thresholds"]
    now_utc = utcnow_naive()
    cutoff = now_utc - timedelta(hours=24)
    min_ymd = (now_utc - timedelta(days=2)).strftime("%Y%m%d")
    n_fail = 0
    n_wait_keys = set()
    by_stage = {}
    by_table_fail = {}
    by_table_wait = {}
    for rec in iter_receipts(cfg["pusher_state"], min_utc=cutoff, max_utc=now_utc,
                             fail_only=True, min_ymd=min_ymd):
        if rec.get("result") is not False:
            continue
        stage = str(rec.get("stage", "?"))
        if stage == "client-validate":     # 已知尾差不计
            continue
        t = str(rec.get("table", "?"))
        if stage == "parent-wait":
            n_wait_keys.add((t, str(rec.get("id", ""))))
            continue
        n_fail += 1
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_table_fail[t] = by_table_fail.get(t, 0) + 1
    # 等待按文书=去重计数（回执每班重复写，不去重会虚高 96 倍）
    by_table_wait = {}
    for t, _ in n_wait_keys:
        by_table_wait[t] = by_table_wait.get(t, 0) + 1
    out = []

    def table_txt(by):
        return "、".join("%s=%d" % (tbl_cn(k), v)
                         for k, v in sorted(by.items(), key=lambda kv: -kv[1])) or "无"

    stage_txt = ", ".join("%s=%d" % kv for kv in sorted(by_stage.items())) or "无"
    if n_fail > th["c5_crit"]:
        out.append(_f("CRITICAL", "推送失败率：最近 24h 真实失败 %d 行（阈值 %d）；"
                      "按文书 %s；按阶段 %s" % (n_fail, th["c5_crit"],
                                              table_txt(by_table_fail), stage_txt)))
    elif n_fail > th["c5_warn"]:
        out.append(_f("WARN", "推送失败率：最近 24h 真实失败 %d 行（阈值 %d）；"
                      "按文书 %s；按阶段 %s" % (n_fail, th["c5_warn"],
                                              table_txt(by_table_fail), stage_txt)))
    n_wait = len(n_wait_keys)
    if n_wait >= th.get("c5_wait_crit", 1000):
        out.append(_f("CRITICAL",
                      "子项等待父受理积压：最近 24h 待父受理子项 %d 行（去重后，阈值 %d）"
                      "——父未受理的子项在等待，父受理后下班自动补发；"
                      "按文书 %s" % (n_wait, th.get("c5_wait_crit", 1000),
                                     table_txt(by_table_wait)),
                      key="c5_wait"))
    elif n_wait >= th.get("c5_wait_warn", 300):
        out.append(_f("WARN",
                      "子项等待父受理积压：最近 24h 待父受理子项 %d 行（去重后，阈值 %d）"
                      "——父未受理的子项在等待，父受理后下班自动补发；"
                      "按文书 %s" % (n_wait, th.get("c5_wait_warn", 300),
                                     table_txt(by_table_wait)),
                      key="c5_wait"))
    return out


def check_c6(cfg, now):
    """C6 日终对账：state/reconcile-<昨日CST>.json。坑6：昨日按宿主本地日期-1（宿主=CST；
    reconcile day_str 由 pusher 以 CST 计算，两侧一致）。CST≥00:40 文件缺失→CRITICAL
    （00:05 班 + 运行时长留 35 分钟余量）；total_failed>0→CRITICAL；total_repushed>500→WARN。"""
    y = (now - timedelta(days=1)).date()
    p = os.path.join(cfg["pusher_state"], "reconcile-%s.json" % y.strftime("%Y%m%d"))
    if not os.path.exists(p):
        if now.time() >= time(0, 40):
            return [_f("CRITICAL", "日终对账缺失：%s 不存在（00:05 班应产出，已过 00:40）"
                       % os.path.basename(p))]
        return []   # 未到对账时刻
    try:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        failed = int(data.get("total_failed", 0))
        repushed = int(data.get("total_repushed", 0))
    except Exception as e:
        return [_f("ERROR", "对账文件解析失败 %s: %s" % (os.path.basename(p), e))]
    if failed > 0:
        bad = ["%s failed=%d" % (tbl_cn(k), int(v.get("failed", 0)))
               for k, v in sorted((data.get("tables") or {}).items())
               if int(v.get("failed", 0)) > 0]
        detail = ("日终对账失败：昨日(%s) total_failed=%d（幂等补推仍失败，需人工）"
                  % (y.strftime("%F"), failed))
        lines = [detail]
        if bad:
            lines.append("失败文书：" + "、".join(bad))
        return [_f("CRITICAL", detail, lines=lines)]
    if repushed > cfg["thresholds"]["c6_repush_warn"]:
        rep = sorted(((tbl_cn(k), int(v.get("repushed", 0)))
                      for k, v in (data.get("tables") or {}).items()),
                     key=lambda x: -x[1])
        detail = ("日终对账补推量偏大：昨日(%s) total_repushed=%d（阈值 %d，正常班应已推齐）"
                  % (y.strftime("%F"), repushed, cfg["thresholds"]["c6_repush_warn"]))
        lines = [detail, "补推分布：" + "、".join("%s=%d" % kv for kv in rep[:6])]
        return [_f("WARN", detail, lines=lines)]
    return []


def check_c7(cfg, now):
    """C7 推送器自警转发：state/ALERT-*（当日/昨日 CST）存在→WARN，内容逐行转发
    （ALERT 行由 pusher 生成，只含 时间戳/GAP-RECOVERED|RECONCILE-FAILED/表名/day/计数，
    白名单安全；逐行 clip 200）。去重键=文件名（§2.2 C7，转发一次后不再重复）。
    这是 reconcile GAP/FAILED 告警触达人的通道。"""
    findings = []
    for d in (now.date(), now.date() - timedelta(days=1)):
        name = "ALERT-%s" % d.strftime("%Y%m%d")
        p = os.path.join(cfg["pusher_state"], name)
        if not os.path.exists(p):
            continue
        try:
            lines = [l.rstrip("\n") for l in
                     open(p, "r", encoding="utf-8", errors="replace").read().splitlines() if l.strip()]
        except OSError as e:
            findings.append(_f("ERROR", "读取 %s 失败: %s" % (name, e)))
            continue
        body = ["文件 %s 内容（%d 行）：" % (name, len(lines))]
        body += [clip(render_alert_line(l)) for l in lines[:20]]
        if len(lines) > 20:
            body.append("（其余 %d 行见该文件/晨报）" % (len(lines) - 20))
        findings.append(_f("WARN", "%s 存在：%s" % (name, clip(render_alert_line("; ".join(lines[:3])) or "空文件")),
                           key="c7:%s" % name, ttl=None, lines=body))
    return findings


def check_c8(cfg, now):
    """C8 水位陈旧：watermark.json 六表**最大** opTime 距今>26h→WARN（取最大而非逐表：
    death 表本身稀疏——实测可 8 天不推进，逐表判会天天误报）。"""
    th = cfg["thresholds"]
    p = os.path.join(cfg["pusher_state"], "watermark.json")
    if not os.path.exists(p):
        return [_f("ERROR", "watermark.json 不存在")]
    try:
        wm = json.loads(Path(p).read_text(encoding="utf-8"))
        parsed = {k: datetime.strptime(v, DATE_FMT) for k, v in wm.items()}
    except Exception as e:
        return [_f("ERROR", "watermark.json 解析失败: %s" % e)]
    if not parsed:
        return [_f("ERROR", "watermark.json 为空")]
    tbl, tmax = max(parsed.items(), key=lambda kv: kv[1])
    age_h = (now - tmax).total_seconds() / 3600.0
    if age_h > th["c8_stale_h"]:
        lines = ["水位陈旧：最大 opTime %s（%s）距今 %.1f 小时（阈值 %d）——"
                 "各表水位均未推进，疑似 ODS 取数停滞"
                 % (tmax.strftime(DATE_FMT), tbl_cn(tbl), age_h, th["c8_stale_h"])]
        # 逐表列出超龄文书（death 稀疏表放宽到 9 天，其余 > 阈值即列；触发条件不变）
        # PT-02 修复：原 if 分支误引用 lambda 内变量 kv（NameError，水位陈旧分支一触发即崩）
        stale = ["%s 停在 %s（%.1f 小时前）" % (tbl_cn(k), v.strftime(DATE_FMT),
                 (now - v).total_seconds() / 3600.0)
                 for k, v in sorted(parsed.items(), key=lambda kv: kv[1])
                 if (now - v).total_seconds() / 3600.0 > (9 * 24 if k == "death"
                                                          else th["c8_stale_h"])]
        if stale:
            lines.append("落后文书：" + "；".join(stale))
        return [_f("WARN", lines[0], lines=lines)]
    return []


def check_c9(cfg, now):
    """C9 磁盘：state/ 所在卷使用率>80%→WARN（receipts 全量日 ~20MB、sent.jsonl 15MB 级缓慢增长）。"""
    th = cfg["thresholds"]
    pct = fs_usage_pct(cfg["pusher_state"])
    if pct is None:
        return [_f("ERROR", "磁盘使用率探测不可用（statvfs 失败）")]
    if pct > th["c9_disk_pct"]:
        return [_f("WARN", "磁盘：state/ 所在卷使用率 %.1f%%（阈值 %d%%）" % (pct, th["c9_disk_pct"]))]
    return []


def check_c10(cfg, now):
    """C10 容器在跑：<worker-container> 非 running→CRITICAL（docker 绝对路径、超时 10 秒）。
    docker inspect 的 .State.Status 输出为小写（running/exited/...），按小写比较。"""
    st = docker_state(cfg["docker_bin"])
    if st.lower() != "running":
        return [_f("CRITICAL", "容器 <worker-container> 状态异常：%r（docker inspect 退出非 0 或状态非 running）"
                   % (st or "inspect 失败"))]
    return []


def relay_probe_check(relay_cfg):
    """PT-02 C11：无签名空 {} 探测（超时 3 秒）——预期 HTTP 401（Missing X-Source-Id）。

    这是应用层真达判据：安全设备可能代答 TCP/HTTP，但网关对无签名请求返回 401
    才证明流量真到 wechat-relay 应用。2xx（网关异常放行无签名请求）与非 401 错误、
    超时、连接拒绝均判 False。零消息副作用（不投递任何事件）。"""
    import urllib.error
    import urllib.request
    try:
        req = urllib.request.Request(
            relay_cfg["url"], data=b"{}", method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                code = resp.getcode()
                body = resp.read(2048).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            code = e.code
            body = clip(e.read(2048).decode("utf-8", "replace"))
        if code == 401:
            return True, ""
        return False, "HTTP %s %s" % (code, clip(body, 120))
    except Exception as e:                     # 超时/连接拒绝/代理异常
        return False, clip(e, 120)


def check_c11(cfg, now):
    """C11 隧道真达自检（PT-02，仅 relay.enabled=True 时执行）：
    relay_probe_check False → WARN（结果同时写入 status.json relay_reachable 字段，
    供监控页直观亮灯）；True → 无 finding。key=c11_relay 独立防轰。"""
    rc = cfg.get("relay") or {}
    if not rc.get("enabled"):
        return []
    ok, err = relay_probe_check(rc)
    if ok:
        return []
    return [_f("WARN", "企微隧道不可达：%s（应用层探测未返回 401 真达判据——隧道/网关离线或代答异常；"
               "恢复后下一班自动补发被吞告警）" % err, key="c11_relay")]


CHECKS = [
    ("c1_freshness", check_c1), ("c2_lastrun_error", check_c2), ("c3_skip_streak", check_c3),
    ("c4_lock_hang", check_c4), ("c5_push_failures", check_c5), ("c6_reconcile", check_c6),
    ("c7_pusher_alerts", check_c7), ("c8_watermark", check_c8), ("c9_disk", check_c9),
    ("c10_container", check_c10), ("c11_relay_tunnel", check_c11),
]


def run_checks(cfg, now=None):
    """跑 C1–C10。返回 (results, overall_level, exit_code)。
    overall：任一 CRITICAL/ERROR→CRITICAL，否则任一 WARN→WARN，否则 OK；
    exit：任一 ERROR→2（自身错误），否则 degraded→1，否则 0。"""
    now = now or datetime.now()
    results = {}
    for name, fn in CHECKS:
        t0 = _time.monotonic()
        try:
            findings = fn(cfg, now) or []
        except Exception as e:   # 单项异常不影响其余检查；按自身错误处理
            findings = [_f("ERROR", "检查自身异常: %s" % e)]
        # 单项聚级 = findings 中最高严重度（ERROR 与 CRITICAL 同severity，展示原级别）
        lvl = "OK"
        for fd in findings:
            if SEV[fd["level"]] > SEV.get(lvl, 0):
                lvl = fd["level"]
        results[name] = {
            "level": lvl,
            "detail": " | ".join(fd["detail"] for fd in findings),
            "elapsed_ms": int((_time.monotonic() - t0) * 1000),
            "findings": findings,
        }
    overall = "OK"
    for r in results.values():
        if SEV[r["level"]] == 2:
            overall = "CRITICAL"
            break
        if SEV[r["level"]] == 1:
            overall = "WARN"
    has_error = any(r["level"] == "ERROR" for r in results.values())
    exit_code = 2 if has_error else (0 if overall == "OK" else 1)
    return results, overall, exit_code


# ------------------------------------------------------------- 告警驱动 ----
# PT-02：驱动统一签名 send(subject, lines, level, meta=None)——meta 携带防轰键与
# 首次恶化时间（relay 幂等键/持续时长用）；file/smtp/webhook 忽略 meta。
def driver_file(state_dir):
    """file 驱动（永远开启）：alerts-YYYYMMDD.jsonl（结构化）+ OPS-ALERT-YYYYMMDD（人读）。
    文件名日期=宿主本地 CST 日期。"""
    def send(subject, lines, level, meta=None):
        day = datetime.now().strftime("%Y%m%d")
        ts = now_str()
        append_line(os.path.join(state_dir, "alerts-%s.jsonl" % day),
                    json.dumps({"ts": ts, "level": level, "subject": subject, "body": lines},
                               ensure_ascii=False))
        with open(os.path.join(state_dir, "OPS-ALERT-%s" % day), "a", encoding="utf-8") as f:
            f.write("\n== %s %s ==\n" % (ts, subject))   # subject 已含 [级别] 前缀
            for l in lines:
                f.write(l + "\n")
        return True
    return send


def driver_smtp(smtp_cfg):
    """smtp 驱动：smtplib，超时 10 秒，STARTTLS 可选；口令（如有）从环境变量
    INF_WATCHDOG_SMTP_PASSWORD 读（宿主 env 文件 600，不进 config 不进仓库——红线 6）。"""
    import smtplib

    def send(subject, lines, level, meta=None):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = smtp_cfg["sender"]
        msg["To"] = ", ".join(smtp_cfg["to"])
        msg.set_content("\n".join(lines) + "\n")
        with smtplib.SMTP(smtp_cfg["host"], int(smtp_cfg["port"]), timeout=10) as s:
            if smtp_cfg.get("starttls"):
                s.starttls()
            pw = os.environ.get("INF_WATCHDOG_SMTP_PASSWORD")
            if pw:
                s.login(smtp_cfg["sender"], pw)
            s.sendmail(msg["From"], list(smtp_cfg["to"]), msg.as_string())
        return True
    return send


def _webhook_payload(subject, lines):
    """企微/钉钉群机器人 text 消息体（两者同构；纯函数，selftest 可离线断言）。"""
    return {"msgtype": "text",
            "text": {"content": subject + "\n" + "\n".join(lines[:20])}}


def driver_webhook(hook_cfg):
    """webhook 驱动：urllib POST JSON（wecom/dingtalk 群机器人 text 消息），超时 10 秒。"""
    import urllib.request

    def send(subject, lines, level, meta=None):
        req = urllib.request.Request(
            hook_cfg["url"], data=json.dumps(_webhook_payload(subject, lines)).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
        try:
            rc = json.loads(body).get("errcode", 0)
            if rc != 0:
                raise RuntimeError("webhook errcode=%s" % rc)
        except ValueError:
            pass    # 非 JSON 响应（部分网关），HTTP 200 即认为送达
        return True
    return send


# ---------------------------------------------------- wechat-relay 驱动 ----
# 协议：qywx docs/04_内网服务器企微推送对接手册.md + docs/08_通用告警推送公共接口对接指南.md
#   POST {url}（v2，告警类幂等入队）
#   X-Signature = HMAC_SHA256(secret, source_id + "." + ts + "." + raw_body) hex 小写
#   签名与发送必须用同一份字节（先定字节→后签名→原样发送，不用 requests json= 类重序列化）。
RELAY_SEV = {"CRITICAL": "red", "ERROR": "red", "WARN": "yellow",
             "RECOVERED": "blue", "OK": "blue", "DAILY": "blue"}


def _relay_payload(relay_cfg, subject, lines, level, hour_bucket,
                   dedupe_key=None, first_bad_ts=None):
    """事件信封（纯函数，selftest 可离线断言）。正文只由 watchdog D5 白名单字段构造
    （dispatch 已拼好主机/时间/结构化段落，这里不再重复拼主机/时间——PT-02 去冗余）。

    PT-02 幂等键修复：source_event_id 改为对**稳定身份** source_id|防轰键|级别|小时桶
    取 SHA256 前 24 位——不再把每 5 分钟变化的动态计数文本哈希进事件 ID（旧实现导致
    relay 侧防重键漂移、同一告警每班都被当成新事件）。同小时内重试幂等（relay 返回
    duplicate），跨小时同类告警可再次投递，与 dispatch 的 1 小时防轰 TTL 对齐。"""
    ident = "%s|%s|%s|%s" % (relay_cfg["source_id"],
                             dedupe_key or subject, level, hour_bucket)
    source_event_id = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]
    body = "\n".join(lines)
    return {
        "source_event_id": source_event_id,
        "event_type": "alert",
        "category": relay_cfg.get("category") or "inf-pusher",
        "severity": RELAY_SEV.get(level, "gray"),
        "title": clip(subject, 200),
        "body": clip(body, 4000),
        "occurred_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S+08:00"),
    }


def driver_relay(relay_cfg, host_label):
    """wechat-relay 驱动：HMAC 签名 v2 事件，超时 10 秒；202=入队 / 200 duplicate=幂等命中
    均视为送达；502 按协议允许原样重试一次；PT-02：429/503 读 Retry-After 温和退避
    （上限 3 秒，不拖慢守护）重试 1 次；其余非 2xx 抛错由 dispatch 降级落 file。"""
    import urllib.error
    import urllib.request

    def send(subject, lines, level, meta=None):
        secret = os.environ.get("INF_WATCHDOG_RELAY_SECRET", "")
        if not secret:
            raise RuntimeError("INF_WATCHDOG_RELAY_SECRET 未设置（relay.enabled=true 时必填）")
        hour_bucket = datetime.now().strftime("%Y%m%d%H")
        payload = _relay_payload(relay_cfg, subject, lines, level, hour_bucket,
                                 dedupe_key=(meta or {}).get("key"),
                                 first_bad_ts=(meta or {}).get("first_bad_ts"))
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ts = str(int(_time.time()))
        sig = hmac.new(secret.encode("utf-8"),
                       ("%s.%s." % (relay_cfg["source_id"], ts)).encode("utf-8") + raw,
                       hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            relay_cfg["url"], data=raw, method="POST",
            headers={"Content-Type": "application/json",
                     "X-Source-Id": relay_cfg["source_id"],
                     "X-Timestamp": ts, "X-Signature": sig})

        def _do():
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.getcode(), resp.read(65536).decode("utf-8", "replace")

        try:
            code, body = _do()
        except urllib.error.HTTPError as e:
            if e.code == 502:      # 协议：502 防重放/去重已释放，可用原签名原样重试一次
                code, body = _do()
            elif e.code in (429, 503):     # PT-02：温和退避重试 1 次（Retry-After 上限 3 秒）
                ra = ""
                try:
                    ra = (e.headers or {}).get("Retry-After", "") or ""
                except Exception:
                    ra = ""
                delay = 1.0
                if str(ra).strip().isdigit():
                    delay = min(3.0, max(0.0, float(ra)))
                _time.sleep(delay)
                code, body = _do()
            else:
                raise RuntimeError("relay HTTP %s: %s"
                                   % (e.code, clip(e.read(2048).decode("utf-8", "replace"))))
        if code not in (200, 202):
            raise RuntimeError("relay HTTP %s: %s" % (code, clip(body)))
        try:
            rc = json.loads(body)
            if rc.get("ok") is False:
                raise RuntimeError("relay ok=false: %s" % clip(body))
        except ValueError:
            pass                   # 非 JSON 响应，2xx 即认为受理
        return True
    return send


def build_drivers(cfg):
    """file 必开；smtp/webhook/relay 按配置启用（驱动失败降级：file 必落盘，check 退出码不受影响）。"""
    drivers = [("file", driver_file(cfg["state_dir"]), False)]
    if cfg["smtp"]["enabled"]:
        drivers.append(("smtp", driver_smtp(cfg["smtp"]), True))
    if cfg["webhook"]["enabled"]:
        drivers.append(("webhook", driver_webhook(cfg["webhook"]), True))
    if cfg["relay"]["enabled"]:
        drivers.append(("relay", driver_relay(cfg["relay"], cfg["host_label"]), True))
    return drivers   # (name, send(subject, lines, level)->bool, external)


# ------------------------------------------------------------- 防轰/恢复 ----
def load_dedupe(state_dir):
    p = os.path.join(state_dir, "dedupe.json")
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("sent", {})
    d.setdefault("levels", {})
    return d


def save_dedupe(state_dir, d):
    atomic_write(os.path.join(state_dir, "dedupe.json"),
                 json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def _level_state(v):
    """dedupe["levels"] 条目归一：旧版为纯字符串级别，新版为
    {"level":…, "first_bad_ts":…}——统一成 dict（旧条目无首次时间）。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        return {"level": v, "first_bad_ts": None}
    return None


def quiet_active(qh, now_dt=None):
    """夜间免打扰窗判定（跨午夜窗：23:00–07:30）。配置缺失/关闭→False。"""
    if not qh or not qh.get("enabled"):
        return False
    now_dt = now_dt or datetime.now()

    def _t(s, dft):
        try:
            hh, mm = str(s).split(":")
            return time(int(hh), int(mm))
        except (ValueError, AttributeError):
            return dft
    start = _t(qh.get("start"), time(23, 0))
    end = _t(qh.get("end"), time(7, 30))
    t = now_dt.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end              # 跨午夜


def dispatch(cfg, drivers, finding, check_name, dedupe, dry_all=False):
    """对单条 finding 外发：file 恒发；smtp/webhook/relay 过防轰（同键 TTL 内只发 1 次，
    ttl=None 永久去重——C7 文件转发）。dry_all 绕过防轰（晨报/test-alert/升级穿透）。

    PT-02 三项关键修复：
    1. 结构化正文：主机/时间（含持续时长）+【异常现象】+ 检查项明细行 +
       【涉及文书】+【处置建议】（全部 D5 白名单字段，无患者信息）；
    2. 隧道闪断不吞告警：只有至少一个外部驱动成功送达才写 dedupe["sent"]——
       全部外部驱动失败（如 SSH 隧道重连中）不写账，下一班 check 立即自动重试补发；
    3. 夜间免打扰：quiet_hours 生效时 WARN 只落本地文件不推企微，同样不写防轰账
       ——次日首个非静默周期若仍处于 WARN 自动补发；CRITICAL/ERROR 绝不静默。"""
    errors = []
    now_ep = _time.time()
    key = finding.get("key") or "%s:%s" % (check_name, finding["level"])
    ttl = finding.get("ttl", DEDUPE_TTL_SEC)
    last = dedupe["sent"].get(key, 0)
    within = (ttl is None and last > 0) or (ttl is not None and now_ep - last < ttl)
    if dry_all:
        within = False
    subject = finding.get("subject") or alert_subject(finding["level"], check_name)
    base_lines = list(finding.get("lines") or [finding["detail"]])   # D5：正文只由白名单字段构造
    qh = cfg.get("quiet_hours") or {}
    suppressed = (not dry_all and quiet_active(qh)
                  and finding["level"] in (qh.get("suppress_levels") or ["WARN"]))
    # 结构化正文（一次拼装，所有驱动共用；relay 不再二次拼主机/时间）
    fkey = "%s|%s" % (check_name, finding.get("key") or check_name)
    st = _level_state(dedupe["levels"].get(fkey))
    dur_txt = ""
    if st and st.get("first_bad_ts"):
        mins = int(max(0.0, now_ep - st["first_bad_ts"]) // 60)
        dur_txt = "（已持续 %d 分钟）" % mins
    lines = ["主机：<prod-host> 推送宿主机（%s）" % cfg.get("host_label", "?"),
             "时间：%s%s" % (now_str(), dur_txt),
             "【异常现象】%s" % finding["detail"]]
    lines += base_lines
    docs = sorted(set(_DOC_RE.findall(finding["detail"] + "\n" + "\n".join(base_lines))))
    if docs:
        lines.append("【涉及文书】" + "、".join(docs))
    advice = ADVICE_CN.get(finding.get("key") or check_name) or ADVICE_CN.get(check_name)
    if advice and finding["level"] not in ("RECOVERED", "OK", "TEST", "DAILY"):
        lines.append("【处置建议】" + advice)
    meta = {"key": key, "first_bad_ts": (st or {}).get("first_bad_ts")}
    has_external = any(ext for _, _, ext in drivers)
    external_ok = False
    for name, send, external in drivers:
        if external and (within or suppressed):
            continue
        try:
            send(subject, lines, finding["level"], meta)
            if external:
                external_ok = True
        except Exception as e:
            errors.append({"driver": name, "key": key, "error": clip(e)})
    # 防轰账写入条件（PT-02）：未在窗内 且 未被静默 且（无外部驱动 或 至少一个外部驱动成功）
    if not within and not suppressed and (not has_external or external_ok):
        dedupe["sent"][key] = now_ep
    return errors


def cmd_check(cfg):
    os.makedirs(cfg["state_dir"], exist_ok=True)
    if not cfg.get("enabled", True):
        atomic_write(os.path.join(cfg["state_dir"], "status.json"),
                     json.dumps({"ts": now_str(), "host": cfg["host_label"],
                                 "level": "DISABLED", "exit_code": 0},
                                ensure_ascii=False, indent=1) + "\n")
        print("watchdog disabled by config")
        return 0
    results, overall, exit_code = run_checks(cfg)
    dedupe = load_dedupe(cfg["state_dir"])
    # PT-02：旧版 levels[name]=纯字符串 → 迁移为新子键格式 name|子键（含首次恶化时间）
    for k, v in list(dedupe["levels"].items()):
        if not isinstance(v, dict):
            dedupe["levels"]["%s|%s" % (k, k)] = {"level": str(v), "first_bad_ts": None}
            del dedupe["levels"][k]
    drivers = build_drivers(cfg)
    driver_errors = []
    for name, r in results.items():
        present = set()
        for fd in r["findings"]:
            fkey = "%s|%s" % (name, fd.get("key") or name)
            present.add(fkey)
            prev = _level_state(dedupe["levels"].get(fkey))
            # PT-02 升级穿透：WARN→CRITICAL 立即外发（绕过 1 小时防轰窗）；
            # 标题用子键中文名（如 c5_wait=子项等待父受理积压），无则回退检查项中文名
            if prev and SEV.get(fd["level"], 0) > SEV.get(prev.get("level", "OK"), 0):
                esc = dict(fd)
                sub_cn = CHECK_CN.get(fd.get("key") or "") or CHECK_CN.get(name, name)
                esc["subject"] = "【升级·%s】传染病上报·%s" % (
                    SEV_CN.get(fd["level"], fd["level"]), sub_cn)
                driver_errors += dispatch(cfg, drivers, esc, name, dedupe, dry_all=True)
            else:
                driver_errors += dispatch(cfg, drivers, fd, name, dedupe)
            # 子键级别跟踪：只升不降（直到该子键消失触发恢复）
            st = _level_state(dedupe["levels"].get(fkey)) or {}
            prev_lvl = st.get("level", "OK")
            if SEV.get(fd["level"], 0) >= SEV.get(prev_lvl, 0):
                dedupe["levels"][fkey] = {
                    "level": fd["level"],
                    "first_bad_ts": st.get("first_bad_ts") or _time.time()}
            else:
                st["level"] = fd["level"]
                dedupe["levels"][fkey] = st
        # PT-02 子键独立恢复：该检查上轮恶化的子键本轮消失 → 立即恢复通知，
        # 不受本检查其他子键仍处告警的影响（如真实失败恢复而等待积压仍在 WARN）
        for fkey in [k for k in list(dedupe["levels"]) if k.startswith(name + "|")]:
            if fkey in present:
                continue
            st = _level_state(dedupe["levels"][fkey])
            if not st or SEV.get(st.get("level", "OK"), 0) <= 0:
                dedupe["levels"].pop(fkey, None)
                continue
            sub_key = fkey.split("|", 1)[1]
            dur_txt = ""
            if st.get("first_bad_ts"):
                mins = int(max(0.0, _time.time() - st["first_bad_ts"]) // 60)
                dur_txt = "持续 %d 分钟，" % mins
            # 子键有专属中文名（如 c5_wait）用之，否则用检查项中文名
            cn = CHECK_CN.get(sub_key) or CHECK_CN.get(name) or name
            msg = ("检查项「%s」已由【%s】恢复为正常（%s恢复时间 %s）"
                   % (cn, SEV_CN.get(st.get("level"), st.get("level")), dur_txt, now_str()))
            driver_errors += dispatch(cfg, drivers,
                                      _f("RECOVERED", msg,
                                         key="%s:RECOVERED" % fkey),
                                      name, dedupe)
            dedupe["levels"].pop(fkey, None)
    save_dedupe(cfg["state_dir"], dedupe)
    relay_reachable = None
    if (cfg.get("relay") or {}).get("enabled"):
        relay_reachable = results.get("c11_relay_tunnel", {}).get("level") == "OK"
    status = {"ts": now_str(), "host": cfg["host_label"], "level": overall,
              "exit_code": exit_code, "relay_reachable": relay_reachable,
              "checks": {n: {"level": r["level"], "detail": r["detail"],
                             "elapsed_ms": r["elapsed_ms"]} for n, r in results.items()},
              "driver_errors": driver_errors}
    atomic_write(os.path.join(cfg["state_dir"], "status.json"),
                 json.dumps(status, ensure_ascii=False, indent=1) + "\n")
    for n, r in results.items():
        print("%-18s %-8s %s" % (n, r["level"], r["detail"][:160]))
    print("OVERALL %s exit=%d driver_errors=%d" % (overall, exit_code, len(driver_errors)))
    return exit_code


# --------------------------------------------------------------- daily ----
_DAILY_TS_RE = re.compile(r'"ts":\s*"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"')
_DAILY_TBL_RE = re.compile(r'"table":\s*"([a-z_0-9]+)"')


def _scan_daily_receipts(pusher_state, win_start, win_end, min_ymd):
    """PT-02 晨报回执四类精确计数（快速子串分流，防 855 万行全量 json.loads 超时）：

    - 先按 '"result":' 子串粗筛，命中行再正则抽 ts（字符串序比较窗口，不构造 datetime）；
    - result=true 行只正则抽表名计 sent（**排除 mock**——演练回执不是真实受理），
      不跑 json.loads（无错行占绝大多数）；
    - result=false 行占比极小才 json.loads 精确分类：
      ① real_failed（stage∉{client-validate,parent-wait}，按表计数，含重试行）；
      ② parent_wait_unique 按 (table,id) 去重（回执每班重复写，不去重虚高 96 倍）；
      ③ client_validate_unique 按 (table,id) 去重。
    返回 (per_table_sent, fail_by_table, wait_keys, cv_keys)。全部只有表名/计数/
    时间窗——id 集合只用于去重计数，绝不进入外发内容（D5）。"""
    per_table = {}
    fail_by_table = {}
    wait_keys = set()
    cv_keys = set()
    win_s = win_start.strftime(DATE_FMT)
    win_e = win_end.strftime(DATE_FMT)
    for path in _receipt_files(pusher_state, min_ymd=min_ymd):
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for raw in fh:
                if '"result": true' in raw or '"result":true' in raw:
                    m = _DAILY_TS_RE.search(raw)
                    if not m or not (win_s <= m.group(1) < win_e):
                        continue
                    if '"mock"' in raw:
                        continue                      # mock 演练不计真实受理
                    t = _DAILY_TBL_RE.search(raw)
                    if t:
                        per_table[t.group(1)] = per_table.get(t.group(1), 0) + 1
                elif '"result": false' in raw or '"result":false' in raw:
                    m = _DAILY_TS_RE.search(raw)
                    if not m or not (win_s <= m.group(1) < win_e):
                        continue
                    try:
                        rec = json.loads(raw)          # 失败行占比极小
                    except ValueError:
                        continue
                    stage = str(rec.get("stage", ""))
                    t = str(rec.get("table", "?"))
                    if stage == "parent-wait":
                        wait_keys.add((t, str(rec.get("id", ""))))
                    elif stage == "client-validate":
                        cv_keys.add((t, str(rec.get("id", ""))))
                    else:
                        fail_by_table[t] = fail_by_table.get(t, 0) + 1
    return per_table, fail_by_table, wait_keys, cv_keys


def daily_report(cfg, now=None):
    """汇总昨日 00:00–24:00 CST（宿主本地日期-1，坑6）。全部为聚合计数/表名/时间戳——
    receipts 的 id 字段不得进入任何外发内容（D5）。

    PT-02 重写：四类精确计数（sent 排 mock / 真实失败排 client-validate 与
    parent-wait / 等待与尾差按 (table,id) 去重，等待绝不再计入 failed）；
    min_ymd 真正过滤历史大文件；补 13 表完整度概览与逐表水位。"""
    now = now or datetime.now()
    y = (now - timedelta(days=1)).date()
    ymd = y.strftime("%Y%m%d")
    # 1) 班次：/var/log/cron 当前文件中昨日月/日的 inf-pusher CMD 与 CMDEND 计数
    cmd_n = end_n = 0
    for l in read_pusher_cron_lines(cfg["cron_log"]):
        ts = parse_cron_ts(l)
        if not ts or (ts[0], ts[1]) != (y.month, y.day):
            continue
        if " CMD (" in l:
            cmd_n += 1
        elif " CMDEND (" in l:
            end_n += 1
    # 2) receipts：行 ts 为容器 UTC——昨日 CST 窗 [Y 00:00 CST, Y+1 00:00 CST) 换算为
    #    [Y-1 16:00 UTC, Y 16:00 UTC)（坑1/坑6：宿主 CST、容器 UTC，仅此处换算）
    win_start = datetime(y.year, y.month, y.day) - timedelta(hours=8)
    win_end = win_start + timedelta(days=1)
    min_ymd = (y - timedelta(days=2)).strftime("%Y%m%d")
    per_table, fail_by_table, wait_keys, cv_keys = _scan_daily_receipts(
        cfg["pusher_state"], win_start, win_end, min_ymd)
    # 3) 日终对账
    recon_p = os.path.join(cfg["pusher_state"], "reconcile-%s.json" % ymd)
    if os.path.exists(recon_p):
        try:
            rd = json.loads(Path(recon_p).read_text(encoding="utf-8"))
            recon_txt = "total_repushed=%d total_failed=%d" % (
                int(rd.get("total_repushed", 0)), int(rd.get("total_failed", 0)))
            pw = rd.get("total_parent_wait")
            if pw is not None:
                recon_txt += "（等待父受理 %d / 必填尾差 %s）" % (
                    int(pw), rd.get("total_skipped_invalid", "?"))
        except Exception as e:
            recon_txt = "解析失败: %s" % clip(e)
    else:
        recon_txt = "reconcile-%s.json 缺失" % ymd
    # 4) 昨日 watchdog 告警列表（自身 alerts-<ymd>.jsonl）
    alerts_n = {"CRITICAL": 0, "WARN": 0, "RECOVERED": 0}
    subjects = []
    ap = os.path.join(cfg["state_dir"], "alerts-%s.jsonl" % ymd)
    if os.path.exists(ap):
        try:
            for line in open(ap, "r", encoding="utf-8", errors="replace"):
                try:
                    a = json.loads(line)
                except ValueError:
                    continue
                lv = a.get("level", "?")
                alerts_n[lv] = alerts_n.get(lv, 0) + 1
                if len(subjects) < 30:
                    subjects.append(clip(a.get("subject", ""), 120))
        except OSError:
            pass
    # 5) 当前水位（最大 opTime + 13 表逐表状态）
    wm_txt = "watermark.json 不可读"
    wm_rows = []
    try:
        wm = json.loads(Path(os.path.join(cfg["pusher_state"], "watermark.json"))
                        .read_text(encoding="utf-8"))
        parsed = {k: datetime.strptime(v, DATE_FMT) for k, v in wm.items()}
        if parsed:
            tbl, tmax = max(parsed.items(), key=lambda kv: kv[1])
            wm_txt = "最大 opTime %s（%s，距今 %.1f 小时）" % (
                tmax.strftime(DATE_FMT), tbl_cn(tbl), (now - tmax).total_seconds() / 3600.0)
            for k, v in sorted(parsed.items(), key=lambda kv: -kv[1].timestamp()):
                wm_rows.append("  %s 停在 %s（%.1f 小时前）"
                               % (tbl_cn(k), v.strftime(DATE_FMT),
                                  (now - v).total_seconds() / 3600.0))
    except Exception:
        pass
    # 6) 13 表完整度概览（PT-02）：昨日有数据 X 张 / 零增量 Y 张
    known = list(TABLE_CN.keys())
    with_data = [t for t in known
                 if per_table.get(t, 0) > 0 or fail_by_table.get(t, 0) > 0
                 or len({k for k in wait_keys if k[0] == t}) > 0
                 or len({k for k in cv_keys if k[0] == t}) > 0]
    zero_tables = [t for t in known if t not in with_data]
    lines = [
        "【晨报】传染病上报·昨日运行总览（%s）—— %s" % (y.strftime("%F"), cfg["host_label"]),
        "生成时间：%s（每日 08:00 timer，可手动 systemctl start inf-watchdog-daily.service）" % now_str(),
        "",
        "【班次】cron CMD %d 班 / CMDEND %d 完成（含 00:05 reconcile 班；/var/log/cron 当前文件口径，可能已轮转）" % (cmd_n, end_n),
        "【昨日回执】（行内 ts 落昨日 CST 窗；真实受理不含 mock；失败行含重试）",
    ]
    all_tables = sorted(set(per_table) | set(fail_by_table)
                        | {k[0] for k in wait_keys} | {k[0] for k in cv_keys})
    for t in all_tables:
        lines.append("  %s sent=%-6d 真实失败=%d 等待父受理=%d 必填尾差=%d"
                     % (tbl_cn(t), per_table.get(t, 0), fail_by_table.get(t, 0),
                        len({k for k in wait_keys if k[0] == t}),
                        len({k for k in cv_keys if k[0] == t})))
    lines.append("【13 表完整度】昨日有数据 %d 张 / 零增量 %d 张%s"
                 % (len(with_data), len(zero_tables),
                    ("（零增量：%s——death 等稀疏表按自然规律属正常，连续多日零增量看水位停滞）"
                     % "、".join(tbl_cn(t) for t in zero_tables)) if zero_tables else ""))
    lines += [
        "【日终对账】%s" % recon_txt,
        "【昨日告警】CRITICAL %d / WARN %d / RECOVERED %d%s" % (
            alerts_n.get("CRITICAL", 0), alerts_n.get("WARN", 0), alerts_n.get("RECOVERED", 0),
            ("" if subjects else "（watchdog 昨日无告警落盘）")),
    ]
    lines += ["  " + s for s in subjects]
    lines += ["【当前水位】%s" % wm_txt]
    lines += wm_rows
    lines += ["", "—— inf_watchdog 自动生成；推送主链路零改动，本组件只读检测+告警。"]
    return ymd, lines


def cmd_daily(cfg, now=None):
    os.makedirs(cfg["state_dir"], exist_ok=True)
    ymd, lines = daily_report(cfg, now=now)
    path = os.path.join(cfg["state_dir"], "daily-%s.txt" % ymd)
    atomic_write(path, "\n".join(lines) + "\n")
    dedupe = load_dedupe(cfg["state_dir"])
    drivers = build_drivers(cfg)
    y_iso = "%s-%s-%s" % (ymd[:4], ymd[4:6], ymd[6:8])
    errors = dispatch(cfg, drivers,
                      _f("DAILY", lines[0], key="daily:%s" % ymd, ttl=1, lines=lines,
                         subject="【晨报】传染病上报·昨日运行总览（%s）" % y_iso),
                      "daily", dedupe, dry_all=True)
    save_dedupe(cfg["state_dir"], dedupe)
    print("\n".join(lines))
    print("DAILY written %s driver_errors=%d" % (path, len(errors)))
    for e in errors:
        print("driver_error: %s %s" % (e["driver"], e["error"]))
    return 0


def cmd_test_alert(cfg):
    os.makedirs(cfg["state_dir"], exist_ok=True)
    drivers = build_drivers(cfg)
    lines = ["本条为通道测试消息：收到即表示 <prod-host> 传染病上报 → 企微告警链路正常。",
             "主机：<prod-host> 推送宿主机（%s）  时间：%s" % (cfg["host_label"], now_str()),
             "无需任何处理。"]
    ok, fail = [], []
    for name, send, external in drivers:
        try:
            send("【测试】传染病上报告警通道联调", lines, "TEST")
            ok.append(name)
        except Exception as e:
            fail.append((name, clip(e)))
    print("test-alert OK drivers: %s" % ",".join(ok))
    for name, err in fail:
        print("test-alert FAIL %s: %s" % (name, err))
    if fail:
        print("NOTE: 外发通道失败不影响 file 落盘；请检查通道配置或入 BACKLOG")
        return 1
    return 0


def main(argv):
    args = argv[1:]
    cfg_path = None
    if "--config" in args:
        i = args.index("--config")
        if i + 1 >= len(args):
            print(__doc__)
            return 2
        cfg_path = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1 or args[0] not in ("check", "daily", "test-alert"):
        print(__doc__)
        return 2
    default_cfg = str(Path(__file__).resolve().parent / "config.json")
    cfg_path = cfg_path or default_cfg
    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        print("CONFIG ERROR: %s" % e, file=sys.stderr)
        # 自身错误也要落盘+file 告警，不得静默（§2.1）
        try:
            state_dir = str(Path(cfg_path).resolve().parent / "state")
            os.makedirs(state_dir, exist_ok=True)
            driver_file(state_dir)("[ERROR] %s inf_watchdog 配置错误" % DEFAULTS["host_label"],
                                   ["config: %s" % cfg_path, "error: %s" % clip(e)], "ERROR")
        except Exception:
            pass
        return 2
    return {"check": cmd_check, "daily": cmd_daily, "test-alert": cmd_test_alert}[args[0]](cfg)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
