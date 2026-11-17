"""W10收尾+W11 正式回归：ConfigRelease完整发布链、run闭包固定、
metrics-only独立进程（不import病理/不建病理表/CSV→映射→校验→冻结→双目标Mock）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import httpx
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from tests.harness import Harness  # noqa: E402
from core_db.models import ConfigRelease, ConfigRevision, WorkflowRun  # noqa: E402

IMPL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ================================================================ W10 ConfigRelease
def test_w10_config_release_snapshot_closure(harness: Harness):
    core = harness.core
    r = core.post("/api/v1/config-releases", json={"note": "w10-snapshot"},
                  headers=harness.admin_headers())
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    assert d["digest"] and len(d["digest"]) >= 32
    assert set(d["entry_types"]) >= {"workflow", "hospital_pack", "dictionary",
                                     "destination_profile"}
    detail = core.get(f"/api/v1/config-releases/{d['release_id']}",
                      headers=harness.admin_headers()).json()["data"]
    types = {}
    for e in detail["revision_ids"]:
        types.setdefault(e["type"], []).append(e)
    # 全部为具体 revision + digest
    for e in detail["revision_ids"]:
        assert e.get("revision_id") and e.get("digest"), e
    assert any(e["key"] == "pathology.mock_push" for e in types["workflow"])
    assert any(e["key"] == "metrics.mock_push" for e in types["workflow"])
    pack_entries = types["hospital_pack"]
    assert len(pack_entries) == 1
    with harness.sf() as db:
        published = (db.query(ConfigRevision)
                     .filter_by(tenant_id=harness.tenant_id,
                                config_type="hospital_pack", state="PUBLISHED")
                     .order_by(ConfigRevision.revision.desc()).first())
        assert pack_entries[0]["revision_id"] == published.id
    # 列表包含digest与回退状态
    lst = core.get("/api/v1/config-releases",
                   headers=harness.admin_headers()).json()["data"]
    assert any(x["digest"] == d["digest"] and not x["rolled_back"] for x in lst)


def test_w10_config_release_rejects_unapproved_dictionary(harness: Harness):
    """Pack引用未批准字典 → 发布409（闭包级审批校验）。"""
    with harness.sf() as db:
        published = (db.query(ConfigRevision)
                     .filter_by(tenant_id=harness.tenant_id,
                                config_type="hospital_pack", state="PUBLISHED")
                     .order_by(ConfigRevision.revision.desc()).first())
        content = dict(published.content_json)
        refs = dict(content.get("approved_dictionaries") or {})
        refs["encounter_kind"] = "demo-unapproved-x"
        content["approved_dictionaries"] = refs
        published.state = "RETIRED"
        db.add(ConfigRevision(
            id="cr-w10-bad", tenant_id=harness.tenant_id,
            config_type="hospital_pack", owner_id="first-hospital",
            revision=published.revision + 1, content_json=content,
            digest="d-w10-bad", state="PUBLISHED", created_by="test"))
        db.commit()
    r = harness.core.post("/api/v1/config-releases", json={},
                          headers=harness.admin_headers())
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROFILE_UNAPPROVED"
    with harness.sf() as db:
        db.get(ConfigRevision, "cr-w10-bad").state = "RETIRED"
        prev = (db.query(ConfigRevision)
                .filter_by(id=published.id)).first()
        prev.state = "PUBLISHED"
        db.commit()


def test_w10_release_pins_pack_for_new_runs_and_rollback(harness: Harness):
    """release固定的pack被新run闭包采用；回退后回落latest-published。"""
    core = harness.core
    rel = core.post("/api/v1/config-releases", json={"note": "pin-test"},
                    headers=harness.admin_headers()).json()["data"]
    with harness.sf() as db:
        rel_id = rel["release_id"]
        entry = next(e for e in db.get(ConfigRelease, rel_id).revision_ids
                     if e["type"] == "hospital_pack")
        pinned_id = entry["revision_id"]
    # 发布新pack（比release更新）
    harness.publish_pack({"approved_dictionaries": {"encounter_kind": "demo-v2"},
                          "sources": {"pis": {"database_candidate": "PITAYA"}},
                          "note": "after-release"})
    with harness.sf() as db:
        newest = (db.query(ConfigRevision)
                  .filter_by(tenant_id=harness.tenant_id,
                             config_type="hospital_pack", state="PUBLISHED")
                  .order_by(ConfigRevision.revision.desc()).first())
        assert newest.id != pinned_id
        # 新run闭包应固定 release 的 pack（不读latest）
        from apps.worker.engine import WorkerEngine
        we = WorkerEngine(harness.sf)
        run = we._launch(db, harness.tenant_id, "metrics.mock_push",
                         {"package_id": "pkg-w10-pin", "rows": []})
        closure = run.context["_config_closure"]
        assert closure["config_release_id"] == rel_id
        assert closure["pack_revision_id"] == pinned_id, closure
    # 回退release → 新run回落latest
    # RT-05 U06 契约迁移：rollback 必填 reason（写语义修复，审计留痕）
    rr = core.post(f"/api/v1/config-releases/{rel_id}/rollback",
                   json={"reason": "W10 回退回落语义验证：撤销当前发布"},
                   headers=harness.admin_headers())
    assert rr.status_code == 200
    with harness.sf() as db:
        from apps.worker.engine import WorkerEngine
        we = WorkerEngine(harness.sf)
        run2 = we._launch(db, harness.tenant_id, "metrics.mock_push",
                          {"package_id": "pkg-w10-pin2", "rows": []})
        closure2 = run2.context["_config_closure"]
        assert closure2["pack_revision_id"] == newest.id


def test_w10_old_run_restart_keeps_old_pack(harness: Harness, monkeypatch):
    """旧run中途切配置、restart后仍用旧依赖（G21核心语义）。

    metrics run 的依赖集=workflow revision（run外键固定）+闭包pack；
    病理运行时经 _runtime_for 读闭包——直接以旧/新run上下文驱动该路径。
    """
    import domains.pathology.handlers as H
    calls: list[str | None] = []
    orig = H.build_runtime

    def spy(db, tenant_id, **kw):
        calls.append(kw.get("pack_revision_id"))
        return orig(db, tenant_id, **kw)
    monkeypatch.setattr(H, "build_runtime", spy)
    with harness.sf() as db:
        old_pub = (db.query(ConfigRevision)
                   .filter_by(tenant_id=harness.tenant_id,
                              config_type="hospital_pack", state="PUBLISHED")
                   .order_by(ConfigRevision.revision.desc()).first())
        from apps.worker.engine import WorkerEngine
        we = WorkerEngine(harness.sf)
        run = we._launch(db, harness.tenant_id, "metrics.mock_push",
                         {"package_id": "pkg-w10-old", "rows": []})
        assert run.context["_config_closure"]["pack_revision_id"] == old_pub.id
    # 中途切换配置（发布新pack）
    harness.publish_pack({"approved_dictionaries": {"encounter_kind": "demo-v2"},
                          "sources": {"pis": {"database_candidate": "PITAYA"}},
                          "note": "switched-mid-run"})
    # restart：新engine实例驱动同一run的剩余步骤；闭包不变
    with harness.sf() as db:
        from apps.worker.engine import WorkerEngine
        we2 = WorkerEngine(harness.sf)
        we2._advance_workflows(db, max_steps=30)
        db.commit()
        r2 = db.get(WorkflowRun, run.id)
        assert r2.context["_config_closure"]["pack_revision_id"] == old_pub.id
        # 旧run的运行时解析路径（_runtime_for）仍读旧pack
        calls.clear()
        H._runtime_for(H.HandlerCtx(db=db, tenant_id=harness.tenant_id,
                                    run=r2, step=None, params={}))
        assert calls == [old_pub.id]
    # 新run使用新pack；其运行时读新pack
    with harness.sf() as db:
        newest = (db.query(ConfigRevision)
                  .filter_by(tenant_id=harness.tenant_id,
                             config_type="hospital_pack", state="PUBLISHED")
                  .order_by(ConfigRevision.revision.desc()).first())
        assert newest.id != old_pub.id
        from apps.worker.engine import WorkerEngine
        we3 = WorkerEngine(harness.sf)
        run2 = we3._launch(db, harness.tenant_id, "metrics.mock_push",
                           {"package_id": "pkg-w10-new", "rows": []})
        assert run2.context["_config_closure"]["pack_revision_id"] == newest.id
        calls.clear()
        H._runtime_for(H.HandlerCtx(db=db, tenant_id=harness.tenant_id,
                                    run=run2, step=None, params={}))
        assert calls == [newest.id]


# ================================================================ W11 metrics-only
@pytest.fixture(scope="module")
def metrics_only_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("metrics-only")
    proc = subprocess.Popen(
        [sys.executable, os.path.join("apps", "run_metrics_only.py"),
         "--root", str(root)],
        cwd=IMPL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    info = {}
    try:
        for _ in range(40):
            line = proc.stdout.readline()
            if not line:
                break
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                info[k] = v
            if k == "PLUGINS":
                break
        assert info.get("PLUGINS") == "metrics", info
        assert info.get("PATHOLOGY_LOADED") == "0", info
        port = int(info["METRICS_ONLY_PORT"])
        for _ in range(60):
            try:
                httpx.get(f"http://127.0.0.1:{port}/health", timeout=1)
                break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    raise RuntimeError(proc.stderr.read()[-3000:])
                time.sleep(0.25)
        yield {"port": port, "info": info, "proc": proc}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def test_w11_metrics_only_process_no_pathology(metrics_only_server, tmp_path):
    db_url = metrics_only_server["info"]["DB_URL"]
    db_path = db_url.replace("sqlite:///", "")
    import sqlite3
    conn = sqlite3.connect(db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert not any(t.startswith("pathology_") for t in tables), tables
        assert not any(t in ("expected_slide_set", "slide_effective_version",
                             "patient_reference") for t in tables), tables
        assert {"job", "workflow_run", "data_package", "destination",
                "config_release"} <= tables
    finally:
        conn.close()


def test_w11_metrics_only_csv_to_dual_targets(metrics_only_server):
    port = metrics_only_server["port"]
    headers = {"Authorization": "Bearer dev-admin-token"}
    csv_text = ("机构,月份,项目,数量,金额\n"
                "H1234567890X,2026-08,门诊切片,120,12345.60\n"
                "H1234567890X,2026-08,术中冰冻,30,9876.50\n")
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers,
                      timeout=30) as c:
        prev = c.post("/api/v1/imports/preview", json={"csv": csv_text}).json()["data"]
        assert prev["total_preview"] == 2 and not prev["row_errors"]
        imp = c.post("/api/v1/imports", json={"csv": csv_text},
                     headers={"Idempotency-Key": "w11-metrics-1"})
        assert imp.status_code == 200, imp.text
        pkg_id = imp.json()["data"]["package_id"]
        # CSV→映射→校验→冻结→双目标Mock（worker轮询至队列静止）
        deadline = time.time() + 60
        subs = pubs = []
        while time.time() < deadline:
            subs = c.get("/api/v1/submissions").json()["data"]
            pubs = c.get("/api/v1/publications").json()["data"]
            if subs and pubs:
                break
            time.sleep(0.5)
        assert subs, "metrics未到达PUSH Mock目的地"
        assert pubs, "metrics未到达视图发布目的地"
        # 幂等重放同包
        imp2 = c.post("/api/v1/imports", json={"csv": csv_text},
                      headers={"Idempotency-Key": "w11-metrics-1"})
        assert imp2.json()["data"]["package_id"] == pkg_id
        # 未知plugin不回退（R19在metrics-only下同样成立）：调一个病理域端点应显式失败
        r = c.get("/api/v1/pathology/cases")
        assert r.status_code in (404, 405), r.status_code  # 病理路由未注册


def test_w11_plugin_manifest_registry():
    import app_runtime
    reg = app_runtime.load_registry(["metrics"])
    assert reg.enabled == ["metrics"]
    assert reg.handler_for("parse", "metrics.csv") is not None
    assert reg.handler_for("resolve", "pathology.resolve") is None  # 无回退
    from connector_sdk import CapabilityError
    import pytest as _pytest
    with _pytest.raises(CapabilityError):
        reg.build_runtime(None, "t")
    full = app_runtime.load_registry()
    # 默认=全部 manifest（2026-10-16 起新增 drug_trace；生产 compose 以
    # BLYB_PLUGINS=pathology,metrics 显式固定，见 docker-compose.yml）
    assert set(full.enabled) == set(app_runtime.PLUGIN_MANIFESTS)
    assert {"pathology", "metrics", "drug_trace"} <= set(full.enabled)
    assert full.handler_for("resolve", "pathology.resolve") is not None
    # manifest字段完整
    for key, m in app_runtime.PLUGIN_MANIFESTS.items():
        assert m["module"] and m["domain"] and m["capabilities"], m
