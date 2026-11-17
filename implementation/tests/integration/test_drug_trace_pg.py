"""药品追溯迁移 009 的真实隔离 PostgreSQL 验证（DTR-A04 PG 侧）。

- 用独立容器/动态空闲端口/随机容器名/卷（不触碰任何现有库）。
- 路径：全新库（001—009 一次到位）+「001—008 旧库升级到 009」两条。
- 依赖：本机 Docker 与本地已有的 postgres:16 镜像（离线不拉取：--pull=never；
  缺 Docker/缺镜像时整个文件干净 skip——该项在现场/有 Docker 的环境执行
  并留证据；任何失败路径都在 finally 清理容器，不泄漏僵尸容器）。
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

DOCKER = shutil.which("docker")
pytestmark = pytest.mark.skipif(DOCKER is None, reason="docker 不可用（隔离 PG 验证留待有 Docker 环境）")

_IMAGE = "postgres:16"
_PASSWORD = "dtr-test-pg"


def _free_port() -> int:
    """动态分配本机空闲端口（RT-02：消除 55432 硬编码冲突）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _docker_available() -> bool:
    try:
        subprocess.run([DOCKER, "version", "--format", "{{.Server.Version}}"],
                       capture_output=True, timeout=30, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _image_available() -> bool:
    """本地已有镜像才继续（离线零拉取：--pull=never 配套）。"""
    try:
        r = subprocess.run([DOCKER, "image", "inspect", _IMAGE],
                           capture_output=True, timeout=60)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def pg():
    if not _docker_available():
        pytest.skip("docker daemon 不可用")
    if not _image_available():
        pytest.skip(f"本地无 {_IMAGE} 镜像（离线不拉取，隔离 PG 验证留待有镜像环境）")
    name = f"blyb-dtr-pg-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    try:
        subprocess.run([DOCKER, "run", "-d", "--name", name, "--pull=never",
                        "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
                        "-e", "POSTGRES_USER=blyb", "-e", "POSTGRES_DB=blyb",
                        "-p", f"127.0.0.1:{port}:5432", _IMAGE],
                       capture_output=True, check=True, timeout=300)
        url = f"postgresql+psycopg://blyb:{_PASSWORD}@127.0.0.1:{port}/blyb"
        # 等就绪
        import psycopg
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                with psycopg.connect(url.replace("+psycopg", ""), connect_timeout=3) as c:
                    with c.cursor() as cur:
                        cur.execute("SELECT 1")
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        else:
            pytest.fail("隔离 PG 未就绪")
        yield url
    finally:
        # 任何退出路径（含 docker run 失败后的残留同名容器）都清理
        subprocess.run([DOCKER, "rm", "-f", name],
                       capture_output=True, timeout=120)


def test_pg_fresh_and_upgrade_paths(pg):
    from core_db import make_engine, run_migrations
    import plugin_registry
    import app_runtime

    # 路径A：旧插件集先行（001—008）→ 增量 009（模拟 <prod-host> 升级）
    e1 = make_engine(pg)
    r1 = run_migrations(e1, extra_migrations=plugin_registry.MIGRATIONS)
    applied1 = {n for _, n, r in r1 if r == "APPLIED"}
    assert "001_core_init" in applied1 and "009_drug_trace_domain" not in applied1
    reg = app_runtime.load_registry(["drug_trace"])
    r2 = run_migrations(e1, extra_migrations=reg.migrations)
    # FIX2：009 建域表（新唯一键模型）+ 010（retry 表/scope 列/索引，幂等）
    # FIX3：011（send_quota 表 + 复合 tenant 外键；详见 test_drug_trace_pg_fix3.py）
    # TUI-01：012（daily_report 日报表）
    # RT-02：020（性能索引迁移，drug_trace_perf_migration）纳入断言
    assert [n for _, n, r in r2 if r == "APPLIED"] == [
        "009_drug_trace_domain", "010_drug_trace_fix2",
        "011_drug_trace_fix3", "012_drug_trace_daily_report",
        "020_drug_trace_perf_indexes"]
    # 幂等重跑
    r3 = run_migrations(e1, extra_migrations=reg.migrations)
    assert all(x == "ALREADY_APPLIED" for _, _, x in r3)
    # 表存在性 + PG 复合唯一索引
    from sqlalchemy import inspect, text
    insp = inspect(e1)
    for t in ("drug_trace_bill", "drug_trace_bill_revision",
              "drug_trace_code_index", "drug_trace_bill_code",
              "drug_trace_production_evidence", "drug_trace_collection_run"):
        assert insp.has_table(t), t
    with e1.connect() as c:
        idx = {r[0] for r in c.execute(text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename IN ('drug_trace_bill','drug_trace_bill_revision')"))}
        assert "ux_drug_trace_bill_tenant_id" in idx
        assert "ux_drug_trace_bill_revision_tenant_id" in idx
    # 领域写入/读取一轮（真实 PG 上的整单采集冒烟）
    from domains.drug_trace import service
    from core_db import make_session_factory
    from core_db.models import Tenant, ConnectorInstance
    from contracts import new_uuid
    sf = make_session_factory(e1)
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    from datetime import datetime
    with sf() as db:
        t = Tenant(id=new_uuid(), code="fh", name="x")
        db.add(t)
        db.flush()
        conn = ConnectorInstance(id=new_uuid(), tenant_id=t.id,
                                 direction="source", plugin_id="p",
                                 name="n", status="ENABLED")
        db.add(conn)
        db.flush()
        wh = SyntheticDrugTraceWarehouse()
        wh.add_bill({"source_system": "HIS", "source_bill_id": "PG1",
                     "source_bill_no": "PG1", "source_revision": "1",
                     "business_kind": "PATIENT_DISPENSE",
                     "pharmacy_code": "YF01", "bill_time": "2026-10-12T07:55:00",
                     "updated_at": "2026-10-12T08:00:00", "finalized": True,
                     "is_void": False})
        wh.add_code({"source_system": "HIS", "source_bill_id": "PG1",
                     "pharmacy_code": "YF01",
                     "business_kind": "PATIENT_DISPENSE",
                     "source_revision": "1", "source_line_id": "L1",
                     "source_code_record_id": "R1", "trace_code": "0001234567",
                     "updated_at": "2026-10-12T08:00:00", "is_deleted": False,
                     "quantity": "1", "unit": "盒"})
        run = service.run_collection(
            db, t.id, conn.id, wh,
            window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
        assert run["revisions_created"] == 1 and run["valid"] == 1
        from domains.drug_trace.models import DrugTraceBill
        assert db.query(DrugTraceBill).count() == 1
    e1.dispose()
