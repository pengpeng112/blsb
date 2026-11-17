"""传染病前置软件推送器（独立部署，stdlib + oracledb + urllib，无第三方 Web 框架）。

用法：
  python pusher.py --config config.json --table death --mode mock     # 演练（默认，零真实副作用）
  python pusher.py --config config.json --table death --mode real    # 真实推送（需配置 enabled=true）
  python pusher.py --config config.json --table all --mode mock
  python pusher.py --config config.json --table all --mode real --since "2026-10-30 00:00:00"  # 显式窗补推（忽略水位，幂等）
  python pusher.py --config config.json --table reconcile --mode real --reconcile-date yesterday  # 日终对账（T+0 方案二）

安全约定（对齐 AGENTS/multi-review 终报告）：
- 目标主机白名单：仅 http://<frontend-host>（配置外的 URL 直接拒绝）；
- MOCK 默认：real 模式必须同时满足 config.enabled=true 且 --mode real；
- 幂等：同 id 重复 POST = 前置机按 ID 更新（规范 3.x）；
- 发送次序保护：本地 sent.jsonl 记录 id→operation_time，非更新（时间不更新）则跳过；
- 增量：水位 + overlap_days 重叠回扫 + full_days 周期全量对账（三种组合，F9 共识）；
- 回执逐条 JSONL 落盘（receipts-<date>.jsonl），失败分类：网络/01/99 重试，02/03/04 记录不重试；
- 病程截断统计：字段长度==2000 记 truncated（GBK 视图层上限，仅本地统计不外发）；
- 上报语义（2026-11-07，手册整改§三-2）：payload operationTime=接口上传时刻；内部水位/去重/sent.jsonl/对账仍用源数据时间（op_time），两者解耦。

运行环境：Oracle 11g（ODS）必须 oracledb thick 模式 + instantclient（部署说明见 README）。
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from tables import DATE_FIELDS, ROLL_OUT_ORDER, TABLES

ALLOWED_HOSTS = ("<frontend-host>",)          # 白名单：只允许这一台前置机
ALLOWED_PORT = 8881
DATE_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------- 基础组件
def now_str():
    return dt.datetime.now().strftime(DATE_FMT)


_MON = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def norm_date_str(v):
    """日期字段里的字符串归一化（真实首推实测 2026-10-28）：
    视图 NVL(VARCHAR2 日期列, DATE 列) 会把 DATE 强转成 Oracle 默认 'DD-MON-RR'
    （前置机报 errorCode 05 For input string "01-MAY-26"），统一转规范格式；
    月份用查表解析，不依赖 locale 的 %b。"""
    try:
        return dt.datetime.strptime(v, DATE_FMT).strftime(DATE_FMT)
    except ValueError:
        pass
    import re as _re
    m = _re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2})(?:\s+(\d{1,2}):(\d{2}):(\d{2}))?$", v.strip())
    if m:
        d, mon, y = int(m.group(1)), _MON.get(m.group(2).upper()), int(m.group(3))
        hh, mi, ss = (int(m.group(4)), int(m.group(5)), int(m.group(6))) if m.group(4) else (0, 0, 0)
        if mon:
            return dt.datetime(2000 + y, mon, d, hh, mi, ss).strftime(DATE_FMT)
    return v


def camel(serialized_map, row):
    """视图行（dict，键=SNAKE 列名）→ 规范 JSON（camelCase，日期格式化，None 丢弃）。"""
    out = {}
    for col, key in serialized_map.items():
        v = row.get(col)
        if v is None:
            continue
        if key in DATE_FIELDS and isinstance(v, str):
            v = norm_date_str(v)
        elif key in DATE_FIELDS and not isinstance(v, str):
            v = v.strftime(DATE_FMT)
        elif isinstance(v, dt.datetime):
            v = v.strftime(DATE_FMT)
        elif isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        out[key] = v
    return out


def validate_required(payload, required):
    missing = [k for k in required if not payload.get(k)]
    return missing


class ReceiptLog:
    def __init__(self, directory):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(
            directory, "receipts-%s.jsonl" % dt.date.today().strftime("%Y%m%d"))
        self.sent_path = os.path.join(directory, "sent.jsonl")
        self._sent = None

    def receipt(self, table, rec):
        rec = dict(rec, table=table, ts=now_str(), mode=rec.get("mode", ""))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def parent_ids_from_sent(self, parent_table):
        """P2：从 sent.jsonl 采集指定父表历史已发父 id 集合（同版本已受理父可复用——A07）。
        sent 记录 id 形如 'parent_table/<父id>'（新）或 '<父id>'（旧），取末段。"""
        out = {}
        if not os.path.exists(self.sent_path):
            return out
        prefix = parent_table + "/"
        with open(self.sent_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    rid = str(r.get("id", ""))
                    if rid.startswith(prefix):
                        out[rid[len(prefix):]] = True
                    elif "/" not in rid and r.get("table") == parent_table:
                        out[rid] = True
                except (ValueError, KeyError):
                    continue
        return out

    def accepted_ids(self, table, receipt_path=None):
        """P2 父受理门禁：从本班次回执文件采集指定表真实受理成功(result=true 且非 mock)的 id 集合。
        仅回读 receipts 参数指定的文件（本班次），不读历史——受理证据=本进程真实 HTTP true。"""
        path = receipt_path or self.path
        ids = {}
        if not os.path.exists(path):
            return ids
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if r.get("table") != table:
                        continue
                    if r.get("mode") == "mock":
                        continue
                    if r.get("result") is True and r.get("stage") != "mock":
                        ids[str(r.get("id"))] = True
                except (ValueError, KeyError):
                    continue
        return ids

    def sent_state(self):
        """'table/id' -> operationTime（次序保护；键分表，不同表 id 语义不同）。"""
        if self._sent is None:
            self._sent = {}
            if os.path.exists(self.sent_path):
                with open(self.sent_path, encoding="utf-8") as f:
                    for line in f:
                        try:
                            r = json.loads(line)
                            key = r["id"] if "/" in str(r["id"]) \
                                else "%s/%s" % (r.get("table", "?"), r["id"])
                            old = self._sent.get(key)
                            if old is None or r.get("opTime", "") >= old:
                                self._sent[key] = r.get("opTime", "")
                        except (ValueError, KeyError):
                            continue
        return self._sent

    def mark_sent(self, table, rid, op_time):
        with open(self.sent_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "%s/%s" % (table, rid), "opTime": op_time or "",
                                "table": table, "ts": now_str()},
                               ensure_ascii=False) + "\n")
        self.sent_state()["%s/%s" % (table, rid)] = op_time or ""


class Watermark:
    """每表水位（上次成功推进到的 operation_time）+ overlap 回扫窗。"""

    def __init__(self, path):
        self.path = path
        self.state = {}
        if os.path.exists(path):
            try:
                self.state = json.load(open(path, encoding="utf-8"))
            except ValueError:
                self.state = {}

    def window(self, table, overlap_days):
        """返回 (since_str, use_window)。无水位→回扫 full_days 由调用方决定。"""
        last = self.state.get(table)
        if not last:
            return None
        t = dt.datetime.strptime(last, DATE_FMT) - dt.timedelta(days=overlap_days)
        return t.strftime(DATE_FMT)

    def advance(self, table, op_time):
        if op_time and (not self.state.get(table) or op_time > self.state[table]):
            self.state[table] = op_time
            json.dump(self.state, open(self.path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)

    def force_full(self, table, days, today=None):
        """周期性全量对账窗（full_days 到期或首次运行）。"""
        t = (today or dt.datetime.now()) - dt.timedelta(days=days)
        return t.strftime(DATE_FMT)


def check_url(url):
    p = urllib.parse.urlparse(url)
    if p.scheme != "http" or p.hostname not in ALLOWED_HOSTS or p.port != ALLOWED_PORT:
        raise SystemExit("目标不在白名单（仅 http://<frontend-host>）: %s" % url)


def post_json(url, payload, timeout=15):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- 取数（oracledb）
_CLIENT_READY = False


def _init_client():
    """ODS 为 Oracle 11g，oracledb 必须 thick 模式；容器内 instantclient 自动发现。"""
    global _CLIENT_READY
    if _CLIENT_READY:
        return
    import glob
    import oracledb
    try:
        cands = sorted(glob.glob("/opt/oracle/instantclient*"))
        oracledb.init_oracle_client(lib_dir=cands[-1] if cands else None)
    except Exception:
        pass  # 已初始化（重复调用）或宿主环境自带，交给连接时报错
    _CLIENT_READY = True


# ODS 连接超时（2026-10-31/11-03 两次取数无限挂起占死 push.lock 事故后加固）：
# tcp_connect_timeout=建连上限秒（connect 参数）；call_timeout=单次往返上限毫秒，
# 是**连接对象属性**而非 connect 参数（oracledb 3.4 实测），挂起会抛 DPI-1067
# 而不是永远阻塞 → 进程退出 → flock 锁释放 → 下一班自动重试。
ODS_CONNECT_KW = {"tcp_connect_timeout": 15}
ODS_CALL_TIMEOUT_MS = 300000


def fetch_rows(dsn_env_user, dsn_env_pwd, dsn_env_dsn, view, since, limit):
    import oracledb  # 部署主机需安装；本函数仅在非 mock 时调用
    _init_client()
    missing = [v for v in (dsn_env_user, dsn_env_pwd, dsn_env_dsn) if not os.environ.get(v)]
    if missing:
        raise SystemExit("缺少 ODS 凭据环境变量 %s——请由 DBA 写入 inf.env（见 README/部署说明）"
                         % ",".join(missing))
    user = os.environ[dsn_env_user]
    pwd = os.environ[dsn_env_pwd]
    dsn = os.environ[dsn_env_dsn]
    sql = ("SELECT * FROM %s WHERE OPERATION_TIME >= :since "
           "ORDER BY OPERATION_TIME" % view)
    if limit:
        sql = ("SELECT * FROM (SELECT v.* FROM %s v WHERE OPERATION_TIME >= :since "
               "ORDER BY OPERATION_TIME) WHERE ROWNUM <= :lim" % view)
    with oracledb.connect(user=user, password=pwd, dsn=dsn,
                          **ODS_CONNECT_KW) as conn:
        conn.call_timeout = ODS_CALL_TIMEOUT_MS
        cur = conn.cursor()
        since_dt = (dt.datetime.strptime(since, DATE_FMT)
                    if isinstance(since, str) else since)
        cur.execute(sql, since=since_dt, **({"lim": limit} if limit else {}))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------- 推送主流程
def apply_view_shims(name, payload, parent_ops):
    """视图空值垫片（FIELD_MAPPING §20）：视图侧兜底上线后自动失效（值非空不再回填）。
    - exam: 报告科室空 → 申请科室（v05 兜底未部署期）
    - admission: 操作人空 → 住院医师（v01 同）
    - exam_item: 操作人空 → 同批主表操作人（v06 同；主表先于子表运行）
    """
    if name == "exam":
        if not payload.get("deptCode"):
            payload["deptCode"] = payload.get("applyDeptCode")
        if not payload.get("deptName"):
            payload["deptName"] = payload.get("applyDeptName")
        op = payload.get("operatorId")
        if op:
            parent_ops[payload.get("id")] = op
    elif name == "admission":
        if not payload.get("operatorId"):
            payload["operatorId"] = payload.get("residentPhysicianId")
    elif name == "exam_item":
        if not payload.get("operatorId"):
            payload["operatorId"] = parent_ops.get(payload.get("exClinicalId"))
    return payload


def _row_op_time(row):
    """行内 OPERATION_TIME → datetime（可能为 datetime 或字符串），异常返回 None。"""
    v = row.get("OPERATION_TIME")
    if isinstance(v, dt.datetime):
        return v
    if isinstance(v, str):
        try:
            return dt.datetime.strptime(v, DATE_FMT)
        except ValueError:
            return None
    return None


def run_table(name, cfg, mode, receipts, wm, limit=None, parent_ops=None,
              since_override=None, window_end=None, parent_accept=None,
              replay_ids=None, force_resend=False):
    t = TABLES[name]
    url = "%s/hclient/emr/receive/%s" % (cfg["base_url"], t["endpoint"])
    check_url(url)
    if since_override:
        # 显式起始窗（对账/补推）：忽略水位，其余逻辑（重叠、次序保护、幂等跳过）不动
        since = since_override
        reason = "manual-override"
    else:
        since = wm.window(name, t["overlap_days"])
        if not since:
            since = wm.force_full(name, t["full_days"])
            reason = "full(first-run)"
        else:
            reason = "watermark+overlap"
    env = cfg.get("env", {})
    rows = fetch_rows(env.get("user"), env.get("pwd"), env.get("dsn"),
                      t["view"], since, limit)
    stats = dict(table=name, since=since, reason=reason, rows=len(rows),
                 sent=0, skipped_same=0, skipped_invalid=0, failed=0,
                 truncated=0)
    if window_end:
        # reconcile 统计：窗口 [since, window_end) 内的视图侧行数 A
        end_dt = (window_end if isinstance(window_end, dt.datetime)
                  else dt.datetime.strptime(window_end, DATE_FMT))
        stats["window_rows"] = sum(1 for r in rows
                                   if _row_op_time(r) is not None
                                   and _row_op_time(r) < end_dt)
    sent_state = receipts.sent_state()
    parent_fk = t.get("parent_fk")
    stats["parent_wait"] = 0
    for row in rows:
        payload = camel(t["map"], row)
        apply_view_shims(name, payload, parent_ops if parent_ops is not None else {})
        op_time = payload.get("operationTime", "")
        rid = payload.get("id")
        skey = "%s/%s" % (name, rid)
        if replay_ids is not None and rid not in replay_ids:
            stats["skipped_replay_filter"] = stats.get("skipped_replay_filter", 0) + 1
            continue
        if (sent_state.get(skey) and sent_state[skey] >= op_time
                and not (force_resend and replay_ids is not None and rid in replay_ids)):
            stats["skipped_same"] += 1
            continue
        # P2 父受理门禁：子项核对父真实受理证据，缺失→parent_wait 不调 HTTP。
        # 证据源优先级：调用方传入 parent_accept > sent.jsonl 历史已受理父（同版本可复用，A07）。
        if parent_fk and parent_accept is None:
            parent_accept = receipts.parent_ids_from_sent(t["depends_on"])
        if parent_fk:
            pval = payload.get(parent_fk)
            if not pval or not (parent_accept or {}).get(str(pval)):
                stats["parent_wait"] += 1
                receipts.receipt(name, {"id": rid, "result": False,
                                        "stage": "parent-wait",
                                        "desc": "父未受理:%s=%s" % (parent_fk, pval)})
                continue
        missing = validate_required(payload, t["required"])
        if missing:
            stats["skipped_invalid"] += 1
            receipts.receipt(name, {"id": rid, "result": False,
                                    "stage": "client-validate",
                                    "desc": "缺必填:" + ",".join(missing)})
            continue
        for f, cap in t.get("truncation_fields", {}).items():
            if payload.get(f) and len(payload[f]) >= cap:
                stats["truncated"] += 1
        if mode == "mock":
            # 演练只记回执与统计：不推水位、不记已发（否则会挡住后续真实推送）
            receipts.receipt(name, {"id": rid, "result": True,
                                    "stage": "mock", "desc": "MOCK 未发送",
                                    "mode": "mock"})
            stats["sent"] += 1
            continue
        payload["operationTime"] = dt.datetime.now(CST).strftime(DATE_FMT)   # 上报语义=接口上传时刻（北京时间，手册整改§三-2）；容器为 UTC 时钟勿用 now_str()；水位/去重/对账仍用 op_time（源时间）
        for attempt in range(3):
            try:
                resp = post_json(url, payload)
                ok = resp.get("result") is True
                receipts.receipt(name, {"id": rid, "result": ok,
                                        "errorCode": resp.get("errorCode"),
                                        "desc": resp.get("desc"), "attempt": attempt})
                if ok:
                    stats["sent"] += 1
                    wm.advance(name, op_time)
                    receipts.mark_sent(name, rid, op_time)
                else:
                    code = resp.get("errorCode")
                    if code in ("01", "99"):   # 服务端异常：重试
                        time.sleep(2 * (attempt + 1))
                        continue
                    stats["failed"] += 1        # 02/03/04：不重试，人工看
                break
            except (urllib.error.URLError, OSError) as e:
                receipts.receipt(name, {"id": rid, "result": False,
                                        "stage": "network", "desc": str(e),
                                        "attempt": attempt})
                time.sleep(2 * (attempt + 1))
        else:
            stats["failed"] += 1
    # 质量观察：编码空值告警（仅统计）
    for field in t.get("quality_watch", {}).get("null_warn", []):
        pass  # 行级已在 client-validate 兜住；此钩子保留给周期对账报告
    return stats


# ---------------------------------------------------------------- 日终对账（T+0 方案二）
CST = dt.timezone(dt.timedelta(hours=8))   # 业务时区：容器时钟为 UTC，日界按北京时间算


def resolve_reconcile_day(spec, today=None):
    """'yesterday' | 'YYYY-MM-DD' → (day_start, day_end, day_str) 对账窗。
    'yesterday' 按 CST 解析（00:05 CST 触发时 UTC 日期仍在前一天，直接用容器
    本地日期会 off-by-one 对错日子）。"""
    base = today or dt.datetime.now(CST).date()
    if spec == "yesterday":
        d = base - dt.timedelta(days=1)
    else:
        d = dt.datetime.strptime(spec, "%Y-%m-%d").date()
    start = dt.datetime(d.year, d.month, d.day)
    return (start.strftime(DATE_FMT),
            (start + dt.timedelta(days=1)).strftime(DATE_FMT),
            d.strftime("%Y%m%d"))


def run_reconcile(cfg, receipts, wm, spec, today=None):
    """对指定日做幂等补推对账：--since D 00:00:00 真实推送（已推记录靠 sent.jsonl 跳过）。
    产出 workdir/reconcile-<D>.json；N>0 追加 GAP-RECOVERED 到 cron.log 并写 ALERT 文件；
    窗口内 failed>0 同样写 ALERT。返回汇总 dict。"""
    since, until, day_str = resolve_reconcile_day(spec, today)
    out = {"day": day_str, "since": since, "until": until,
           "ran_at": now_str(), "tables": {}}
    total_repushed = total_failed = 0
    parent_ops = {}
    parent_accept = None                           # P2：reconcile 场景由 run_table 内部 fallback sent.jsonl 历史父受理
    for n in ROLL_OUT_ORDER:                       # 与常规班次相同的开闸顺序
        try:
            s = run_table(n, cfg, "real", receipts, wm, None, parent_ops,
                          since_override=since, window_end=until,
                          parent_accept=parent_accept)
        except Exception as e:                     # 单表取数/网络异常不中断对账（2026-11-03 加固）
            s = {"table": n, "error": str(e)[:300], "sent": 0, "failed": 1,
                 "skipped_same": 0, "skipped_invalid": 0, "rows": 0,
                 "since": since, "reason": "manual-override"}
        out["tables"][n] = {"window_rows": s.get("window_rows", 0),
                            "repushed": s["sent"], "failed": s["failed"]}
        total_repushed += s["sent"]
        total_failed += s["failed"]
    out["total_repushed"] = total_repushed
    out["total_failed"] = total_failed
    json.dump(out, open(os.path.join(cfg["workdir"],
                                     "reconcile-%s.json" % day_str),
                        "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if total_repushed > 0 or total_failed > 0:
        # ALERT 先写（新建文件，state/ 可写，必达）；cron.log 属主可能是 root
        # （cron 重定向创建，10001 无权追加）——GAP 行失败降级为告警，不致命。
        alert = os.path.join(cfg["workdir"], "ALERT-%s"
                             % (today or dt.datetime.now(CST).date()).strftime("%Y%m%d"))
        with open(alert, "a", encoding="utf-8") as f:
            stamp = now_str()
            for n, v in out["tables"].items():
                if v["repushed"] > 0:
                    f.write("%s GAP-RECOVERED table=%s day=%s count=%d\n"
                            % (stamp, n, day_str, v["repushed"]))
                if v["failed"] > 0:
                    f.write("%s RECONCILE-FAILED table=%s day=%s count=%d\n"
                            % (stamp, n, day_str, v["failed"]))
        if total_repushed > 0:
            try:
                with open(os.path.join(cfg["workdir"], "cron.log"), "a",
                          encoding="utf-8") as f:
                    f.write("[%s] GAP-RECOVERED day=%s repushed=%d failed=%d\n"
                            % (now_str(), day_str, total_repushed, total_failed))
            except OSError as e:
                print("WARN: GAP 行未能写入 cron.log（%s）——见 ALERT 文件" % e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--table", required=True,
                    help="表名/all（按开闸顺序）")
    ap.add_argument("--mode", choices=["mock", "real"], default="mock")
    ap.add_argument("--limit", type=int, default=None, help="限行数（首推小批量用）")
    ap.add_argument("--since", default=None,
                    help='显式起始窗 "YYYY-MM-DD HH:MM:SS"（忽略水位，对账/补推用）')
    ap.add_argument("--replay-manifest", dest="replay_manifest", default=None,
                    help="P3 定向重放 manifest（JSON：table/ids/force/源时间校验）")
    ap.add_argument("--apply", action="store_true",
                    help="与 --replay-manifest 连用：真实执行（缺省=dry-run 仅隔离审阅）")
    ap.add_argument("--reconcile-date", dest="reconcile_date", default=None,
                    help="reconcile 目标日：yesterday（默认）或 YYYY-MM-DD")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    if args.mode == "real" and not cfg.get("enabled", False):
        raise SystemExit("config.enabled=false，禁止真实发送（MOCK 先行约定）")

    if args.since:
        try:
            dt.datetime.strptime(args.since, DATE_FMT)
        except ValueError:
            raise SystemExit('--since 格式必须为 "%s"' % DATE_FMT)

    workdir = cfg["workdir"]
    os.makedirs(workdir, exist_ok=True)
    receipts = ReceiptLog(workdir)
    wm = Watermark(os.path.join(workdir, "watermark.json"))

    if args.table == "reconcile":
        if args.mode != "real":
            raise SystemExit("reconcile 只允许 --mode real（幂等补推，避免 mock 假统计）")
        spec = args.reconcile_date or "yesterday"
        out = run_reconcile(cfg, receipts, wm, spec)
        print(json.dumps(out, ensure_ascii=False))
        json.dump(out, open(os.path.join(workdir, "last-run-reconcile.json"),
                            "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return

    names = ROLL_OUT_ORDER if args.table == "all" else args.table.split(",")
    for n in names:
        if n not in TABLES:
            raise SystemExit("未知表: %s" % n)
    # P3 定向重放 manifest：校验 → dry-run/apply
    replay_ids = None
    force_resend = False
    if args.replay_manifest:
        import json as _json
        mf = _json.load(open(args.replay_manifest, encoding="utf-8"))
        if mf.get("table") != (args.table if args.table != "all" else args.table):
            pass  # table 在 --table 参数侧校验
        exp_table = mf.get("table")
        if exp_table != args.table:
            raise SystemExit("manifest.table(%s) 与 --table(%s) 不一致" % (exp_table, args.table))
        if not args.apply:
            raise SystemExit("dry-run：加 --apply 才会真实执行；本次仅校验通过 "
                             "(ids=%d, force=%s)" % (len(mf.get("ids", [])), mf.get("force", False)))
        exp_src = mf.get("expected_source_time")
        if exp_src and args.since and not args.since.startswith(exp_src[:10]):
            raise SystemExit("manifest.expected_source_time(%s) 与 --since(%s) 不一致"
                             % (exp_src, args.since))
        replay_ids = set(mf.get("ids", []))
        force_resend = bool(mf.get("force"))
        print(json.dumps({"replay": True, "table": exp_table, "ids": len(replay_ids),
                          "force": force_resend}, ensure_ascii=False))
    # 子表排到其主表之后（exam_item 依赖 exam）
    order = {n: i for i, n in enumerate(ROLL_OUT_ORDER)}
    dep_rank = {n: order.get(TABLES[n].get("depends_on"), -1) for n in names}
    names = sorted(names, key=lambda n: (dep_rank[n], order.get(n, 99)))
    all_stats = []
    parent_ops = {}
    parent_accept = {}                           # P2 父受理门禁：{父id: True}（本进程真实受理证据）
    for n in names:
        try:
            s = run_table(n, cfg, args.mode, receipts, wm, args.limit, parent_ops,
                          since_override=args.since, parent_accept=parent_accept,
                          replay_ids=replay_ids, force_resend=force_resend)
        except Exception as e:                     # 单表失败不中断其余表（2026-11-03 加固）
            s = {"table": n, "error": str(e)[:300]}
        # P2：本表真实受理后，把其 id 登记为父受理证据（子表 depends_on 本表时门禁核对用）。
        # 受理证据来源=本班次回执文件 true 记录（幂等跳过不算受理，不登记）。
        for rid in receipts.accepted_ids(n, receipts.path):
            parent_accept["%s/%s" % (n, rid)] = True
            parent_accept[str(rid)] = True
        all_stats.append(s)
        print(json.dumps(s, ensure_ascii=False))
    json.dump(all_stats, open(os.path.join(
        workdir, "last-run-%s.json" % args.mode), "w", encoding="utf-8"),
        ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
