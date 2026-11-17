"""药品追溯只读视图抽取（W04 → FIX2-03 第二轮修订）。

- 模板固定视图名（r5 契约 V_DRUG_TRACE_BILL / V_DRUG_TRACE_CODE），对象名经
  白名单渲染，值全部绑定参数；任何非 SELECT 在执行前被 _assert_readonly 阻断。
- Oracle11g 兼容：外层 ROWNUM 包裹 keyset 内层查询（不用 OFFSET/FETCH）。
- FIX2-03/R2-07 修订：DBAPI 输出在唯一适配边界规范化——列名小写（Oracle 未
  加引号列名返回大写）、datetime → ISO 字符串（可逆、确定、json 可存）、
  Decimal → 字符串原值（码/单号不丢前导零）；Oracle 空串即 NULL 不再经
  bool('0') 误判（布尔归一在领域 quality.as_bool）。
- FIX2-03/R2-08 修订：主单发现游标与 ORDER BY 均含 source_system（与 Python
  复合键完全一致）；header/codes 模板按完整复合键（含 source_system）过滤。
- FIX3-02/R3-02 修订：poll_bills 游标谓词改「等值前缀 + 逐字段比较」，与
  ORDER BY 独立列序同构；合成仓储同步逐字段元组比较（不再竖线拼接字符串）。
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timezone
from decimal import Decimal

from connector_sdk import CapabilityError

# 视图名白名单（r5；院方建视图/授权后才能真实访问。r6 草案见
# docs/development/drug-trace-20261016/view-contract-r6；r5 原文保留）。
DRUG_TRACE_OBJECTS = {"bill_view": "V_DRUG_TRACE_BILL", "code_view": "V_DRUG_TRACE_CODE"}

TEMPLATES: dict[str, dict] = {
    # 主单发现：(updated_at, 完整复合键含 source_system) 稳定 keyset 游标；半开窗口。
    # FIX3-02/R3-02 修订：游标谓词改为「等值前缀 + 逐字段比较」，与 ORDER BY 的
    # 独立列序严格同构——不再用竖线拼接字符串比较（YF1/YF10、含分隔符键值下
    # 拼接序与元组序不一致，会整页跳过或重复）。Python 侧（合成仓储与 ingest
    # 游标）共享同一排序契约。
    "drugtrace.poll_bills": {
        "oracle": (
            "SELECT * FROM (SELECT source_system, source_bill_id, source_bill_no,"
            " source_revision, business_kind, pharmacy_code, warehouse_code,"
            " supplier_code, bill_time, updated_at, source_event_seq, finalized,"
            " is_void, expected_code_count, patient_ref, encounter_ref,"
            " source_bill_kind_raw FROM {bill_view}"
            " WHERE updated_at >= :begin_time AND updated_at < :end_time"
            " AND (:since_ss IS NULL OR updated_at > :since_time OR (updated_at = :since_time AND ("
            "      source_system > :since_ss OR (source_system = :since_ss AND ("
            "       pharmacy_code > :since_pc OR (pharmacy_code = :since_pc AND ("
            "        business_kind > :since_bk OR (business_kind = :since_bk AND"
            "         source_bill_id > :since_sb))))))))"
            " ORDER BY updated_at, source_system, pharmacy_code, business_kind,"
            " source_bill_id)"
            " WHERE ROWNUM <= {limit}"),
        "params": ["begin_time", "end_time", "since_time", "since_ss",
                   "since_pc", "since_bk", "since_sb"],
        "note": "半开区间 + (updated_at, 逐字段复合键) keyset；谓词与 ORDER BY "
                "同构（FIX3-02 修订，替代拼接字符串比较）",
    },
    # 整单明细：单据内按 source_code_record_id keyset 翻页（不跨药房、不 DISTINCT）
    "drugtrace.bill_codes": {
        "oracle": (
            "SELECT * FROM (SELECT source_system, source_bill_id, pharmacy_code,"
            " business_kind, source_revision, source_line_id, source_code_record_id,"
            " trace_code, quantity, unit, code_level, parent_code, updated_at,"
            " is_deleted, f_trace_code, s_trace_code, t_trace_code, bill_time"
            " FROM {code_view}"
            " WHERE source_system = :source_system"
            " AND pharmacy_code = :pharmacy_code AND business_kind = :business_kind"
            " AND source_bill_id = :source_bill_id"
            # Oracle将首屏空串绑定为NULL；先允许空游标，后续页仍按原始ID严格递增。
            " AND (:after_record_id IS NULL"
            "      OR source_code_record_id > :after_record_id)"
            " ORDER BY source_code_record_id)"
            " WHERE ROWNUM <= {limit}"),
        "params": ["source_system", "pharmacy_code", "business_kind",
                   "source_bill_id", "after_record_id"],
        "note": "整单读取：完整复合键（含 source_system）关联（R2-08 修订）",
    },
    # 主单单行复核（版本变化检测/二次确认/失败重取）
    "drugtrace.bill_header": {
        "oracle": (
            "SELECT source_system, source_bill_id, source_bill_no, source_revision,"
            " business_kind, pharmacy_code, warehouse_code, supplier_code, bill_time,"
            " updated_at, source_event_seq, finalized, is_void, expected_code_count,"
            " patient_ref, encounter_ref, source_bill_kind_raw"
            " FROM {bill_view}"
            " WHERE source_system = :source_system"
            " AND pharmacy_code = :pharmacy_code AND business_kind = :business_kind"
            " AND source_bill_id = :source_bill_id"),
        "params": ["source_system", "pharmacy_code", "business_kind",
                   "source_bill_id"],
    },
}


def render_sql(template_id: str, dialect: str = "oracle", *, limit: int = 200,
               bill_view: str = DRUG_TRACE_OBJECTS["bill_view"],
               code_view: str = DRUG_TRACE_OBJECTS["code_view"]) -> str:
    t = TEMPLATES[template_id]
    if dialect not in t:
        raise CapabilityError("CAPABILITY_UNSUPPORTED",
                              f"template {template_id} has no {dialect} variant")
    sql = t[dialect].format(limit=limit, bill_view=bill_view, code_view=code_view)
    head = sql.lstrip().upper()
    if not head.startswith("SELECT"):
        raise CapabilityError("CAPABILITY_UNSUPPORTED",
                              "read-only guard rejected non-SELECT statement")
    return sql


def _normalize_value(v):
    """DBAPI 值规范化（R2-07）：datetime/date → ISO 字符串；Decimal → str。

    - datetime 保留原墙钟（HIS 本地时间口径，EXT-02）；带时区值折算 UTC 后
      去时区（与 ingest._norm_dt 的游标可比口径一致）。
    - Decimal 直接 str（NUMBER 精度与文本原值保持；码/单号列视图应 TO_CHAR）。
    """
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def normalize_row(row: dict) -> dict:
    """行规范化：列名小写（Oracle 大写列名）+ 值规范化。唯一适配边界。"""
    return {str(k).lower(): _normalize_value(v) for k, v in row.items()}


class SyntheticDrugTraceWarehouse:
    """合成药品追溯仓储（测试/演示；按 r5 契约字段过滤，不执行 SQL）。

    线程安全；支持测试动态变更行（补码/删除/作废/跨药房重号）。
    行结构与视图一致：bill 行 + code 行（每条真实扫码一行，含 0/1/N 码场景）。
    FIX2-03：排序/过滤定义与真 SQL 模板保持同一口径（含 source_system）。
    """

    dialect_name = "synthetic"

    def __init__(self, namespace: str = "HIS"):
        self._lock = threading.RLock()
        self.namespace = namespace
        self.bills: list[dict] = []
        self.codes: list[dict] = []

    # ---- 测试辅助 ----
    def add_bill(self, row: dict) -> None:
        with self._lock:
            row.setdefault("__namespace", self.namespace)
            self.bills.append(dict(row))

    def add_code(self, row: dict) -> None:
        with self._lock:
            row.setdefault("__namespace", self.namespace)
            self.codes.append(dict(row))

    def mutate_bill(self, match: dict, changes: dict) -> int:
        return self._mutate(self.bills, match, changes)

    def mutate_code(self, match: dict, changes: dict) -> int:
        return self._mutate(self.codes, match, changes)

    @staticmethod
    def _mutate(rows: list[dict], match: dict, changes: dict) -> int:
        n = 0
        for r in rows:
            if all(r.get(k) == v for k, v in match.items()):
                r.update(changes)
                n += 1
        return n

    # ---- Warehouse 协议 ----
    def dialect(self) -> str:
        return self.dialect_name

    @staticmethod
    def _t(v):
        """时间比较归一：ISO 字符串 → naive datetime。"""
        if isinstance(v, str):
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        else:
            dt = v
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    def _ns(self, rows: list[dict]) -> list[dict]:
        return [r for r in rows if r.get("__namespace", self.namespace) == self.namespace]

    @staticmethod
    def _key_tuple(r: dict) -> tuple:
        """逐字段复合键元组（与 SQL 谓词/ORDER BY 同一契约；FIX3-02）。"""
        return (str(r.get("source_system", "") or ""),
                str(r.get("pharmacy_code", "") or ""),
                str(r.get("business_kind", "") or ""),
                str(r.get("source_bill_id", "") or ""))

    def fetch_all(self, template_id: str, params: dict, limit: int, *,
                  database: str = "", schema: str = "") -> list[dict]:
        with self._lock:
            if template_id == "drugtrace.poll_bills":
                rows = [r for r in self._ns(self.bills)
                        if self._t(params["begin_time"]) <= self._t(r["updated_at"])
                        < self._t(params["end_time"])]
                since_t = params.get("since_time")
                if since_t is not None:
                    since_k = (str(params.get("since_ss", "") or ""),
                               str(params.get("since_pc", "") or ""),
                               str(params.get("since_bk", "") or ""),
                               str(params.get("since_sb", "") or ""))
                    rows = [r for r in rows
                            if self._t(r["updated_at"]) > self._t(since_t)
                            or (self._t(r["updated_at"]) == self._t(since_t)
                                and self._key_tuple(r) > since_k)]
                rows.sort(key=lambda r: (self._t(r["updated_at"]),
                                         self._key_tuple(r)))
                return [dict(r) for r in rows[:limit]]
            if template_id == "drugtrace.bill_codes":
                rows = [r for r in self._ns(self.codes)
                        if r.get("source_system") == params.get("source_system")
                        and r.get("pharmacy_code") == params["pharmacy_code"]
                        and r.get("business_kind") == params["business_kind"]
                        and r.get("source_bill_id") == params["source_bill_id"]
                        and str(r.get("source_code_record_id", ""))
                        > str(params.get("after_record_id", ""))]
                rows.sort(key=lambda r: str(r.get("source_code_record_id", "")))
                return [dict(r) for r in rows[:limit]]
            if template_id == "drugtrace.bill_header":
                rows = [r for r in self._ns(self.bills)
                        if r.get("source_system") == params.get("source_system")
                        and r.get("pharmacy_code") == params["pharmacy_code"]
                        and r.get("business_kind") == params["business_kind"]
                        and r.get("source_bill_id") == params["source_bill_id"]]
                return [dict(r) for r in rows[:limit]]
        raise CapabilityError("CAPABILITY_UNSUPPORTED", f"unknown template {template_id}")

    # O4（LT-08）：按页批量整单读取；默认关闭（batch_fetch=False），语义与
    # 逐单 read_bill_whole 完全一致（版本变化/头缺失的键不入结果 → 调用方
    # 逐单回退，reread/VERSION_CONFLICT/SOURCE_UNAVAILABLE 行为保留）。
    batch_fetch = False

    def fetch_bills_batch(self, headers: list[dict], *,
                          code_page_limit: int = 200) -> dict | None:
        with self._lock:
            out: dict = {}
            for h in headers:
                key = self._key_tuple(h)
                heads = [r for r in self._ns(self.bills)
                         if self._key_tuple(r) == key]
                if not heads:
                    continue                       # 头缺失 → 回退逐单（报缺失）
                head = dict(heads[0])
                if str(head.get("source_revision")) != \
                        str(h.get("source_revision")):
                    continue                       # 版本变化 → 回退逐单重取
                codes = sorted(
                    (r for r in self._ns(self.codes)
                     if self._key_tuple(r) == key),
                    key=lambda r: str(r.get("source_code_record_id", "")))
                out[key] = (head, [dict(r) for r in codes])
            return out


class OracleDrugTraceWarehouse:
    """Oracle 真驱动（EXT-01 关闭前不可用于生产；仅按只读模板+绑定参数执行）。

    FIX2-03 修订：
    - DSN 可显式注入（连接器绑定受限只读账号），或注入连接工厂（测试 fake）。
    - 输出行经 normalize_row 规范化（列名小写/datetime/Decimal；R2-07）。
    FIX3-02 修订（G3-06）：
    - bill_view/code_view 对象名可按批准连接器配置注入（默认 r5 契约名），
      仅接受 [SCHEMA.]OBJECT 形状的标识符——对象白名单来自配置本身，模板仍
      只读、值仍全部绑定参数。
    call_timeout 毫秒级；异常翻译为能力错误（不伪装空结果）。
    """

    dialect_name = "oracle"
    _IDENT_RE = None  # compiled lazily (module-level import kept local)

    def __init__(self, connection_factory=None, *, dsn: str = "",
                 timeout_seconds: float = 30.0, objects: dict | None = None,
                 expected_user: str = "", batch_fetch: bool = False):
        self._conn_factory = connection_factory
        self._dsn = dsn or None
        self._timeout_ms = int(timeout_seconds * 1000)
        self._expected_user = expected_user
        self.batch_fetch = bool(batch_fetch)   # O4（LT-08）：默认关闭
        self.progress_hook = None
        self._reuse_connection = False
        self._session_connection = None
        self.metrics = {'connect_count':0,'connect_seconds':0.0,'query_count':0,'query_seconds':0.0}
        self._bill_view = DRUG_TRACE_OBJECTS["bill_view"]
        self._code_view = DRUG_TRACE_OBJECTS["code_view"]
        if objects:
            self._bill_view = self._validated_object(
                objects.get("bill_view") or self._bill_view, "bill_view")
            self._code_view = self._validated_object(
                objects.get("code_view") or self._code_view, "code_view")
        if connection_factory is not None:
            return
        # DPL-P01：HIS 为旧 Oracle 11g，真实驱动路径必须先完成 Thick 初始化
        # （同进程首个连接前生效；缺 Client 即阻断，不回退 Thin）。测试
        # connection_factory（fake 驱动）路径不触发。
        from .oracle_client import ensure_oracle_client
        ensure_oracle_client()
        try:
            import oracledb  # noqa: F401
        except ImportError as exc:
            from connector_sdk import driver_unavailable
            raise driver_unavailable("oracledb", str(exc)) from exc
        if not self._dsn:
            import os
            self._dsn = os.environ.get("BLYB_DRUGTRACE_ORACLE_DSN", "")
        if not self._dsn:
            from connector_sdk import driver_unavailable
            raise driver_unavailable(
                "oracledb", "driver installed but DSN not set (per-connector "
                            "binding required, EXT-01)")

    @staticmethod
    def _validated_object(name: str, what: str) -> str:
        """对象白名单校验：仅 [SCHEMA.]OBJECT 标识符，防配置注入任意 SQL 片段。"""
        import re
        part = r"[A-Za-z][A-Za-z0-9_$#]*"
        if not re.fullmatch(rf"{part}(\.{part})?", str(name)):
            raise CapabilityError(
                "CAPABILITY_UNSUPPORTED",
                f"invalid {what} object name {name!r}; identifier expected")
        return str(name)

    def dialect(self) -> str:
        return self.dialect_name

    def collection_session(self):
        """One bounded page owns one connection; normal standalone reads unchanged."""
        from contextlib import contextmanager
        @contextmanager
        def scope():
            if self._reuse_connection:raise RuntimeError('nested Oracle collection session')
            self._reuse_connection=True
            try:yield self
            finally:
                self._reuse_connection=False
                self._close_connection()
        return scope()

    def _close_connection(self):
        conn=self._session_connection;self._session_connection=None
        if conn is not None:
            try:conn.close()
            except Exception:pass

    def fetch_all(self, template_id: str, params: dict, limit: int, *,
                  database: str = "", schema: str = "") -> list[dict]:
        sql = render_sql(template_id, "oracle", limit=limit,
                         bill_view=self._bill_view, code_view=self._code_view)
        import time
        if self.progress_hook:self.progress_hook()
        conn = None
        failed = False
        try:
            conn = self._session_connection if self._reuse_connection else None
            if conn is None:
                started=time.monotonic()
                conn = self._conn_factory() if self._conn_factory else __import__(
                    "oracledb").connect(self._dsn)
                self.metrics['connect_count']+=1
                self.metrics['connect_seconds']+=time.monotonic()-started
                if self._reuse_connection:self._session_connection=conn
            if self._expected_user and str(getattr(conn, "username", "")).upper() \
                    != self._expected_user.upper():
                raise CapabilityError("SOURCE_PROFILE_UNVERIFIED",
                                      "Oracle reader does not match source profile")
            try:
                conn.call_timeout = self._timeout_ms
            except Exception:  # noqa: BLE001 —— fake驱动可不支持
                pass
            cur = conn.cursor()
            started=time.monotonic()
            try:
                cur.execute(sql, {k: params[k]
                                  for k in TEMPLATES[template_id]["params"]})
                cols = [d[0] for d in cur.description]
                result = [normalize_row(dict(zip(cols, row)))
                          for row in cur.fetchall()]
            finally:
                self.metrics['query_count']+=1
                self.metrics['query_seconds']+=time.monotonic()-started
                cur.close()
        except CapabilityError:
            failed = True
            raise
        except Exception as exc:  # noqa: BLE001
            failed = True
            from .warehouse import _translate_dbapi_error
            raise _translate_dbapi_error(exc) from exc
        finally:
            if failed and self._reuse_connection:
                self._close_connection()
            elif conn is not None and not self._reuse_connection:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if self.progress_hook:self.progress_hook()
        return result

    # ---------------------------------------------------------------- O4 批量取数
    _HEADER_COLS = ("source_system, source_bill_id, source_bill_no, source_revision,"
                    " business_kind, pharmacy_code, warehouse_code, supplier_code,"
                    " bill_time, updated_at, source_event_seq, finalized, is_void,"
                    " expected_code_count, patient_ref, encounter_ref,"
                    " source_bill_kind_raw")
    _CODE_COLS = ("source_system, source_bill_id, pharmacy_code, business_kind,"
                  " source_revision, source_line_id, source_code_record_id,"
                  " trace_code, quantity, unit, code_level, parent_code,"
                  " updated_at, is_deleted, f_trace_code, s_trace_code,"
                  " t_trace_code, bill_time")
    _BATCH_CAP_FACTOR = 4       # 码批量上限 = 键数×code_page_limit×4，超限整页回退

    def fetch_bills_batch(self, headers: list[dict], *,
                          code_page_limit: int = 200) -> "dict | None":
        """按页批量整单读取（LT-08 O4；须配置 batch_fetch=true 才被调用）。

        2 条 SQL 覆盖整页（每键仍为复合键等值谓词 OR 连接——与单键查询同一
        FBI 谓词形式，仅合并往返；启用前须 DBA EXPLAIN 确认 OR 展开走索引）：

        1) 码明细：全部页键的行按 (键, source_code_record_id) 排序一次取回；
        2) 单头复核：全部页键一次取回（与逐单 read_bill_whole 的复核同构）。

        语义保证：版本变化（复核 source_revision ≠ 轮询时值）或头缺失的键
        **不入结果**——调用方逐单回退 read_bill_whole，reread/VERSION_CONFLICT/
        SOURCE_UNAVAILABLE 行为逐字保留；码行数触及批量上限返回 None（整页
        回退，绝不截断/部分结果）。返回 {键元组: (final_header, lines)}。
        """
        import time
        keys: list[tuple[str, str, str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for h in headers:
            k = (str(h.get("source_system", "") or ""),
                 str(h.get("pharmacy_code", "") or ""),
                 str(h.get("business_kind", "") or ""),
                 str(h.get("source_bill_id", "") or ""))
            if k not in seen:
                seen.add(k)
                keys.append(k)
        if not keys:
            return {}
        pred_parts: list[str] = []
        binds: dict[str, str] = {}
        for i, k in enumerate(keys):
            pred_parts.append(
                f"(source_system = :ss{i} AND pharmacy_code = :pc{i}"
                f" AND business_kind = :bk{i} AND source_bill_id = :sb{i})")
            binds[f"ss{i}"], binds[f"pc{i}"] = k[0], k[1]
            binds[f"bk{i}"], binds[f"sb{i}"] = k[2], k[3]
        pred = " OR ".join(pred_parts)
        header_cap = 2 * len(keys)
        code_cap = len(keys) * code_page_limit * self._BATCH_CAP_FACTOR
        header_sql = (
            f"SELECT * FROM (SELECT {self._HEADER_COLS} FROM {self._bill_view}"
            f" WHERE {pred} ORDER BY source_system, pharmacy_code,"
            f" business_kind, source_bill_id) WHERE ROWNUM <= {header_cap}")
        code_sql = (
            f"SELECT * FROM (SELECT {self._CODE_COLS} FROM {self._code_view}"
            f" WHERE {pred} ORDER BY source_system, pharmacy_code,"
            f" business_kind, source_bill_id, source_code_record_id)"
            f" WHERE ROWNUM <= {code_cap}")
        if self.progress_hook:self.progress_hook()
        conn = None
        failed = False
        try:
            conn = self._session_connection if self._reuse_connection else None
            if conn is None:
                started = time.monotonic()
                conn = self._conn_factory() if self._conn_factory else __import__(
                    "oracledb").connect(self._dsn)
                self.metrics['connect_count'] += 1
                self.metrics['connect_seconds'] += time.monotonic() - started
                if self._reuse_connection:self._session_connection = conn
            if self._expected_user and str(getattr(conn, "username", "")).upper() \
                    != self._expected_user.upper():
                raise CapabilityError("SOURCE_PROFILE_UNVERIFIED",
                                      "Oracle reader does not match source profile")
            try:
                conn.call_timeout = self._timeout_ms
            except Exception:  # noqa: BLE001 —— fake驱动可不支持
                pass

            def _run(sql: str) -> list[dict]:
                cur = conn.cursor()
                started = time.monotonic()
                try:
                    cur.execute(sql, dict(binds))
                    cols = [d[0] for d in cur.description]
                    return [normalize_row(dict(zip(cols, row)))
                            for row in cur.fetchall()]
                finally:
                    self.metrics['query_count'] += 1
                    self.metrics['query_seconds'] += time.monotonic() - started
                    cur.close()

            code_rows = _run(code_sql)
            if len(code_rows) >= code_cap:
                return None                     # 超限：整页回退逐单（不截断）
            header_rows = _run(header_sql)
        except CapabilityError:
            failed = True
            raise
        except Exception as exc:  # noqa: BLE001
            failed = True
            from .warehouse import _translate_dbapi_error
            raise _translate_dbapi_error(exc) from exc
        finally:
            if failed and self._reuse_connection:
                self._close_connection()
            elif conn is not None and not self._reuse_connection:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if self.progress_hook:self.progress_hook()
        heads: dict[tuple[str, str, str, str], dict] = {}
        for r in header_rows:
            k = (str(r.get("source_system", "") or ""),
                 str(r.get("pharmacy_code", "") or ""),
                 str(r.get("business_kind", "") or ""),
                 str(r.get("source_bill_id", "") or ""))
            heads.setdefault(k, r)              # 排序取首，同 read_bill_whole 的首行
        codes: dict[tuple[str, str, str, str], list[dict]] = {}
        for r in code_rows:
            k = (str(r.get("source_system", "") or ""),
                 str(r.get("pharmacy_code", "") or ""),
                 str(r.get("business_kind", "") or ""),
                 str(r.get("source_bill_id", "") or ""))
            codes.setdefault(k, []).append(r)   # SQL 已按 record_id 排序
        out: dict = {}
        poll_rev = {(str(h.get("source_system", "") or ""),
                     str(h.get("pharmacy_code", "") or ""),
                     str(h.get("business_kind", "") or ""),
                     str(h.get("source_bill_id", "") or "")):
                    str(h.get("source_revision")) for h in headers}
        for k, head in heads.items():
            if poll_rev.get(k) is None:
                continue
            if str(head.get("source_revision")) != poll_rev[k]:
                continue                         # 版本变化 → 调用方逐单回退
            out[k] = (head, codes.get(k, []))
        return out
