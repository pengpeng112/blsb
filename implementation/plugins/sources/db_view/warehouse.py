"""Warehouse 实现：合成仓储 + SQL Server/Oracle 真驱动入口。

- SyntheticWarehouse：不执行SQL，按 template_id 语义过滤合成行；测试可变更行并
  以事件序列号驱动 poll_changes（F02 唤醒、F14 内容修订）。
- SqlServerWarehouse / OracleWarehouse：模板→方言SQL、参数绑定、行数上限；
  驱动未安装时抛 DRIVER_UNAVAILABLE，不假成功。
"""
from __future__ import annotations

import threading

from connector_sdk import CapabilityError, driver_unavailable

# ---------------------------------------------------------------- 模板注册表
# SQL 仅在真实驱动路径使用；对象名来自批准允许列表，值全部参数绑定。
TEMPLATES: dict[str, dict] = {
    "pis.slide_by_barcode": {
        "sqlserver": "SELECT TOP ({limit}) Id, qptmh, PathologyID, lkh, qpzt, zpzt, tmdyzt "
                     "FROM [{database}].[{schema}].[Pitaya_Slice] WHERE qptmh = ? ORDER BY Id",
        "params": ["barcode"],
    },
    "pis.case_by_pathology": {
        "sqlserver": "SELECT TOP ({limit}) * FROM [{database}].[{schema}].[Pitaya] "
                     "WHERE PathologyID = ?",
        "params": ["pathology_no"],
    },
    "pis.slides_by_case": {
        "sqlserver": "SELECT TOP ({limit}) Id, qptmh, PathologyID, lkh, qpzt, zpzt, tmdyzt "
                     "FROM [{database}].[{schema}].[Pitaya_Slice] "
                     "WHERE PathologyID = ? AND Id > ? ORDER BY Id",
        "params": ["pathology_no", "after_id"],
        "note": "keyset分页（R31）：Id游标逐页取，短页即末页",
    },
    "pis.applications_by_reqnote": {
        "sqlserver": "SELECT TOP ({limit}) * FROM [{database}].[{schema}].[患者检查申请信息表] "
                     "WHERE 申请号 = ?",
        "params": ["req_note_id"],
    },
    "pis.reports_by_case": {
        "sqlserver": "SELECT TOP ({limit}) * FROM [{database}].[{schema}].[患者检查报告信息表] "
                     "WHERE 报告流水号 = ? OR 报告流水号 LIKE ? ORDER BY 报告流水号",
        "params": ["pathology_no", "pathology_like"],
    },
    "pis.poll_cases_created": {
        "sqlserver": "SELECT TOP ({limit}) PathologyID, CreateTime FROM "
                     "[{database}].[{schema}].[Pitaya] "
                     "WHERE (CreateTime > ? OR (CreateTime = ? AND PathologyID > ?)) "
                     "AND CreateTime <= ? ORDER BY CreateTime, PathologyID",
        "params": ["since", "since", "after_key", "until"],
        "note": "INSERT_DISCOVERY_ONLY + (CreateTime,PathologyID)复合游标（R11："
                "同刻多行不丢；包含/排除边界明确：since为开区间下界、until为闭区间上界）",
    },
    "pis.scan_partition_cases": {
        "sqlserver": "SELECT TOP ({limit}) * FROM [{database}].[{schema}].[Pitaya] "
                     "WHERE PathologyID >= ? AND PathologyID < ? AND PathologyID > ? "
                     "ORDER BY PathologyID",
        "params": ["key_from", "key_to", "after_key"],
        "note": "keyset分页（R10）：after_key为已读末键，翻页到短页才算末页",
    },
    "pis.images_by_exam": {
        "sqlserver": "SELECT TOP ({limit}) * FROM [{database}].[{schema}].[V_PIS_IMAGE] "
                     "WHERE EXAMID = ?",
        "params": ["pathology_no"],
    },
    "his.patient_by_id": {
        "oracle": "SELECT * FROM (SELECT PATIENT_ID, ID_TYPE, ID_NO, NAME, SEX, DATE_OF_BIRTH "
                  "FROM {schema}.PAT_MASTER_INDEX WHERE PATIENT_ID = :patient_id) "
                  "WHERE ROWNUM <= {limit}",
        "params": ["patient_id"],
    },
    "his.encounters_by_patient": {
        "oracle": "SELECT * FROM (SELECT PATIENT_ID, VISIT_ID, INSURANCE_TYPE "
                  "FROM {schema}.PAT_VISIT WHERE PATIENT_ID = :patient_id) "
                  "WHERE ROWNUM <= {limit}",
        "params": ["patient_id"],
    },
}


def render_sql(template_id: str, dialect: str, *, limit: int,
               database: str = "", schema: str = "") -> str:
    t = TEMPLATES[template_id]
    if dialect not in t:
        raise CapabilityError("CAPABILITY_UNSUPPORTED",
                              f"template {template_id} has no {dialect} variant")
    return t[dialect].format(limit=limit, database=database, schema=schema)


_READONLY_PREFIX = "SELECT"


def _assert_readonly(sql: str) -> None:
    """W09只读防护：模板渲染后的SQL必须以SELECT开头（空白忽略）。

    允许列表内的对象名经 render_sql 拼接，值全部参数绑定；任何非SELECT
    语句（含被篡改模板产生的 UPDATE/DELETE/DROP）在执行前阻断。
    """
    head = sql.lstrip().upper()
    if not head.startswith(_READONLY_PREFIX):
        raise CapabilityError("CAPABILITY_UNSUPPORTED",
                              f"read-only warehouse rejected non-SELECT statement: "
                              f"{head.split()[0] if head.split() else '<empty>'}")


def _translate_dbapi_error(exc: Exception) -> Exception:
    """DBAPI驱动异常 → 明确能力错误（连接失败/超时不得伪装成空结果）。"""
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg or "cancel" in msg:
        return CapabilityError("SOURCE_QUERY_TIMEOUT", f"source query timed out: {exc}")
    return CapabilityError("SOURCE_CONNECT_FAILED", f"source connection failed: {exc}")


class SyntheticWarehouse:
    """按 template_id 语义查询合成行。线程安全；支持测试中动态改行。"""

    dialect_name = "synthetic"

    def __init__(self, *, namespace: str = "PITAYA", his_namespace: str = "MEDREC"):
        from . import fixtures
        self._lock = threading.RLock()
        self.namespace = namespace
        self.his_namespace = his_namespace
        self.rows = {
            "Pitaya_Slice": [dict(r) for r in fixtures.SLIDES],
            "Pitaya": [dict(r) for r in fixtures.CASES],
            "患者检查申请信息表": [dict(r) for r in fixtures.APPLICATIONS],
            "患者检查报告信息表": [dict(r) for r in fixtures.REPORTS],
            "PAT_MASTER_INDEX": [dict(r) for r in fixtures.PATIENTS],
            "PAT_VISIT": [dict(r) for r in fixtures.ENCOUNTERS],
            "V_PIS_IMAGE": [dict(r) for r in fixtures.V_PIS_IMAGE],
        }

    # -- 测试辅助 ------------------------------------------------------------
    def upsert_row(self, table: str, row: dict) -> None:
        with self._lock:
            self.rows[table].append(dict(row))

    def mutate_row(self, table: str, match: dict, changes: dict) -> int:
        n = 0
        with self._lock:
            for r in self.rows[table]:
                if all(r.get(k) == v for k, v in match.items()):
                    r.update(changes)
                    n += 1
        return n

    def mark_void(self, table: str, match: dict) -> int:
        return self.mutate_row(table, match, {"作废标记": 1})

    # -- Warehouse 协议 -------------------------------------------------------
    def dialect(self) -> str:
        return self.dialect_name

    def _ns_rows(self, table: str):
        if table in ("Pitaya_Slice", "Pitaya", "患者检查申请信息表", "患者检查报告信息表"):
            return [r for r in self.rows[table]
                    if r.get("__namespace", "PITAYA") == self.namespace]
        return self.rows[table]

    def fetch_all(self, template_id: str, params: dict, limit: int, *,
                  database: str = "", schema: str = "") -> list[dict]:
        # R21：Warehouse 协议统一接收对象作用域（合成仓储按 namespace 隔离，忽略该参数）
        with self._lock:
            if template_id == "pis.slide_by_barcode":
                rows = [r for r in self._ns_rows("Pitaya_Slice")
                        if r["qptmh"] == params["barcode"]]
                rows.sort(key=lambda r: r["Id"])
                return rows[:limit]
            if template_id == "pis.case_by_pathology":
                return [r for r in self._ns_rows("Pitaya")
                        if r["PathologyID"] == params["pathology_no"]][:limit]
            if template_id == "pis.slides_by_case":
                rows = [r for r in self._ns_rows("Pitaya_Slice")
                        if r["PathologyID"] == params["pathology_no"]
                        and r["Id"] > params.get("after_id", 0)]
                rows.sort(key=lambda r: r["Id"])
                return rows[:limit]
            if template_id == "pis.applications_by_reqnote":
                return [r for r in self._ns_rows("患者检查申请信息表")
                        if r["申请号"] == params["req_note_id"]][:limit]
            if template_id == "pis.reports_by_case":
                rows = [r for r in self._ns_rows("患者检查报告信息表")
                        if r["报告流水号"] == params.get("pathology_no")
                        or r["报告流水号"].startswith(params.get("pathology_no", "") + "-")]
                return rows[:limit]
            if template_id == "pis.poll_cases_created":
                after_key = params.get("after_key", "")
                rows = [r for r in self._ns_rows("Pitaya")
                        if (r["CreateTime"] > params["since"]
                            or (r["CreateTime"] == params["since"]
                                and str(r["PathologyID"]) > after_key))
                        and r["CreateTime"] <= params["until"]]
                rows.sort(key=lambda r: (r["CreateTime"], r["PathologyID"]))
                return rows[:limit]
            if template_id == "pis.scan_partition_cases":
                after_key = params.get("after_key", "")
                rows = [r for r in self._ns_rows("Pitaya")
                        if params["key_from"] <= r["PathologyID"] < params["key_to"]
                        and str(r["PathologyID"]) > after_key]
                rows.sort(key=lambda r: r["PathologyID"])
                return rows[:limit]
            if template_id == "pis.images_by_exam":
                return [r for r in self._ns_rows("V_PIS_IMAGE")
                        if r["EXAMID"] == params["pathology_no"]][:limit]
            if template_id == "his.patient_by_id":
                return [r for r in self.rows["PAT_MASTER_INDEX"]
                        if r["PATIENT_ID"] == params["patient_id"]][:limit]
            if template_id == "his.encounters_by_patient":
                rows = [r for r in self.rows["PAT_VISIT"]
                        if r["PATIENT_ID"] == params["patient_id"]]
                rows.sort(key=lambda r: r["VISIT_ID"])
                return rows[:limit]
        raise CapabilityError("CAPABILITY_UNSUPPORTED", f"unknown template {template_id}")


class SqlServerWarehouse:
    """SQL Server 真驱动访问。未安装 pyodbc/未配置 DSN 时显式失败。

    W09：只读防护（非SELECT阻断）、参数绑定、cursor显式关闭、
    连接级查询超时（conn.timeout）与取消（conn.cancel）、
    连接失败/超时翻译为能力错误（不伪装空结果）。
    """

    dialect_name = "sqlserver"

    def __init__(self, connection_factory=None, *, timeout_seconds: float = 30.0):
        self._conn_factory = connection_factory  # 注入用于隔离测试；生产由驱动创建
        self._driver = None
        self._timeout = timeout_seconds
        if connection_factory is None:
            try:
                import pyodbc  # noqa: F401
                self._driver = pyodbc
            except ImportError as exc:
                raise driver_unavailable("pyodbc", str(exc)) from exc

    def dialect(self) -> str:
        return self.dialect_name

    def fetch_all(self, template_id: str, params: dict, limit: int,
                  *, database: str = "", schema: str = "") -> list[dict]:
        sql = render_sql(template_id, "sqlserver", limit=limit,
                         database=database, schema=schema)
        _assert_readonly(sql)
        order = TEMPLATES[template_id]["params"]
        args = [params[k] for k in order]
        conn = None
        try:
            conn = self._conn_factory() if self._conn_factory else self._driver.connect(
                _sqlserver_dsn_from_env())
            try:
                conn.timeout = self._timeout  # pyodbc 连接级查询超时
            except Exception:  # noqa: BLE001 —— fake驱动可不支持该属性
                pass
            cur = conn.cursor()
            try:
                cur.execute(sql, args)  # qmark参数绑定，值不进SQL文本
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                cur.close()
        except CapabilityError:
            raise
        except Exception as exc:  # noqa: BLE001
            cancel = getattr(conn, "cancel", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:  # noqa: BLE001
                    pass
            raise _translate_dbapi_error(exc) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


class OracleWarehouse:
    """Oracle 真驱动访问（R22）。构造条件：注入连接工厂，或
    （驱动可导入 且 BLYB_ORACLE_DSN 已配置）；缺驱动/缺DSN显式 DRIVER_UNAVAILABLE，
    不伪成功也不误报。连接延迟到 fetch_all。

    W09：只读防护、named绑定、cursor关闭、call_timeout毫秒级超时、
    连接失败/超时翻译为能力错误。
    """

    dialect_name = "oracle"

    def __init__(self, connection_factory=None, *, timeout_seconds: float = 30.0):
        self._conn_factory = connection_factory
        self._dsn = None
        self._timeout_ms = int(timeout_seconds * 1000)
        if connection_factory is not None:
            return
        import os
        # DPL-P01：真实驱动路径先完成 Thick 一次初始化（11g HIS 不回退 Thin）
        from .oracle_client import ensure_oracle_client
        ensure_oracle_client()
        try:
            import oracledb  # noqa: F401
        except ImportError as exc:
            raise driver_unavailable("oracledb", str(exc)) from exc
        self._dsn = os.environ.get("BLYB_ORACLE_DSN", "")
        if not self._dsn:
            raise driver_unavailable(
                "oracledb", "driver installed but BLYB_ORACLE_DSN not configured; "
                            "set it for integration test")

    def dialect(self) -> str:
        return self.dialect_name

    def fetch_all(self, template_id: str, params: dict, limit: int,
                  *, database: str = "", schema: str = "") -> list[dict]:
        sql = render_sql(template_id, "oracle", limit=limit, database=database, schema=schema)
        _assert_readonly(sql)
        conn = None
        try:
            conn = self._conn_factory() if self._conn_factory else __import__(
                "oracledb").connect(self._dsn)
            try:
                conn.call_timeout = self._timeout_ms  # oracledb 毫秒级调用超时
            except Exception:  # noqa: BLE001 —— fake驱动可不支持该属性
                pass
            cur = conn.cursor()
            try:
                cur.execute(sql, {k: params[k] for k in TEMPLATES[template_id]["params"]})
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
            finally:
                cur.close()
        except CapabilityError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate_dbapi_error(exc) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


def _sqlserver_dsn_from_env() -> str:
    import os
    dsn = os.environ.get("BLYB_PIS_SQLSERVER_DSN")
    if not dsn:
        raise driver_unavailable("pyodbc", "BLYB_PIS_SQLSERVER_DSN not configured")
    return dsn
