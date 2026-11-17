"""DTR-BATCH-FIX-20261108 性能守卫：prepare 换代检查不得退化为 O(N) 扫描。

背景（2026-11-05 生产诊断）：prepare_submission 换代检查曾载入该目的地全部
submission 再逐行 db.get(PackageRevision)——生产 22,734 个提交时累计对
package_revision 产生 2.61 亿次索引扫描（=Σ1..22734），每个新 prepare 随存量
线性变慢，最终把夜间批次拖到 collect_timeout（Fix1 详见
docs/development/drug-trace-perf-fix-20261105/PLAN.md §2）。

本守卫：在已有 N=200 个历史提交的目的地上冻结新单，统计 prepare 期间应用库
SELECT 语句数——定点查询应为个位数；若回退成 O(N)（>150 条）立即失败。
语义正确性由 test_drug_trace_fix2/fix3 的既有换代用例守护，本文件只看查询数。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db.models import Destination, PackageRevision, Submission  # noqa: E402

# 复用 fix2 的夹具（env/_bill_row/_code_row/_connector_id/_wh_with/_dicts）
from tests.integration.test_drug_trace_fix2 import (  # noqa: E402
    _bill_row, _code_row, _connector_id, _dicts, _wh_with, env)  # noqa: F401


def _seed_prior_submissions(env, dest_id: str, n: int) -> None:
    """直接造 n 个指向其他 bill revision 的历史 PREPARED 提交（不走冻结，快速）。"""
    from core_db.models import DataPackage
    with env["sf"]() as db:
        pkg = DataPackage(id=new_uuid(), tenant_id=env["tenant_id"],
                          domain="drug_trace", business_key="junk-pkg")
        db.add(pkg)
        db.flush()
        pkg_id = pkg.id
        for i in range(n):
            pr = PackageRevision(
                id=new_uuid(), tenant_id=env["tenant_id"],
                package_id=pkg_id, revision=i + 1,
                provenance={"bill_revision_id": f"other-rev-{i}"},
                state="FROZEN")
            db.add(pr)
            db.flush()
            db.add(Submission(
                id=new_uuid(), tenant_id=env["tenant_id"],
                package_revision_id=pr.id, destination_id=dest_id,
                idempotency_key=f"junk-key-{i}", state="PREPARED"))
        db.commit()


def test_prepare_generation_check_is_constant_query(env):
    """已有 200 个历史提交时，一次新 prepare 的 SELECT 数为常数级（<150）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision

    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("GUARD1", kind="PATIENT_DISPENSE",
                   source_bill_kind_raw="323")],
        [_code_row("GUARD1", kind="PATIENT_DISPENSE", record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        rev_id = rev.id
    _dicts(env)
    with env["sf"]() as db:
        dest = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="guard-dest",
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload"], is_mock=True, status="ENABLED")
        db.add(dest)
        db.commit()
        dest_id = dest.id
    _seed_prior_submissions(env, dest_id, 200)

    selects = {"n": 0}
    from sqlalchemy import event

    def _count(conn_, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects["n"] += 1

    event.listen(env["engine"], "before_cursor_execute", _count)
    try:
        with env["sf"]() as db:
            result = service.prepare_submission(
                db, env["tenant_id"], rev_id,
                profile="alihealth_yljg_retail_v1", destination_id=dest_id)
    finally:
        from sqlalchemy import event as _e
        _e.remove(env["engine"], "before_cursor_execute", _count)

    assert result["created"] is True
    # 旧实现：1 次载入全部提交 + 200 次 db.get(PackageRevision) ≈ 210+ 条 SELECT；
    # 定点实现：个位数。阈值 150 —— 常数级实现稳过，任何 O(N) 回退立即爆。
    assert selects["n"] < 150, \
        f"prepare did {selects['n']} SELECTs with 200 prior submissions — " \
        "generation check regressed to O(N)? (DTR-BATCH-FIX Fix1 guard)"
    # 结果正确性抽查：新提交与其冻结包一一对应
    with env["sf"]() as db:
        s = db.get(Submission, result["submission_id"])
        assert s.state == "PREPARED"
        pr = db.get(PackageRevision, s.package_revision_id)
        assert (pr.provenance or {}).get("bill_revision_id") == rev_id
