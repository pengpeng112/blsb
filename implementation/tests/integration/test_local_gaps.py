"""本地收尾轮（第4轮）正式回归：04矩阵登记缺口补齐（T1—T4、T6—T10、T14）。

- T1 POST /mappings/{key}/simulate；T2 POST /ingress/{cid}/events；
  T3 GET /packages/{id}/revisions/{rev}；T4 POST /publications/{id}/revoke；
  T6 GET /agents/{id}/configuration；T7 手动 submissions/publications；
  T8 列表cursor统一；T9 迁移007；T10 PII读取路径；T14 令牌加固。
- T5 存储迁移见 tests/fault/test_local_gaps_storage.py。
"""
from __future__ import annotations

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from tests.harness import Harness  # noqa: E402
from contracts import new_uuid  # noqa: E402
from contracts.api import digest_of, hash_token  # noqa: E402


# ================================================================ 公共辅助
def _other_tenant_headers(harness: Harness) -> dict:
    """第二租户 platform_admin 令牌（跨租户404不泄露存在性用）。"""
    with harness.sf() as db:
        from core_db.models import AuthSubject, Tenant
        t = db.query(Tenant).filter_by(code="other-hospital").first()
        if t is None:
            t = Tenant(id=new_uuid(), code="other-hospital", name="二院（测试）")
            db.add(t)
            db.flush()
        token = "other-admin-token-t404"
        if not db.query(AuthSubject).filter_by(tenant_id=t.id,
                                               subject_code="admin").first():
            db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind="user",
                               subject_code="admin", display_name="二院管理员",
                               token_hash=hash_token(token),
                               roles=["platform_admin"]))
        db.commit()
    return {"Authorization": f"Bearer {token}"}


def _connector_headers(harness: Harness, token: str = "conn-t2-token"):
    """连接器主体令牌 + connector_id（T2 认证主体必须是该connector）。"""
    with harness.sf() as db:
        from core_db.models import AuthSubject, ConnectorInstance
        ci = (db.query(ConnectorInstance)
              .filter_by(tenant_id=harness.tenant_id).first())
        code = f"connector:{ci.id[:8]}"
        if not db.query(AuthSubject).filter_by(tenant_id=harness.tenant_id,
                                               subject_code=code).first():
            db.add(AuthSubject(id=new_uuid(), tenant_id=harness.tenant_id,
                               kind="connector", subject_code=code,
                               display_name=f"connector {ci.name}",
                               token_hash=hash_token(token), roles=["service"],
                               connector_id=ci.id))
        db.commit()
        return {"Authorization": f"Bearer {token}"}, ci.id


def _metrics_package(harness: Harness, tag: str) -> dict:
    csv_text = (f"机构,月份,项目,数量,金额\nH,2026-10,{tag},1,12.34")
    r = harness.core.post("/api/v1/imports", json={"csv": csv_text},
                          headers={**harness.admin_headers(),
                                   "Idempotency-Key": f"lg-{tag}"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    return {"package_id": data["package_id"], "rows": data["rows"]}


# ================================================================ T1 映射模拟
def test_T1_mapping_simulate(harness: Harness):
    core = harness.core
    cfg = {"Authorization": "Bearer dev-configer-token"}
    rules = {"ops": [
        {"target": "org", "op": "field", "source_field": "org"},
        {"target": "amount2", "op": "decimal_scale", "source_field": "amount",
         "places": 2},
        {"target": "kind", "op": "dict_map", "source_field": "kind",
         "dictionary": "encounter_kind", "default": "9"},
    ]}
    body = {"mapping_key": "t1.m", "rules": rules, "dictionary_refs": [
        {"dictionary": "encounter_kind", "namespace": "encounter_kind",
         "version": "demo-v2"}]}
    r = core.post("/api/v1/mappings", json=body, headers=cfg)
    assert r.status_code == 201, r.text
    rev = r.json()["data"]["revision"]

    sim = core.post("/api/v1/mappings/t1.m/simulate", headers=cfg, json={
        "records": [
            {"org": "H", "amount": "12.3", "kind": "住院"},
            {"org": "H2", "amount": "bad", "kind": "未知类别"},
            {"kind": "门诊"},
        ]})
    assert sim.status_code == 200, sim.text
    recs = sim.json()["data"]["records"]
    assert sim.json()["data"]["revision"] == rev
    # 正常转换 + 字典应用
    f0 = recs[0]["fields"]
    assert f0["amount2"]["value"] == "12.30"
    assert f0["kind"]["value"] == "2"
    assert f0["kind"]["dictionary_applied"] is True
    # 单字段错误不影响其余字段/记录
    f1 = recs[1]["fields"]
    assert f1["amount2"]["error"] is not None
    assert f1["org"]["value"] == "H2"
    assert f1["kind"]["value"] == "9" and f1["kind"]["dictionary_applied"] is False
    # 未知字段：field 取值 None（非错误）
    assert recs[2]["fields"]["org"]["value"] is None
    assert recs[2]["fields"]["kind"]["value"] == "1"

    # 禁用操作（import 等）被拒：API创建即422
    bad = core.post("/api/v1/mappings", headers=cfg, json={
        "mapping_key": "t1.bad",
        "rules": {"ops": [{"target": "x", "op": "field",
                           "source_field": "import os; os.system('x')"}]}})
    assert bad.status_code == 422
    assert any("import" in str(fe) for fe in bad.json()["error"]["field_errors"])
    # 灾难正则拒绝（绕过创建直插DB，simulate 路径同样拒绝）
    from core_db.models import MappingRevision
    with harness.sf() as db:
        db.add(MappingRevision(
            id=new_uuid(), tenant_id=harness.tenant_id, mapping_key="t1.cata",
            revision=1, state="ACTIVE",
            rules={"ops": [{"target": "x", "op": "regex_replace",
                            "source_field": "a", "pattern": "(a|aa)+$",
                            "replacement": "b"}]}))
        db.commit()
    r3 = core.post("/api/v1/mappings/t1.cata/simulate", headers=cfg,
                   json={"records": [{"a": "x"}]})
    assert r3.status_code == 422
    # 映射不存在 404；越权 403；records 上限 20
    assert core.post("/api/v1/mappings/no.such/simulate", headers=cfg,
                     json={"records": [{}]}).status_code == 404
    assert core.post("/api/v1/mappings/t1.m/simulate",
                     headers={"Authorization": "Bearer dev-reviewer-token"},
                     json={"records": [{}]}).status_code == 403
    assert core.post("/api/v1/mappings/t1.m/simulate", headers=cfg,
                     json={"records": [{}] * 21}).status_code == 422
    # 未解析字典引用 → 422（field_errors 指明）
    with harness.sf() as db:
        db.add(MappingRevision(
            id=new_uuid(), tenant_id=harness.tenant_id, mapping_key="t1.nodict",
            revision=1, state="ACTIVE",
            rules={"ops": [{"target": "k", "op": "dict_map",
                            "source_field": "k", "dictionary": "nope"}]},
            dictionary_refs=[{"dictionary": "nope", "namespace": "nope",
                              "version": "v9"}]))
        db.commit()
    r4 = core.post("/api/v1/mappings/t1.nodict/simulate", headers=cfg,
                   json={"records": [{"k": "x"}]})
    assert r4.status_code == 422
    assert any("nope" in str(fe) for fe in r4.json()["error"]["field_errors"])


# ================================================================ T2 推式ingress
def test_T2_ingress_events(harness: Harness):
    core = harness.core
    conn_h, ci_id = _connector_headers(harness)
    pkg = _metrics_package(harness, "t2")
    payload = {"event_type": "package.frozen", "domain": "metrics",
               "package_id": pkg["package_id"], "rows": pkg["rows"]}
    body = {"external_event_id": "ext-t2-1", "external_revision": "1",
            "payload": payload}
    # 非connector主体 → 404（不泄露）
    r404a = core.post(f"/api/v1/ingress/{ci_id}/events", json=body,
                      headers=harness.admin_headers())
    assert r404a.status_code == 404
    # connector 主体 → 202，已知类型 ROUTED
    r1 = core.post(f"/api/v1/ingress/{ci_id}/events", json=body, headers=conn_h)
    assert r1.status_code == 202, r1.text
    d1 = r1.json()["data"]
    assert d1["processed_state"] == "ROUTED"
    ev_id = d1["inbox_event_id"]
    # 同ID同revision同内容 → 200 duplicate
    r2 = core.post(f"/api/v1/ingress/{ci_id}/events", json=body, headers=conn_h)
    assert r2.status_code == 200
    assert r2.json()["data"] == {"duplicate": True, "inbox_event_id": ev_id}
    # 同ID异内容 → 409 IDEMPOTENCY_CONFLICT
    r3 = core.post(f"/api/v1/ingress/{ci_id}/events", headers=conn_h,
                   json={"external_event_id": "ext-t2-1",
                         "payload": {"event_type": "package.frozen",
                                     "domain": "metrics",
                                     "package_id": pkg["package_id"],
                                     "rows": []}})
    assert r3.status_code == 409
    assert r3.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    # 同ID新revision → 新事件
    r4 = core.post(f"/api/v1/ingress/{ci_id}/events", headers=conn_h,
                   json={"external_event_id": "ext-t2-1",
                         "external_revision": "2", "payload": payload})
    assert r4.status_code == 202 and r4.json()["data"]["inbox_event_id"] != ev_id
    # 已知类型经 worker outbox 路由产生 metrics.run Job
    harness.tick(5)
    from core_db.models import Job
    with harness.sf() as db:
        jobs = db.query(Job).filter(
            Job.tenant_id == harness.tenant_id,
            Job.job_type == "metrics.run",
            Job.dedup_key.contains(pkg["package_id"])).all()
        assert jobs, "已知event_type应产生 metrics.run Job（_route_event语义）"
    # 未知 event_type：保持 RECEIVED、无副作用
    with harness.sf() as db:
        from core_db.models import OutboxEvent
        before_outbox = db.query(OutboxEvent).count()
        before_jobs = db.query(Job).count()
    ru = core.post(f"/api/v1/ingress/{ci_id}/events", headers=conn_h,
                   json={"external_event_id": "ext-t2-unk",
                         "payload": {"event_type": "vendor.something",
                                     "foo": 1}})
    assert ru.status_code == 202
    assert ru.json()["data"]["processed_state"] == "RECEIVED"
    harness.tick(5)
    with harness.sf() as db:
        from core_db.models import OutboxEvent
        assert db.query(OutboxEvent).count() == before_outbox
        assert db.query(Job).count() == before_jobs
    # 跨租户 connector：二租户主体访问一租户连接器 → 404
    other_h = _other_tenant_headers(harness)
    with harness.sf() as db:
        from core_db.models import AuthSubject, ConnectorInstance, Tenant
        t2 = db.query(Tenant).filter_by(code="other-hospital").first()
        ci = db.query(ConnectorInstance).filter_by(id=ci_id).first()
        ci2 = ConnectorInstance(id=new_uuid(), tenant_id=t2.id, direction="source",
                                plugin_id="metrics.csv", name="other-conn",
                                status="ENABLED")
        db.add(ci2)
        db.flush()
        if not db.query(AuthSubject).filter_by(
                tenant_id=t2.id, subject_code="connector:other").first():
            db.add(AuthSubject(id=new_uuid(), tenant_id=t2.id, kind="connector",
                               subject_code="connector:other", roles=["service"],
                               token_hash=hash_token("other-conn-token"),
                               connector_id=ci2.id))
        db.commit()
        other_ci_id = ci2.id
    rx = core.post(f"/api/v1/ingress/{ci_id}/events",
                   json={"external_event_id": "x", "payload": {"event_type":
                    "package.frozen"}},
                   headers={**other_h, "connector_id": other_ci_id})
    assert rx.status_code == 404


# ================================================================ T3 固定包读取
def test_T3_package_revision_read(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    # metrics 包：结构读取
    pkg = _metrics_package(harness, "t3")
    r = core.get(f"/api/v1/packages/{pkg['package_id']}/revisions/1", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["state"] == "FROZEN" and d["revision"] == 1
    assert d["payload"]["rows"] and d["payload"]["rows"][0]["amount"] == "12.34"
    assert d["attachments"] == []
    with harness.sf() as db:
        from core_db.models import PackageRevision
        pr = (db.query(PackageRevision)
              .filter_by(package_id=pkg["package_id"], revision=1).first())
        assert d["digest"] == pr.digest  # 冻结内容一致（digest原文）
    # 病理包：索引串掩码（T10读取路径防护）
    plain_id = "110105199001011234"
    serialized = f"111-{plain_id}-2-B20260001-P2600001-20261001120000-H1234567890X-1"
    payload = {
        "case": {"source_case_id": "P2600001", "pathology_no_raw": "P2600001"},
        "index": {"serialized": serialized, "profile": "pathology-internal-v1",
                  "status": "CANDIDATE"},
        "slides": [{"slide_id": "s1", "barcode_raw": "900001", "scans": [
            {"effective": True, "assets": [
                {"asset_id": "a-1", "sha256": "ff" * 32, "size_bytes": 7,
                 "role": "primary"}]}]}],
        "reports": []}
    from core_db.models import DataPackage, PackageRevision
    with harness.sf() as db:
        p2 = DataPackage(id=new_uuid(), tenant_id=harness.tenant_id,
                         domain="pathology", business_key="t3:case")
        db.add(p2)
        db.flush()
        pr2 = PackageRevision(
            id=new_uuid(), tenant_id=harness.tenant_id, package_id=p2.id,
            revision=1, payload=payload, digest=digest_of(payload),
            state="FROZEN")
        db.add(pr2)
        p2.current_revision = 1
        db.commit()
        pkg2_id = p2.id
    r2 = core.get(f"/api/v1/packages/{pkg2_id}/revisions/1", headers=h)
    assert r2.status_code == 200
    d2 = r2.json()["data"]
    got_serialized = d2["payload"]["index"]["serialized"]
    assert plain_id not in got_serialized, "读取路径不得回传完整证件号"
    seg = got_serialized.split("-")[1]
    assert seg.startswith("1101") and seg.endswith("1234") and "*" in seg
    assert d2["digest"] == digest_of(payload)
    assert d2["attachments"] == [{"asset_id": "a-1", "sha256": "ff" * 32,
                                  "size_bytes": 7, "role": "primary"}]
    # 不存在 revision / 不存在包 / 跨租户 → 404
    assert core.get(f"/api/v1/packages/{pkg2_id}/revisions/9",
                    headers=h).status_code == 404
    assert core.get("/api/v1/packages/no-such-pkg/revisions/1",
                    headers=h).status_code == 404
    assert core.get(f"/api/v1/packages/{pkg2_id}/revisions/1",
                    headers=_other_tenant_headers(harness)).status_code == 404


# ================================================================ T4 发布撤回
def test_T4_publication_revoke(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    payload_bytes = b"T4-revoke-bytes"
    sha = hashlib.sha256(payload_bytes).hexdigest()
    obj_path = os.path.join(harness.tmp, "t4-object.bin")
    with open(obj_path, "wb") as f:
        f.write(payload_bytes)
    from core_db.models import (Asset, AssetLocation, Blob, DataPackage,
                                Destination, PackageRevision, Publication,
                                PublicationItem)
    with harness.sf() as db:
        dest = db.query(Destination).filter_by(
            tenant_id=harness.tenant_id, code="first-hospital-exchange").first()
        scan = harness.register_scan("900001", scan_id="scan-t4")
        # PRAGMA foreign_keys=ON：发布必须引用真实 package_revision 行
        pkg = DataPackage(id=new_uuid(), tenant_id=harness.tenant_id,
                          domain="pathology", business_key="t4:case")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=harness.tenant_id,
                             package_id=pkg.id, revision=1, payload={},
                             digest="d-t4", state="FROZEN")
        db.add(pr)
        pkg.current_revision = 1
        db.flush()
        blob = Blob(id=new_uuid(), tenant_id=harness.tenant_id,
                    size_bytes=len(payload_bytes), sha256=sha, state="AVAILABLE")
        db.add(blob)
        db.flush()
        asset = Asset(id=new_uuid(), tenant_id=harness.tenant_id,
                      acquisition_id=scan["acquisition_id"],
                      blob_id=blob.id, expected_size=len(payload_bytes),
                      expected_sha256=sha, state="AVAILABLE")
        db.add(asset)
        db.flush()
        db.add(AssetLocation(id=new_uuid(), tenant_id=harness.tenant_id,
                             blob_id=blob.id, volume_id="vol-t",
                             physical_key=obj_path, role="primary",
                             state="AVAILABLE"))
        pub = Publication(id=new_uuid(), tenant_id=harness.tenant_id,
                          package_revision_id=pr.id,
                          destination_id=dest.id, generation_id="gen-t4-1",
                          state="PREPARING")
        db.add(pub)
        db.flush()
        items = [
            PublicationItem(id=new_uuid(), tenant_id=harness.tenant_id,
                            publication_id=pub.id, entity_type="chk_info",
                            external_key="P2600001", state="PUBLISHED"),
            PublicationItem(id=new_uuid(), tenant_id=harness.tenant_id,
                            publication_id=pub.id, entity_type="asset_info",
                            external_key=asset.id, asset_id=asset.id,
                            state="PUBLISHED")]
        db.add_all(items)
        db.commit()
        pub_id, asset_id = pub.id, asset.id
    # 交换库建批+路由+发布
    from destinations.view_publication.exchange_db import ExchangeDb
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    batch = ex.begin_batch(pub_id, "gen-t4-1", "first-hospital-exchange",
                           manifest_digest="d-t4", tenant_id=harness.tenant_id)
    route_id = ex.add_asset_route(batch, asset.id, obj_path,
                                  len(payload_bytes), sha,
                                  "application/octet-stream")
    ex.mark_asset_reachable(route_id)
    ex.publish_batch(batch)
    ex.close()
    with harness.sf() as db:
        pub = db.get(Publication, pub_id)
        pub.state = "PUBLISHED"
        pub.published_at = pub.created_at
        pub.manifest_ref = {"batch_id": batch, "generation": "gen-t4-1",
                            "routes": [route_id]}
        db.commit()
    # 撤回前路由可下载（collector/service 主体）
    coll = {"Authorization": "Bearer dev-collector-token"}
    assert harness.exchange.get(f"/exchange/v1/files/{route_id}",
                                headers=coll).status_code == 200
    # 缺 reason → 422
    r_bad = core.post(f"/api/v1/publications/{pub_id}/revoke", json={},
                      headers=h)
    assert r_bad.status_code == 422
    # 跨租户 → 404
    assert core.post(f"/api/v1/publications/{pub_id}/revoke",
                     json={"reason": "x"},
                     headers=_other_tenant_headers(harness)).status_code == 404
    # 正常撤回
    rr = core.post(f"/api/v1/publications/{pub_id}/revoke",
                   json={"reason": "test revoke T4"}, headers=h)
    assert rr.status_code == 200, rr.text
    d = rr.json()["data"]
    assert d["state"] == "REVOKED" and d["routes_revoked"] == 1 \
        and d["tombstones_written"] == 2
    # 旧路由下载 404
    assert harness.exchange.get(f"/exchange/v1/files/{route_id}",
                                headers=coll).status_code == 404
    # 墓碑写入（dataset=entity_type, external_key, reason）
    ex2 = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    tomb = ex2.conn.execute(
        "SELECT dataset, external_key, reason FROM tombstone WHERE batch_id=?",
        (batch,)).fetchall()
    ex2.close()
    got = {(t["dataset"], t["external_key"]) for t in tomb}
    assert got == {("chk_info", "P2600001"), ("asset_info", asset.id)}
    assert all(t["reason"] == "test revoke T4" for t in tomb)
    # 状态/审计
    with harness.sf() as db:
        from core_db.models import AuditEvent
        pub = db.get(Publication, pub_id)
        assert pub.state == "REVOKED" and pub.revoked_at is not None
        assert db.query(AuditEvent).filter_by(
            tenant_id=harness.tenant_id, action="publication.revoke",
            target=f"publication:{pub_id}").count() == 1
    # 幂等重复撤回
    rr2 = core.post(f"/api/v1/publications/{pub_id}/revoke",
                    json={"reason": "again"}, headers=h)
    assert rr2.status_code == 200
    assert rr2.json()["data"]["idempotent"] is True


# ================================================================ T6 Agent只读配置
def test_T6_agent_configuration(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    agent_id = harness.agent_id
    # 无已应用配置 → applied=null
    r = core.get(f"/api/v1/agents/{agent_id}/configuration", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["applied"] is None and d["device_code"] == "SCAN-WS-01"
    assert "status" in d
    # 写入一条 ACKED apply_config → applied 携带 digest/version
    from core_db.models import AgentCommand
    cfg = {"watch_root": os.path.join(harness.tmp, "watch"),
           "core_url": "http://127.0.0.1:8000"}
    digest = digest_of(cfg)
    with harness.sf() as db:
        db.add(AgentCommand(
            id=new_uuid(), tenant_id=harness.tenant_id, agent_id=agent_id,
            type="apply_config", payload={"config": cfg, "digest": digest},
            config_revision="cfg-rev-7", state="ACKED",
            result={"ok": True, "applied_digest": digest}))
        db.commit()
    r2 = core.get(f"/api/v1/agents/{agent_id}/configuration", headers=h)
    d2 = r2.json()["data"]
    assert d2["applied"]["config_version"] == "cfg-rev-7"
    assert d2["applied"]["digest"] == digest
    assert d2["watch"]["watch_root_configured"] is True
    assert d2["watch"]["watch_root_digest"]
    # 响应不含令牌类字段/值
    text = r2.text.lower()
    assert "token" not in text and "secret" not in text
    # 跨租户 404
    assert core.get(f"/api/v1/agents/{agent_id}/configuration",
                    headers=_other_tenant_headers(harness)).status_code == 404


# ================================================================ T7 手动 submissions / publications
def test_T7_manual_submission_and_publication(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    pkg = _metrics_package(harness, "t7")
    with harness.sf() as db:
        from core_db.models import Destination
        dest = db.query(Destination).filter_by(
            tenant_id=harness.tenant_id, code="mock-push").first()
        dest_id = dest.id
    # 缺包 404 / 缺参 422
    assert core.post("/api/v1/submissions",
                     json={"package_id": "no-such", "destination_id": dest_id},
                     headers=h).status_code == 404
    assert core.post("/api/v1/submissions", json={}, headers=h).status_code == 422
    # 真实目的地守卫：is_mock=False + B01—B09 OPEN → 409 PROFILE_UNAPPROVED
    with harness.sf() as db:
        real = Destination(id=new_uuid(), tenant_id=harness.tenant_id,
                           code="real-push", mode="PUSH", is_mock=False,
                           status="ENABLED")
        db.add(real)
        db.commit()
        real_id = real.id
    rg = core.post("/api/v1/submissions",
                   json={"package_id": pkg["package_id"],
                         "destination_id": real_id}, headers=h)
    assert rg.status_code == 409
    assert rg.json()["error"]["code"] == "PROFILE_UNAPPROVED"
    # 正常创建 → tick → 存在且唯一；重复请求同资源
    r1 = core.post("/api/v1/submissions",
                   json={"package_id": pkg["package_id"],
                         "destination_id": dest_id}, headers=h)
    assert r1.status_code == 202, r1.text
    sub_id = r1.json()["data"]["submission_id"]
    harness.tick(12)
    r2 = core.post("/api/v1/submissions",
                   json={"package_id": pkg["package_id"],
                         "destination_id": dest_id}, headers=h)
    assert r2.status_code == 200
    assert r2.json()["data"]["submission_id"] == sub_id
    assert r2.json()["data"]["existing"] is True
    with harness.sf() as db:
        from core_db.models import Submission, PackageRevision
        pr = (db.query(PackageRevision)
              .filter_by(package_id=pkg["package_id"], revision=1).first())
        subs = db.query(Submission).filter_by(
            tenant_id=harness.tenant_id, package_revision_id=pr.id,
            destination_id=dest_id).all()
        assert len(subs) == 1
        assert subs[0].state in ("ACCEPTED", "CONFIRMED")
        assert subs[0].idempotency_key == f"{dest_id}:{pr.id}"
    # 手动发布（metrics → dual workflow 含 publish 节点；主seed未发布dual，先补发布）
    from apps.seed import metrics_dual_workflow
    from core_db.models import WorkflowRevision
    from contracts.api import digest_of as _digest_of
    with harness.sf() as db:
        dsl = metrics_dual_workflow()
        if not db.query(WorkflowRevision).filter_by(
                tenant_id=harness.tenant_id,
                workflow_key=dsl["workflow_key"],
                revision=dsl["revision"]).first():
            db.add(WorkflowRevision(
                id=new_uuid(), tenant_id=harness.tenant_id,
                workflow_key=dsl["workflow_key"], revision=dsl["revision"],
                dsl=dsl, digest=_digest_of(dsl), status="PUBLISHED",
                production_enabled=False, created_by="test-t7"))
            db.commit()
    rp = core.post("/api/v1/publications",
                   json={"package_id": pkg["package_id"]}, headers=h)
    assert rp.status_code == 202, rp.text
    pr_id = rp.json()["data"]["package_revision_id"]
    harness.tick(20)
    with harness.sf() as db:
        from core_db.models import Publication, PackageRevision
        pr = db.get(PackageRevision, pr_id)
        pubs = db.query(Publication).filter_by(
            tenant_id=harness.tenant_id, package_revision_id=pr_id).all()
        assert len(pubs) == 1 and pubs[0].state == "PUBLISHED"
    # case 形式：不存在的 case → 404
    assert core.post("/api/v1/publications", json={"case_id": "no-such"},
                     headers=h).status_code == 404


# ================================================================ T8 列表cursor统一
@pytest.mark.parametrize("path,role_token", [
    ("/api/v1/alerts", "dev-admin-token"),
    ("/api/v1/audit", "dev-auditor-token"),
])
def test_T8_cursor_pagination(harness: Harness, path: str, role_token: str):
    core = harness.core
    h = {"Authorization": f"Bearer {role_token}"}
    # 造 25 条同一时间戳的行（同刻翻页：ISO字符串直比会漏行的回归形态）
    from core_db.engine import utcnow
    same_ts = utcnow()
    if path == "/api/v1/alerts":
        from core_db.models import AlertEvent
        with harness.sf() as db:
            for i in range(25):
                db.add(AlertEvent(id=new_uuid(), tenant_id=harness.tenant_id,
                                  alert_type="T8", severity="info",
                                  message=f"t8-{i}", created_at=same_ts))
            db.commit()
    else:
        from core_db.models import AuditEvent
        with harness.sf() as db:
            for i in range(25):
                db.add(AuditEvent(id=new_uuid(), tenant_id=harness.tenant_id,
                                  actor="test:t8", action="t8.marker",
                                  target=f"t:{i}", at=same_ts))
            db.commit()
    # 无cursor请求兼容（返回列表形态）
    first = core.get(f"{path}?limit=10", headers=h).json()["data"]
    assert isinstance(first, list) and len(first) == 10
    # cursor 翻页：跨页无重复无丢失、总数一致
    seen: set[str] = set()
    cursor = ""
    pages = 0
    while True:
        r = core.get(f"{path}?limit=10&cursor={cursor}", headers=h).json()["data"]
        assert isinstance(r, dict) and "items" in r and "next_cursor" in r
        ids = [row["id"] for row in r["items"]]
        assert not (set(ids) & seen), "跨页不得重复"
        seen |= set(ids)
        pages += 1
        if not r["next_cursor"]:
            break
        cursor = r["next_cursor"]
        assert pages < 10, "防止游标死循环"
    assert len(seen) == 25, f"翻页总数应25，实际{len(seen)}（同刻行不得丢失）"


def test_T8_cursor_on_domain_lists(harness: Harness):
    """submissions/publications 等业务列表同样支持 cursor 信封（抽样2个）。"""
    core = harness.core
    h = harness.admin_headers()
    pkg = _metrics_package(harness, "t8")
    from core_db.models import Submission
    with harness.sf() as db:
        from core_db.models import Destination, PackageRevision
        pr = (db.query(PackageRevision)
              .filter_by(package_id=pkg["package_id"], revision=1).first())
        dest = db.query(Destination).filter_by(
            tenant_id=harness.tenant_id, code="mock-push").first()
        for i in range(7):
            db.add(Submission(
                id=new_uuid(), tenant_id=harness.tenant_id,
                package_revision_id=pr.id, destination_id=dest.id,
                snapshot_digest="d", idempotency_key=f"t8-{i}",
                state="PREPARED"))
        db.commit()
    r = core.get("/api/v1/submissions?limit=3", headers=h).json()["data"]
    assert isinstance(r, list) and len(r) == 3
    p = core.get("/api/v1/submissions?limit=3&cursor=", headers=h).json()["data"]
    assert isinstance(p, dict) and len(p["items"]) == 3 and p["next_cursor"]
    ids = {row["id"] for row in p["items"]}
    p2 = core.get(f"/api/v1/submissions?limit=50&cursor={p['next_cursor']}",
                  headers=h).json()["data"]
    assert not ({row["id"] for row in p2["items"]} & ids)
    # publications 同契约
    q = core.get("/api/v1/publications?limit=2&cursor=", headers=h).json()["data"]
    assert isinstance(q, dict) and "next_cursor" in q


# ================================================================ T9 迁移007
def test_T9_migration_chain_fresh_idempotent_with_data(tmp_path):
    from core_db import make_engine, run_migrations
    import plugin_registry
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path/'t9a.sqlite3'}"
    engine = make_engine()
    try:
        results = run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
        applied = {name for _, name, res in results if res == "APPLIED"}
        for want in ("001_core_init", "007_tenant_composite_constraints",
                     "008_publication_revoked_at_reservation_generalize"):
            assert want in applied
        # 带数据重跑：幂等、不清数据
        from core_db.models import AlertEvent
        from core_db.engine import make_session_factory
        sf = make_session_factory(engine)
        with sf() as db:
            db.add(AlertEvent(id=new_uuid(), tenant_id="t9", alert_type="T9",
                              severity="info", message="keep"))
            db.commit()
        results2 = run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
        assert all(res == "ALREADY_APPLIED" for _, _, res in results2)
        with sf() as db:
            assert db.query(AlertEvent).filter_by(alert_type="T9").count() == 1
    finally:
        engine.dispose()


def test_T9_old_db_upgrade_and_violation_alert(tmp_path):
    """旧库（仅001）升级007 + 跨租户行只告警跳过对应约束。"""
    import sqlite3
    from core_db import make_engine, run_migrations
    from core_db.migrate import _001_core
    import plugin_registry
    dbp = tmp_path / "t9b.sqlite3"
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{dbp}"
    engine = make_engine()
    try:
        _001_core(engine)  # 模拟旧库：只有001
        from core_db.engine import make_session_factory
        from core_db.models import Acquisition, Asset, SourceSystem, Tenant
        sf = make_session_factory(engine)
        with sf() as db:
            ta = Tenant(id=new_uuid(), code="t9-tenant-a", name="A")
            tb = Tenant(id=new_uuid(), code="t9-tenant-b", name="B")
            db.add_all([ta, tb])
            db.flush()
            src = SourceSystem(id=new_uuid(), tenant_id=ta.id, code="t9-src",
                               kind="scanner")
            db.add(src)
            db.flush()
            acq = Acquisition(id=new_uuid(), tenant_id=ta.id,
                              source_system_id=src.id,
                              external_event_id="e1", source_revision="1",
                              manifest={}, manifest_digest="d",
                              state="REGISTERED")
            db.add(acq)
            db.flush()
            # 违规行：asset.tenant=B 引用 A 的 acquisition
            bad = Asset(id=new_uuid(), tenant_id=tb.id,
                        acquisition_id=acq.id, state="AVAILABLE")
            db.add(bad)
            # 干净行：asset.tenant=A 引用 A 的 acquisition
            good = Asset(id=new_uuid(), tenant_id=ta.id,
                         acquisition_id=acq.id, state="AVAILABLE")
            db.add(good)
            db.commit()
            bad_id, good_id = bad.id, good.id
        results = run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
        applied = {name for _, name, res in results if res == "APPLIED"}
        assert "007_tenant_composite_constraints" in applied  # 迁移不失败
        with sf() as db:
            from core_db.models import AlertEvent
            alerts = db.query(AlertEvent).filter_by(
                alert_type="TENANT_REF_VIOLATION").all()
            assert any(a.target_ref == f"asset:{bad_id}" for a in alerts)
            # 数据不被改动
            assert db.get(Asset, bad_id).tenant_id == tb.id
            assert db.get(Asset, good_id).tenant_id == ta.id
        # SQLite 分档：违规对（asset→acquisition）的父表唯一索引被跳过；
        # 干净对（upload_session→asset）的父表唯一索引已建
        conn = sqlite3.connect(dbp)
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
        conn.close()
        assert "ux_acquisition_tenant_id" not in idx, "违规对应跳过约束"
        assert "ux_asset_tenant_id" in idx
    finally:
        engine.dispose()


def test_T9_pg_ddl_compiles_tenant_constraints():
    """PG方言DDL编译检查（参照 test_w07_pg_ddl_compiles_bigint 写法）：
    007 在 PG 上执行的唯一索引与复合FK可被 postgresql 方言编译。"""
    from sqlalchemy import (Column, ForeignKeyConstraint, Index, MetaData,
                            String, Table)
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import AddConstraint, CreateIndex
    md = MetaData()
    parent = Table("t9_parent", md, Column("tenant_id", String(36)),
                   Column("id", String(36)))
    child = Table("t9_child", md, Column("tenant_id", String(36)),
                  Column("parent_id", String(36)))
    idx = Index("ux_t9_parent_tenant_id", parent.c.tenant_id, parent.c.id,
                unique=True)
    ddl_idx = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert ddl_idx.startswith("CREATE UNIQUE INDEX")
    fk = ForeignKeyConstraint([child.c.tenant_id, child.c.parent_id],
                              [parent.c.tenant_id, parent.c.id],
                              name="fk_t9_child_tenant")
    ddl_fk = str(AddConstraint(fk).compile(dialect=postgresql.dialect()))
    assert "FOREIGN KEY(tenant_id, parent_id)" in ddl_fk
    assert "REFERENCES t9_parent (tenant_id, id)" in ddl_fk
    # 007 覆盖的父子对齐全（与迁移注册表一致）
    from core_db import migrate as mig
    pairs = {(c, k, p) for c, k, p in mig._TENANT_REF_PAIRS}
    assert ("asset", "acquisition_id", "acquisition") in pairs
    assert ("publication", "package_revision_id", "package_revision") in pairs
    assert ("asset_location", "blob_id", "blob") in pairs
    assert len(pairs) == 6


# ================================================================ T10 PII读取路径
def test_T10_read_paths_do_not_expose_plain_id(harness: Harness):
    core = harness.core
    h = harness.admin_headers()
    plain_id = "11010519850505432X"
    serialized = f"111-{plain_id}-1-B20260002-P2600002-20261001120000-H1234567890X-1"
    masked = "111-1101**********432X-1-B20260002-P2600002-20261001120000-H1234567890X-1"
    with harness.sf() as db:
        from core_db.models import SourceSystem
        from domains.pathology.models import (PathologyCase, PathologyIndexRecord,
                                              PatientReference)
        src = db.query(SourceSystem).filter_by(
            tenant_id=harness.tenant_id, code="pis").first()
        case = PathologyCase(
            id=new_uuid(), tenant_id=harness.tenant_id,
            source_system_id=src.id, registration_namespace="ns-t10",
            source_case_id="P2600002", pathology_no_raw="P2600002",
            state="ASSOCIATED", files_complete=True, slides_linked=True)
        db.add(case)
        db.flush()
        pat = PatientReference(
            id=new_uuid(), tenant_id=harness.tenant_id, namespace="ns-t10",
            source_patient_id="PX1", display_name_masked="张*",
            identity_snapshot={
                "outcome": {"patient_state": "CONFIRMED",
                            "id_type_code": "111"},
                "pis_id_masked": "1101**********432X"},
            state="CONFIRMED")
        db.add(pat)
        db.flush()
        case.patient_ref_id = pat.id
        db.add(PathologyIndexRecord(
            id=new_uuid(), tenant_id=harness.tenant_id, case_id=case.id,
            segments={}, display_value=masked, serialized_value=serialized,
            status="CANDIDATE"))
        db.commit()
        case_id = case.id
    # 病例详情：掩码 display_value 返回，明文 serialized 不外发
    r = core.get(f"/api/v1/pathology/cases/{case_id}", headers=h)
    assert r.status_code == 200, r.text
    assert plain_id not in r.text
    assert masked in r.text
    # T3 包读取端点的索引掩码已在 test_T3 断言；列表端点无患者证件字段
    for path in ("/api/v1/assets", "/api/v1/submissions", "/api/v1/acquisitions"):
        lr = core.get(path, headers=h)
        assert lr.status_code == 200
        assert plain_id not in lr.text
    # 关联候选匹配基线不带明文（match_basis 仅方法/截断标志）
    from domains.pathology.resolver import _mask_id
    assert _mask_id(plain_id).startswith("1101")
    assert plain_id not in _mask_id(plain_id)


# ================================================================ T14 令牌加固
def test_T14_seed_token_hardening(tmp_path, monkeypatch):
    import secrets
    from core_db import make_engine, make_session_factory, run_migrations
    import plugin_registry
    from apps.seed import seed
    dbp = tmp_path / "t14.sqlite3"
    monkeypatch.setenv("BLYB_DATABASE_URL", f"sqlite:///{dbp}")
    monkeypatch.setenv("BLYB_EXCHANGE_DB", str(tmp_path / "t14-ex.sqlite3"))
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    boot_token = "boot-" + secrets.token_urlsafe(32)
    try:
        # 1) 全新库：禁用dev + bootstrap → 无dev主体、引导主体可用
        monkeypatch.setenv("BLYB_DISABLE_DEV_TOKENS", "1")
        monkeypatch.setenv("BLYB_BOOTSTRAP_ADMIN_TOKEN", boot_token)
        created = seed(engine, sf, with_volume=False)
        tid = created["tenant_id"]
        from core_db.models import AuthSubject
        with sf() as db:
            for code in ("admin", "configer", "reviewer", "operator",
                         "auditor", "collector", "agent:SCAN-WS-01"):
                assert db.query(AuthSubject).filter_by(
                    tenant_id=tid, subject_code=code).first() is None, code
            boot = db.query(AuthSubject).filter_by(
                subject_code="bootstrap-admin").first()
            assert boot is not None and boot.roles == ["platform_admin"]
            assert boot.token_hash == hash_token(boot_token)
        # 2) 旧库形态：先常规seed（创建dev主体）再开启禁用 → 既有dev主体被禁用
        monkeypatch.delenv("BLYB_DISABLE_DEV_TOKENS")
        monkeypatch.delenv("BLYB_BOOTSTRAP_ADMIN_TOKEN")
        seed(engine, sf, with_volume=False)
        with sf() as db:
            assert db.query(AuthSubject).filter_by(
                tenant_id=tid, subject_code="admin").first().status == "ACTIVE"
        monkeypatch.setenv("BLYB_DISABLE_DEV_TOKENS", "1")
        monkeypatch.setenv("BLYB_BOOTSTRAP_ADMIN_TOKEN", boot_token)
        info = seed(engine, sf, with_volume=False)
        assert info["dev_disabled"] >= 7  # 6用户+collector+agent 主体全部禁用
        with sf() as db:
            for code in ("admin", "configer", "reviewer", "operator",
                         "auditor", "collector", "agent:SCAN-WS-01"):
                sub = db.query(AuthSubject).filter_by(
                    tenant_id=tid, subject_code=code).first()
                assert sub is not None and sub.status == "DISABLED", code
            assert db.query(AuthSubject).filter_by(
                subject_code="bootstrap-admin").first().status == "ACTIVE"
        # 3) 登录语义：禁用令牌401；bootstrap令牌200；错误令牌401
        from fastapi.testclient import TestClient
        from apps.core_api.app import create_app as create_core
        core = TestClient(create_core(session_factory=sf, engine=engine))
        r1 = core.post("/api/v1/auth/login", json={"token": "dev-admin-token"})
        assert r1.status_code == 401
        r2 = core.post("/api/v1/auth/login", json={"token": boot_token})
        assert r2.status_code == 200
        assert "platform_admin" in r2.json()["data"]["roles"]
        r3 = core.post("/api/v1/auth/login", json={"token": "boot-wrong"})
        assert r3.status_code == 401
        # 4) 未设置变量时行为与现状一致（回归）：清掉后 seed 不再改动状态
        monkeypatch.delenv("BLYB_DISABLE_DEV_TOKENS")
        monkeypatch.delenv("BLYB_BOOTSTRAP_ADMIN_TOKEN")
        seed(engine, sf, with_volume=False)
        with sf() as db:
            assert db.query(AuthSubject).filter_by(
                tenant_id=tid, subject_code="admin").first().status == "DISABLED"
            # bootstrap主体不因未设置变量而被删除/禁用（幂等不回退）
            assert db.query(AuthSubject).filter_by(
                subject_code="bootstrap-admin").first().status == "ACTIVE"
    finally:
        engine.dispose()


def test_T14_volume_root_isolated(tmp_path, monkeypatch):
    """内网卷根必须跟 BLYB_VOLUME_ROOT，不能落进开发栈的 var/volume1。"""
    import secrets
    from core_db import make_engine, make_session_factory, run_migrations
    import plugin_registry
    from apps.seed import seed
    from core_db.models import AssetLocation, Blob, StorageVolume

    dbp = tmp_path / "vol.sqlite3"
    monkeypatch.setenv("BLYB_DATABASE_URL", f"sqlite:///{dbp}")
    monkeypatch.setenv("BLYB_EXCHANGE_DB", str(tmp_path / "vol-ex.sqlite3"))
    monkeypatch.setenv("BLYB_DISABLE_DEV_TOKENS", "1")
    monkeypatch.setenv("BLYB_BOOTSTRAP_ADMIN_TOKEN", "boot-" + secrets.token_urlsafe(24))
    root1 = tmp_path / "vol-a"
    monkeypatch.setenv("BLYB_VOLUME_ROOT", str(root1))
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    try:
        seed(engine, sf)
        with sf() as db:
            vol = db.query(StorageVolume).filter_by(name="vol1").one()
            vol_id = vol.id
            assert os.path.abspath(vol.root_ref) == os.path.abspath(root1)
            assert os.path.isfile(os.path.join(vol.root_ref, f".blyb-vol-{vol_id}"))
            assert os.path.isdir(os.path.join(vol.root_ref, "objects"))
        root2 = tmp_path / "vol-b"
        monkeypatch.setenv("BLYB_VOLUME_ROOT", str(root2))
        seed(engine, sf)
        with sf() as db:
            vol = db.query(StorageVolume).filter_by(name="vol1").one()
            assert vol.id == vol_id
            assert os.path.abspath(vol.root_ref) == os.path.abspath(root2)
            assert not os.path.isfile(os.path.join(str(root1), f".blyb-vol-{vol_id}"))
        with sf() as db:
            vol = db.query(StorageVolume).filter_by(name="vol1").one()
            blob = Blob(id=new_uuid(), tenant_id=vol.tenant_id, size_bytes=1,
                        sha256="a" * 64, state="AVAILABLE")
            db.add(blob)
            db.flush()
            db.add(AssetLocation(
                id=new_uuid(), tenant_id=vol.tenant_id, blob_id=blob.id,
                volume_id=vol.id, physical_key=os.path.join(str(root2), "objects", "x"),
                state="AVAILABLE"))
            db.commit()
        monkeypatch.setenv("BLYB_VOLUME_ROOT", str(tmp_path / "vol-c"))
        with pytest.raises(RuntimeError, match="refusing to switch root"):
            seed(engine, sf)
        with sf() as db:
            vol = db.query(StorageVolume).filter_by(name="vol1").one()
            assert os.path.abspath(vol.root_ref) == os.path.abspath(root2)
    finally:
        engine.dispose()


# ================================================================ T4b 撤回×结构化当前视图（LG-13）
def test_T4b_revoke_structured_current_view(harness: Harness):
    """撤回不仅停文件路由，还要处理结构化当前视图（增量指针模型）：
    批次B撤回→B拥有的键从 dataset_current 消失；批次A拥有的键不受影响；
    再撤A→全部消失；current_generation 不再指向已撤回批次。"""
    core = harness.core
    h = harness.admin_headers()
    from core_db.models import (DataPackage, Destination, PackageRevision,
                                Publication, PublicationItem)
    with harness.sf() as db:
        dest = db.query(Destination).filter_by(
            tenant_id=harness.tenant_id, code="first-hospital-exchange").first()
        dest_id = dest.id
    pubs = {}
    for tag in ("a", "b"):
        with harness.sf() as db:
            pkg = DataPackage(id=new_uuid(), tenant_id=harness.tenant_id,
                              domain="pathology", business_key=f"t4b:case-{tag}")
            db.add(pkg)
            db.flush()
            pr = PackageRevision(id=new_uuid(), tenant_id=harness.tenant_id,
                                 package_id=pkg.id, revision=1, payload={},
                                 digest=f"d-t4b-{tag}", state="FROZEN")
            db.add(pr)
            pkg.current_revision = 1
            pub = Publication(id=new_uuid(), tenant_id=harness.tenant_id,
                              package_revision_id=pr.id, destination_id=dest_id,
                              generation_id=f"gen-t4b-{tag}", state="PREPARING")
            db.add(pub)
            db.flush()
            db.add(PublicationItem(
                id=new_uuid(), tenant_id=harness.tenant_id,
                publication_id=pub.id, entity_type="chk_info",
                external_key=f"P-T4B-{tag.upper()}", state="PUBLISHED"))
            db.commit()
            pubs[tag] = pub.id
    from destinations.view_publication.exchange_db import ExchangeDb
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    batches = {}
    for tag, key in (("a", "P-T4B-A"), ("b", "P-T4B-B")):
        batch = ex.begin_batch(pubs[tag], f"gen-t4b-{tag}",
                               "first-hospital-exchange",
                               manifest_digest=f"d-t4b-{tag}",
                               tenant_id=harness.tenant_id)
        ex.upsert_projection(batch, "chk_info", key,
                             {"pathology_no": key, "note": f"case {tag}"})
        ex.publish_batch(batch)
        batches[tag] = batch
    ex.close()
    with harness.sf() as db:
        for tag in ("a", "b"):
            pub = db.get(Publication, pubs[tag])
            pub.state = "PUBLISHED"
            pub.published_at = pub.created_at
            pub.manifest_ref = {"batch_id": batches[tag],
                                "generation": f"gen-t4b-{tag}", "routes": []}
        db.commit()
    gw_h = {"Authorization": "Bearer dev-collector-token"}

    def _keys():
        r = harness.exchange.get("/exchange/v1/datasets/chk_info",
                                 params={"limit": 200}, headers=gw_h)
        assert r.status_code == 200, r.text
        return {row["external_key"] for row in r.json()["data"]["rows"]}

    before = _keys()
    assert {"P-T4B-A", "P-T4B-B"} <= before
    gen = harness.exchange.get("/exchange/v1/generations/current",
                               headers=gw_h).json()["data"]
    assert gen["batch_id"] == batches["b"]
    rr = core.post(f"/api/v1/publications/{pubs['b']}/revoke",
                   json={"reason": "t4b revoke B"}, headers=h)
    assert rr.status_code == 200, rr.text
    assert rr.json()["data"]["current_pointers_removed"] == 1
    after = _keys()
    assert "P-T4B-B" not in after and "P-T4B-A" in after
    gen2 = harness.exchange.get("/exchange/v1/generations/current",
                                headers=gw_h).json()["data"]
    assert gen2 is None
    rr2 = core.post(f"/api/v1/publications/{pubs['b']}/revoke",
                    json={"reason": "again"}, headers=h)
    assert rr2.status_code == 200 and rr2.json()["data"]["idempotent"] is True
    assert _keys() == after
    ex2 = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    n_tomb = ex2.conn.execute(
        "SELECT COUNT(*) c FROM tombstone WHERE batch_id=?",
        (batches["b"],)).fetchone()["c"]
    ex2.close()
    assert n_tomb == 1
    rra = core.post(f"/api/v1/publications/{pubs['a']}/revoke",
                    json={"reason": "t4b revoke A"}, headers=h)
    assert rra.status_code == 200, rra.text
    assert "P-T4B-A" not in _keys()
    ex3 = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    n_proj = ex3.conn.execute(
        "SELECT COUNT(*) c FROM projection_row WHERE batch_id IN (?,?)",
        (batches["a"], batches["b"])).fetchone()["c"]
    states = {r["batch_id"]: r["state"] for r in ex3.conn.execute(
        "SELECT batch_id, state FROM release_batch WHERE batch_id IN (?,?)",
        (batches["a"], batches["b"])).fetchall()}
    ex3.close()
    assert n_proj == 2 and set(states.values()) == {"REVOKED"}


def test_T4c_tombstone_unique_index_and_legacy_tolerance(harness: Harness):
    """LG-05：exchange.003 tombstone 唯一索引——新库直接生效；
    存量重复墓碑的旧库升级不崩溃（索引跳过+告警，人工盘点）。"""
    from destinations.view_publication.exchange_db import ExchangeDb
    ex = ExchangeDb(os.environ["BLYB_EXCHANGE_DB"])
    ex.add_tombstone("batch-ux", "chk_info", "K1", "r1")
    ex.add_tombstone("batch-ux", "chk_info", "K1", "r2")
    rows = ex.conn.execute(
        "SELECT reason FROM tombstone WHERE batch_id='batch-ux'").fetchall()
    assert len(rows) == 1 and rows[0]["reason"] == "r1"
    ex.close()
    import sqlite3
    legacy = os.path.join(harness.tmp, "legacy-exchange.sqlite3")
    conn = sqlite3.connect(legacy)
    conn.executescript("""
      CREATE TABLE tombstone (id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT,
        dataset TEXT, external_key TEXT, reason TEXT, created_at TEXT);
      INSERT INTO tombstone (batch_id, dataset, external_key, reason, created_at)
        VALUES ('b','d','k','r1','t'), ('b','d','k','r2','t');
    """)
    conn.commit()
    conn.close()
    ex2 = ExchangeDb(legacy)  # 不抛异常
    idx = ex2.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND name='ux_tombstone_key'").fetchall()
    assert not idx  # 重复存在 → 索引跳过（告警日志），待人工清理
    ex2.close()


# ================================================================ T6b 设备owner授权
def test_T6b_agent_configuration_owner(harness: Harness):
    """T6 owner 授权：Agent 主体只能读自身配置；读其他设备 403；
    operator/admin 可读任意本租户设备（跨租户404已在 test_T6 覆盖）。"""
    core = harness.core
    agent1 = harness.agent_id
    own = {"Authorization": "Bearer dev-agent-token"}
    r = core.get(f"/api/v1/agents/{agent1}/configuration", headers=own)
    assert r.status_code == 200, r.text
    with harness.sf() as db:
        from core_db.models import Agent, AuthSubject, SourceSystem
        ss = (db.query(SourceSystem)
              .filter_by(tenant_id=harness.tenant_id).first())
        a2 = Agent(id=new_uuid(), tenant_id=harness.tenant_id,
                   source_system_id=ss.id,
                   device_code="SCAN-WS-02", status="ACTIVE")
        db.add(a2)
        db.flush()
        db.add(AuthSubject(id=new_uuid(), tenant_id=harness.tenant_id,
                           kind="agent", subject_code="agent:SCAN-WS-02",
                           display_name="Agent SCAN-WS-02",
                           token_hash=hash_token("dev-agent2-token"),
                           roles=["service"], agent_id=a2.id))
        db.commit()
        agent2 = a2.id
    r2 = core.get(f"/api/v1/agents/{agent2}/configuration", headers=own)
    assert r2.status_code == 403, r2.text
    other = {"Authorization": "Bearer dev-agent2-token"}
    r3 = core.get(f"/api/v1/agents/{agent2}/configuration", headers=other)
    assert r3.status_code == 200, r3.text
    r4 = core.get(f"/api/v1/agents/{agent2}/configuration",
                  headers=harness.admin_headers())
    assert r4.status_code == 200, r4.text


# ================================================================ T7b 手动提交 UNKNOWN 自动查证（LG-11）
def test_T7b_manual_unknown_autopoll(harness: Harness, monkeypatch):
    core = harness.core
    h = harness.admin_headers()
    pkg = _metrics_package(harness, "t7b")
    with harness.sf() as db:
        from core_db.models import Destination
        dest_id = db.query(Destination).filter_by(
            tenant_id=harness.tenant_id, code="mock-push").first().id
    r = core.post("/api/v1/submissions",
                  json={"package_id": pkg["package_id"],
                        "destination_id": dest_id}, headers=h)
    assert r.status_code == 202, r.text
    sub_id = r.json()["data"]["submission_id"]
    import core_handlers

    def _fake_push(db, tenant_id, submission, payload):
        from contracts import SubmissionState
        submission.state = SubmissionState.UNKNOWN
        db.commit()
        return {"outcome": "UNKNOWN", "http_status": None}

    monkeypatch.setattr(core_handlers, "perform_push", _fake_push)
    harness.tick(12)
    with harness.sf() as db:
        from core_db.models import Job, Submission
        # 核心断言（确定性）：UNKNOWN 后自动排 destination.query_unknown。
        # sub.state 允许 UNKNOWN（查证未及跑）或已被查证/重发推进（功能本身）。
        jobs = db.query(Job).filter_by(
            tenant_id=harness.tenant_id,
            job_type="destination.query_unknown").all()
        assert any((j.payload or {}).get("submission_id") == sub_id
                   for j in jobs), "UNKNOWN 后必须自动排 destination.query_unknown"
        sub = db.get(Submission, sub_id)
        assert sub.state in ("UNKNOWN", "ACCEPTED", "CONFIRMED", "SENDING")
    monkeypatch.undo()


# ================================================================ S15 字段血缘真实数据
def test_field_lineage_real_data(harness: Harness):
    """lineage 不再是静态占位：configured 来自真实 MappingRevision；
    evaluated 按 case 返回实际生效的包修订与规则版本；design 标注来源。"""
    core = harness.core
    cfg = {"Authorization": "Bearer dev-configer-token"}
    body = {"mapping_key": "lin.m", "rules": {"ops": [
        {"target": "org", "op": "field", "source_field": "org"},
        {"target": "kind", "op": "dict_map", "source_field": "kind",
         "dictionary": "encounter_kind"}]},
        "dictionary_refs": [{"dictionary": "encounter_kind",
                             "namespace": "encounter_kind",
                             "version": "demo-v2"}]}
    assert core.post("/api/v1/mappings", json=body, headers=cfg).status_code == 201
    r = core.get("/api/v1/field-lineage", headers=harness.admin_headers())
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert set(d.keys()) >= {"design", "configured", "evaluated"}
    assert d["design"]["case.pathology_no"]
    lin = next((m for m in d["configured"] if m["mapping_key"] == "lin.m"), None)
    assert lin is not None and lin["state"] == "ACTIVE"
    fmap = {f["target"]: f for f in lin["fields"]}
    assert fmap["org"]["source_field"] == "org"
    assert fmap["kind"]["dictionary"] == "encounter_kind"
    # 建真实病例并冻结（同 F16 模式：register→resolve→freeze）
    reg = harness.register_scan("090001")
    harness.resolve(reg["acquisition_id"])
    with harness.sf() as db:
        import plugin_registry
        from domains.pathology import pipeline as path_pipeline
        from domains.pathology.models import PathologyCase
        case_obj = (db.query(PathologyCase)
                    .filter_by(tenant_id=harness.tenant_id,
                               pathology_no_raw="0900001").first())
        assert case_obj is not None
        rt = plugin_registry.build_runtime(db, harness.tenant_id)
        pr = path_pipeline.freeze_case(db, harness.tenant_id, case_obj, rt,
                                       revision_note="lineage-test")
        assert pr is not None and pr.state == "FROZEN", "case应可冻结（合成批准规则）"
        db.commit()
        case_id = case_obj.id
    r2 = core.get(f"/api/v1/field-lineage?case_id={case_id}",
                  headers=harness.admin_headers())
    assert r2.status_code == 200, r2.text
    ev = r2.json()["data"]["evaluated"]
    assert ev["found"] is True
    assert ev["package_revision"] == pr.revision
    assert ev["rule_versions"], "索引记录必须带实际规则版本"
    assert ev["index_display"]
    r3 = core.get("/api/v1/field-lineage?case_id=no-such-case",
                  headers=_other_tenant_headers(harness))
    assert r3.status_code == 200
    assert r3.json()["data"]["evaluated"]["found"] is False
