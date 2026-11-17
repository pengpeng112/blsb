"""传染病前置软件推送器（独立部署，stdlib + oracledb + urllib，无第三方 Web 框架）。

用法：
  python pusher.py --config config.json --table death --mode mock     # 演练（默认，零真实副作用）
  python pusher.py --config config.json --table death --mode real    # 真实推送（需配置 enabled=true）
  python pusher.py --config config.json --table all --mode mock
  python pusher.py --config config.json --table all --mode real --since "2026-10-30 00:00:00"  # 显式窗补推（忽略水位，幂等）
  python pusher.py --config config.json --table reconcile --mode real --reconcile-date yesterday  # 日终对账（T+0 方案二）
  python pusher.py --config config.json --table ex_lab --mode real --replay-manifest m.json [--apply]  # 定向重放

安全约定（对齐 AGENTS/multi-review 终报告 + PT-01 revision-2 R2~R4）：
- 目标主机白名单：仅 http://<frontend-host>（配置外的 URL 直接拒绝）；
- MOCK 默认：real 模式必须同时满足 config.enabled=true 且 --mode real；
- 幂等：同 id 重复 POST = 前置机按 ID 更新（规范 3.x）；
- 发送次序保护：本地 sent.jsonl 记录 id→operation_time+内容摘要（digest），
  非更新（时间不更新）则跳过；
- 父受理门禁（R2/F04/F05）：ParentAcceptanceTracker 统一供 main/单表/reconcile/
  manifest 重放共用——命名空间键 "父表:父ID"（裸 ID 跨表共用彻底废除）；
  本班次父失败（client-validate/02/03/04/01/99/网络耗尽/行异常）→ FAILED_CURRENT
  严格阻断子项，绝不被 sent.jsonl 旧成功或 Mock 回执绕过；同版本历史父跨日可复用；
- 持久等待队列（R3/F06）：PendingWaitStore（pending-wait.json，仅表名/ID/父ID/
  源时间/原因，零患者隐私）；常规增量扫描前先按 ID 定向重查待办，等待项越过
  watermark-overlap 与重启后仍 100% 自动补回；台账写失败按失败处理不推水位；
- 状态文件原子写：watermark/pending-wait/last-run/reconcile/manifest-state 一律
  同目录临时文件 + fsync + os.replace（杜绝进程被杀产生空文件全量重扫）；
- 严格重放（R4/F07）：manifest schema（manifest_id/table/target/source_instance/
  expires_at/max_count/重复 ID）在任何执行分支前校验；逐行 expected_source_time/
  expected_digest 与源视图核对（不依赖 --since）；缺失目标明确报错非零退出；
  manifest-state-<id>.json 持久逐项进度，同清单二跑对已成功项零重复 HTTP；
  未加 --apply 时 100% 零副作用（零 HTTP、零状态改写）；
- 增量：水位 + overlap_days 重叠回扫 + full_days 周期全量对账（三种组合，F9 共识）；
- 回执逐条 JSONL 落盘（receipts-<date>.jsonl），失败分类：网络/01/99 重试，02/03/04 记录不重试；
- 病程截断统计：字段长度==2000 记 truncated（GBK 视图层上限，仅本地统计不外发）；
- 上报语义（2026-11-07，手册整改§三-2）：payload operationTime=接口上传时刻；内部水位/
  去重/sent.jsonl/对账仍用源数据时间（op_time），两者解耦；内容摘要（digest）在
  operationTime 替换前计算，上传时刻不参与版本身份；
- 取数：fetch_rows 流式 fetchmany(2000) 分批（防百万行 fetchall 内存暴涨）；
  fetch_rows_by_ids 参数化 IN 定向查询（每批 ≤500）。

运行环境：Oracle 11g（ODS）必须 oracledb thick 模式 + instantclient（部署说明见 README）。
"""
import argparse
import datetime as dt
import hashlib
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
MANIFEST_DEFAULT_MAX_COUNT = 5000       # R4：重放清单数量上限
PENDING_REFETCH_BATCH = 500             # R3：待办定向重查每批 ID 数
FETCHMANY_SIZE = 2000                   # R3：流式取数批大小（防 fetchall OOM）


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


def atomic_write_json(path, data):
    """R3：状态文件原子写——同目录临时文件 + flush + fsync + os.replace。

    杜绝进程被杀导致 watermark.json 等变成空/半文件（否则 180 天全量重扫）。
    适用于 watermark/pending-wait/last-run/reconcile/manifest-state 全部本地 JSON。"""
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def payload_version_digest(payload):
    """R2：规范化父内容版本摘要——上传时刻替换前的源报文 dict，
    json.dumps(sort_keys=True, ensure_ascii=False) 的 sha256 前 32 位。
    动态上传时刻（operationTime 重写）与传输键不参与版本身份。"""
    core = {k: payload[k] for k in payload if k != "__endpoint"}
    return hashlib.sha256(
        json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]


class ReceiptLog:
    def __init__(self, directory):
        os.makedirs(directory, exist_ok=True)
        self.path = os.path.join(
            directory, "receipts-%s.jsonl" % dt.date.today().strftime("%Y%m%d"))
        self.sent_path = os.path.join(directory, "sent.jsonl")
        self._sent = None

    def receipt(self, table, rec):
        # 严禁把患者姓名/证件号等 payload 明文写入回执——只允许 id/opTime/digest/
        # 结果码/阶段与错误描述；op_time/digest 由调用方按需附带（R2）。
        rec = dict(rec, table=table, ts=now_str(), mode=rec.get("mode", ""))
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

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

    def sent_versions(self):
        """R2：'table/id' -> {"op_time":…, "digest":…}（sent.jsonl 每键最后一条）。

        历史持久层证据源：父表最后一次成功发送的源时间+内容摘要（跨日有效父复用，
        revision-2 F05）；无 digest 的历史旧行 digest=""（LEGACY，不影响放行语义，
        摘要仅用于审计与 manifest 漂移核对）。"""
        out = {}
        if not os.path.exists(self.sent_path):
            return out
        with open(self.sent_path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    key = r["id"] if "/" in str(r["id"]) \
                        else "%s/%s" % (r.get("table", "?"), r["id"])
                    old = out.get(key)
                    if old is None or r.get("opTime", "") >= old["op_time"]:
                        out[key] = {"op_time": r.get("opTime", ""),
                                    "digest": r.get("digest", "")}
                except (ValueError, KeyError):
                    continue
        return out

    def mark_sent(self, table, rid, op_time, digest=""):
        with open(self.sent_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"id": "%s/%s" % (table, rid), "opTime": op_time or "",
                                "digest": digest or "", "table": table, "ts": now_str()},
                               ensure_ascii=False) + "\n")
        self.sent_state()["%s/%s" % (table, rid)] = op_time or ""


class Watermark:
    """每表水位（上次成功推进到的 operation_time）+ overlap 回扫窗。
    R3：advance 改原子写（临时文件 + fsync + os.replace）。"""

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
            atomic_write_json(self.path, self.state)

    def force_full(self, table, days, today=None):
        """周期性全量对账窗（full_days 到期或首次运行）。"""
        t = (today or dt.datetime.now()) - dt.timedelta(days=days)
        return t.strftime(DATE_FMT)


class ParentAcceptanceTracker:
    """R2/F04/F05：统一父版本状态解析器（main / 单表 / reconcile / manifest 重放共用）。

    证据分层（优先级从高到低）：
    1. session_outcomes——本进程/本班次对父表记录的处置结果：
       ACCEPTED（真实发送成功）/ ACCEPTED_HISTORICAL（幂等跳过=同版本历史已受理）/
       FAILED_CURRENT（本班次 client-validate 缺必填、02/03/04 拒绝、01/99/网络
       重试耗尽、行异常）——FAILED_CURRENT 必须阻断子项，绝不向下层放行；
    2. 当日回执层——构造时快照 receipts-YYYYMMDD.jsonl：同一 (父表, 父ID) 最新一条
       非 mock 回执 result=False → 视同 FAILED_CURRENT（早先单跑父表刚失败，
       紧接着单跑子表同样不放行）；result=True → 当日真实受理证据；
    3. 外部显式放行（merge_external_accept，兼容旧 parent_accept 入参/审计脚本）；
    4. 历史持久层——sent.jsonl 存在该父表该 ID 的成功发送记录（跨日有效父复用）；
    5. 以上皆无 → 父键为空/父无受理证据 → 等待。

    mock 回执永不作为 real 模式放行子表的受理证据；命名空间键 "父表:父ID"
    彻底废除裸 ID 跨表共用（其他表同 ID 不能放行本表父）。"""

    def __init__(self, receipts):
        self.receipts = receipts
        self.session = {}          # pkey -> {"status","op_time","digest"}
        self._ext_bare = set()     # 兼容层：外部传入的裸 ID 放行
        self.today = {}            # (table, id) -> 最新非 mock 回执 result(bool)
        path = getattr(receipts, "path", None)
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("mode") == "mock":
                        continue
                    if r.get("table") and r.get("id") is not None:
                        self.today[(str(r.get("table")), str(r.get("id")))] = \
                            r.get("result") is True
        self._hist_cache = {}      # parent_table -> {pid: {"op_time","digest"}}

    # ---- 历史持久层 ----
    def _hist(self, parent_table):
        if parent_table not in self._hist_cache:
            versions = {}
            fn = getattr(self.receipts, "sent_versions", None)
            if callable(fn):
                versions = fn()
            prefix = parent_table + "/"
            out = {}
            for key, v in versions.items():
                if key.startswith(prefix):
                    out[key[len(prefix):]] = v
                elif "/" not in key:
                    # 旧格式裸 id 行缺表名——无法证明属于本父表，不入证据（F04：跨表同ID隔离）
                    pass
            self._hist_cache[parent_table] = out
        return self._hist_cache[parent_table]

    # ---- session 登记（run_table 行处理钩子调用）----
    def mark_accepted(self, parent_table, pid, op_time="", digest=""):
        if pid in (None, ""):
            return
        self.session["%s:%s" % (parent_table, pid)] = {
            "status": "ACCEPTED", "op_time": op_time or "", "digest": digest or ""}

    def mark_accepted_historical(self, parent_table, pid, op_time=""):
        if pid in (None, ""):
            return
        self.session["%s:%s" % (parent_table, pid)] = {
            "status": "ACCEPTED_HISTORICAL", "op_time": op_time or "", "digest": ""}

    def mark_failed_current(self, parent_table, pid, op_time="", reason=""):
        if pid in (None, ""):
            return
        pkey = "%s:%s" % (parent_table, pid)
        # FAILED_CURRENT 终局性：本班次已知失败不被后续任何登记降级覆盖
        self.session[pkey] = {"status": "FAILED_CURRENT",
                              "op_time": op_time or "", "digest": "", "reason": reason}

    # ---- 兼容层（旧 parent_accept 入参：裸 ID / "table/id" / "table:id" → 放行）----
    def merge_external_accept(self, mapping):
        if not mapping:
            return
        for k, v in (mapping or {}).items():
            if not v:
                continue
            k = str(k)
            if ":" in k:
                self.session[k] = {"status": "ACCEPTED", "op_time": "",
                                   "digest": "", "external": True}
            elif "/" in k:
                self.session[k.replace("/", ":", 1)] = {
                    "status": "ACCEPTED", "op_time": "", "digest": "",
                    "external": True}
            else:
                self._ext_bare.add(k)

    # ---- 子表门禁判定 ----
    def is_parent_ready(self, parent_table, pid):
        """→ (ready: bool, reason: str)。FAILED_CURRENT 优先于一切历史成功。"""
        pid = str(pid or "")
        if not pid:
            return False, "父键为空"
        pkey = "%s:%s" % (parent_table, pid)
        cur = self.session.get(pkey)
        if cur:
            if cur["status"] == "FAILED_CURRENT":
                return False, "当前父版本未受理(本班次失败)"
            return True, cur["status"]
        if pkey in self._ext_bare or pid in self._ext_bare:
            return True, "external-accept"
        latest = self.today.get((parent_table, pid))
        if latest is False:
            return False, "当前父版本未受理(当日回执失败)"
        if latest is True:
            return True, "ACCEPTED_TODAY_RECEIPT"
        hist = self._hist(parent_table)
        if pid in hist:
            return True, "ACCEPTED_HISTORICAL"
        return False, "父无受理证据"


class PendingWaitStore:
    """R3/F06：持久化等待队列（<workdir>/pending-wait.json）。

    结构：{"version": 1, "items": {"<table>/<id>": {"table","id","parent_table",
    "parent_id","first_seen_at","last_checked_at","op_time","attempts","reason"}}}。
    只存表名/记录ID/父ID/源时间/原因，零患者隐私字段。
    写入即时原子落盘：即使后续较新记录推进 watermark 越过 overlap_days，
    等待项仍留在本文件中跨重启自动恢复，永不丢项。"""

    def __init__(self, path):
        self.path = path
        self.state = {"version": 1, "items": {}}
        if path and os.path.exists(path):
            try:
                loaded = json.load(open(path, encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("items"), dict):
                    self.state = loaded
            except ValueError:
                self.state = {"version": 1, "items": {}}

    def list_for_table(self, table):
        return [dict(v, key=k) for k, v in self.state["items"].items()
                if v.get("table") == table]

    def upsert(self, table, rid, parent_table, parent_id, op_time,
               reason="parent-wait"):
        key = "%s/%s" % (table, rid)
        it = self.state["items"].get(key)
        if it is None:
            it = {"table": table, "id": str(rid), "first_seen_at": now_str(),
                  "attempts": 0}
        it.update({"parent_table": parent_table, "parent_id": str(parent_id or ""),
                   "op_time": op_time or "", "last_checked_at": now_str(),
                   "reason": reason, "attempts": int(it.get("attempts", 0)) + 1})
        self.state["items"][key] = it
        self.save()                       # 台账写失败按异常上抛 → 调用方计失败不推水位

    def remove(self, table, rid):
        key = "%s/%s" % (table, rid)
        if key in self.state["items"]:
            self.state["items"].pop(key)
            self.save()

    def save(self):
        if self.path:
            atomic_write_json(self.path, self.state)


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
    """流式取数（R3）：cur.arraysize=2000 + fetchmany(2000) 分批生成器，
    防百万行 fetchall 一次装入内存（打桩场景直接返回 list[dict]，迭代兼容）。

    返回行迭代器（dict/行）；真实路径为惰性生成器——凭据缺失/连接异常在
    开始迭代时按既有语义报错（SystemExit / DPI 异常上抛）。"""
    import oracledb  # 部署主机需安装；本函数仅在非 mock 时调用
    _init_client()
    missing = [v for v in (dsn_env_user, dsn_env_pwd, dsn_env_dsn)
               if not v or not os.environ.get(v)]
    if missing:
        raise SystemExit("缺少 ODS 凭据环境变量 %s——请由 DBA 写入 inf.env（见 README/部署说明）"
                         % ",".join(str(v) for v in missing))
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
        cur.arraysize = FETCHMANY_SIZE
        since_dt = (dt.datetime.strptime(since, DATE_FMT)
                    if isinstance(since, str) else since)
        cur.execute(sql, since=since_dt, **({"lim": limit} if limit else {}))
        cols = [c[0] for c in cur.description]
        fetchmany = getattr(cur, "fetchmany", None)
        if fetchmany is None:              # 极旧打桩仅提供 fetchall（防御兼容）
            for r in cur.fetchall():
                yield dict(zip(cols, r))
            return
        while True:
            batch = fetchmany(FETCHMANY_SIZE)
            if not batch:
                break
            for r in batch:
                yield dict(zip(cols, r))


def _fetch_rows_by_ids_oracle(dsn_env_user, dsn_env_pwd, dsn_env_dsn,
                              view, id_col, ids):
    """R3/R4：参数化 IN 定向查询（每批 ≤500，绑定变量，无拼接注入）。"""
    import oracledb
    _init_client()
    missing = [v for v in (dsn_env_user, dsn_env_pwd, dsn_env_dsn)
               if not v or not os.environ.get(v)]
    if missing:
        raise SystemExit("缺少 ODS 凭据环境变量 %s——请由 DBA 写入 inf.env（见 README/部署说明）"
                         % ",".join(str(v) for v in missing))
    user = os.environ[dsn_env_user]
    pwd = os.environ[dsn_env_pwd]
    dsn = os.environ[dsn_env_dsn]
    out = []
    with oracledb.connect(user=user, password=pwd, dsn=dsn,
                          **ODS_CONNECT_KW) as conn:
        conn.call_timeout = ODS_CALL_TIMEOUT_MS
        cur = conn.cursor()
        for i in range(0, len(ids), PENDING_REFETCH_BATCH):
            batch = [str(x) for x in ids[i:i + PENDING_REFETCH_BATCH]]
            ph = ",".join(":b%d" % j for j in range(len(batch)))
            sql = ("SELECT * FROM %s WHERE %s IN (%s)" % (view, id_col, ph))
            cur.execute(sql, {"b%d" % j: v for j, v in enumerate(batch)})
            cols = [c[0] for c in cur.description]
            for r in cur.fetchall():       # 定向查询行数有限（≤500/批）
                out.append(dict(zip(cols, r)))
    return out


def fetch_rows_by_ids(dsn_env_user, dsn_env_pwd, dsn_env_dsn,
                      view, id_col, ids):
    """R3/R4：按 ID 定向捞回最新行快照。

    真实路径=参数化 IN 批查询；无 oracledb 驱动或凭据未配置（离线/测试打桩环境）
    时回退模块级 fetch_rows 宽窗取回后按 ID 过滤（上限 MANIFEST_DEFAULT_MAX_COUNT
    行防失控全量），保持"仅打桩 fetch_rows"的既有测试 100% 兼容。"""
    if not ids:
        return []
    ready = False
    if dsn_env_user and dsn_env_pwd and dsn_env_dsn:
        try:
            import oracledb  # noqa: F401
            ready = all(os.environ.get(k) for k in
                        (dsn_env_user, dsn_env_pwd, dsn_env_dsn))
        except ImportError:
            ready = False
    if ready:
        try:
            return _fetch_rows_by_ids_oracle(dsn_env_user, dsn_env_pwd,
                                             dsn_env_dsn, view, id_col, ids)
        except SystemExit:
            raise                       # 凭据缺失属部署错误，明确失败
        except Exception:
            pass                        # 连接异常 → 回退宽窗（fetch_rows 同样会失败并按既有语义报错）
    wide = fetch_rows(dsn_env_user, dsn_env_pwd, dsn_env_dsn, view,
                      "1900-01-01 00:00:00", MANIFEST_DEFAULT_MAX_COUNT)
    idset = {str(i) for i in ids}
    return [r for r in wide if str(r.get(id_col)) in idset]


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


def _fmt_row_op_time(row):
    """行内 OPERATION_TIME → 规范字符串（源时间，用于 manifest 源时间核对）。"""
    v = _row_op_time(row)
    return v.strftime(DATE_FMT) if v else str(row.get("OPERATION_TIME") or "")


def run_table(name, cfg, mode, receipts, wm, limit=None, parent_ops=None,
              since_override=None, window_end=None, parent_accept=None,
              replay_ids=None, force_resend=False,
              tracker=None, pending_store=None, manifest_state=None,
              rows_override=None):
    """单表扫描+发送。前 7 个位置参数与历史签名完全兼容。

    R2：tracker（未传则自建）统一父门禁；parent_accept 仅作外部显式放行
    兼容层并入 tracker（不再是死参数，也不再是唯一证据源）。
    R3：常规增量路径（mode=real、since_override=None、非重放）先按 ID 定向
    重查 PendingWaitStore 待办再扫窗口；parent_wait 即时持久化，越过水位/
    重启后自动补回；全部状态 JSON 原子写。
    R4：manifest_state 提供时逐行持久执行进度（SUCCEEDED/FAILED/PENDING）。
    rows_override：定向行列表（重放分支），跳过窗口取数与待办消费。
    """
    t = TABLES[name]
    url = "%s/hclient/emr/receive/%s" % (cfg["base_url"], t["endpoint"])
    check_url(url)
    env = cfg.get("env", {})
    workdir = cfg.get("workdir")
    if tracker is None:
        tracker = ParentAcceptanceTracker(receipts)
    if parent_accept:
        tracker.merge_external_accept(parent_accept)
    if pending_store is None and workdir:
        pending_store = PendingWaitStore(os.path.join(workdir, "pending-wait.json"))

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

    stats = dict(table=name, since=since, reason=reason, rows=0,
                 sent=0, skipped_same=0, skipped_invalid=0, failed=0,
                 truncated=0, parent_wait=0, recovered_from_wait=0,
                 wait_expired=0)
    sent_state = receipts.sent_state()
    parent_fk = t.get("parent_fk")
    parent_tbl = t.get("depends_on")

    def _save_manifest_state(rid, status, extra=None):
        if manifest_state is None:
            return
        it = {"status": status, "updated_at": now_str()}
        if extra:
            it.update(extra)
        manifest_state["items"][str(rid)] = it

    def _process_row(row):
        """单行完整处理（camel→垫片→幂等→父门禁→必填→截断→发送→记账）。
        返回动作字符串；所有真实处置同步登记 tracker 与 manifest_state。"""
        payload = camel(t["map"], row)
        apply_view_shims(name, payload, parent_ops if parent_ops is not None else {})
        op_time = payload.get("operationTime", "")
        rid = payload.get("id")
        skey = "%s/%s" % (name, rid)
        if replay_ids is not None and rid not in replay_ids:
            stats["skipped_replay_filter"] = stats.get("skipped_replay_filter", 0) + 1
            return "replay_filtered"
        if (sent_state.get(skey) and sent_state[skey] >= op_time
                and not (force_resend and replay_ids is not None and rid in replay_ids)):
            stats["skipped_same"] += 1
            # 同版本历史已受理（源时间未变且 sent.jsonl 有账）→ 父证据可跨日复用（F05）；
            # 主表自身（无 parent_fk）同样登记——其子表门禁依赖该证据
            if mode == "real":
                tracker.mark_accepted_historical(name, rid, op_time)
                if pending_store:
                    try:
                        pending_store.remove(name, rid)   # 历史已受理的旧等待项一并出队
                    except OSError:
                        pass
            return "skipped_same"
        # R2 父受理门禁：命名空间键 "父表:父ID"；本班次父失败严格阻断，
        # 不被 sent.jsonl 旧成功或其他表同 ID 绕过
        if parent_fk:
            pval = str(payload.get(parent_fk, "") or "")
            if not pval:
                stats["parent_wait"] += 1
                receipts.receipt(name, {"id": rid, "result": False,
                                        "stage": "parent-wait",
                                        "desc": "父键为空:%s" % parent_fk})
                return "parent_wait_empty_fk"
            ready, why = tracker.is_parent_ready(parent_tbl, pval)
            if not ready:
                stats["parent_wait"] += 1
                receipts.receipt(name, {"id": rid, "result": False,
                                        "stage": "parent-wait",
                                        "desc": "父未受理:%s=%s(%s)" % (parent_fk, pval, why)})
                if mode == "real" and pending_store:
                    # 等待项先持久化（原子写）——即使后续较新记录推进水位越过
                    # overlap，本项也 100% 留在队列中跨重启自动恢复；台账写失败
                    # 按失败处理，不得让水位静默越过未持久化等待项
                    try:
                        pending_store.upsert(name, rid, parent_tbl, pval, op_time)
                    except OSError as e:
                        stats["failed"] += 1
                        receipts.receipt(name, {"id": rid, "result": False,
                                                "stage": "wait-persist-failed",
                                                "desc": str(e)[:200]})
                return "parent_wait"
        missing = validate_required(payload, t["required"])
        if missing:
            stats["skipped_invalid"] += 1
            receipts.receipt(name, {"id": rid, "result": False,
                                    "stage": "client-validate",
                                    "desc": "缺必填:" + ",".join(missing)})
            if mode == "real":    # 父表自身缺必填=当前版本不可受理 → FAILED_CURRENT
                tracker.mark_failed_current(name, rid, op_time, "client-validate")
            return "skipped_invalid"
        for f, cap in t.get("truncation_fields", {}).items():
            if payload.get(f) and len(payload[f]) >= cap:
                stats["truncated"] += 1
        if mode == "mock":
            # 演练只记回执与统计：不推水位、不记已发、不登记父受理（Mock 永不作 real 证据）
            receipts.receipt(name, {"id": rid, "result": True,
                                    "stage": "mock", "desc": "MOCK 未发送",
                                    "mode": "mock"})
            stats["sent"] += 1
            return "mock_sent"
        digest = payload_version_digest(payload)   # 上传时刻替换前计算（R2）
        payload["operationTime"] = dt.datetime.now(CST).strftime(DATE_FMT)   # 上报语义=接口上传时刻（北京时间）；水位/去重/对账仍用 op_time（源时间）
        try:
            for attempt in range(3):
                try:
                    resp = post_json(url, payload)
                    ok = resp.get("result") is True
                    receipts.receipt(name, {"id": rid, "result": ok,
                                            "errorCode": resp.get("errorCode"),
                                            "desc": resp.get("desc"),
                                            "attempt": attempt,
                                            "opTime": op_time, "digest": digest})
                    if ok:
                        stats["sent"] += 1
                        wm.advance(name, op_time)
                        try:
                            receipts.mark_sent(name, rid, op_time, digest)
                        except TypeError:          # 旧打桩 mark_sent(table,rid,op)
                            receipts.mark_sent(name, rid, op_time)
                        tracker.mark_accepted(name, rid, op_time, digest)
                        if pending_store:
                            try:
                                pending_store.remove(name, rid)
                            except OSError:
                                pass
                        _save_manifest_state(rid, "SUCCEEDED",
                                             {"op_time": op_time, "digest": digest})
                        return "sent"
                    code = resp.get("errorCode")
                    if code in ("01", "99"):   # 服务端异常：重试
                        time.sleep(2 * (attempt + 1))
                        continue
                    stats["failed"] += 1        # 02/03/04：不重试，人工看
                    tracker.mark_failed_current(name, rid, op_time,
                                                "reject-%s" % code)
                    _save_manifest_state(rid, "FAILED", {"error": str(code)})
                    return "failed"
                except (urllib.error.URLError, OSError) as e:
                    receipts.receipt(name, {"id": rid, "result": False,
                                            "stage": "network", "desc": str(e),
                                            "attempt": attempt,
                                            "opTime": op_time, "digest": digest})
                    time.sleep(2 * (attempt + 1))
            stats["failed"] += 1                # 01/99/网络重试耗尽
            tracker.mark_failed_current(name, rid, op_time, "retry-exhausted")
            _save_manifest_state(rid, "FAILED", {"error": "retry-exhausted"})
            return "failed"
        except Exception as e:                   # 行自身异常（编解码/未知）按失败处理
            stats["failed"] += 1
            receipts.receipt(name, {"id": rid, "result": False,
                                    "stage": "row-error",
                                    "desc": str(e)[:200]})
            tracker.mark_failed_current(name, rid, op_time, "row-error")
            _save_manifest_state(rid, "FAILED", {"error": str(e)[:200]})
            return "failed"

    # ---- R3：先消费持久等待待办（仅常规增量路径：real、无 since_override、非重放）----
    seen_ids = set()
    pending_items = []
    if (mode == "real" and since_override is None and replay_ids is None
            and rows_override is None and pending_store is not None):
        pending_items = pending_store.list_for_table(name)
        pending_ids = [it["id"] for it in pending_items]
        if pending_ids:
            try:
                extra_rows = fetch_rows_by_ids(env.get("user"), env.get("pwd"),
                                               env.get("dsn"), t["view"], "ID",
                                               pending_ids)
            except Exception as e:               # 待办重查失败不中断主扫描，保留待办下次再试
                extra_rows = []
                stats["wait_refetch_error"] = str(e)[:200]
            fetched = {str(r.get("ID")): r for r in extra_rows}
            pending_by_id = {it["id"]: it for it in pending_items}
            for pid in pending_ids:
                if pid not in fetched:
                    # 源视图中已不存在（如源端作废删除）→ 移入 expired 统计，不死锁
                    stats["wait_expired"] += 1
                    receipts.receipt(name, {"id": pid, "result": False,
                                            "stage": "wait-expired",
                                            "desc": "源视图已无此行"})
                    pending_store.remove(name, pid)
                    continue
                seen_ids.add(pid)
                action = _process_row(fetched[pid])
                if action == "sent":
                    stats["recovered_from_wait"] += 1     # 父恢复后自动补回成功
                elif action == "parent_wait":
                    pass                                  # 已在 _process_row 内更新待办

    # ---- 窗口流式扫描 ----
    if rows_override is not None:
        window_rows_iter = iter(rows_override)
    else:
        rows_src = fetch_rows(env.get("user"), env.get("pwd"), env.get("dsn"),
                              t["view"], since, limit)
        window_rows_iter = rows_src if isinstance(rows_src, (list, tuple)) \
            else rows_src
    window_rows = 0
    for row in window_rows_iter:
        rid_src = str(row.get("ID"))
        if rid_src in seen_ids:                  # 已在待办重试中处理（幂等去重）
            stats["skipped_dup_window"] = stats.get("skipped_dup_window", 0) + 1
            continue
        seen_ids.add(rid_src)
        window_rows += 1
        if window_end and _row_op_time(row) is not None \
                and _row_op_time(row) < (window_end if isinstance(window_end, dt.datetime)
                                         else dt.datetime.strptime(window_end, DATE_FMT)):
            stats["window_rows"] = stats.get("window_rows", 0) + 1
        try:
            _process_row(row)
        except Exception as e:                   # 行级异常隔离：单行崩溃不中断整表
            stats["failed"] += 1
            receipts.receipt(name, {"id": row.get("ID"), "result": False,
                                    "stage": "row-error", "desc": str(e)[:200]})
    stats["rows"] = window_rows

    if manifest_state is not None:
        manifest_state["updated_at"] = now_str()
        atomic_write_json(manifest_state["_path"], manifest_state)
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


def run_reconcile(cfg, receipts, wm, spec, today=None,
                  tracker=None, pending_store=None):
    """对指定日做幂等补推对账：--since D 00:00:00 真实推送（已推记录靠 sent.jsonl 跳过）。
    产出 workdir/reconcile-<D>.json（原子写）；恒等式含 recovered_wait/parent_wait/
    skipped_invalid，total_failed=0 不再掩盖未完成子项；parent_wait>0 写
    RECONCILE-PARENT-WAIT 告警行；N>0 追加 GAP-RECOVERED 到 cron.log 并写 ALERT 文件。"""
    since, until, day_str = resolve_reconcile_day(spec, today)
    out = {"day": day_str, "since": since, "until": until,
           "ran_at": now_str(), "tables": {}}
    if tracker is None:
        tracker = ParentAcceptanceTracker(receipts)   # R2：reconcile 与 main 同一门禁
    if pending_store is None and cfg.get("workdir"):
        pending_store = PendingWaitStore(os.path.join(cfg["workdir"],
                                                      "pending-wait.json"))
    total_repushed = total_failed = total_parent_wait = total_skipped_invalid = 0
    parent_ops = {}
    for n in ROLL_OUT_ORDER:                       # 与常规班次相同的开闸顺序
        try:
            s = run_table(n, cfg, "real", receipts, wm, None, parent_ops,
                          since_override=since, window_end=until,
                          tracker=tracker, pending_store=pending_store)
        except Exception as e:                     # 单表取数/网络异常不中断对账（2026-11-03 加固）
            s = {"table": n, "error": str(e)[:300], "sent": 0, "failed": 1,
                 "skipped_same": 0, "skipped_invalid": 0, "rows": 0,
                 "parent_wait": 0, "recovered_from_wait": 0,
                 "since": since, "reason": "manual-override"}
        out["tables"][n] = {"window_rows": s.get("window_rows", 0),
                            "repushed": s["sent"],
                            "recovered_wait": s.get("recovered_from_wait", 0),
                            "parent_wait": s.get("parent_wait", 0),
                            "skipped_invalid": s.get("skipped_invalid", 0),
                            "failed": s["failed"]}
        total_repushed += s["sent"]
        total_failed += s["failed"]
        total_parent_wait += s.get("parent_wait", 0)
        total_skipped_invalid += s.get("skipped_invalid", 0)
    out["total_repushed"] = total_repushed
    out["total_failed"] = total_failed
    out["total_parent_wait"] = total_parent_wait
    out["total_skipped_invalid"] = total_skipped_invalid
    atomic_write_json(os.path.join(cfg["workdir"], "reconcile-%s.json" % day_str), out)
    if total_repushed > 0 or total_failed > 0 or total_parent_wait > 0:
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
                if v["parent_wait"] > 0:
                    f.write("%s RECONCILE-PARENT-WAIT table=%s day=%s count=%d\n"
                            % (stamp, n, day_str, v["parent_wait"]))
                if v["skipped_invalid"] > 0:
                    f.write("%s RECONCILE-INVALID table=%s day=%s count=%d\n"
                            % (stamp, n, day_str, v["skipped_invalid"]))
        if total_repushed > 0:
            try:
                with open(os.path.join(cfg["workdir"], "cron.log"), "a",
                          encoding="utf-8") as f:
                    f.write("[%s] GAP-RECOVERED day=%s repushed=%d failed=%d parent_wait=%d\n"
                            % (now_str(), day_str, total_repushed, total_failed,
                               total_parent_wait))
            except OSError as e:
                print("WARN: GAP 行未能写入 cron.log（%s）——见 ALERT 文件" % e)
    return out


# ---------------------------------------------------------------- R4：严格重放 manifest
def _manifest_reject(msg):
    print("[manifest 拒绝] %s" % msg, file=sys.stderr)
    raise SystemExit(2)


def _parse_expires(v):
    """expires_at 宽松解析：'YYYY-MM-DDTHH:MM:SS(Z|+08:00)' 或 DATE_FMT。"""
    s = str(v).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", DATE_FMT, "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s.replace("Z", "+0000"), fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d
        except ValueError:
            continue
    return None


def validate_and_load_manifest(path, cfg, args_table):
    """R4/F07：严格 manifest schema 校验（在任何执行分支/HTTP/状态改写之前）。

    必填/校验项：table（与 --table 完全一致且不允许 all）、target（默认
    <frontend-host>；提供则必须在允许清单）、source_instance（与 config 均存在
    时须一致）、expires_at（过期立即拒绝）、max_count（正整数，默认 5000；
    空清单/超量/重复 ID 拒绝）、items[{id,expected_source_time,expected_digest}]
    或 ids[...]（顶层 expected_* 作为缺省）。manifest_id 可选（未提供时按核心
    字段派生稳定 auto id，用于 manifest-state 持久进度文件名）。"""
    try:
        mf = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        _manifest_reject("manifest 无法读取/解析: %s" % e)
    if not isinstance(mf, dict):
        _manifest_reject("manifest 顶层必须是 JSON 对象")

    exp_table = mf.get("table")
    if not exp_table or not isinstance(exp_table, str):
        _manifest_reject("manifest.table 必填（非空字符串）")
    if args_table == "all":
        _manifest_reject("--table all 不允许与 --replay-manifest 组合（重放必须单表定向）")
    if exp_table != args_table:
        _manifest_reject("manifest.table(%s) 与 --table(%s) 不一致"
                         % (exp_table, args_table))
    if exp_table not in TABLES:
        _manifest_reject("manifest.table 未知: %s" % exp_table)

    tgt = mf.get("target")
    if tgt is not None and str(tgt) not in ("inf_cdc_frontend", "<frontend-host>"):
        _manifest_reject("manifest.target(%s) 不在允许清单（inf_cdc_frontend / "
                         "<frontend-host>）" % tgt)
    si = mf.get("source_instance")
    if si is not None and cfg.get("source_instance") \
            and str(si) != str(cfg.get("source_instance")):
        _manifest_reject("manifest.source_instance(%s) 与 config(%s) 不一致"
                         % (si, cfg.get("source_instance")))
    exp = mf.get("expires_at")
    if exp is not None:
        d = _parse_expires(exp)
        if d is None:
            _manifest_reject("manifest.expires_at(%s) 无法解析" % exp)
        if dt.datetime.now(dt.timezone.utc) > d:
            _manifest_reject("manifest expired（expires_at=%s）" % exp)
    mc = mf.get("max_count", MANIFEST_DEFAULT_MAX_COUNT)
    if not isinstance(mc, int) or isinstance(mc, bool) or mc <= 0 or mc > MANIFEST_DEFAULT_MAX_COUNT:
        _manifest_reject("manifest.max_count 必须为 1..%d 正整数（得到 %r）"
                         % (MANIFEST_DEFAULT_MAX_COUNT, mc))

    top_est = mf.get("expected_source_time")
    top_ed = mf.get("expected_digest")
    entries = []
    items = mf.get("items")
    if items is not None:
        if not isinstance(items, list):
            _manifest_reject("manifest.items 必须为数组")
        for it in items:
            if not isinstance(it, dict) or not it.get("id") \
                    or not isinstance(it.get("id"), str):
                _manifest_reject("manifest.items[] 每项需要非空字符串 id")
            entries.append({"id": it["id"],
                            "expected_source_time": it.get("expected_source_time", top_est),
                            "expected_digest": it.get("expected_digest", top_ed)})
    else:
        ids = mf.get("ids")
        if not isinstance(ids, list) or not ids:
            _manifest_reject("manifest 需要非空 items[] 或 ids[]")
        for rid in ids:
            if rid is None or str(rid) == "":
                _manifest_reject("manifest.ids[] 含空 ID")
            entries.append({"id": str(rid),
                            "expected_source_time": top_est,
                            "expected_digest": top_ed})
    if not entries:
        _manifest_reject("manifest 清单为空")
    if len(entries) > mc:
        _manifest_reject("manifest 条目 %d 超出 max_count=%d" % (len(entries), mc))
    seen = [e["id"] for e in entries]
    if len(set(seen)) != len(seen):
        dup = sorted({x for x in seen if seen.count(x) > 1})
        _manifest_reject("manifest 存在重复 ID: %s" % ",".join(dup))

    mid = mf.get("manifest_id")
    if mid is not None:
        if not isinstance(mid, str) or not mid.strip():
            _manifest_reject("manifest_id 提供时必须为非空字符串")
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", mid):
            _manifest_reject("manifest_id 仅允许字母/数字/下划线/连字符（≤64）")
    else:
        core = json.dumps({"table": exp_table, "ids": seen,
                           "force": bool(mf.get("force")),
                           "items": items is not None},
                          sort_keys=True, ensure_ascii=False)
        mid = "auto-" + hashlib.sha256(core.encode("utf-8")).hexdigest()[:16]
    return {"manifest_id": mid, "table": exp_table, "entries": entries,
            "force": bool(mf.get("force")), "max_count": mc, "raw": mf}


def _load_manifest_state(workdir, manifest):
    """R4：manifest-state-<id>.json 持久进度（PENDING/SUCCEEDED/FAILED）。"""
    path = os.path.join(workdir, "manifest-state-%s.json" % manifest["manifest_id"])
    if os.path.exists(path):
        try:
            st = json.load(open(path, encoding="utf-8"))
            if isinstance(st, dict) and isinstance(st.get("items"), dict):
                st.setdefault("manifest_id", manifest["manifest_id"])
                st.setdefault("table", manifest["table"])
                st["_path"] = path
                return st
        except ValueError:
            pass
    return {"manifest_id": manifest["manifest_id"], "table": manifest["table"],
            "items": {}, "_path": path}


def run_replay(manifest, cfg, receipts, wm, tracker, pending_store=None):
    """R4：定向重放执行（--apply）。

    顺序：持久进度过滤（已 SUCCEEDED 直接跳过，零重复 HTTP）→ 按 ID 定向取数 →
    缺失目标明确报错非零退出（绝不静默 sent=0 退出 0）→ 逐行 expected_source_time/
    expected_digest 与源视图核对（VERSION_DRIFT 拒发该行并最终非零退出，不依赖
    --since）→ 经 run_table 发送（force 只绕过 sent.jsonl 旧账，绝不绕过同清单
    完成账与父表受理门禁）。"""
    t = TABLES[manifest["table"]]
    env = cfg.get("env", {})
    mstate = _load_manifest_state(cfg["workdir"], manifest)
    pending_entries = [e for e in manifest["entries"]
                       if mstate["items"].get(e["id"], {}).get("status") != "SUCCEEDED"]
    for e in manifest["entries"]:
        mstate["items"].setdefault(e["id"], {"status": "PENDING"})
    print(json.dumps({"replay": True, "manifest_id": manifest["manifest_id"],
                      "table": manifest["table"],
                      "total": len(manifest["entries"]),
                      "already_succeeded": len(manifest["entries"]) - len(pending_entries),
                      "to_run": len(pending_entries),
                      "force": manifest["force"]}, ensure_ascii=False))
    if not pending_entries:
        atomic_write_json(mstate["_path"], mstate)
        return {"sent": 0, "missing": [], "drift": [], "skipped_done":
                len(manifest["entries"])}

    want_ids = [e["id"] for e in pending_entries]
    rows_by_id = {}
    try:
        got = fetch_rows_by_ids(env.get("user"), env.get("pwd"), env.get("dsn"),
                                t["view"], "ID", want_ids)
    except SystemExit:
        raise
    except Exception as e:
        print("[manifest 拒绝] 源端定向取数失败: %s" % str(e)[:200], file=sys.stderr)
        raise SystemExit(2)
    for r in got:
        rows_by_id[str(r.get("ID"))] = r
    missing = [i for i in want_ids if i not in rows_by_id]
    if missing:
        print("[manifest 拒绝] 清单目标在源视图中缺失（%d 个）: %s"
              % (len(missing), ",".join(str(m) for m in missing)), file=sys.stderr)
        raise SystemExit(2)

    # 逐行版本/摘要核对（不依赖 --since）：漂移行拒绝发送并最终非零退出
    drift = []
    runnable = []
    for e in pending_entries:
        row = rows_by_id[e["id"]]
        if e.get("expected_source_time"):
            actual = _fmt_row_op_time(row)
            if actual != str(e["expected_source_time"]):
                drift.append({"id": e["id"], "kind": "SOURCE_TIME",
                              "expected": e["expected_source_time"],
                              "actual": actual})
                continue
        if e.get("expected_digest"):
            payload = camel(t["map"], row)
            actual_digest = payload_version_digest(payload)
            if actual_digest != str(e["expected_digest"]):
                drift.append({"id": e["id"], "kind": "DIGEST",
                              "expected": e["expected_digest"],
                              "actual": actual_digest})
                continue
        runnable.append(e)
    if drift:
        print("[manifest 拒绝] %d 行版本漂移（VERSION_DRIFT，拒绝发送）: %s"
              % (len(drift), json.dumps(drift, ensure_ascii=False)), file=sys.stderr)
        for d in drift:
            mstate["items"][d["id"]] = {"status": "VERSION_DRIFT",
                                        "updated_at": now_str(),
                                        "detail": d}
        atomic_write_json(mstate["_path"], mstate)
        raise SystemExit(2)

    rows = [rows_by_id[e["id"]] for e in runnable]
    stats = run_table(manifest["table"], cfg, "real", receipts, wm,
                      since_override="1900-01-01 00:00:00",
                      replay_ids={e["id"] for e in runnable},
                      force_resend=manifest["force"],
                      tracker=tracker, pending_store=pending_store,
                      manifest_state=mstate, rows_override=rows)
    return {"sent": stats.get("sent", 0), "failed": stats.get("failed", 0),
            "missing": [], "drift": []}


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
                    help="R4 定向重放 manifest（严格 schema 校验；需搭配 --apply 才真实执行）")
    ap.add_argument("--apply", action="store_true",
                    help="与 --replay-manifest 连用：真实执行（缺省=dry-run 仅校验，零副作用）")
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
    tracker = ParentAcceptanceTracker(receipts)     # R2：全入口统一父门禁
    pending_store = PendingWaitStore(os.path.join(workdir, "pending-wait.json"))

    # R4/F07：参数组合与 manifest 完整校验必须先于所有执行分支（含 reconcile）、
    # 真实状态文件初始化与 HTTP——不支持组合显式拒绝，无 apply 绝不发送
    if args.replay_manifest and args.table == "reconcile":
        print("错误：--replay-manifest 与 --table reconcile 不支持组合，请分开执行",
              file=sys.stderr)
        raise SystemExit(2)
    manifest = None
    if args.replay_manifest:
        manifest = validate_and_load_manifest(args.replay_manifest, cfg, args.table)
        if not args.apply:
            # DRY-RUN：100% 零副作用（零 HTTP、零 sent/watermark/receipts 改写）——
            # 仅完整 schema 校验 + 源端定向干跑核对 + 预览摘要
            preview = {"dry_run": True, "manifest_id": manifest["manifest_id"],
                       "table": manifest["table"], "count": len(manifest["entries"]),
                       "force": manifest["force"]}
            try:
                probe = fetch_rows_by_ids(
                    cfg.get("env", {}).get("user"), cfg.get("env", {}).get("pwd"),
                    cfg.get("env", {}).get("dsn"), TABLES[manifest["table"]]["view"],
                    "ID", [e["id"] for e in manifest["entries"]])
                found = {str(r.get("ID")) for r in probe}
                preview["source_hit"] = len(found)
                preview["source_missing"] = [e["id"] for e in manifest["entries"]
                                             if e["id"] not in found]
            except (Exception, SystemExit) as e:   # 源端不可达（离线演练/无凭据）：
                # dry-run 优雅降级——schema 已全部校验通过，探针结果仅作参考
                preview["source_probe"] = "unavailable: %s" % str(e)[:120]
            print(json.dumps(preview, ensure_ascii=False))
            print("DRY-RUN：--replay-manifest 需搭配 --apply 才会真实发送，本次仅校验")
            return

    if args.table == "reconcile":
        if args.mode != "real":
            raise SystemExit("reconcile 只允许 --mode real（幂等补推，避免 mock 假统计）")
        spec = args.reconcile_date or "yesterday"
        out = run_reconcile(cfg, receipts, wm, spec,
                            tracker=tracker, pending_store=pending_store)
        print(json.dumps(out, ensure_ascii=False))
        atomic_write_json(os.path.join(workdir, "last-run-reconcile.json"), out)
        return

    names = ROLL_OUT_ORDER if args.table == "all" else args.table.split(",")
    for n in names:
        if n not in TABLES:
            raise SystemExit("未知表: %s" % n)

    if manifest is not None:
        # R4：定向重放执行（--apply）——持久断点 + 定向取数 + 缺失/漂移明确报错
        out = run_replay(manifest, cfg, receipts, wm, tracker, pending_store)
        print(json.dumps(out, ensure_ascii=False))
        atomic_write_json(os.path.join(workdir, "last-run-%s.json" % args.mode), [out])
        return

    # 子表排到其主表之后（exam_item 依赖 exam）
    order = {n: i for i, n in enumerate(ROLL_OUT_ORDER)}
    dep_rank = {n: order.get(TABLES[n].get("depends_on"), -1) for n in names}
    names = sorted(names, key=lambda n: (dep_rank[n], order.get(n, 99)))
    all_stats = []
    parent_ops = {}
    for n in names:
        try:
            s = run_table(n, cfg, args.mode, receipts, wm, args.limit, parent_ops,
                          since_override=args.since,
                          tracker=tracker, pending_store=pending_store)
        except Exception as e:                     # 单表失败不中断其余表（2026-11-03 加固）
            s = {"table": n, "error": str(e)[:300]}
        all_stats.append(s)
        print(json.dumps(s, ensure_ascii=False))
    atomic_write_json(os.path.join(workdir, "last-run-%s.json" % args.mode),
                      all_stats)


if __name__ == "__main__":
    main()
