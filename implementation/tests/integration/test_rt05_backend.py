"""RT-05 后端：rollback 理由审计、restore 真实恢复语义、run 闭包一致性、
插件/调度元数据端点。

U06 验收：rollback=撤销标记（reason 必填+同事务审计）；restore=以历史闭包
创建新发布（漂移拒绝/幂等/不改历史）；restore 后新 run 闭包落到恢复的
历史 pack（真实运行时语义），rollback 后回落 latest——两者行为分立。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401
from tests.harness import Harness  # noqa: E402
from core_db.models import (AuditEvent, ConfigRelease, ConfigRevision,  # noqa: E402
                            DictionaryRevision, WorkflowRun)


def _release(harness: Harness, note: str) -> dict:
    r = harness.core.post("/api/v1/config-releases", json={"note": note},
                          headers=harness.admin_headers())
    assert r.status_code == 201, r.text
    return r.json()["data"]


def _run_via_api(harness: Harness, dedup: str) -> dict:
    r = harness.core.post(
        "/api/v1/workflow-runs",
        json={"workflow_key": "metrics.mock_push",
              "context": {"package_id": f"pkg-rt05-{dedup}", "rows": [],
                          "_dedup": f"rt05-{dedup}"}},
        headers={**harness.admin_headers(), "Idempotency-Key": f"rt05-{dedup}"})
    assert r.status_code == 201, r.text
    rid = r.json()["data"]["run_id"]
    with harness.sf() as db:
        run = db.get(WorkflowRun, rid)
        return dict(run.context or {}).get("_config_closure") or {}


# ================================================================ rollback
def test_rt05_rollback_requires_reason_and_audits(harness: Harness):
    core = harness.core
    d = _release(harness, "rt05-rollback")
    # 无理由/短理由 → 422（写语义修复：不再无理由标记）
    r0 = core.post(f"/api/v1/config-releases/{d['release_id']}/rollback",
                   json={}, headers=harness.admin_headers())
    assert r0.status_code == 422
    r1 = core.post(f"/api/v1/config-releases/{d['release_id']}/rollback",
                   json={"reason": "太短"}, headers=harness.admin_headers())
    assert r1.status_code == 422
    # 正常撤销：reason 进同事务审计
    r2 = core.post(f"/api/v1/config-releases/{d['release_id']}/rollback",
                   json={"reason": "闭包字典待重新批准，先撤销"},
                   headers=harness.admin_headers())
    assert r2.status_code == 200, r2.text
    body = r2.json()["data"]
    assert body["already_rolled_back"] is False and body["rolled_back_at"]
    with harness.sf() as db:
        evs = (db.query(AuditEvent)
               .filter_by(target=f"config_release:{d['release_id']}",
                          action="config.release.rollback")
               .order_by(AuditEvent.at).all())
        assert len(evs) == 1
        assert evs[0].new_ref["reason"] == "闭包字典待重新批准，先撤销"
        assert evs[0].new_ref["already_rolled_back"] is False
    # 幂等：重复撤销不重设时间戳，already_rolled_back=true 且审计如实标注
    r3 = core.post(f"/api/v1/config-releases/{d['release_id']}/rollback",
                   json={"reason": "重复提交验证幂等"}, headers=harness.admin_headers())
    assert r3.status_code == 200
    assert r3.json()["data"]["already_rolled_back"] is True
    with harness.sf() as db:
        evs = (db.query(AuditEvent)
               .filter_by(target=f"config_release:{d['release_id']}",
                          action="config.release.rollback")
               .order_by(AuditEvent.at).all())
        assert len(evs) == 2
        assert evs[-1].new_ref["already_rolled_back"] is True


def test_rt05_rollback_requires_editor_role(harness: Harness):
    d = _release(harness, "rt05-role")
    r = harness.core.post(
        f"/api/v1/config-releases/{d['release_id']}/rollback",
        json={"reason": "非编辑角色尝试撤销"}, headers=harness.agent_headers())
    assert r.status_code == 403


# ================================================================ restore
def test_rt05_restore_creates_new_release_with_source_closure(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    with harness.sf() as db:
        pack_a = (db.query(ConfigRevision)
                  .filter_by(tenant_id=harness.tenant_id,
                             config_type="hospital_pack", state="PUBLISHED")
                  .order_by(ConfigRevision.revision.desc()).first())
    r1 = _release(harness, "rt05-src")
    harness.publish_pack({"note": "rt05-pack-b",
                          "approved_dictionaries": {"encounter_kind": "demo-v2"}})
    with harness.sf() as db:
        pack_b = (db.query(ConfigRevision)
                  .filter_by(tenant_id=harness.tenant_id,
                             config_type="hospital_pack", state="PUBLISHED")
                  .order_by(ConfigRevision.revision.desc()).first())
    assert pack_b.id != pack_a.id
    # 恢复到 r1（其闭包 pin pack_a）
    rr = core.post(f"/api/v1/config-releases/{r1['release_id']}/restore",
                   json={"reason": "新pack配置有误，恢复到上一个已验证闭包"},
                   headers=h)
    assert rr.status_code == 201, rr.text
    d = rr.json()["data"]
    assert d["idempotent"] is False and d["source_release_id"] == r1["release_id"]
    assert d["digest"] == r1["digest"]                    # 闭包=源闭包
    with harness.sf() as db:
        rel = db.get(ConfigRelease, d["release_id"])
        assert rel is not None and rel.id != r1["release_id"]
        assert (rel.test_report or {}).get("restored_from") == r1["release_id"]
        src = db.get(ConfigRelease, r1["release_id"])
        assert rel.revision_ids == src.revision_ids       # 不可变闭包原样
        assert rel.digest == src.digest
        # pack_a/pack_b 均未被改写（历史冻结包不退休、新账不删除）
        assert db.get(ConfigRevision, pack_a.id).state == "RETIRED"
        assert db.get(ConfigRevision, pack_b.id).state == "PUBLISHED"
        ev = (db.query(AuditEvent)
              .filter_by(target=f"config_release:{rel.id}",
                         action="config.release.restore").first())
        assert ev is not None and ev.new_ref["reason"].startswith("新pack配置有误")
    # 幂等：同源同闭包重复恢复 → 返回同一 release
    rr2 = core.post(f"/api/v1/config-releases/{r1['release_id']}/restore",
                    json={"reason": "重复提交恢复验证幂等"}, headers=h)
    assert rr2.status_code == 200
    d2 = rr2.json()["data"]
    assert d2["idempotent"] is True and d2["release_id"] == d["release_id"]
    # 无理由 → 422；未知 release → 404
    assert core.post(f"/api/v1/config-releases/{r1['release_id']}/restore",
                     json={}, headers=h).status_code == 422
    assert core.post("/api/v1/config-releases/no-such/restore",
                     json={"reason": "不存在的发布恢复尝试"},
                     headers=h).status_code == 404


def test_rt05_restore_rejects_drifted_closure(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    r1 = _release(harness, "rt05-drift")
    # 直接改写被 pin 的历史 pack 修订的 digest（测试注入漂移）
    with harness.sf() as db:
        entry = next(e for e in (db.get(ConfigRelease, r1["release_id"])
                                 .revision_ids or [])
                     if e.get("type") == "hospital_pack")
        pinned = db.get(ConfigRevision, entry["revision_id"])
        pinned.digest = "0" * 64
        db.commit()
    rr = core.post(f"/api/v1/config-releases/{r1['release_id']}/restore",
                   json={"reason": "闭包已漂移时的恢复应被拒绝"},
                   headers=h)
    assert rr.status_code == 409
    assert rr.json()["error"]["code"] == "CLOSURE_DRIFTED"


def test_rt05_restore_rejects_expired_dictionary(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    r1 = _release(harness, "rt05-dict")
    with harness.sf() as db:
        entry = next(e for e in (db.get(ConfigRelease, r1["release_id"])
                                 .revision_ids or [])
                     if e.get("type") == "dictionary")
        ns, _, ver = str(entry["key"]).partition("@")
        d = (db.query(DictionaryRevision)
             .filter_by(tenant_id=harness.tenant_id, namespace=ns,
                        version=ver).one())
        from datetime import datetime, timezone
        d.effective_to = datetime(2026, 1, 1, tzinfo=timezone.utc)
        db.commit()
    rr = core.post(f"/api/v1/config-releases/{r1['release_id']}/restore",
                   json={"reason": "字典过期时恢复应拒绝"}, headers=h)
    assert rr.status_code == 409
    assert rr.json()["error"]["code"] == "PROFILE_UNAPPROVED"


def test_rt05_restore_pins_new_api_run_closure_and_rollback_falls_back(harness: Harness):
    """restore/rollback 的真实运行时分立：API 建的 run 闭包
    - 有未回退 release → pack 按 release 闭包 pin（API/worker 同语义修复）
    - rollback → 回落最新 PUBLISHED pack
    - restore → 再落回恢复的历史 pack，config_release_id=新发布 id"""
    core = harness.core
    h = harness.admin_headers()
    with harness.sf() as db:
        pack_a = (db.query(ConfigRevision)
                  .filter_by(tenant_id=harness.tenant_id,
                             config_type="hospital_pack", state="PUBLISHED")
                  .order_by(ConfigRevision.revision.desc()).first())
    r1 = _release(harness, "rt05-pin")
    harness.publish_pack({"note": "rt05-pin-b",
                          "approved_dictionaries": {"encounter_kind": "demo-v2"}})
    with harness.sf() as db:
        pack_b = (db.query(ConfigRevision)
                  .filter_by(tenant_id=harness.tenant_id,
                             config_type="hospital_pack", state="PUBLISHED")
                  .order_by(ConfigRevision.revision.desc()).first())
    # 1) release 未回退：API run 闭包 pin pack_a（与 worker 同语义）
    c1 = _run_via_api(harness, "pin1")
    assert c1["pack_revision_id"] == pack_a.id
    assert c1["config_release_id"] == r1["release_id"]
    # 2) rollback（reason 审计）→ 新 API run 回落 latest pack_b
    rr = core.post(f"/api/v1/config-releases/{r1['release_id']}/rollback",
                   json={"reason": "撤销验证回落语义"}, headers=h)
    assert rr.status_code == 200
    c2 = _run_via_api(harness, "pin2")
    assert c2["pack_revision_id"] == pack_b.id
    # 3) restore r1 → 新 API run 再落回 pack_a，release=恢复发布
    rs = core.post(f"/api/v1/config-releases/{r1['release_id']}/restore",
                   json={"reason": "恢复验证重新固定历史闭包"}, headers=h)
    assert rs.status_code == 201, rs.text
    restored_id = rs.json()["data"]["release_id"]
    c3 = _run_via_api(harness, "pin3")
    assert c3["pack_revision_id"] == pack_a.id
    assert c3["config_release_id"] == restored_id


# ================================================================ 元数据端点
def test_rt05_plugins_metadata_endpoint(harness: Harness):
    r = harness.core.get("/api/v1/plugins", headers=harness.admin_headers())
    assert r.status_code == 200
    rows = r.json()["data"]
    keys = {x["key"] for x in rows}
    assert {"pathology", "metrics"} <= keys
    for x in rows:
        assert x["description"] and isinstance(x["capabilities"], list)


def test_rt05_schedules_meta_endpoint(harness: Harness):
    r = harness.core.get("/api/v1/schedules/meta",
                         headers=harness.admin_headers())
    assert r.status_code == 200
    d = r.json()["data"]
    assert {"system.run_guard", "metrics.run"} <= set(d["job_types"])
    assert d["misfire_policies"] == ["SKIP", "RUN_ONCE"]
    assert d["concurrency_modes"] == ["FORBID", "ALLOW"]
