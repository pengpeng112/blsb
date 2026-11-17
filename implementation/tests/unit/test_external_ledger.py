"""LT-05 A 段单测：导入器白名单/脱敏/幂等/时区/拒绝/dry-run + summary/ledger
钩子 + 次序归并（旧失败/Mock 不覆盖新成功）。"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (ExternalImportBatch,  # noqa: E402
                            ExternalSubmissionRecord, Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "fixtures", "inf_pusher_state")

# RT-02：夹具日期固定在 2026-11-05（CST），hooks.target_summary 的 7 天滚动
# 窗口随真实时间推进越界（2026-11-12 08:00 CST 起必失败）。_files() 把全部
# 时间戳统一平移到「最新 ts = 现在(CST) - 2 小时」；平移量模块级缓存——同一
# 测试会话内多次 _files() 产出逐字节一致（幂等/去重/冲突断言依赖内容一致），
# 相对次序与语义不变。
_TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\b")
_FIX_NEWEST = datetime(2026, 11, 5, 10, 0, 0)      # 夹具中最新 ts（CST 朴素）
_shift_cache: timedelta | None = None


def _ts_shift() -> timedelta:
    global _shift_cache
    if _shift_cache is None:
        now_cst = datetime.utcnow() + timedelta(hours=8)
        _shift_cache = (now_cst - timedelta(hours=2)) - _FIX_NEWEST
    return _shift_cache


def _shift_text(text: str) -> str:
    def _sub(m: re.Match) -> str:
        t = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
        return (t + _ts_shift()).strftime("%Y-%m-%d %H:%M:%S")
    return _TS_RE.sub(_sub, text)


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/ext.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["infectious"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="ext-h", name="台账导入")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "registry": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _files():
    return [{"name": n,
             "content": _shift_text(
                 open(os.path.join(FIX, n), "r", encoding="utf-8").read()
             ).encode("utf-8")}
            for n in os.listdir(FIX)]


def test_import_fixture_classify_buckets_and_levels(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        stats = import_files(db, env["tid"], "inf_cdc_frontend",
                             files=_files(), source_label="合成夹具",
                             source_instance="synthetic", source_tz="CST",
                             actor="t")
        db.commit()
        assert stats["rows_rejected"] == 0
        recs = {r.record_key: r for r in
                db.query(ExternalSubmissionRecord).all()}
        # ACCEPTED 不升 CONFIRMED（证据级别=FRONTEND_ACCEPTED）
        death = recs["death/TEST-death-001"]
        assert death.last_result == "ACCEPTED"
        assert death.bucket == "ACCEPTED"
        assert death.evidence_level == "FRONTEND_ACCEPTED"
        # PT-03 权威编码：death=17（旧映射 07 错位已修，迁移 023 同口径修正存量）
        assert death.doc_type_code == "17"
        assert death.op_time is not None          # sent.jsonl 关联
        # 01 重试后成功（attempt=1 较新）→ ACCEPTED
        assert recs["admission/TEST-admission-001"].last_result == "ACCEPTED"
        # 02 → REJECTED；client-validate → CLIENT_INVALID→REJECTED 桶
        assert recs["exam/TEST-exam-001"].bucket == "REJECTED"
        assert recs["first_course/TEST-first-001"].last_result == "CLIENT_INVALID"
        assert recs["first_course/TEST-first-001"].bucket == "REJECTED"
        # network 3 次失败 → UNKNOWN；attempts=可见行数下界 3
        daily = recs["daily_course/TEST-daily-001"]
        assert daily.last_result == "NETWORK_ERROR" and daily.bucket == "UNKNOWN"
        assert daily.attempts == 3
        # Mock 单列 NOT_REPORTABLE
        mock = recs["exam/TEST-exam-002"]
        assert mock.bucket == "NOT_REPORTABLE"
        assert mock.last_result == "MOCK"


def test_import_whitelist_and_redact(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        stats = import_files(db, env["tid"], "inf_cdc_frontend",
                             files=_files(), source_label="s",
                             source_instance="synthetic", source_tz="CST",
                             actor="t")
        db.commit()
        assert stats["rows_read"] >= 12
        desc = [r for r in db.query(ExternalSubmissionRecord).all()
                if "TEST-exam-003" in r.record_key][0]
        assert "000000199001010000" not in desc.last_error_message   # 脱敏
        # patientName 非白名单 → 未入库（列只有白名单语义，无从存患者名）


def test_import_idcard_id_rejected_whole_row(env):
    from plugins.external_ledger import import_files
    line = json.dumps({"id": "000000199001010000", "result": True,
                       "table": "death", "ts": "2026-11-05 08:00:00"},
                      ensure_ascii=False)
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-20261106.jsonl", "content":
                    line.encode()}],
            source_instance="s2", source_tz="CST", actor="t")
        db.commit()
        assert stats["rows_rejected"] == 1
        assert any(r["reason"] == "ID_LOOKS_LIKE_IDCARD"
                   for r in stats["rejects"])
        assert db.query(ExternalSubmissionRecord).count() == 0


def test_import_requires_explicit_tz(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        with pytest.raises(ValueError, match="source_tz"):
            import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                         source_tz="", actor="t")


def test_import_dry_run_zero_writes(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        stats = import_files(db, env["tid"], "inf_cdc_frontend",
                             files=_files(), source_label="dry",
                             source_instance="synthetic", source_tz="CST",
                             actor="t", dry_run=True)
        db.rollback()
        assert stats["dry_run"] is True and stats["records_total"] > 0
        assert db.query(ExternalImportBatch).count() == 0
        assert db.query(ExternalSubmissionRecord).count() == 0


def test_import_idempotent_same_source_same_position(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        s1 = import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                          source_instance="synthetic", source_tz="CST",
                          actor="t")
        db.commit()
        n_records = db.query(ExternalSubmissionRecord).count()
        s2 = import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                          source_instance="synthetic", source_tz="CST",
                          actor="t")
        db.commit()
        assert s1["rows_applied"] == n_records
        assert s2["rows_applied"] == 0                      # 全部重复
        assert s2["rows_skipped_duplicate"] == n_records
        assert db.query(ExternalSubmissionRecord).count() == n_records


def test_import_different_source_instance_not_deduped(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                     source_instance="host-a", source_tz="CST", actor="t")
        db.commit()
        # RT-04 A3 新契约：记录唯一键含 source_instance（迁移022）——跨实例
        # 同 record_key 不再合并（各实例独立记录，证据互不覆盖）
        before = db.query(ExternalSubmissionRecord).count()
        import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                     source_instance="host-b", source_tz="CST", actor="t")
        db.commit()
        after = db.query(ExternalSubmissionRecord).count()
        assert after == before * 2              # 每实例一套记录
        insts = {r.source_instance for r in
                 db.query(ExternalSubmissionRecord).all()}
        assert insts == {"host-a", "host-b"}


def test_import_old_failure_does_not_override_new_success(env):
    from plugins.external_ledger import import_files
    day1 = json.dumps({"id": "TEST-x", "result": True, "table": "death",
                       "ts": "2026-11-05 08:00:00"}).encode()
    day2 = json.dumps({"id": "TEST-x", "result": False, "errorCode": "02",
                       "table": "death",
                       "ts": "2026-11-04 08:00:00"}).encode()   # 更旧的失败
    with env["sf"]() as db:
        import_files(db, env["tid"], "inf_cdc_frontend",
                     files=[{"name": "receipts-20261105.jsonl",
                             "content": day1}],
                     source_instance="s", source_tz="CST", actor="t")
        db.commit()
        import_files(db, env["tid"], "inf_cdc_frontend",
                     files=[{"name": "receipts-20261104.jsonl",
                             "content": day2}],
                     source_instance="s", source_tz="CST", actor="t")
        db.commit()
        rec = (db.query(ExternalSubmissionRecord)
               .filter_by(record_key="death/TEST-x").one())
        assert rec.last_result == "ACCEPTED"       # 旧失败未覆盖新成功


def test_import_tz_semantics_utc_vs_cst(env):
    from plugins.external_ledger import import_files
    line = json.dumps({"id": "TEST-tz", "result": True, "table": "death",
                       "ts": "2026-11-05 08:00:00"}).encode()
    with env["sf"]() as db:
        import_files(db, env["tid"], "inf_cdc_frontend",
                     files=[{"name": "receipts-20261105.jsonl",
                             "content": line}],
                     source_instance="cst1", source_tz="CST", actor="t")
        db.commit()
        rec = (db.query(ExternalSubmissionRecord)
               .filter_by(record_key="death/TEST-tz").one())
        assert rec.last_attempt_at.isoformat().startswith("2026-11-05T00:00")  # CST 8点=UTC 0点


def test_import_file_limits(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-20261105.jsonl",
                    "content": "not-json\n".encode()}],
            source_instance="s", source_tz="CST", actor="t")
        db.commit()
        assert stats["rows_rejected"] == 1
        # base64 非法 → 文件级拒绝（不抛出，报告含原因）
        s2 = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "receipts-x.jsonl",
                    "content_base64": "!!!not-b64"}],
            source_instance="s", source_tz="CST", actor="t")
        assert any(r["reason"] == "BASE64_INVALID" for r in s2["rejects"])


# ---------------------------------------------------------------- 钩子
def test_hooks_summary_and_ledger(env):
    from domains.infectious import hooks
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        s = hooks.target_summary(db, env["tid"], "inf_cdc_frontend", 7)
        assert s is None                       # 无批次 → None（none 数据源）
        import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                     source_label="合成夹具", source_instance="synthetic",
                     source_tz="CST", actor="t")
        db.commit()
        s2 = hooks.target_summary(db, env["tid"], "inf_cdc_frontend", 7)
        assert s2 is not None and s2.data_source == "external_ledger"
        assert s2.last_success_at is None       # 无平台业务确认证据
        assert s2.evidence_kind == "real"
        assert "最近导入" in s2.note and "数据截至" in s2.note
        assert s2.buckets.get("ACCEPTED", 0) >= 2
        assert s2.backlog_total >= 1            # network 3 次失败 UNKNOWN
        # ledger
        from plugins.domain_hooks import LedgerFilter
        page = hooks.ledger_search(db, env["tid"], "inf_cdc_frontend",
                                   LedgerFilter(bucket="UNKNOWN"), None, 50)
        assert page.total_estimate >= 1
        keys = {r.business_key for r in page.items}
        assert any("TEST-daily-001" in k for k in keys)


def test_external_import_api_permissions_and_size(env):
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    from core_db.models import AuthSubject
    from contracts.api import hash_token
    with env["sf"]() as db:
        for role, roles in (("admin", ["platform_admin"]),
                            ("op", ["operator"]), ("viewer", ["viewer"])):
            db.add(AuthSubject(id=new_uuid(), tenant_id=env["tid"],
                               kind="user", subject_code=f"u-{role}",
                               roles=roles,
                               token_hash=hash_token(f"tk-{role}"),
                               status="ACTIVE", target_scopes=["*"]))
        db.commit()
    client = TestClient(create_app(session_factory=env["sf"],
                                   engine=env["engine"],
                                   registry=env["registry"]))
    files = [{"name": n,
              "content_base64": base64.b64encode(
                  open(os.path.join(FIX, n), "rb").read()).decode()}
             for n in os.listdir(FIX)]
    body = {"target": "inf_cdc_frontend", "source_label": "api 导入",
            "source_instance": "api", "source_tz": "CST", "files": files}
    r0 = client.post("/api/v1/report-center/external-imports", json=body,
                     headers={"Authorization": "Bearer tk-viewer"})
    assert r0.status_code == 403
    r1 = client.post("/api/v1/report-center/external-imports", json=body,
                     headers={"Authorization": "Bearer tk-op"})
    assert r1.status_code == 200
    assert r1.json()["data"]["rows_applied"] > 0
    # 缺 tz → 422
    r2 = client.post("/api/v1/report-center/external-imports",
                     json={**body, "source_tz": ""},
                     headers={"Authorization": "Bearer tk-admin"})
    assert r2.status_code == 422
    # 超限 → 413
    big = {**body, "files": [{"name": "receipts-x.jsonl",
                              "content_base64": "A" * (21 * 1024 * 1024)}]}
    r3 = client.post("/api/v1/report-center/external-imports", json=big,
                     headers={"Authorization": "Bearer tk-admin"})
    assert r3.status_code == 413
    r4 = client.get("/api/v1/report-center/external-imports?target=inf_cdc_frontend",
                    headers={"Authorization": "Bearer tk-viewer"})
    assert r4.status_code == 200 and len(r4.json()["data"]) == 1


# ---------------------------------------------------------------- 补充（CLI/游标/边界）
def test_cli_dry_run_and_import(env, capsys):
    from apps.import_external_ledger import main
    tid = _tid(env)
    rc = main(["--tenant", tid, "--target", "inf_cdc_frontend",
               "--dir", FIX, "--source-tz", "CST",
               "--source-instance", "cli", "--dry-run", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and out["records_total"] > 0
    rc2 = main(["--tenant", tid, "--target", "inf_cdc_frontend",
                "--dir", FIX, "--source-tz", "CST",
                "--source-instance", "cli2"])
    assert rc2 == 0
    with pytest.raises(SystemExit) as ei:      # argparse 缺 --source-tz → 退出码 2
        main(["--tenant", tid, "--target", "inf_cdc_frontend", "--dir", FIX])
    assert ei.value.code == 2
    capsys.readouterr()


def _tid(env):
    with env["sf"]() as db:
        return db.query(Tenant).first().id


def test_ledger_cursor_context_mismatch(env):
    from domains.infectious import hooks
    from plugins.external_ledger import import_files
    from plugins.domain_hooks import LedgerFilter
    from contracts import ApiError
    with env["sf"]() as db:
        import_files(db, env["tid"], "inf_cdc_frontend", files=_files(),
                     source_instance="s", source_tz="CST", actor="t")
        db.commit()
        page = hooks.ledger_search(db, env["tid"], "inf_cdc_frontend",
                                   LedgerFilter(), None, 2)
        assert page.next_cursor
        with pytest.raises(ApiError) as ei:
            hooks.ledger_search(db, env["tid"], "inf_cdc_frontend",
                                LedgerFilter(bucket="UNKNOWN"),
                                page.next_cursor, 2)
        assert ei.value.code == "CURSOR_CONTEXT_MISMATCH"


def test_registry_hooks_aggregation_and_no_batch_none(env):
    with env["sf"]() as db:
        s = env["registry"].target_summary(db, env["tid"],
                                           "inf_cdc_frontend", 7)
        assert s is None                       # 无批次：不编造正常
        page = env["registry"].ledger_search(
            db, env["tid"], "inf_cdc_frontend",
            __import__("plugins.domain_hooks", fromlist=["LedgerFilter"])
            .LedgerFilter(), None, 10)
        assert page is None or page.total_estimate == 0


def test_watermark_json_only_no_records_no_rejects(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "watermark.json", "content":
                    open(os.path.join(FIX, "watermark.json"), "rb").read()}],
            source_instance="wm", source_tz="CST", actor="t")
        db.commit()
        assert stats["records_total"] == 0 and stats["rows_rejected"] == 0


def test_renamed_receipts_same_logical_identity(env):
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        s1 = import_files(db, env["tid"], "inf_cdc_frontend",
                          files=[{"name": "receipts-20261105.jsonl",
                                  "content": open(os.path.join(
                                      FIX, "receipts-20261105.jsonl"),
                                      "rb").read()}],
                          source_instance="s", source_tz="CST", actor="t")
        db.commit()
        s2 = import_files(db, env["tid"], "inf_cdc_frontend",
                          files=[{"name": "receipts-20991231.jsonl",
                                  "content": open(os.path.join(
                                      FIX, "receipts-20261105.jsonl"),
                                      "rb").read()}],
                          source_instance="s", source_tz="CST", actor="t")
        db.commit()
        assert s1["rows_applied"] > 0
        assert s2["rows_applied"] == 0        # 逻辑身份（receipts:YYYYMMDD）不变


def test_sent_only_entries_create_no_records(env):
    """RT-04 A3 新契约：纯 sent.jsonl 不丢弃——生成 SENT_LEDGER_ONLY 观测
    记录（bucket=ACCEPTED 依据生产只读代码证明；last_attempt_at 未知=None；
    不映射 CONFIRMED）。"""
    from plugins.external_ledger import import_files
    with env["sf"]() as db:
        stats = import_files(
            db, env["tid"], "inf_cdc_frontend",
            files=[{"name": "sent.jsonl", "content":
                    open(os.path.join(FIX, "sent.jsonl"), "rb").read()}],
            source_instance="so", source_tz="CST", actor="t")
        db.commit()
        assert stats["sent_only_records"] == stats["sent_entries"] > 0
        rows = db.query(ExternalSubmissionRecord).all()
        assert len(rows) == stats["sent_entries"]
        for r in rows:
            assert r.last_result == "SENT_LEDGER_ONLY"
            assert r.evidence_level == "SENT_LEDGER_ONLY"
            assert r.bucket == "ACCEPTED"          # 生产代码证明=平台受理
            assert r.last_attempt_at is None       # 未知不冒充
            assert r.op_time is not None           # 源 opTime 保留


def test_same_position_different_digest_counts_conflict(env):
    """同来源同位置行内容变化 → conflict 计数显式待核（不静默丢弃/覆盖）。"""
    from plugins.external_ledger import import_files
    line1 = json.dumps({"id": "TEST-c", "result": True, "table": "death",
                        "ts": "2026-11-05 08:00:00"}).encode()
    line2 = json.dumps({"id": "TEST-c", "result": True, "table": "death",
                        "ts": "2026-11-05 08:00:01"}).encode()
    with env["sf"]() as db:
        s1 = import_files(db, env["tid"], "inf_cdc_frontend",
                          files=[{"name": "receipts-20261105.jsonl",
                                  "content": line1}],
                          source_instance="s", source_tz="CST", actor="t")
        db.commit()
        assert s1["conflicts"] == 0
        s2 = import_files(db, env["tid"], "inf_cdc_frontend",
                          files=[{"name": "receipts-20261105.jsonl",
                                  "content": line2}],
                          source_instance="s", source_tz="CST", actor="t")
        db.commit()
        assert s2["conflicts"] == 1
        # 批次报告含待核说明（无原文）
        from core_db.models import ExternalImportBatch
        b = (db.query(ExternalImportBatch)
             .order_by(ExternalImportBatch.started_at.desc()).first())
        assert b.report.get("conflicts") == 1 and "待核" in b.report["note"]
