"""W09正式回归：fake DBAPI 驱动验证真驱动装配路径（不连真库）。

验证 SqlServerWarehouse / OracleWarehouse 实际执行形态：
- SQL 限定 [database].[schema].对象 与 TOP/ROWNUM 限制；
- 值全部参数绑定（不进SQL文本）；
- cursor 显式关闭、连接最终关闭；
- 连接级超时属性被设置；超时/异常时 conn.cancel 被调用并翻译为能力错误；
- 只读防护：任何非 SELECT 渲染结果在执行前阻断；
- 连接失败 → 显式错误，绝不伪装成空结果（0行与错误可区分）。
真库路径保持 DSN 开关（BLYB_PIS_SQLSERVER_DSN / BLYB_ORACLE_DSN），未配置不触发。
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from connector_sdk import CapabilityError  # noqa: E402
from sources.db_view import warehouse as W  # noqa: E402


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.closed = False
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self._conn.cursor_calls.append(("execute", sql, params))
        behavior = self._conn.behavior
        if behavior == "timeout":
            raise RuntimeError("HYT00 query timeout expired")
        if behavior == "connect_fail":
            raise RuntimeError("08001 cannot connect")
        if behavior == "empty":
            self.description = [("Id", int), ("qptmh", str)]
            self._rows = []
            return
        self.description = [("Id", int), ("qptmh", str)]
        self._rows = [(1, self._conn.rows_value)]

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True
        self._conn.cursor_calls.append(("cursor_close", None, None))


class FakeConnection:
    """pyodbc/oracledb 形态的 fake：记录 timeout/cancel/close 与全部SQL调用。"""

    def __init__(self, *, behavior="ok", rows_value="BC-1"):
        self.behavior = behavior
        self.rows_value = rows_value
        self.timeout = None
        self.call_timeout = None
        self.cancelled = 0
        self.closed = False
        self.cursor_calls: list[tuple] = []

    def cursor(self):
        cur = FakeCursor(self)
        self.cursor_calls.append(("cursor_open", None, None))
        return cur

    def cancel(self):
        self.cancelled += 1

    def close(self):
        self.closed = True


def test_w09_sqlserver_sql_scope_and_binding():
    conn = FakeConnection()
    wh = W.SqlServerWarehouse(connection_factory=lambda: conn, timeout_seconds=7.5)
    rows = wh.fetch_all("pis.slide_by_barcode",
                        {"barcode": "BC0001'; DROP TABLE x;--"}, limit=25,
                        database="PITAYA", schema="dbo")
    assert rows == [{"Id": 1, "qptmh": "BC-1"}]
    kinds = [c[0] for c in conn.cursor_calls]
    assert kinds.count("cursor_open") == 1 and kinds.count("cursor_close") == 1
    sql, params = [c for c in conn.cursor_calls if c[0] == "execute"][0][1:]
    # 对象作用域进入SQL，值绝不进入
    assert "[PITAYA].[dbo].[Pitaya_Slice]" in sql
    assert "TOP (25)" in sql
    assert params == ["BC0001'; DROP TABLE x;--"]
    assert "DROP TABLE" not in sql
    # 超时属性已设置；连接关闭
    assert conn.timeout == 7.5
    assert conn.closed


def test_w09_oracle_named_binding_and_call_timeout():
    conn = FakeConnection()
    wh = W.OracleWarehouse(connection_factory=lambda: conn, timeout_seconds=2.0)
    rows = wh.fetch_all("his.patient_by_id", {"patient_id": "P'9"}, limit=5,
                        database="", schema="MEDREC")
    assert rows == [{"Id": 1, "qptmh": "BC-1"}]
    sql, params = [c for c in conn.cursor_calls if c[0] == "execute"][0][1:]
    assert "MEDREC.PAT_MASTER_INDEX" in sql and "ROWNUM <= 5" in sql
    assert params == {"patient_id": "P'9"}
    assert "P'9" not in sql
    assert conn.call_timeout == 2000  # 毫秒
    assert [c[0] for c in conn.cursor_calls].count("cursor_close") == 1
    assert conn.closed


def test_w09_timeout_sets_cancel_and_capability_error():
    conns = [FakeConnection(behavior="timeout")]
    wh = W.SqlServerWarehouse(connection_factory=lambda: conns[0], timeout_seconds=1)
    with pytest.raises(CapabilityError) as ei:
        wh.fetch_all("pis.slide_by_barcode", {"barcode": "X"}, limit=5)
    assert ei.value.code == "SOURCE_QUERY_TIMEOUT"
    assert conns[0].cancelled >= 1  # 超时路径调用 cancel
    assert conns[0].closed


def test_w09_connect_failure_is_explicit_not_empty():
    conns = [FakeConnection(behavior="connect_fail")]
    wh = W.OracleWarehouse(connection_factory=lambda: conns[0])
    with pytest.raises(CapabilityError) as ei:
        wh.fetch_all("his.patient_by_id", {"patient_id": "P"}, limit=5)
    assert ei.value.code == "SOURCE_CONNECT_FAILED"


def test_w09_zero_rows_distinct_from_failure():
    conn = FakeConnection(behavior="empty")
    wh = W.SqlServerWarehouse(connection_factory=lambda: conn)
    rows = wh.fetch_all("pis.slide_by_barcode", {"barcode": "NOPE"}, limit=5)
    assert rows == []  # 正常空结果不是错误：连接失败才有能力错误


def test_w09_readonly_guard_blocks_non_select():
    with pytest.raises(CapabilityError) as ei:
        W._assert_readonly("DELETE FROM [PITAYA].[dbo].[Pitaya]")
    assert ei.value.code == "CAPABILITY_UNSUPPORTED"
    with pytest.raises(CapabilityError):
        W._assert_readonly("  update x set y=1")
    W._assert_readonly("SELECT TOP (5) * FROM t")  # 通过
    # 经 fetch_all 的模板级防篡改：临时替换模板为 UPDATE → 执行前阻断
    conn = FakeConnection()
    wh = W.SqlServerWarehouse(connection_factory=lambda: conn)
    saved = dict(W.TEMPLATES["pis.slide_by_barcode"])
    try:
        W.TEMPLATES["pis.slide_by_barcode"] = {
            "sqlserver": "UPDATE [{database}].[{schema}].[Pitaya_Slice] SET qpzt = ?",
            "params": ["barcode"]}
        with pytest.raises(CapabilityError):
            wh.fetch_all("pis.slide_by_barcode", {"barcode": "X"}, limit=5)
        assert not [c for c in conn.cursor_calls if c[0] == "execute"]  # 未执行
    finally:
        W.TEMPLATES["pis.slide_by_barcode"] = saved


def test_w09_fake_driver_module_via_sys_modules(monkeypatch):
    """真驱动装配路径：注入 fake pyodbc 模块 + DSN 环境变量（不连真库）。"""
    fake = types.ModuleType("pyodbc")
    created = []

    def connect(dsn):
        created.append(dsn)
        return FakeConnection()

    fake.connect = connect
    monkeypatch.setitem(sys.modules, "pyodbc", fake)
    monkeypatch.setenv("BLYB_PIS_SQLSERVER_DSN", "DSN=fake-pis;UID=u;PWD=p")
    wh = W.SqlServerWarehouse()
    rows = wh.fetch_all("pis.case_by_pathology", {"pathology_no": "P123"}, limit=3,
                        database="QPIS", schema="dbo")
    assert rows and created == ["DSN=fake-pis;UID=u;PWD=p"]
    # DSN未配置时fetch显式失败（不静默、不伪装空结果）
    monkeypatch.delenv("BLYB_PIS_SQLSERVER_DSN", raising=False)
    wh2 = W.SqlServerWarehouse()
    with pytest.raises(CapabilityError):
        wh2.fetch_all("pis.case_by_pathology", {"pathology_no": "P1"}, limit=3)
