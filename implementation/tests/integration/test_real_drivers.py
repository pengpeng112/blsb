"""真实数据库驱动集成入口（有DSN才执行；默认skip——如实记录，不假成功）。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402


@pytest.mark.skipif(not os.environ.get("BLYB_PIS_SQLSERVER_DSN"),
                    reason="BLYB_PIS_SQLSERVER_DSN 未配置（B01 授权后启用）")
def test_sqlserver_live():
    from sources.db_view.warehouse import SqlServerWarehouse, render_sql
    wh = SqlServerWarehouse()
    sql = render_sql("pis.slide_by_barcode", "sqlserver", limit=2,
                     database=os.environ.get("BLYB_PIS_DB", "PITAYA"),
                     schema="dbo")
    assert "TOP (2)" in sql and "?" in sql
    rows = wh.fetch_all("pis.slide_by_barcode", {"barcode": "000000"}, 2)
    assert isinstance(rows, list), "返回行数组（0行合法）"


@pytest.mark.skipif(not os.environ.get("BLYB_ORACLE_DSN"),
                    reason="BLYB_ORACLE_DSN 未配置（B01/B05 授权后启用）")
def test_oracle_live():
    from sources.db_view.warehouse import OracleWarehouse
    wh = OracleWarehouse()
    rows = wh.fetch_all("his.patient_by_id", {"patient_id": "P0001"}, 2)
    assert isinstance(rows, list)


def test_drivers_explicitly_unavailable_without_dsn():
    """无驱动/DSN时必须显式失败（CAPABILITY/DRIVER_UNAVAILABLE），不得假成功。"""
    from connector_sdk import CapabilityError
    from sources.db_view.warehouse import OracleWarehouse, SqlServerWarehouse
    try:
        import pyodbc  # noqa: F401
        pytest.skip("pyodbc installed")
    except ImportError:
        with pytest.raises(CapabilityError):
            SqlServerWarehouse()
    try:
        import oracledb  # noqa: F401
    except ImportError:
        with pytest.raises(CapabilityError):
            OracleWarehouse()
