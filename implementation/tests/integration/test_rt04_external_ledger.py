"""RT-04 A3：外部导入计数口径（行级恒等式/记录级两口径）、sent-only 观测、
详情 hook、分类统计、EXTERNAL 处置拒绝、迁移022 来源实例隔离。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import Tenant  # noqa: E402
from plugins.report_targets import seed_default_targets  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt04a3.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["infectious"])
    engine = make_engine()
    results = run_migrations(engine, extra_migrations=reg.migrations)
    applied = {r[0] for r in results if r[2] == "APPLIED"}
    assert 21 in applied and 22 in applied       # 迁移021/022 本库执行
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt04-a3", name="外部台账")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _receipt(rid, table, ts, *, result=True, code="00", attempt=0):
    return {"id": rid, "result": result, "errorCode": code, "desc": "合成回执",
            "attempt": attempt, "stage": "", "mode": "", "table": table, "ts": ts}


# PT-02 修复：分类/台账窗口按"最近 N 天"滚动——硬编码 2026-11-10 会在日历
# 滚出 7 天窗后整批红（11-17 绿、11-18 红的日历漂移）。改相对日期：
# D1=昨天、D0=前天（源时间 CST 口径；尝试时刻=CST-8 的 UTC）。
from datetime import datetime as _dt, timedelta as _td  # noqa: E402
_D1 = (_dt.now() - _td(days=1)).strftime("%Y-%m-%d")
_D0 = (_dt.now() - _td(days=2)).strftime("%Y-%m-%d")


def _jl(objs) -> bytes:
    """JSON 行（dict 或已序列化字符串）→ JSONL bytes（不二次 dumps）。"""
    parts = [o if isinstance(o, str)
             else json.dumps(o, ensure_ascii=False) for o in objs]
    return ("\n".join(parts) + "\n").encode("utf-8")


# ---------------------------------------------------------------- 行级恒等式
def test_a3_row_identity_equation_and_shape_invalid_counted(env):
    """rows_read = rows_valid + parse + shape + privacy（SHAPE_INVALID 修复：
    坏形状行计入 rows_read 与拒绝，不再漏计）。"""
    from plugins.external_ledger import import_files
    lines = [
        json.dumps(_receipt("R1", "death", _D1 + " 08:00:00")),
        "{bad json",                                   # NOT_JSON → parse
        json.dumps([1, 2]),                            # 非 dict → SHAPE_INVALID
        json.dumps({"result": True}),                  # 缺 id/table → shape
        json.dumps({"id": "11010119900101000X",        # 证件号形态 → privacy
                    "result": True, "table": "death",
                    "ts": _D1 + " 08:00:00"}),
        json.dumps(_receipt("R2", "death", _D1 + " 09:00:00")),
    ]
    content = _jl(lines) + b"\n\n"        # 追加真实空行（不计 rows_read）
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-20261110.jsonl", "content": content}],
            source_instance="eq", source_tz="CST", actor="t")
        db.commit()
        assert stats["rows_read"] == 6          # 非空行 6（空行不计）
        assert stats["rows_rejected_parse"] == 1
        assert stats["rows_rejected_shape"] == 2
        assert stats["rows_rejected_privacy"] == 1
        assert stats["rows_rejected"] == 4
        assert stats["rows_valid"] == 2
        assert stats["rows_read"] == stats["rows_valid"] + \
            stats["rows_rejected_parse"] + stats["rows_rejected_shape"] + \
            stats["rows_rejected_privacy"]


def test_a3_multi_receipt_merge_record_level_counts(env):
    """多行回执合并为一个记录：行/记录两口径分开；重复导入 → created=0、
    unchanged 计数；后到旧失败不覆盖新成功。"""
    from plugins.external_ledger import import_files
    r = "MR1"
    lines1 = [
        _receipt(r, "death", _D1 + " 08:00:00", result=True),
        _receipt(r, "death", _D1 + " 09:00:00", result=True),
        _receipt(r, "death", _D1 + " 07:00:00", result=False, code="02"),
    ]
    with env["sf"]() as db:
        s1 = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-20261110.jsonl", "content": _jl(lines1)}],
            source_instance="mr", source_tz="CST", actor="t")
        db.commit()
        # 行级：3 行全 valid；记录级：1 个业务键
        assert s1["rows_valid"] == 3 and s1["records_created"] == 1
        assert s1["records_total"] == 1
        # 后到旧失败（07:00 < 09:00）不覆盖新成功
        from core_db.models import ExternalSubmissionRecord
        rec = (db.query(ExternalSubmissionRecord)
               .filter_by(record_key=f"death/{r}").one())
        assert rec.bucket == "ACCEPTED"
        # source_tz=CST → 入库统一 UTC（09:00 CST = 01:00 UTC）
        assert rec.last_attempt_at.strftime("%Y-%m-%d %H:%M") == _D1 + " 01:00"
        # 重复导入（同内容）：created=0、unchanged=1、行账 duplicate 计数
        s2 = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-20261110.jsonl", "content": _jl(lines1)}],
            source_instance="mr", source_tz="CST", actor="t")
        db.commit()
        assert s2["records_created"] == 0
        assert s2["records_unchanged"] == 1
        assert s2["lines_duplicate_position"] == 3
        assert db.query(ExternalSubmissionRecord).count() == 1


def test_a3_dry_run_zero_writes_and_counts(env):
    from plugins.external_ledger import import_files
    from core_db.models import (ExternalImportBatch,
                                ExternalSubmissionRecord)
    lines = [_receipt("DR1", "death", _D1 + " 08:00:00")]
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-20261110.jsonl", "content": _jl(lines)}],
            source_instance="dr", source_tz="CST", actor="t", dry_run=True)
        db.rollback()
        assert stats["dry_run"] is True and stats["records_total"] == 1
        assert db.query(ExternalImportBatch).count() == 0
        assert db.query(ExternalSubmissionRecord).count() == 0


# ---------------------------------------------------------------- sent-only
def test_a3_sent_only_observation_records_and_coalesce_list(env):
    """纯 sent.jsonl → SENT_LEDGER_ONLY 观测（bucket=ACCEPTED 依据生产代码
    证明；不映射 CONFIRMED；last_attempt_at 未知）；列表 COALESCE 稳定排序
    不丢 sent-only 行。"""
    from domains.infectious import hooks
    from plugins.domain_hooks import LedgerFilter
    from plugins.external_ledger import import_files
    sent = [
        {"id": "death/S1", "opTime": _D0 + " 08:00:00", "table": "death",
         "ts": _D0 + " 08:00:01"},
        {"id": "death/S2", "opTime": _D0 + " 09:00:00", "table": "death",
         "ts": "2026-11-09 09:00:01"},
        {"id": "death/S3", "opTime": _D0 + " 10:00:00", "table": "death",
         "ts": "2026-11-09 10:00:01"},
    ]
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "sent.jsonl", "content": _jl(sent)}],
            source_instance="so", source_tz="CST", actor="t")
        db.commit()
        assert stats["sent_only_records"] == 3
        # 三页翻完不丢行（NULL 排序键经 COALESCE 稳定分页）
        seen = []
        cursor = None
        for _ in range(5):
            page = hooks.ledger_search(db, env["tid"], "inf_cdc_frontend",
                                       LedgerFilter(), cursor, 2)
            seen.extend(r.id for r in page.items)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        assert len(seen) == 3 and len(set(seen)) == 3
        rows = {r.business_key: r for r in
                hooks.ledger_search(db, env["tid"], "inf_cdc_frontend",
                                    LedgerFilter(), None, 10).items}
        assert rows["death/S1"].raw_state == "SENT_LEDGER_ONLY"
        assert rows["death/S1"].bucket == "ACCEPTED"


# ---------------------------------------------------------------- 详情/分类
@pytest.fixture()
def loaded(env):
    from plugins.external_ledger import import_files
    lines = [
        _receipt("DT1", "death", _D1 + " 08:00:00"),
        _receipt("AD1", "admission", _D1 + " 09:00:00",
                 result=False, code="02"),
        _receipt("EX1", "exam", _D1 + " 10:00:00"),
    ]
    sent = [{"id": "death/S1", "opTime": _D0 + " 08:00:00",
             "table": "death", "ts": _D0 + " 08:00:01"}]
    with env["sf"]() as db:
        import_files(db, env["tid"], "inf_cdc_frontend",
                     files=[{"name": "receipts-20261110.jsonl",
                             "content": _jl(lines)},
                            {"name": "sent.jsonl", "content": _jl(sent)}],
                     source_instance="cls", source_tz="CST", actor="t")
        db.commit()
    return env


def test_a3_ledger_detail_bound_and_masked(loaded):
    """详情 200 + 跨租户/不存在拒绝；字段口径（源版本时间≠尝试时间；
    sent-only 尝试时间未知=None）。"""
    from domains.infectious import hooks
    env = loaded
    with env["sf"]() as db:
        from core_db.models import ExternalSubmissionRecord
        rec = (db.query(ExternalSubmissionRecord)
               .filter_by(record_key="death/DT1").one())
        d = hooks.ledger_detail(db, env["tid"], "inf_cdc_frontend", rec.id)
        assert d and d["kind"] == "external_record"
        assert d["doc_type_code"] == "17"      # PT-03 权威编码：death=17（旧映射 07 错位已修）
        assert d["source_op_time"] is None      # 回执行不带源 opTime（不冒充）
        assert d["last_attempt_at"].startswith(_D1 + "T00:00")  # 08:00 CST→UTC
        assert d["attempts_lower_bound"] == 1
        assert d["imported_at"] is not None
        # 跨租户 → 按不存在处理
        assert hooks.ledger_detail(db, "other-tenant", "inf_cdc_frontend",
                                   rec.id) is None
        # 未知 id → None（路由层 404）
        assert hooks.ledger_detail(db, env["tid"], "inf_cdc_frontend",
                                   "no-such") is None
        so = (db.query(ExternalSubmissionRecord)
              .filter_by(record_key="death/S1").one())
        d2 = hooks.ledger_detail(db, env["tid"], "inf_cdc_frontend", so.id)
        assert d2["last_attempt_at"] is None          # 未知不冒充
        assert d2["source_op_time"] is not None       # 源 opTime 保留


def test_a3_classification_matrix_and_alignment(loaded):
    """六类文书 × REPORT_BUCKETS（含零值行）+ 总和与同窗口台账对齐 +
    外部/影子 Mock 分栏。"""
    from domains.infectious import hooks
    env = loaded
    with env["sf"]() as db:
        out = hooks.classification(db, env["tid"], "inf_cdc_frontend", 7)
        assert out["sum_in_window"] == out["ledger_window_total"]
        types = {t["doc_type_code"]: t for t in out["doc_types"]}
        # PT-03：完整 13 张在推接口全在（含零值行）+ 未登记其他表
        from contracts import REPORT_BUCKETS
        for code in ("06", "07", "08", "09", "10", "11", "12", "13",
                     "14", "15", "16", "17", "18", ""):
            assert code in types, code
            assert set(types[code]["buckets"].keys()) == set(REPORT_BUCKETS)
        # PT-03 权威编码：death=17、admission=09、exam=11（旧映射错位已修）
        assert types["17"]["buckets"]["ACCEPTED"] == 2      # DT1 + sent-only S1（death）
        assert types["09"]["buckets"]["REJECTED"] == 1      # AD1 code=02（admission，桶=REJECTED）
        assert types["11"]["buckets"]["ACCEPTED"] == 1      # EX1（exam）
        assert types["18"]["total"] == 0                    # 生命体征零值行
        assert out["source_instances"] == ["cls"]
        assert out["last_import_at"] is not None
        assert "shadow_mock" in out and isinstance(
            out["shadow_mock"]["items"], list)
        # 其他插件未认领的业务 → None
        assert hooks.classification(db, env["tid"], "drug_trace", 7) is None


def test_a3_classification_route_scope_and_contract(loaded):
    """GET /report-targets/{code}/classification：登录+范围守卫；契约字段。"""
    from contracts.api import hash_token
    env = loaded
    from core_db.models import AuthSubject
    with env["sf"]() as db:
        other = Tenant(id=new_uuid(), code="rt04-a3x", name="他租户")
        db.add(other)
        db.flush()
        db.add(AuthSubject(id=new_uuid(), tenant_id=env["tid"], kind="user",
                           subject_code="u-op", roles=["operator"],
                           token_hash=hash_token("tk-a3"), status="ACTIVE",
                           target_scopes=["inf_cdc_frontend"]))
        db.add(AuthSubject(id=new_uuid(), tenant_id=other.id, kind="user",
                           subject_code="u-x", roles=["operator"],
                           token_hash=hash_token("tk-a3x"), status="ACTIVE",
                           target_scopes=["drug_trace"]))   # 无该业务范围
        db.commit()
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    import app_runtime
    reg = app_runtime.load_registry(["infectious"])
    client = TestClient(create_app(session_factory=env["sf"],
                                   engine=env["engine"], registry=reg))
    hdr = {"Authorization": "Bearer tk-a3"}
    r = client.get("/api/v1/report-targets/inf_cdc_frontend/classification",
                   headers=hdr)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert {"doc_types", "sum_in_window", "ledger_window_total",
            "unknown_time_records", "source_instances", "observed_until",
            "last_import_at", "shadow_mock", "window"} <= set(data)
    # 范围外账号 403
    r2 = client.get("/api/v1/report-targets/inf_cdc_frontend/classification",
                    headers={"Authorization": "Bearer tk-a3x"})
    assert r2.status_code == 403
    # 未提供分类的业务 → NOT_IMPLEMENTED（无插件认领；drug_trace 不认领）
    r3 = client.get("/api/v1/report-targets/drug_trace/classification",
                    headers=hdr)
    assert r3.status_code == 404 or r3.status_code == 403  # 无范围→403


def test_a3_external_manual_resolution_rejected(env):
    """EXTERNAL 运行形态业务不开放平台 UNKNOWN 手工重发（外部 record 不是
    Submission）。"""
    from core_db.models import AuthSubject
    from contracts.api import hash_token
    with env["sf"]() as db:
        db.add(AuthSubject(id=new_uuid(), tenant_id=env["tid"], kind="user",
                           subject_code="u-rev",
                           roles=["business_reviewer"],
                           token_hash=hash_token("tk-rev"), status="ACTIVE",
                           target_scopes=["*"]))
        db.commit()
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=env["sf"],
                                   engine=env["engine"], registry=None))
    r = client.post(
        "/api/v1/report-center/submissions/inf_cdc_frontend/any-id/"
        "manual-resolution",
        json={"resolution": "CONFIRMED", "reason": "人工确认测试",
              "evidence_ref": "x", "expected_state": "UNKNOWN"},
        headers={"Authorization": "Bearer tk-rev"})
    assert r.status_code == 404 or r.status_code == 403 or \
        (r.status_code == 400 and "USE_DOMAIN_WORKFLOW" in r.text)
    # 直接路由层验证（绕过 404 链）：带绑定目的地也拒绝
    with env["sf"]() as db:
        from core_db.models import Destination
        from plugins import report_targets as RT
        t = RT.get_target(db, env["tid"], "inf_cdc_frontend")
        d = Destination(id=new_uuid(), tenant_id=env["tid"], code="inf-x",
                        profile_revision="destination.generic_http_json@1",
                        mode="PUSH", is_mock=True, status="ENABLED")
        db.add(d)
        db.flush()
        t.destination_id = d.id
        db.commit()
    r2 = client.post(
        "/api/v1/report-center/submissions/inf_cdc_frontend/any-id/"
        "manual-resolution",
        json={"resolution": "CONFIRMED", "reason": "人工确认测试",
              "evidence_ref": "x", "expected_state": "UNKNOWN"},
        headers={"Authorization": "Bearer tk-rev"})
    assert r2.status_code == 400
    assert "USE_DOMAIN_WORKFLOW" in r2.text
    assert "外部" in r2.json()["error"]["message"]


def test_a3_migration022_old_db_unique_key_upgrade(tmp_path):
    """迁移022：旧库（旧唯一键 uq_esr_key）升级 → 新键含 source_instance，
    数据原样保留。"""
    from sqlalchemy import create_engine, inspect as sa_inspect, text
    from core_db import models as M
    # 手工建旧形态表（模拟 LT-05 旧库）
    eng = create_engine(f"sqlite:///{tmp_path}/old022.sqlite3")
    old_cols = [(c.name, c.type) for c in
                M.ExternalSubmissionRecord.__table__.columns]
    col_defs = ", ".join(
        f'"{n}" {"VARCHAR(%d)" % (getattr(t, "length", None) or 64)
         if "VARCHAR" in str(t) else str(t)}'
        for n, t in old_cols if n != "source_instance")
    with eng.begin() as c:
        c.execute(text(
            f'CREATE TABLE external_submission_record ({col_defs}, '
            'CONSTRAINT uq_esr_key UNIQUE (tenant_id, target_code, '
            'record_key))'))
        c.execute(text(
            "INSERT INTO external_submission_record (id, tenant_id, "
            "target_code, record_key, business_type, doc_type_code) "
            "VALUES ('r1','t1','inf','death/X1','death','07')"))
    from core_db.migrate import _022_external_record_source_instance_identity
    _022_external_record_source_instance_identity(eng)
    insp = sa_inspect(eng)
    assert insp.has_table("external_submission_record")
    with eng.connect() as c:
        n = c.execute(text("SELECT count(*) FROM "
                           "external_submission_record")).scalar()
        assert n == 1                       # 数据保留
        ddl = c.execute(text(
            "SELECT sql FROM sqlite_master WHERE name="
            "'external_submission_record'")).scalar()
        assert "uq_esr_key_src" in ddl and "source_instance" in ddl
    eng.dispose()
