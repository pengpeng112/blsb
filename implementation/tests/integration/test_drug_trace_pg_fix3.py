"""FIX3 迁移 011 与真实隔离 PostgreSQL 恢复演练（G3-18）。

覆盖：
- 旧 009/010 库真实升级到 011（幂等；复合 tenant 外键补齐）。
- 复合 tenant 外键：跨租户/孤儿写入被数据库层拒绝（复合唯一索引≠外键）。
- 坏数据预检：存在孤儿/跨租户悬挂行时 011 报错隔离（不默删）。
- 本机隔离 PG 真正 backup→restore→对象/配置/提交/任务校验（无需生产窗口）。

依赖：本机 Docker 与 postgres:16 镜像（缺 Docker 时整个文件 skip，该项在
有 Docker 的环境执行并留证据）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

DOCKER = shutil.which("docker")
pytestmark = pytest.mark.skipif(DOCKER is None, reason="docker 不可用（隔离 PG 验证留待有 Docker 环境）")

_PORT = 55433
_PASSWORD = "dtr-fix3-pg"


def _docker_available() -> bool:
    try:
        subprocess.run([DOCKER, "version", "--format", "{{.Server.Version}}"],
                       capture_output=True, timeout=30, check=True)
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="module")
def pg():
    if not _docker_available():
        pytest.skip("docker daemon 不可用")
    name = f"blyb-dtr-pg-fix3-{uuid.uuid4().hex[:8]}"
    subprocess.run([DOCKER, "run", "-d", "--name", name,
                    "-e", f"POSTGRES_PASSWORD={_PASSWORD}",
                    "-e", "POSTGRES_USER=blyb", "-e", "POSTGRES_DB=blyb",
                    "-p", f"127.0.0.1:{_PORT}:5432", "postgres:16"],
                   capture_output=True, check=True, timeout=300)
    url = f"postgresql+psycopg://blyb:{_PASSWORD}@127.0.0.1:{_PORT}/blyb"
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
        subprocess.run([DOCKER, "rm", "-f", name], capture_output=True)
        pytest.fail("隔离 PG 未就绪")
    yield url, name
    subprocess.run([DOCKER, "rm", "-f", name], capture_output=True, timeout=120)


def _pg_url(db: str) -> str:
    return f"postgresql+psycopg://blyb:{_PASSWORD}@127.0.0.1:{_PORT}/{db}"


@pytest.fixture()
def fresh_db(pg):
    """每个测试独占一个空数据库（同一容器内 createdb/dropdb，互不污染）。"""
    import psycopg
    url, cname = pg
    dbname = f"fix3_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(url.replace("+psycopg", ""), autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{dbname}"')
    yield _pg_url(dbname), cname, dbname
    with psycopg.connect(url.replace("+psycopg", ""), autocommit=True) as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE datname = %s AND pid <> pg_backend_pid()", (dbname,))
        c.execute(f'DROP DATABASE IF EXISTS "{dbname}"')


def test_g3_18_migration_011_fk_and_upgrade(fresh_db):
    """旧 001—008 库升级（009→010→011）；复合 tenant 外键生效；幂等重跑。"""
    from core_db import make_engine, make_session_factory, run_migrations
    import plugin_registry
    import app_runtime
    from sqlalchemy import inspect, text

    url = fresh_db[0]
    e1 = make_engine(url)
    run_migrations(e1, extra_migrations=plugin_registry.MIGRATIONS)  # 001—008
    reg = app_runtime.load_registry(["drug_trace"])
    r2 = run_migrations(e1, extra_migrations=reg.migrations)
    assert [n for _, n, r in r2 if r == "APPLIED"] == \
        ["009_drug_trace_domain", "010_drug_trace_fix2", "011_drug_trace_fix3",
         "012_drug_trace_daily_report", "020_drug_trace_perf_indexes"]
    # 幂等
    r3 = run_migrations(e1, extra_migrations=reg.migrations)
    assert all(x == "ALREADY_APPLIED" for _, _, x in r3)

    with e1.connect() as c:
        # 011 结构：send_quota 表 + 复合 tenant 外键（复合唯一索引不替代外键）
        insp_fks = {r[0] for r in c.execute(text(
            "SELECT conname FROM pg_constraint WHERE contype='f' AND conrelid "
            "IN ('drug_trace_bill_revision'::regclass,"
            " 'drug_trace_code_index'::regclass,"
            " 'drug_trace_collection_run'::regclass,"
            " 'drug_trace_retry_bill'::regclass)"))}
        assert {"fk_dt_rev_bill_tenant", "fk_dt_code_rev_tenant",
                "fk_dt_run_conn_tenant", "fk_dt_retry_conn_tenant"} <= insp_fks
        # 孤儿/跨租户写入被数据库拒绝（复合 FK；父行经 ORM 建立保证必填列齐全）
        from domains.drug_trace.models import DrugTraceBill
        from domains.drug_trace.models import DrugTraceBillRevision as _Rev
    sf1 = make_session_factory(e1)
    with sf1() as db:
        db.execute(text(
            "INSERT INTO tenant(id, code, name, timezone, status, created_at) "
            "VALUES ('t1','tt','T','Asia/Shanghai','ACTIVE',now())"))
        bill = DrugTraceBill(id="b1", tenant_id="t1", connector_id="c1",
                             source_system="HIS", pharmacy_code="YF01",
                             business_kind="K", source_bill_id="S1",
                             source_bill_no="S1")
        db.add(bill)
        db.flush()
        # 同租户正常子行 OK（ORM 建行 → 必填列齐全）
        db.add(_Rev(id="r1", tenant_id="t1", bill_id="b1",
                    source_revision="1", content_digest="d",
                    header={}, lines=[]))
        db.flush()
        # 跨租户悬挂：父行存在但 tenant 不同 → 复合 FK 拒绝
        db.execute(text(
            "INSERT INTO tenant(id, code, name, timezone, status, created_at) "
            "VALUES ('t2','t2','T2','Asia/Shanghai','ACTIVE',now())"))
        with pytest.raises(Exception) as ei:
            db.execute(text(
                "INSERT INTO drug_trace_bill_revision(id, tenant_id, bill_id,"
                " source_revision, content_digest, header, lines, quality_state,"
                " quality_issues, source_evidence, created_at)"
                " VALUES ('r2','t2','b1','2','d','{}','[]','WAITING','[]',"
                "'{}',now())"))
        assert "fk_dt_rev_bill_tenant" in str(ei.value)
        db.rollback()
    e1.dispose()


def test_g3_18b_migration_011_orphan_preflight_blocks(fresh_db):
    """坏数据预检：011 前已存在孤儿行 → 报错隔离（不默删、不跳过）。"""
    from core_db import make_engine, run_migrations
    import plugin_registry
    import app_runtime
    from sqlalchemy import text

    url = fresh_db[0]
    e1 = make_engine(url)
    run_migrations(e1, extra_migrations=plugin_registry.MIGRATIONS)
    reg = app_runtime.load_registry(["drug_trace"])
    migrations = {m.name: m for m in reg.migrations}
    # 手工模拟「只应用 009/010 且已产生孤儿」的旧库：直接建 009（含孤儿）→
    # 跳过 010 修复步骤中的 FK（010 不建 FK）→ 011 预检应报错
    run_migrations(e1, extra_migrations=[migrations["009_drug_trace_domain"]])
    with e1.begin() as c:
        c.execute(text(
            "INSERT INTO tenant(id, code, name, timezone, status, created_at) "
            "VALUES ('t1','tt','T','Asia/Shanghai','ACTIVE',now())"))
        c.execute(text(
            "INSERT INTO drug_trace_bill_revision(id, tenant_id, bill_id,"
            " source_revision, content_digest, header, lines, quality_state,"
            " quality_issues, source_evidence, created_at)"
            " VALUES ('orphan-r','t1','no-such-bill','1','d','{}','[]',"
            "'WAITING','[]','{}',now())"))
    with pytest.raises(RuntimeError) as ei:
        run_migrations(e1, extra_migrations=[migrations["011_drug_trace_fix3"]])
    assert "orphan" in str(ei.value)
    e1.dispose()


def test_g3_18c_real_backup_restore_drill(fresh_db):
    """真实 backup→restore→对象/交换/配置/提交/任务校验（本机隔离 PG 演练）。

    - 源库灌入代表性数据（租户/连接器/字典/配置/目的地/采集→冻结→提交/任务/
      配额台账），pg_dump 导出；
    - 新建空库恢复，逐类校验行数与关键内容一致、迁移台账完整（001—011）、
      领域查询在恢复库上可继续工作。
    """
    from core_db import make_engine, make_session_factory, run_migrations
    import app_runtime
    from sqlalchemy import text

    url, cname, dbname = fresh_db
    e1 = make_engine(url)
    reg = app_runtime.load_registry(["drug_trace"])
    run_migrations(e1, extra_migrations=reg.migrations)
    sf = make_session_factory(e1)

    # ---- 灌数据（复用领域服务：真实采集→冻结→PREPARED 提交）
    from contracts import new_uuid
    from core_db.models import (ConfigRevision, ConnectorInstance, Destination,
                                DictionaryRevision, Job, Submission, Tenant)
    from domains.drug_trace import service
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    from datetime import datetime
    with sf() as db:
        t = Tenant(id=new_uuid(), code="restore-hospital", name="恢复演练")
        db.add(t)
        db.flush()
        tid = t.id
        conn = ConnectorInstance(id=new_uuid(), tenant_id=tid, direction="source",
                                 plugin_id="sources.db_view.drug_trace",
                                 name="his", status="ENABLED")
        db.add(conn)
        db.flush()
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "HOSP-REF"}]),
                ("drugtrace.query_bill_type", [{"code": "323", "target": "A"}])):
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=tid, namespace=ns, version="1",
                items=items, source="target", approved_by="admin"))
        db.commit()
        conn_id = conn.id
    wh = SyntheticDrugTraceWarehouse()
    wh.add_bill({"source_system": "HIS", "source_bill_id": "RS1",
                 "source_bill_no": "RS1", "source_revision": "1",
                 "business_kind": "PATIENT_DISPENSE",
                 "pharmacy_code": "YF01", "bill_time": "2026-10-12T07:55:00",
                 "updated_at": "2026-10-12T08:00:00", "finalized": True,
                 "is_void": False, "source_bill_kind_raw": "323"})
    wh.add_code({"source_system": "HIS", "source_bill_id": "RS1",
                 "pharmacy_code": "YF01",
                 "business_kind": "PATIENT_DISPENSE", "source_revision": "1",
                 "source_line_id": "L1", "source_code_record_id": "R1",
                 "trace_code": "000123456789012345",
                 "updated_at": "2026-10-12T08:00:00", "is_deleted": False,
                 "quantity": "1", "unit": "盒"})
    from domains.drug_trace.models import DrugTraceBillRevision
    with sf() as db:
        service.run_collection(db, tid, conn_id, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        dest = Destination(id=new_uuid(), tenant_id=tid, code="restore-dest",
                           profile_revision="alihealth_yljg_retail_v1",
                           mode="PUSH", capabilities=["upload"],
                           is_mock=True, status="ENABLED")
        cfg = ConfigRevision(
            id=new_uuid(), tenant_id=tid, config_type="connector",
            owner_id=dest.id, revision=1, content_json={"app_key": "restore"},
            digest="d", state="PUBLISHED")
        dest.config_revision = cfg.id
        db.add(dest)
        db.add(cfg)
        db.commit()
        cfg_id = cfg.id
        prep = service.prepare_submission(
            db, tid, rev.id, profile="alihealth_yljg_retail_v1",
            destination_id=dest.id, config_release_id=cfg_id)
        sub_id = prep["submission_id"]
        # 任务表也留一行（对账 job），配额台账一行
        db.add(Job(id=new_uuid(), tenant_id=tid,
                   job_type="drug_trace.reconcile", dedup_key="recon:seed",
                   payload={}))
        from domains.drug_trace.models import DrugTraceSendQuota
        db.add(DrugTraceSendQuota(
            id=new_uuid(), tenant_id=tid, evidence_id="ev-seed",
            used=1, max_bills=5))
        db.commit()
    with sf() as db:
        expected = {
            "tenant": db.query(Tenant).count(),
            "connector_instance": db.query(ConnectorInstance).count(),
            "dictionary_revision": db.query(DictionaryRevision).count(),
            "config_revision": db.query(ConfigRevision).count(),
            "destination": db.query(Destination).count(),
            "submission": db.query(Submission).count(),
            "job": db.query(Job).count(),
            "drug_trace_bill": db.execute(
                text("SELECT count(*) FROM drug_trace_bill")).scalar(),
            "drug_trace_bill_revision": db.execute(
                text("SELECT count(*) FROM drug_trace_bill_revision")).scalar(),
            "drug_trace_code_index": db.execute(
                text("SELECT count(*) FROM drug_trace_code_index")).scalar(),
            "drug_trace_send_quota": db.execute(
                text("SELECT count(*) FROM drug_trace_send_quota")).scalar(),
            "schema_migrations": db.execute(
                text("SELECT count(*) FROM schema_migrations")).scalar(),
        }
        assert expected["submission"] == 1 and expected["job"] >= 1
    e1.dispose()

    # ---- pg_dump（容器内）→ 新库 restore
    dump = "/tmp/fix3-restore.sql"
    restored = dbname + "_restored"
    subprocess.run([DOCKER, "exec", cname, "pg_dump", "-U", "blyb",
                    "-d", dbname, "-f", dump],
                   capture_output=True, check=True, timeout=300)
    subprocess.run([DOCKER, "exec", cname, "createdb", "-U", "blyb", restored],
                   capture_output=True, check=True, timeout=120)
    subprocess.run([DOCKER, "exec", cname, "psql", "-U", "blyb",
                    "-d", restored, "-v", "ON_ERROR_STOP=1", "-f", dump],
                   capture_output=True, check=True, timeout=600)

    # ---- 恢复库逐类校验
    e2 = make_engine(_pg_url(restored))
    sf2 = make_session_factory(e2)
    with sf2() as db:
        from domains.drug_trace.models import DrugTraceBill
        got = {
            "tenant": db.query(Tenant).count(),
            "connector_instance": db.query(ConnectorInstance).count(),
            "dictionary_revision": db.query(DictionaryRevision).count(),
            "config_revision": db.query(ConfigRevision).count(),
            "destination": db.query(Destination).count(),
            "submission": db.query(Submission).count(),
            "job": db.query(Job).count(),
            "drug_trace_bill": db.query(DrugTraceBill).count(),
            "drug_trace_bill_revision": db.query(DrugTraceBillRevision).count(),
            "drug_trace_code_index": db.execute(
                text("SELECT count(*) FROM drug_trace_code_index")).scalar(),
            "drug_trace_send_quota": db.execute(
                text("SELECT count(*) FROM drug_trace_send_quota")).scalar(),
            "schema_migrations": db.execute(
                text("SELECT count(*) FROM schema_migrations")).scalar(),
        }
        assert got == expected, f"恢复后行数不一致: {got} != {expected}"
        # 关键内容：提交状态/冻结身份/配额计数
        sub = db.get(Submission, sub_id)
        assert sub is not None and sub.state == "PREPARED"
        quota = db.execute(text(
            "SELECT used, max_bills FROM drug_trace_send_quota")).one()
        assert tuple(quota) == (1, 5)
        # 迁移台账完整（001—011 都在）
        names = {r[0] for r in db.execute(
            text("SELECT name FROM schema_migrations"))}
        assert "011_drug_trace_fix3" in names
        # 恢复库上领域查询继续可用（对源单据仍可定位/幂等重导）
        bill = db.query(DrugTraceBill).one()
        assert bill.source_bill_id == "RS1"
    e2.dispose()
    subprocess.run([DOCKER, "exec", cname, "dropdb", "-U", "blyb", restored],
                   capture_output=True, check=True)
