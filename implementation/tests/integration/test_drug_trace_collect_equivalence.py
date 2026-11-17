"""LT-08 等价守护：采集管道优化前后业务结果 100% 逐行等价（黄金快照）。

背景：生产采集管道做性能优化（批量预取 / 质量项跳过空删 / gate 合并读 /
可选源端批量取数）。本文件用固定种子合成数据锁定优化前的业务结果，任何
优化不得改变以下内容的一行一字：

- DrugTraceBill / DrugTraceBillRevision / DrugTraceCodeIndex 业务列；
- 每单 content_digest、质量规则判定与质量项（rule_code/severity/detail）；
- NOT_REPORTABLE / WAITING / VALID / INVALID 分类与 collection summary 计数；
- SourceRecord/SourceRecordRevision 血缘、outbox BILL_INGESTED 事件（uuid
  映射为自然键后逐字段比对）；
- 中途暂停（gate=paused）时已入库数据与 checkpoint 断点；
- 同窗重复采集幂等（第二次零新 revision、快照不变）；
- 两种执行形态（单次整窗 100/页 vs 生产切片 25/页×yield）最终状态一致。

黄金文件：``tests/integration/goldens/drug_trace_collect_equivalence.json``；
需有意更新时以 ``BLYB_DT_REGEN_GOLDEN=1`` 重生成并在交接中说明语义变化原因。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from tests.performance.bench_drug_trace_collect import (  # noqa: E402
    TIMED_WINDOW, build_view_db, generate_dataset, make_view_warehouse)

GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "goldens", "drug_trace_collect_equivalence.json")
N_BILLS = 200
SEED = 42


# ---------------------------------------------------------------- 环境搭建
def _setup(tmp_path, name: str):
    """独立应用库 + 视图库；返回 (sf, engine, tenant_id, connector_id, warehouse)。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/{name}-app.sqlite3"
    import app_runtime
    from contracts import new_uuid
    from core_db import make_engine, run_migrations
    from core_db.engine import make_session_factory
    from core_db.models import ConnectorInstance, Tenant
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code=f"eq-{name}", name="等价测试")
        db.add(t)
        db.commit()
        c = ConnectorInstance(id=new_uuid(), tenant_id=t.id, direction="source",
                              plugin_id="sources.db_view.drug_trace",
                              name="his-drugtrace", status="ENABLED")
        db.add(c)
        db.commit()
        tid, cid = t.id, c.id
    bills, codes = generate_dataset(N_BILLS, seed=SEED, window=TIMED_WINDOW)
    view_db = str(tmp_path / f"{name}-view.sqlite3")
    build_view_db(view_db, bills, codes)
    return sf, engine, tid, cid, make_view_warehouse(view_db)


def _collect(sf, tid, cid, wh, *, shape="single", run_id=None):
    """跑一次采集。shape=single（100/页整窗）或 production（25/页×yield 循环）。"""
    from domains.drug_trace import service as svc
    wb, we = TIMED_WINDOW
    if shape == "single":
        with sf() as db:
            return svc.run_collection(db, tid, cid, wh, window_begin=wb,
                                      window_end=we, partition="eq",
                                      mode="synthetic", page_limit=100,
                                      max_pages=None, run_id=run_id)
    result = {}
    while True:
        with sf() as db:
            with wh.collection_session():
                result = svc.run_collection(
                    db, tid, cid, wh, window_begin=wb, window_end=we,
                    partition="eq", mode="synthetic", page_limit=25,
                    max_pages=1, run_id=run_id)
        run_id = result["run_id"]
        if not (result.get("yielded") and result.get("state") == "RUNNING"):
            return result


# ---------------------------------------------------------------- 快照
def _snapshot(sf, tenant_id: str, connector_id: str, *, run: dict | None = None,
              partition: str = "eq") -> dict:
    """把采集落库后的业务状态序列化为可 JSON 化、可逐行比对的字典。

    只含业务列/判定/计数；时间戳（created_at/updated_at/observed_at/
    progress_at）与自增/uuid 不入快照（uuid 经自然键映射保留语义）。
    """
    with sf() as db:
        from core_db.models import (OutboxEvent, QualityIssue,
                                    SourceCheckpoint, SourceRecord,
                                    SourceRecordRevision)
        from domains.drug_trace.models import (
            DrugTraceBill, DrugTraceBillRevision, DrugTraceCodeIndex,
            DrugTraceRetryBill)

        def bkey(b):
            return "|".join([b.source_system, b.pharmacy_code,
                             b.business_kind, b.source_bill_id])

        bills = (db.query(DrugTraceBill)
                 .filter_by(tenant_id=tenant_id, connector_id=connector_id)
                 .all())
        bkeys = {b.id: bkey(b) for b in bills}
        revs = (db.query(DrugTraceBillRevision)
                .filter_by(tenant_id=tenant_id).all())
        revkey = {r.id: f"{bkeys.get(r.bill_id, r.bill_id)}@{r.source_revision}"
                  for r in revs}

        snap: dict = {}
        snap["bills"] = sorted([
            {"key": bkey(b), "source_bill_no": b.source_bill_no,
             "warehouse_code": b.warehouse_code,
             "current_source_revision": b.current_source_revision,
             "current_revision": revkey.get(b.current_revision_id, ""),
             "source_updated_at": b.source_updated_at.isoformat()
             if b.source_updated_at else "",
             "bill_time": b.bill_time.isoformat() if b.bill_time else "",
             "source_event_seq": b.source_event_seq,
             "finalized": b.finalized, "is_void": b.is_void,
             "source_status": b.source_status} for b in bills],
            key=lambda r: r["key"])
        snap["revisions"] = sorted([
            {"key": revkey[r.id], "bill_key": bkeys.get(r.bill_id, ""),
             "digest": r.content_digest, "state": r.quality_state,
             "issues": r.quality_issues or [],
             "line_count": len(r.lines or []),
             "event_time": r.event_time.isoformat() if r.event_time else "",
             "source_evidence": r.source_evidence or {},
             "config_release_id": r.config_release_id} for r in revs],
            key=lambda r: r["key"])
        idx = (db.query(DrugTraceCodeIndex)
               .filter_by(tenant_id=tenant_id).all())
        snap["code_index"] = sorted([
            {"bill_key": bkeys.get(i.bill_id, ""),
             "revision": revkey.get(i.bill_revision_id, ""),
             "record_id": i.source_code_record_id,
             "line_id": i.source_line_id, "trace_code": i.trace_code,
             "code_level": i.code_level, "parent_code": i.parent_code,
             "quantity": i.quantity, "unit": i.unit,
             "is_deleted": i.is_deleted,
             "updated_at": i.updated_at.isoformat() if i.updated_at else ""}
            for i in idx],
            key=lambda r: (r["bill_key"], r["revision"], r["record_id"]))
        issues = (db.query(QualityIssue)
                  .filter(QualityIssue.tenant_id == tenant_id,
                          QualityIssue.entity_ref.like(
                              "drug_trace_bill_revision:%")).all())
        rev_by_id = {r.id: r for r in revs}
        snap["issues"] = sorted([
            {"revision": revkey.get(
                _rev_id_of(i.entity_ref), ""),
             "rule": i.rule_code, "severity": i.severity,
             "blocking_scope": i.blocking_scope, "field_path": i.field_path,
             "context": i.context_json} for i in issues],
            key=lambda r: (r["revision"], r["rule"], json.dumps(
                r["context"], ensure_ascii=False, sort_keys=True)))
        recs = (db.query(SourceRecord)
                .filter_by(tenant_id=tenant_id,
                           entity_type="drug_trace_bill").all())
        srr = (db.query(SourceRecordRevision)
               .filter_by(tenant_id=tenant_id).all()
               if recs else [])
        by_rec: dict[str, list] = {}
        for s in srr:
            if s.record_id in {r.id for r in recs}:
                by_rec.setdefault(s.record_id, []).append(s)
        snap["source_records"] = sorted([
            {"external_id": r.external_id, "namespace": r.namespace,
             "current_revision": revkey.get(r.current_revision, ""),
             "revisions": sorted([
                 {"source_version": s.source_version, "digest": s.digest,
                  "observed_by": s.observed_by} for s in by_rec.get(r.id, [])],
                 key=lambda x: x["source_version"])} for r in recs],
            key=lambda r: r["external_id"])
        events = (db.query(OutboxEvent)
                  .filter_by(tenant_id=tenant_id,
                             event_type="drug_trace.bill_ingested").all())
        snap["outbox"] = sorted([
            {"aggregate_ref": e.aggregate_ref,
             "payload": {k: (revkey.get(v, v) if k == "revision_id" else v)
                         for k, v in (e.payload or {}).items()}} for e in events],
            key=lambda e: e.aggregate_ref)
        cp = (db.query(SourceCheckpoint)
              .filter_by(tenant_id=tenant_id, connector_id=connector_id,
                         stream="drugtrace.bills", partition=partition).first())
        snap["checkpoint"] = cp.cursor_json if cp is not None else None
        retries = (db.query(DrugTraceRetryBill)
                   .filter_by(tenant_id=tenant_id,
                              connector_id=connector_id).all())
        snap["retry_bills"] = sorted([
            {"key": "|".join([r.source_system, r.pharmacy_code,
                              r.business_kind, r.source_bill_id]),
             "reason": r.reason, "attempts": r.attempts} for r in retries],
            key=lambda r: r["key"])
        if run is not None:
            snap["run"] = {k: v for k, v in run.items()
                           if k not in ("profile", "run_id", "progress_at")}
    return snap


def _rev_id_of(entity_ref: str) -> str:
    return str(entity_ref).split(":", 1)[1]


# ---------------------------------------------------------------- 黄金比对
def _assert_matches_golden(snap: dict, section: str) -> None:
    if os.environ.get("BLYB_DT_REGEN_GOLDEN", "") == "1":
        golden = json.load(open(GOLDEN_PATH, encoding="utf-8")) \
            if os.path.exists(GOLDEN_PATH) else {}
        golden[section] = snap
        os.makedirs(os.path.dirname(GOLDEN_PATH), exist_ok=True)
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump(golden, f, ensure_ascii=False, indent=1, sort_keys=True)
        return
    assert os.path.exists(GOLDEN_PATH), \
        f"golden file missing: {GOLDEN_PATH} (regen with BLYB_DT_REGEN_GOLDEN=1)"
    golden = json.load(open(GOLDEN_PATH, encoding="utf-8"))
    assert section in golden, f"golden section {section!r} missing"
    expected = golden[section]
    assert snap == expected, _first_diff(snap, expected)


def _first_diff(a, b) -> str:
    """deep diff 首处差异定位（避免整份快照刷屏）。"""
    import difflib
    sa = json.dumps(a, ensure_ascii=False, sort_keys=True, indent=1).splitlines()
    sb = json.dumps(b, ensure_ascii=False, sort_keys=True, indent=1).splitlines()
    diff = [line for line in difflib.unified_diff(sb, sa, "golden", "current",
                                                  lineterm="", n=0)
            if line[:1] in "+-@"]
    head = "\n".join(diff[:12])
    return f"snapshot != golden (first diff):\n{head}"


# ---------------------------------------------------------------- 测试
@pytest.fixture(autouse=True)
def _no_profile_env():
    os.environ.pop("BLYB_DT_PROFILE", None)
    yield
    os.environ.pop("BLYB_DT_PROFILE", None)


def test_single_shot_matches_golden(tmp_path):
    """单次整窗采集（100/页）业务快照 == 黄金。"""
    sf, engine, tid, cid, wh = _setup(tmp_path, "single")
    try:
        result = _collect(sf, tid, cid, wh, shape="single")
        assert result["state"] == "DONE"
        snap = _snapshot(sf, tid, cid, run=result)
        run_section = snap.pop("run")
        _assert_matches_golden(snap, "main")
        _assert_matches_golden(run_section, "single_run")
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def test_production_slices_match_golden(tmp_path):
    """生产切片形态（25/页×yield）最终状态与单次整窗一致（同黄金 main）。"""
    sf, engine, tid, cid, wh = _setup(tmp_path, "slices")
    try:
        result = _collect(sf, tid, cid, wh, shape="production")
        assert result["state"] == "DONE"
        snap = _snapshot(sf, tid, cid)
        _assert_matches_golden(snap, "main")
        assert result["bills_seen"] == N_BILLS
        assert result["revisions_created"] == N_BILLS
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def test_idempotent_rerun_zero_new_revisions(tmp_path):
    """同窗重复采集：零新 revision、业务快照逐行不变。"""
    sf, engine, tid, cid, wh = _setup(tmp_path, "rerun")
    try:
        r1 = _collect(sf, tid, cid, wh, shape="production")
        assert r1["state"] == "DONE" and r1["revisions_created"] == N_BILLS
        r2 = _collect(sf, tid, cid, wh, shape="production")
        assert r2["state"] == "DONE"
        assert r2["revisions_created"] == 0, r2
        assert r2["idempotent"] == N_BILLS
        snap = _snapshot(sf, tid, cid)
        _assert_matches_golden(snap, "main")
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def test_cancel_at_page_boundary_partial_state(tmp_path):
    """首个完整页后 gate=paused：已入库 25 单 + 断点 checkpoint == 黄金 cancel 节。

    gate 取消响应上限 = 一页采集时长（页边界安全点语义，LT-08 优化不得放宽）。
    """
    from domains.drug_trace import service as svc
    sf, engine, tid, cid, wh = _setup(tmp_path, "cancel")
    try:
        calls = {"n": 0}

        def gate():
            calls["n"] += 1
            return "paused" if calls["n"] > 1 else None

        wb, we = TIMED_WINDOW
        with sf() as db:
            result = svc.run_collection(
                db, tid, cid, wh, window_begin=wb, window_end=we,
                partition="eq", mode="synthetic", page_limit=25,
                max_pages=None, gate_override=gate)
        assert result["state"] == "PAUSED", result
        assert result.get("stopped") == "paused"
        snap = _snapshot(sf, tid, cid, run=result)
        assert len(snap["bills"]) == 25
        assert snap["checkpoint"]["completed_window"] is False
        _assert_matches_golden(snap, "cancel")
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def test_batch_fetch_matches_golden(tmp_path):
    """O4 源端批量取数（batch_fetch=True）业务结果与逐单读取逐行一致。"""
    sf, engine, tid, cid, wh = _setup(tmp_path, "batch")
    try:
        assert wh.batch_fetch is False      # 默认关闭
        wh.batch_fetch = True
        result = _collect(sf, tid, cid, wh, shape="production")
        assert result["state"] == "DONE"
        assert result["revisions_created"] == N_BILLS
        snap = _snapshot(sf, tid, cid)
        _assert_matches_golden(snap, "main")
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def test_batch_fetch_version_change_falls_back_per_bill(tmp_path):
    """O4：批量读取期间版本变化的单逐单回退重取（reread 语义保留）。"""
    sf, engine, tid, cid, wh = _setup(tmp_path, "batchvc")
    try:
        wh.batch_fetch = True
        # 页内首单（keyset 序）在源侧版本 +1：批结果不含该键 → 逐单重取入库
        bills, _codes = generate_dataset(N_BILLS, seed=SEED,
                                         window=TIMED_WINDOW)
        first = min(bills, key=lambda b: (b["updated_at"], b["source_system"],
                                          b["pharmacy_code"],
                                          b["business_kind"],
                                          b["source_bill_id"]))
        import sqlite3
        view_db = str(tmp_path / "batchvc-view.sqlite3")
        conn = sqlite3.connect(view_db)
        conn.execute(
            "UPDATE V_DRUG_TRACE_BILL SET source_revision='2'"
            " WHERE source_bill_id=? AND pharmacy_code=? AND business_kind=?",
            (first["source_bill_id"], first["pharmacy_code"],
             first["business_kind"]))
        conn.commit()
        conn.close()
        result = _collect(sf, tid, cid, wh, shape="production")
        assert result["state"] == "DONE"
        assert result["revisions_created"] == N_BILLS
        snap = _snapshot(sf, tid, cid)
        keys_golden = {r["key"] for r in json.load(
            open(GOLDEN_PATH, encoding="utf-8"))["main"]["revisions"]}
        keys_now = {r["key"] for r in snap["revisions"]}
        # 同键集，唯一差异 = 变化单的版本号 @1 → @2（其余 199 单逐字一致）
        first_key = f"{first['source_system']}|{first['pharmacy_code']}|" \
                    f"{first['business_kind']}|{first['source_bill_id']}"
        assert (keys_golden ^ keys_now) == {f"{first_key}@1", f"{first_key}@2"}
        changed = [r for r in snap["revisions"] if r["key"].endswith("@2")]
        assert len(changed) == 1
        assert changed[0]["bill_key"] == first_key
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)


def test_batch_fetch_cap_overflow_returns_none(tmp_path):
    """O4：码行数触及批量上限 → Oracle 适配器返回 None（整页回退不截断）。"""
    from sources.db_view.drug_trace import OracleDrugTraceWarehouse
    from tests.performance.bench_drug_trace_collect import _LatencyConnection
    bills, codes = generate_dataset(6, seed=SEED, window=TIMED_WINDOW)
    view_db = str(tmp_path / "cap-view.sqlite3")
    build_view_db(view_db, bills, codes)
    wh = OracleDrugTraceWarehouse(
        connection_factory=lambda: _LatencyConnection(view_db, 0.0))
    # 正常：全部键一次取回且行不跨单混入
    out = wh.fetch_bills_batch(bills[:4], code_page_limit=200)
    assert out is not None and len(out) == 4
    for _k, (h, cs) in out.items():
        assert all(c["source_bill_id"] == h["source_bill_id"] for c in cs)
    # 超限：code_page_limit=1 → cap=4×1×4=16 < 6 单实际码行数 → None
    out2 = wh.fetch_bills_batch(bills[:4], code_page_limit=1)
    assert out2 is None


def test_profile_enabled_does_not_change_business_result(tmp_path):
    """BLYB_DT_PROFILE=1 只增加计时，不改变任何业务结果。"""
    os.environ["BLYB_DT_PROFILE"] = "1"
    sf, engine, tid, cid, wh = _setup(tmp_path, "prof")
    try:
        result = _collect(sf, tid, cid, wh, shape="single")
        assert result["state"] == "DONE"
        assert "profile" in result, result.get("profile")
        snap = _snapshot(sf, tid, cid, run=result)
        snap.pop("run", None)          # run 含 profile 键，单比对业务部分
        golden = json.load(open(GOLDEN_PATH, encoding="utf-8"))["main"]
        golden_no_run = {k: v for k, v in golden.items() if k != "run"}
        assert snap == golden_no_run, _first_diff(snap, golden_no_run)
    finally:
        engine.dispose()
        os.environ.pop("BLYB_DATABASE_URL", None)
        os.environ.pop("BLYB_DT_PROFILE", None)
