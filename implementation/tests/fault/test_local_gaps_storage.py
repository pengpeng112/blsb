"""本地收尾轮（第4轮）T5：存储迁移 API（预览→执行）。

- preview 计数与可行性（free-safety_margin-预留账本）；
- 低空间/哨兵缺失拒绝（507/503语义）；
- 执行后双 location 且字节/hash 相等、源对象完好保留；
- 重复执行幂等；注入 RetryLater 后 Operation 可恢复；
- 跨租户 404。
"""
from __future__ import annotations

import hashlib
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from contracts import new_uuid  # noqa: E402
from contracts.api import hash_token  # noqa: E402

ADMIN = {"Authorization": "Bearer dev-admin-token"}


class _RetryLater(Exception):
    retry_after = 60  # 制造延迟窗口，验证RETRY_WAIT中间态后再放行


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path / 't5.sqlite3'}"
    os.environ["BLYB_EXCHANGE_DB"] = str(tmp_path / "t5-ex.sqlite3")
    from core_db import make_engine, make_session_factory, run_migrations
    import plugin_registry
    from apps.seed import seed
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    from core_db.models import StorageVolume, Tenant
    from storage_backend import VolumeHandle
    with sf() as db:
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        for name in ("vol-a", "vol-b"):
            root = tmp_path / name
            root.mkdir()
            h = VolumeHandle(name, str(root), 0)
            h.ensure_layout()
            h.ensure_sentinel()
            db.add(StorageVolume(id=name, tenant_id=t.id, backend="filesystem",
                                 name=name, root_ref=str(root),
                                 safety_margin_bytes=0, state="ACTIVE",
                                 health="OK"))
        db.commit()
        tenant_id = t.id
    from fastapi.testclient import TestClient
    from apps.storage_service.app import create_app as create_storage
    storage = TestClient(create_storage(session_factory=sf, engine=engine,
                                        embedded_worker=False))
    from apps.worker.engine import WorkerEngine
    worker = WorkerEngine(sf)
    yield {"sf": sf, "engine": engine, "tenant_id": tenant_id,
           "storage": storage, "worker": worker, "tmp": str(tmp_path)}
    engine.dispose()


def _make_asset(env, tag: str, payload: bytes) -> dict:
    sha = hashlib.sha256(payload).hexdigest()
    src_path = os.path.join(env["tmp"], f"t5-src-{tag}.bin")
    with open(src_path, "wb") as f:
        f.write(payload)
    from core_db.models import Acquisition, Asset, AssetLocation, Blob, SourceSystem
    with env["sf"]() as db:
        src = (db.query(SourceSystem)
               .filter_by(tenant_id=env["tenant_id"],
                          code="scanner-simulator").first())
        acq = Acquisition(
            id=new_uuid(), tenant_id=env["tenant_id"], source_system_id=src.id,
            external_event_id=f"ev-t5-{tag}", source_revision="1",
            manifest={}, manifest_digest=f"d-{tag}", state="FILES_COMPLETE")
        db.add(acq)
        db.flush()
        blob = Blob(id=new_uuid(), tenant_id=env["tenant_id"],
                    size_bytes=len(payload), sha256=sha, state="AVAILABLE")
        db.add(blob)
        db.flush()
        asset = Asset(id=new_uuid(), tenant_id=env["tenant_id"],
                      acquisition_id=acq.id, blob_id=blob.id,
                      expected_size=len(payload), expected_sha256=sha,
                      state="AVAILABLE")
        db.add(asset)
        db.flush()
        db.add(AssetLocation(id=new_uuid(), tenant_id=env["tenant_id"],
                             blob_id=blob.id, volume_id="vol-a",
                             physical_key=src_path, role="primary",
                             state="AVAILABLE"))
        db.commit()
        return {"asset_id": asset.id, "blob_id": blob.id, "sha": sha,
                "size": len(payload), "src_path": src_path}


def _drive(env, rounds: int = 6):
    for _ in range(rounds):
        env["worker"].tick(max_jobs=10, max_steps=8, retry_wait_seconds=0)
        time.sleep(0.02)


def _register_volume(env, name: str, *, safety: int = 0, sentinel: bool = True):
    root = os.path.join(env["tmp"], f"root-{name}")
    os.makedirs(root, exist_ok=True)
    from storage_backend import VolumeHandle
    h = VolumeHandle(name, root, safety)
    h.ensure_layout()
    if sentinel:
        h.ensure_sentinel()
    r = env["storage"].post("/api/v1/storage/volumes", headers=ADMIN, json={
        "name": name, "root_ref": root, "capacity_bytes": 0,
        "safety_margin_bytes": safety})
    assert r.status_code == 200, r.text
    return r.json()["data"]["volume_id"]


def test_T5_preview_counts_and_feasibility(env):
    a1 = _make_asset(env, "p1", b"A" * 1000)
    a2 = _make_asset(env, "p2", b"B" * 300)
    r = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "preview", "asset_ids": [a1["asset_id"], a2["asset_id"]],
        "target_volume_id": "vol-b"})
    assert r.status_code == 200, r.text
    plan = r.json()["data"]
    assert len(plan["items"]) == 2
    assert plan["total_bytes"] == 1300
    assert {i["from_volume"] for i in plan["items"]} == {"vol-a"}
    assert {i["to_volume"] for i in plan["items"]} == {"vol-b"}
    assert plan["feasible"] is True and plan["reason"] == ""
    # 缺参/未知资产/未知卷
    assert env["storage"].post("/api/v1/storage/migrations", headers=ADMIN,
                               json={"mode": "preview"}).status_code == 422
    assert env["storage"].post(
        "/api/v1/storage/migrations", headers=ADMIN,
        json={"mode": "preview", "asset_ids": ["no-such"],
              "target_volume_id": "vol-b"}).status_code == 404
    assert env["storage"].post(
        "/api/v1/storage/migrations", headers=ADMIN,
        json={"mode": "preview", "asset_ids": [a1["asset_id"]],
              "target_volume_id": "no-such-vol"}).status_code == 404


def test_T5_execute_dual_location_and_idempotent(env):
    payload = os.urandom(4096)
    a = _make_asset(env, "x1", payload)
    r = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "execute", "asset_ids": [a["asset_id"]],
        "target_volume_id": "vol-b"})
    assert r.status_code == 202, r.text
    op_id = r.json()["data"]["operation_id"]
    _drive(env)
    op = env["storage"].get(f"/api/v1/operations/{op_id}",
                            headers=ADMIN).json()["data"]
    assert op["state"] == "SUCCEEDED", op
    assert op["result"]["migrated"] == 1 and op["result"]["total"] == 1
    # T5 计划快照随 Operation 持久化，且结算后仍保留（进度/补偿可查证）
    plan = op["result"]["plan"]
    assert plan["target_volume_id"] == "vol-b"
    assert plan["items"][0]["asset_id"] == a["asset_id"]
    assert plan["items"][0]["from_volume"] == "vol-a"
    assert plan["total_bytes"] == len(payload)
    from core_db.models import AssetLocation, StorageVolume
    with env["sf"]() as db:
        locs = (db.query(AssetLocation)
                .filter_by(blob_id=a["blob_id"], state="AVAILABLE").all())
        assert len(locs) == 2, "双location（primary+replica）"
        roles = {loc.role for loc in locs}
        assert roles == {"primary", "replica"}
        replica = [loc for loc in locs if loc.role == "replica"][0]
        assert replica.volume_id == "vol-b"
        with open(replica.physical_key, "rb") as f:
            got = f.read()
        assert hashlib.sha256(got).hexdigest() == a["sha"]
        assert got == payload
        # 源对象完好保留（不删不改）
        with open(a["src_path"], "rb") as f:
            assert f.read() == payload
        src_loc = [loc for loc in locs if loc.role == "primary"][0]
        assert src_loc.physical_key == a["src_path"] and src_loc.state == "AVAILABLE"
        # CAS 释放预留：目标卷 reserved_bytes 回落
        vb = db.get(StorageVolume, "vol-b")
        assert vb.reserved_bytes == 0
    # 重复执行幂等：不再建Job，Operation 直接 SUCCEEDED
    r2 = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "execute", "asset_ids": [a["asset_id"]],
        "target_volume_id": "vol-b"})
    assert r2.status_code == 202
    d2 = r2.json()["data"]
    assert d2["already_migrated"] == 1 and d2["state"] == "SUCCEEDED"
    from core_db.models import AssetLocation as _AL
    with env["sf"]() as db:
        assert db.query(_AL).filter_by(blob_id=a["blob_id"]).count() == 2


def test_T5_low_space_rejected(env):
    a = _make_asset(env, "tiny", b"C" * 100)
    vid = _register_volume(env, "vol-tiny", safety=10 ** 15)
    r = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "preview", "asset_ids": [a["asset_id"]],
        "target_volume_id": vid})
    assert r.status_code == 200
    plan = r.json()["data"]
    assert plan["feasible"] is False
    assert "STORAGE_CAPACITY_LOW" in plan["reason"]
    rx = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "execute", "asset_ids": [a["asset_id"]],
        "target_volume_id": vid})
    assert rx.status_code == 507
    assert rx.json()["error"]["code"] == "STORAGE_CAPACITY_LOW"
    # 哨兵缺失：VOLUME_OFFLINE 拒绝（注册会自动写哨兵，注册后删除模拟卸载顶替）
    vid2 = _register_volume(env, "vol-nosent")
    sentinel = os.path.join(env["tmp"], "root-vol-nosent", f".blyb-vol-{vid2}")
    os.remove(sentinel)
    rp = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "preview", "asset_ids": [a["asset_id"]],
        "target_volume_id": vid2})
    assert rp.status_code == 200
    assert rp.json()["data"]["feasible"] is False
    assert "sentinel" in rp.json()["data"]["reason"]
    rq = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "execute", "asset_ids": [a["asset_id"]],
        "target_volume_id": vid2})
    assert rq.status_code == 503
    assert rq.json()["error"]["code"] == "VOLUME_OFFLINE"


def test_T5_retrylater_operation_recoverable(env, monkeypatch):
    payload = os.urandom(2048)
    a = _make_asset(env, "r1", payload)
    from apps.storage_service import service as svc
    orig = svc.migrate_asset
    calls = {"n": 0}

    def flaky(db, job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _RetryLater("injected transient failure")
        return orig(db, job)

    monkeypatch.setattr(svc, "migrate_asset", flaky)
    r = env["storage"].post("/api/v1/storage/migrations", headers=ADMIN, json={
        "mode": "execute", "asset_ids": [a["asset_id"]],
        "target_volume_id": "vol-b"})
    assert r.status_code == 202
    op_id = r.json()["data"]["operation_id"]
    env["worker"].tick(max_jobs=10, max_steps=8, retry_wait_seconds=0)
    # 可重试异常：Operation 保持运行态（未FAILED），Job 转 RETRY_WAIT
    op = env["storage"].get(f"/api/v1/operations/{op_id}",
                            headers=ADMIN).json()["data"]
    assert op["state"] in ("RUNNING", "PENDING"), op
    assert calls["n"] == 1
    from core_db.models import Job
    from core_db.engine import utcnow
    with env["sf"]() as db:
        job = (db.query(Job)
               .filter_by(tenant_id=env["tenant_id"],
                          job_type="storage.migrate_asset").first())
        assert job.state == "RETRY_WAIT"
        assert "injected transient failure" in str(job.last_error)
        # 放行重试（available_at 提前到当前时刻）
        job.available_at = utcnow()
        db.commit()
    monkeypatch.setattr(svc, "migrate_asset", orig)
    _drive(env, 6)
    op2 = env["storage"].get(f"/api/v1/operations/{op_id}",
                             headers=ADMIN).json()["data"]
    assert op2["state"] == "SUCCEEDED", op2
    from core_db.models import AssetLocation
    with env["sf"]() as db:
        assert db.query(AssetLocation).filter_by(
            blob_id=a["blob_id"], state="AVAILABLE").count() == 2


def test_T5_cross_tenant_404(env):
    a = _make_asset(env, "ct", b"D" * 10)
    with env["sf"]() as db:
        from core_db.models import AuthSubject, Tenant
        t = Tenant(id=new_uuid(), code="other-hospital", name="二院")
        db.add(t)
        db.flush()
        db.add(AuthSubject(id=new_uuid(), tenant_id=t.id, kind="user",
                           subject_code="admin",
                           token_hash=hash_token("other-admin-t5"),
                           roles=["platform_admin"]))
        db.commit()
    other = {"Authorization": "Bearer other-admin-t5"}
    r = env["storage"].post("/api/v1/storage/migrations", headers=other, json={
        "mode": "preview", "asset_ids": [a["asset_id"]],
        "target_volume_id": "vol-b"})
    assert r.status_code == 404  # 跨租户资产不泄露存在性


def test_T5_reservation_ledger_generalized_on_legacy_db(tmp_path):
    """迁移008：旧库（storage_reservation 带上传会话FK，连接PRAGMA FK=ON会
    执行该约束）经表重建去除FK；既有账本数据保留；迁移派生键 thereafter 可
    插入（T5 复用预留CAS账本的前提）。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path / 't5legacy.sqlite3'}"
    from core_db import make_engine, run_migrations
    from core_db.engine import utcnow
    import plugin_registry
    engine = make_engine()
    try:
        run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
        from sqlalchemy import text
        # 造旧形态：带上传会话FK的账本表 + 一行真实数据
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE storage_reservation"))
            conn.execute(text("""
                CREATE TABLE storage_reservation (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    tenant_id VARCHAR(36) REFERENCES tenant (id),
                    volume_id VARCHAR(36) REFERENCES storage_volume (id),
                    session_id VARCHAR(36) NOT NULL REFERENCES upload_session (id),
                    bytes_amount BIGINT NOT NULL,
                    state VARCHAR(16),
                    reason VARCHAR(16),
                    created_at TIMESTAMP,
                    released_at TIMESTAMP,
                    CONSTRAINT uq_reservation_session UNIQUE (session_id)
                )"""))
            conn.execute(text("DELETE FROM schema_migrations WHERE step = 8"))
        from core_db.engine import make_session_factory
        from core_db.models import (Acquisition, SourceSystem, StorageReservation,
                                    StorageVolume, Tenant, UploadSession)
        sf = make_session_factory(engine)
        from apps.seed import seed
        seed(engine, sf, with_volume=False)
        legacy_key = "legacy-session-0001"
        with sf() as db:
            t = db.query(Tenant).filter_by(code="first-hospital").first()
            db.add(StorageVolume(id="vol-leg", tenant_id=t.id, backend="filesystem",
                                 name="volleg", root_ref=str(tmp_path),
                                 safety_margin_bytes=0, state="ACTIVE", health="OK"))
            src = db.query(SourceSystem).filter_by(
                tenant_id=t.id, code="scanner-simulator").first()
            acq = Acquisition(id=new_uuid(), tenant_id=t.id,
                              source_system_id=src.id,
                              external_event_id="ev-t5-leg", source_revision="1",
                              manifest={}, manifest_digest="d",
                              state="FILES_COMPLETE")
            db.add(acq)
            db.flush()
            from core_db.models import Asset
            db.add(Asset(id="asset-x", tenant_id=t.id,
                         acquisition_id=acq.id, expected_size=10,
                         expected_sha256="ff" * 32, state="AVAILABLE"))
            db.flush()
            db.add(UploadSession(
                id=legacy_key, tenant_id=t.id, acquisition_id=acq.id,
                asset_id="asset-x", volume_id="vol-leg", expected_size=10,
                expected_sha256="ff" * 32, chunk_size=10, chunk_count=1,
                expires_at=utcnow(), idempotency_key="idem-leg"))
            db.flush()
            db.add(StorageReservation(
                id=new_uuid(), tenant_id=t.id, volume_id="vol-leg",
                session_id=legacy_key, bytes_amount=20, state="RELEASED"))
            db.commit()
        results = run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
        assert any(name.startswith("008") and res == "APPLIED"
                   for _, name, res in results)
        # FK 已去除、数据保留
        with engine.connect() as conn:
            ddl = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE name="
                "'storage_reservation'")).scalar()
            assert "REFERENCES upload_session" not in ddl
            n = conn.execute(text(
                "SELECT COUNT(*) FROM storage_reservation"
                " WHERE session_id = :k"), {"k": legacy_key}).scalar()
            assert n == 1
        # 迁移派生键（非上传会话）可插入账本——T5 复用前提
        from apps.storage_service.service import migration_reservation_key
        key = migration_reservation_key("asset-mig", "vol-leg")
        with sf() as db:
            db.add(StorageReservation(
                id=new_uuid(), tenant_id=db.query(Tenant).filter_by(
                    code="first-hospital").first().id,
                volume_id="vol-leg", session_id=key,
                bytes_amount=100, state="ACTIVE"))
            db.commit()
    finally:
        engine.dispose()
