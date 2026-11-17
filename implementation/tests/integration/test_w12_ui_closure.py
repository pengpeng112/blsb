"""W12 正式回归：workflow-runs 通用端点（创建/游标列表/详情/取消/固定版本回放）。

浏览器闭环（构建版UI真实浏览器）证据见 docs/audit 与台账；此处固化API契约。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from tests.harness import Harness  # noqa: E402
from core_db.models import WorkflowRun  # noqa: E402


def test_w12_run_create_list_cursor_detail_replay_cancel(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    # 创建（metrics，带rows使链条可走）
    csv_text = ("机构,月份,项目,数量,金额\nH,2026-08,x,1,12.34")
    imp = core.post("/api/v1/imports", json={"csv": csv_text},
                    headers={**h, "Idempotency-Key": "w12-imp"})
    pkg = imp.json()["data"]["package_id"]
    ctx = {"package_id": pkg, "rows": [{"org": "H", "amount": "12.34"}]}
    r1 = core.post("/api/v1/workflow-runs",
                   json={"workflow_key": "metrics.mock_push", "context": ctx,
                         "dry_run": True},
                   headers={**h, "Idempotency-Key": "w12-run-1"})
    assert r1.status_code == 201, r1.text
    run_id = r1.json()["data"]["run_id"]
    # dry-run 事件隔离：验证上下文带 _dry_run
    with harness.sf() as db:
        rr = db.get(WorkflowRun, run_id)
        assert rr.context["_dry_run"] is True
        assert rr.context["_config_closure"]["workflow_revision_id"]
    # 再建一个run，凑足游标分页数据
    r0 = core.post("/api/v1/workflow-runs",
                   json={"workflow_key": "metrics.mock_push", "context": dict(
                       ctx, _dedum="", _dedup=f"w12-extra-{pkg[:6]}")},
                   headers={**h, "Idempotency-Key": "w12-run-0"})
    assert r0.status_code == 201
    # 游标列表
    lst = core.get("/api/v1/workflow-runs?limit=1", headers=h).json()["data"]
    assert len(lst["items"]) <= 2
    assert lst["next_cursor"]
    page2 = core.get("/api/v1/workflow-runs?limit=1&cursor=" + lst["next_cursor"],
                     headers=h).json()["data"]
    assert page2["items"]
    ids1 = {i["id"] for i in lst["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert not (ids1 & ids2), "游标分页不得重复"
    # 详情（含步骤）
    d = core.get(f"/api/v1/workflow-runs/{run_id}", headers=h).json()["data"]
    assert d["workflow_key"] == "metrics.mock_push"
    assert isinstance(d["steps"], list)
    # 未发布workflow拒绝
    r404 = core.post("/api/v1/workflow-runs",
                     json={"workflow_key": "no.such", "context": {}},
                     headers={**h, "Idempotency-Key": "w12-run-x"})
    assert r404.status_code == 404
    # 取消（WAITING态运行）：制造一个等待回执的run
    harness.tick(20)
    r2 = core.post("/api/v1/workflow-runs",
                   json={"workflow_key": "metrics.mock_push",
                         "context": dict(ctx, _dedup=f"w12-wait-{pkg[:8]}")},
                   headers={**h, "Idempotency-Key": "w12-run-2"})
    rid2 = r2.json()["data"]["run_id"]
    harness.tick(25)
    # 无论终态与否：终态→409；非终态→取消成功
    rc = core.post(f"/api/v1/workflow-runs/{rid2}/cancel", headers=h)
    with harness.sf() as db:
        state = db.get(WorkflowRun, rid2).state
    if rc.status_code == 200:
        assert state == "CANCELLED"
    else:
        assert rc.status_code == 409 and state in ("SUCCEEDED", "FAILED",
                                                   "NEEDS_REVIEW")
    # 回放：非终态409 / 终态创建同revision新run
    r1b = core.post(f"/api/v1/workflow-runs/{run_id}/replay", headers=h)
    if r1b.status_code == 409:
        # run仍非终态（dry链条等待）→ 先取消再回放
        core.post(f"/api/v1/workflow-runs/{run_id}/cancel", headers=h)
        r1b = core.post(f"/api/v1/workflow-runs/{run_id}/replay", headers=h)
    assert r1b.status_code == 201, r1b.text
    d2 = r1b.json()["data"]
    assert d2["replayed_from"] == run_id
    with harness.sf() as db:
        old = db.get(WorkflowRun, run_id)
        new = db.get(WorkflowRun, d2["run_id"])
        assert new.workflow_revision_id == old.workflow_revision_id  # 固定版本
        # 依赖闭包原样复制（不读latest）
        assert (new.context.get("_config_closure") or {}).get(
            "pack_revision_id") == (old.context.get("_config_closure") or {}).get(
            "pack_revision_id")


def test_w12_cancel_blocks_new_side_effects(harness: Harness):
    """取消后引擎不再推进（无新副作用）：步骤保持CANCELLED。"""
    from apps.worker.engine import WorkerEngine
    # 建 run（dry） → 立即取消 → tick → 状态不变
    csv_text = ("机构,月份,项目,数量,金额\nH,2026-09,y,2,55.55")
    imp = harness.core.post(
        "/api/v1/imports", json={"csv": csv_text},
        headers={**harness.admin_headers(), "Idempotency-Key": "w12-imp2"})
    pkg = imp.json()["data"]["package_id"]
    r = harness.core.post(
        "/api/v1/workflow-runs",
        json={"workflow_key": "metrics.mock_push", "dry_run": True,
              "context": {"package_id": pkg, "_dedup": f"w12-cancel-{pkg[:6]}"}},
        headers={**harness.admin_headers(), "Idempotency-Key": "w12-run-c"})
    rid = r.json()["data"]["run_id"]
    rc = harness.core.post(f"/api/v1/workflow-runs/{rid}/cancel",
                           headers=harness.admin_headers())
    assert rc.status_code == 200, rc.text
    harness.tick(15)
    with harness.sf() as db:
        assert db.get(WorkflowRun, rid).state == "CANCELLED"
