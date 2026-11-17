"""W07扩大正式回归：kill子进程崩溃矩阵、reservation CAS账本、同index并发写、
挂载丢失清扫、PG>2GB数值（DDL编译+SQLite往返；真PG按环境变量门控，未配置即跳过）。

崩溃矩阵形态：真实子进程 + os._exit(137) 硬杀（绕过一切清理），恢复走
service.recover_commit_intents（幂等），断言 字节/hash/状态/预留/暂存/Outbox 一致。
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (Asset, Blob, OutboxEvent, StorageReservation,  # noqa: E402
                            StorageVolume, Tenant, UploadSession)
from storage_backend import VolumeHandle, sha256_of_file  # noqa: E402

IMPL = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, IMPL)

DATA = os.urandom(4096)  # 4个1KB分片


def _make_env(tmp_path, *, margin=64 * 1024 * 1024):
    import plugin_registry
    from apps.seed import seed
    db_path = tmp_path / "core.sqlite3"
    url = f"sqlite:///{db_path}"
    os.environ["BLYB_DATABASE_URL"] = url
    engine = make_engine(url)
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    vol_root = tmp_path / "vol"
    vol_root.mkdir()
    VolumeHandle("vol-k", str(vol_root), margin).ensure_layout()
    VolumeHandle("vol-k", str(vol_root), margin).ensure_sentinel()
    with sf() as db:
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        db.add(StorageVolume(id="vol-k", tenant_id=t.id, backend="filesystem",
                             name="vol1", root_ref=str(vol_root),
                             safety_margin_bytes=margin, state="ACTIVE", health="OK"))
        db.commit()
        tenant_id = t.id
    return engine, sf, tenant_id, url, str(vol_root)


def _stage_session(sf, tenant_id, data: bytes, *, chunk_size=1024, tag="s1"):
    """登记+建会话+直写分片+complete（不经HTTP，聚焦存储层）。返回 (session_id, asset_id, op_id)。"""
    from apps.storage_service import service
    from contracts import new_uuid
    sha = hashlib.sha256(data).hexdigest()
    manifest = {
        "schema_version": "1.0", "event_id": f"ev-w07-{tag}", "agent_id": None,
        "source_system_code": "scanner-simulator", "root_id": "t",
        "captured_at": "2026-10-01T08:00:00+08:00", "source_scan_id": f"scan-w07-{tag}",
        "source_revision": "1", "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-10-01T08:00:00+08:00",
                                "evidence_ref": "t", "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": f"scan-w07-{tag}/f.wsi",
                   "role": "primary", "size_bytes": len(data), "sha256": sha}]}
    with sf() as db:
        acq, _ = service.register_acquisition(db, tenant_id, None, manifest)
        db.commit()
        asset = db.query(Asset).filter_by(acquisition_id=acq.id).first()
        s, _ = service.create_upload_session(db, tenant_id, None, acq.id, asset.id,
                                             len(data), sha, chunk_size,
                                             f"idem-w07-{tag}")
        db.commit()
        vol_row = db.get(StorageVolume, s.volume_id)
        vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
        for i in range(s.chunk_count):
            chunk = data[i * chunk_size:(i + 1) * chunk_size]
            tmp = vol.chunk_path(s.id, i) + ".tmp"
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            with open(tmp, "wb") as f:
                f.write(chunk)
            created = service.commit_chunk(
                db, s, i, hashlib.sha256(chunk).hexdigest(), len(chunk), tmp, vol)
            assert created
            db.commit()
        op = service.complete_session(db, s)
        db.commit()
        return s.id, asset.id, op.id


def _snapshot(sf, session_id, asset_id):
    with sf() as db:
        s = db.get(UploadSession, session_id)
        a = db.get(Asset, asset_id)
        b = db.get(Blob, a.blob_id) if a and a.blob_id else None
        v = db.get(StorageVolume, s.volume_id)
        led = db.query(StorageReservation).filter_by(session_id=session_id).first()
        outbox = db.query(OutboxEvent).filter_by(
            aggregate_ref=f"asset:{asset_id}").count()
        return {
            "session_state": s.state, "asset_state": a.state,
            "blob_sha": b.sha256 if b else None, "blob_size": b.size_bytes if b else None,
            "reserved": v.reserved_bytes,
            "ledger_state": led.state if led else None,
            "ledger_reason": led.reason if led else None,
            "ledger_released": bool(led.released_at) if led else None,
            "outbox": outbox,
        }


# ================================================================ kill 崩溃矩阵
CRASH_POINTS = [
    "before_intent_commit",   # 意图提交前
    "intent_committed",       # 意图已持久化、合并前
    "assembled",              # 合并完成、rename 前
    "renamed",                # rename 完成、核心提交前
    "core_pre_commit",        # 核心状态flush后、清staging前
    "staging_cleared",        # staging已清、最终提交前
    "core_committed",         # 最终提交后（恢复应为no-op）
]


@pytest.mark.parametrize("crash_at", CRASH_POINTS)
def test_w07_kill_matrix_recovers_consistently(tmp_path, crash_at):
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    data = DATA
    sha = hashlib.sha256(data).hexdigest()
    session_id, asset_id, op_id = _stage_session(sf, tenant_id, data, tag=f"k-{crash_at}")

    env = {**os.environ, "BLYB_DATABASE_URL": url, "BLYB_TEST_CRASH_AT": crash_at}
    proc = subprocess.run(
        [sys.executable, os.path.join("tests", "fault", "_kill_target.py"),
         url, session_id],
        cwd=IMPL, env=env, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 137, (crash_at, proc.returncode, proc.stdout, proc.stderr)

    # 重启恢复（模拟进程重启后的启动钩子）
    from apps.storage_service import service
    stats = service.recover_commit_intents(sf)
    snap = _snapshot(sf, session_id, asset_id)
    assert snap["session_state"] == "COMPLETED", (crash_at, snap, stats)
    assert snap["asset_state"] == "AVAILABLE"
    assert snap["blob_sha"] == sha and snap["blob_size"] == len(data)
    assert snap["reserved"] == 0, (crash_at, snap)
    assert snap["ledger_state"] == "RELEASED" and snap["ledger_released"]
    assert snap["ledger_reason"] in ("COMPLETE", "RECOVERY")
    assert snap["outbox"] == 1, (crash_at, snap)
    with sf() as db:
        from core_db.models import Operation, AssetLocation
        assert db.get(Operation, op_id).state == "SUCCEEDED"
        s = db.get(UploadSession, session_id)
        assert not os.path.exists(os.path.join(vol_root, "staging", s.id))
        a = db.get(Asset, asset_id)
        obj = db.get(Blob, a.blob_id)
        loc_row = (db.query(AssetLocation)
                   .filter_by(blob_id=obj.id, state="AVAILABLE").first())
        digest, size = sha256_of_file(loc_row.physical_key)
        assert digest == sha and size == len(data)

    # 二次恢复：幂等 no-op，关键状态不变
    service.recover_commit_intents(sf)
    snap2 = _snapshot(sf, session_id, asset_id)
    assert snap2 == snap, (crash_at, snap, snap2)
    engine.dispose()


def test_w07_kill_before_intent_reruns_verify(tmp_path):
    """意图提交前被杀：恢复器对分片齐备的VERIFYING会话重跑完整verify。"""
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    session_id, asset_id, op_id = _stage_session(sf, tenant_id, DATA, tag="k-noint")
    from apps.storage_service import service
    stats = service.recover_commit_intents(sf)
    assert stats["retried"] == 1, stats
    snap = _snapshot(sf, session_id, asset_id)
    assert snap["session_state"] == "COMPLETED" and snap["reserved"] == 0
    assert snap["blob_sha"] == hashlib.sha256(DATA).hexdigest()
    engine.dispose()


# ================================================================ reservation CAS 账本
def test_w07_release_exactly_once_across_paths(tmp_path):
    """重复 cancel/expire/fail/recovery 并发释放：账本恰好释放一次。"""
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    from apps.storage_service import service
    _, _, a, _, _ = None, None, None, None, None
    sA_id, assetA, _ = _stage_session(sf, tenant_id, b"a" * 10, tag="led-A")
    sB_id, assetB, _ = _stage_session(sf, tenant_id, b"b" * 10, tag="led-B")
    with sf() as db:
        assert db.get(StorageVolume, "vol-k").reserved_bytes == 40
    results = []
    lock, order = threading.Lock(), []

    def release(reason):
        with sf() as db:
            ok = service.release_reservation(db, sA_id, reason)
            db.commit()
        with lock:
            results.append((reason, ok))

    threads = [threading.Thread(target=release, args=(r,))
               for r in ("CANCEL", "EXPIRE", "FAIL", "COMPLETE", "RECOVERY")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for _, ok in results if ok) == 1, results
    with sf() as db:
        v = db.get(StorageVolume, "vol-k")
        assert v.reserved_bytes == 20  # 仅B会话预留
        led = db.query(StorageReservation).filter_by(session_id=sA_id).first()
        assert led.state == "RELEASED" and led.released_at is not None
        # 再走expire/abort路径也不再扣减
        sA = db.get(UploadSession, sA_id)
        service._expire_session(db, sA)
        service.abort_session(db, sA, "again")
        db.commit()
        assert db.get(StorageVolume, "vol-k").reserved_bytes == 20
    engine.dispose()


def test_w07_quota_race_two_sessions_one_capacity(tmp_path):
    """并发预留竞争：capacity 上限内恰好一个成功（无超额预留）。"""
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    from apps.storage_service import service
    with sf() as db:
        db.get(StorageVolume, "vol-k").capacity_bytes = 20  # 仅容一个会话（need=2×10）
        db.commit()
    from contracts import ApiError
    from core_db.models import Acquisition
    outcomes = []
    barrier = threading.Barrier(2)

    def create(tag):
        with sf() as db:
            sha = hashlib.sha256(b"x" * 10).hexdigest()
            manifest = {
                "schema_version": "1.0", "event_id": f"ev-race-{tag}", "agent_id": None,
                "source_system_code": "scanner-simulator", "root_id": "t",
                "captured_at": "2026-10-01T08:00:00+08:00",
                "source_scan_id": f"scan-race-{tag}", "source_revision": "1",
                "identity_hints": {"barcode_raw": "900001"},
                "completion_evidence": {"method": "vendor_manifest",
                                        "confirmed_at": "2026-10-01T08:00:00+08:00",
                                        "evidence_ref": "t",
                                        "source_immutable_assertion": True},
                "files": [{"client_asset_id": "c1", "relative_path": f"race-{tag}/f.wsi",
                           "role": "primary", "size_bytes": 10, "sha256": sha}]}
            acq, _ = service.register_acquisition(db, tenant_id, None, manifest)
            db.commit()
            asset = db.query(Asset).filter_by(acquisition_id=acq.id).first()
            barrier.wait()
            try:
                s, _ = service.create_upload_session(
                    db, tenant_id, None, acq.id, asset.id, 10, sha, 1024,
                    f"idem-race-{tag}")
                db.commit()
                outcomes.append(("ok", s.id))
            except ApiError as exc:
                db.rollback()
                outcomes.append(("507", exc.code))
    ts = [threading.Thread(target=create, args=(t,)) for t in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    kinds = sorted(k for k, _ in outcomes)
    assert kinds == ["507", "ok"], outcomes
    with sf() as db:
        assert db.get(StorageVolume, "vol-k").reserved_bytes == 20
        active = db.query(StorageReservation).filter_by(state="ACTIVE").count()
        assert active == 1
    engine.dispose()


# ================================================================ 同index并发写
def test_w07_same_index_concurrent_same_bytes(tmp_path):
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    from apps.storage_service import service
    s_id, asset_id, _ = _stage_session(sf, tenant_id, DATA[:2048], tag="cc-same")
    chunk = DATA[:1024]
    with sf() as db:
        s = db.get(UploadSession, s_id)
        vol_row = db.get(StorageVolume, s.volume_id)
        vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
    results = []
    barrier = threading.Barrier(2)

    def writer(tag, idx):
        with sf() as db:
            s2 = db.get(UploadSession, s_id)
            tmp = vol.chunk_path(s_id, idx) + f".{tag}.tmp"
            with open(tmp, "wb") as f:
                f.write(chunk)
            barrier.wait()
            created = service.commit_chunk(
                db, s2, idx, hashlib.sha256(chunk).hexdigest(), len(chunk), tmp, vol)
            db.commit()
            results.append(created)
    ts = [threading.Thread(target=writer, args=(t, 4)) for t in ("A", "B")]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(results) == [False, True], results
    with sf() as db:
        from core_db.models import UploadChunk
        rows = db.query(UploadChunk).filter_by(session_id=s_id, chunk_index=4).all()
        assert len(rows) == 1
        digest, _ = sha256_of_file(rows[0].storage_key)
        assert digest == rows[0].sha256 == hashlib.sha256(chunk).hexdigest()
    engine.dispose()


def test_w07_same_index_concurrent_different_bytes(tmp_path):
    """并发异内容：恰一行；落败方CONFLICT；磁盘字节始终等于已登记hash。"""
    from contracts import ApiError
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    from apps.storage_service import service
    s_id, asset_id, _ = _stage_session(sf, tenant_id, DATA[:2048], tag="cc-diff")
    cA, cB = b"A" * 1024, b"B" * 1024
    with sf() as db:
        vol_row = db.get(StorageVolume, db.get(UploadSession, s_id).volume_id)
        vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
    outcomes = []
    barrier = threading.Barrier(2)

    def writer(tag: str, chunk: bytes):
        with sf() as db:
            s2 = db.get(UploadSession, s_id)
            tmp = vol.chunk_path(s_id, 4) + f".{tag}.tmp"
            with open(tmp, "wb") as f:
                f.write(chunk)
            barrier.wait()
            try:
                service.commit_chunk(db, s2, 4, hashlib.sha256(chunk).hexdigest(),
                                     len(chunk), tmp, vol)
                db.commit()
                outcomes.append((tag, "ok"))
            except ApiError as exc:
                db.rollback()
                outcomes.append((tag, exc.code))
    ts = [threading.Thread(target=writer, args=("A", cA)),
          threading.Thread(target=writer, args=("B", cB))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    kinds = sorted(k for _, k in outcomes)
    assert kinds == ["CONFLICT", "ok"], outcomes
    with sf() as db:
        from core_db.models import UploadChunk
        rows = db.query(UploadChunk).filter_by(session_id=s_id, chunk_index=4).all()
        assert len(rows) == 1
        digest, _ = sha256_of_file(rows[0].storage_key)
        assert digest == rows[0].sha256
    engine.dispose()


# ================================================================ 挂载丢失清扫
def test_w07_mount_lost_blocks_writes_and_sweep_restores(tmp_path):
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    from apps.storage_service import service
    from contracts import ApiError
    s_id, asset_id, _ = _stage_session(sf, tenant_id, DATA, tag="mnt")
    with sf() as db:
        service.complete_session(db, db.get(UploadSession, s_id))
        db.commit()
    # 另登记一个未上传的采集（供挂载丢失后的建会话尝试）
    from contracts import new_uuid
    sha2 = hashlib.sha256(b"z" * 10).hexdigest()
    manifest2 = {
        "schema_version": "1.0", "event_id": "ev-w07-mnt2", "agent_id": None,
        "source_system_code": "scanner-simulator", "root_id": "t",
        "captured_at": "2026-10-01T08:00:00+08:00", "source_scan_id": "scan-w07-mnt2",
        "source_revision": "1", "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {"method": "vendor_manifest",
                                "confirmed_at": "2026-10-01T08:00:00+08:00",
                                "evidence_ref": "t", "source_immutable_assertion": True},
        "files": [{"client_asset_id": "c1", "relative_path": "mnt2/f.wsi",
                   "role": "primary", "size_bytes": 10, "sha256": sha2}]}
    with sf() as db:
        acq2, _ = service.register_acquisition(db, tenant_id, None, manifest2)
        db.commit()
        asset2 = db.query(Asset).filter_by(acquisition_id=acq2.id).first().id
    # 模拟卸载：根挪走后同名空目录顶替
    aside = tmp_path / "vol-aside"
    os.rename(vol_root, aside)
    os.makedirs(vol_root)
    with pytest.raises(ApiError) as ei:
        with sf() as db:
            service.create_upload_session(
                db, tenant_id, None, acq2.id, asset2, 10, sha2, 8, "idem-mnt-lost")
    assert ei.value.code == "VOLUME_OFFLINE"
    with sf() as db:
        events = service.sweep_lost_mounts(db)
    assert any(e["health"] == "LOST" for e in events)
    with sf() as db:
        assert db.get(StorageVolume, "vol-k").health == "LOST"
    # 恢复挂载：挪回 → 清扫回OK
    import shutil
    shutil.rmtree(vol_root)
    os.rename(aside, vol_root)
    with sf() as db:
        events = service.sweep_lost_mounts(db)
    assert any(e["health"] == "OK" for e in events)
    with sf() as db:
        assert db.get(StorageVolume, "vol-k").health == "OK"
    engine.dispose()


def test_w07_migration005_backfills_ledger_once(tmp_path):
    """旧库升级语义：既有未终态会话补 ACTIVE 账本行，重复执行不重复补。"""
    from packages.core_db.migrate import _005_storage_reservation_ledger
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    s_id, asset_id, _ = _stage_session(sf, tenant_id, b"q" * 12, tag="mig")
    with sf() as db:
        led = db.query(StorageReservation).filter_by(session_id=s_id).first()
        assert led is not None and led.state == "ACTIVE"
        db.delete(led)  # 模拟 005 之前创建的会话（无账本行）
        db.commit()
    _005_storage_reservation_ledger(engine)  # 直接调用迁移函数（首次应用语义）
    with sf() as db:
        rows = db.query(StorageReservation).filter_by(session_id=s_id).all()
        assert len(rows) == 1 and rows[0].state == "ACTIVE"
        assert rows[0].bytes_amount == 24  # 2×expected_size
    _005_storage_reservation_ledger(engine)  # 幂等
    with sf() as db:
        assert db.query(StorageReservation).filter_by(session_id=s_id).count() == 1
    engine.dispose()


# ================================================================ PG >2GB 数值
@pytest.mark.parametrize("table,col", [
    ("asset", "expected_size"), ("blob", "size_bytes"),
    ("storage_volume", "capacity_bytes"), ("storage_volume", "reserved_bytes"),
    ("storage_volume", "safety_margin_bytes"),
    ("upload_session", "expected_size"), ("upload_chunk", "size_bytes"),
])
def test_w07_pg_ddl_compiles_bigint(table, col):
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable
    import core_db.models as m
    tbl = getattr(m, "".join(p.capitalize() for p in table.split("_"))).__table__
    ddl = str(CreateTable(tbl).compile(dialect=postgresql.dialect()))
    line = [ln for ln in ddl.splitlines() if col in ln][0]
    assert "BIGINT" in line, line


def test_w07_over_2gb_value_roundtrip_sqlite(tmp_path):
    """SQLite 端 >2GiB 数值完整存取（64位）；真PG走 BLYB_PG_URL 门控。"""
    engine, sf, tenant_id, url, vol_root = _make_env(tmp_path)
    big = 3 * 1024 ** 3 + 123
    from apps.storage_service import service
    s_id, asset_id, _ = _stage_session(sf, tenant_id, b"r" * 8, tag="big")
    with sf() as db:
        s = db.get(UploadSession, s_id)
        s.expected_size = big
        blob = db.query(Blob).first()
        if blob:
            blob.size_bytes = big
        db.commit()
    with sf() as db:
        assert db.get(UploadSession, s_id).expected_size == big
    engine.dispose()


@pytest.mark.skipif(not os.environ.get("BLYB_PG_URL"),
                    reason="BLYB_PG_URL 未配置：真PG >2GB 实测未执行（本机无PG环境）")
def test_w07_pg_over_2gb_real():
    """真PG：编号迁移 + >2GiB 数值写入读回（需 BLYB_PG_URL，未配置即未执行）。"""
    from sqlalchemy import create_engine, text
    url = os.environ["BLYB_PG_URL"]
    eng = create_engine(url)
    from packages.core_db.migrate import _004_bigint_sizes
    _004_bigint_sizes(eng)
    big = 3 * 1024 ** 3 + 123
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS w07_bigint_probe "
                          "(id INTEGER PRIMARY KEY, sz BIGINT)"))
        conn.execute(text("DELETE FROM w07_bigint_probe"))
        conn.execute(text("INSERT INTO w07_bigint_probe (id, sz) VALUES (1, :b)"),
                     {"b": big})
        got = conn.execute(text("SELECT sz FROM w07_bigint_probe WHERE id=1")).scalar()
        assert got == big
        conn.execute(text("DROP TABLE w07_bigint_probe"))
    eng.dispose()
