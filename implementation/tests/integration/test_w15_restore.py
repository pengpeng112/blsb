"""W15 正式回归：新根备份恢复演练（G29）。

- 备份：核心库（sqlite backup API 一致快照）+ 交换库 + 对象卷拷贝；
- 恢复到全新根目录：重映射 volume.root_ref 与全部 AssetLocation.physical_key
  （S13：仅改 storage_volume.root_ref 不够）；
- 恢复后旧库/旧卷改名不可访问，资产字节从新卷读取、digest一致；
- exchange generation/route 引用一致；孤儿对象只登记不删除。

50GB窗口与异地介质轮换不在本测试（如实：未执行）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402


def _sqlite_backup(src: str, dst: str) -> None:
    src_conn = sqlite3.connect(src)
    dst_conn = sqlite3.connect(dst)
    with dst_conn:
        src_conn.backup(dst_conn)
    src_conn.close()
    dst_conn.close()


@pytest.fixture()
def env(tmp_path):
    from core_db import make_engine, make_session_factory, run_migrations
    import plugin_registry
    from apps.seed import seed
    from core_db.models import StorageVolume, Tenant
    from storage_backend import VolumeHandle
    from apps.storage_service import service
    from contracts import new_uuid

    root = tmp_path / "oldroot"
    root.mkdir()
    db_path = root / "core.sqlite3"
    ex_path = root / "exchange.sqlite3"
    vol_root = root / "vol"
    vol_root.mkdir()
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["BLYB_EXCHANGE_DB"] = str(ex_path)
    engine = make_engine()
    run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
    sf = make_session_factory(engine)
    seed(engine, sf, with_volume=False)
    VolumeHandle("vol-r", str(vol_root), 32 * 1024 * 1024).ensure_sentinel()
    with sf() as db:
        t = db.query(Tenant).filter_by(code="first-hospital").first()
        db.add(StorageVolume(id="vol-r", tenant_id=t.id, backend="filesystem",
                             name="vol1", root_ref=str(vol_root),
                             safety_margin_bytes=32 * 1024 * 1024, state="ACTIVE",
                             health="OK"))
        db.commit()
        tenant_id = t.id

    # 上传两个资产（真实对象字节）
    assets = []
    from core_db.models import Asset
    for tag, payload in (("a", b"restore-A" * 512), ("b", b"restore-B" * 300)):
        sha = hashlib.sha256(payload).hexdigest()
        manifest = {
            "schema_version": "1.0", "event_id": f"ev-w15-{tag}", "agent_id": None,
            "source_system_code": "scanner-simulator", "root_id": "t",
            "captured_at": "2026-10-01T00:00:00+08:00",
            "source_scan_id": f"scan-w15-{tag}", "source_revision": "1",
            "identity_hints": {"barcode_raw": "900001"},
            "completion_evidence": {"method": "vendor_manifest",
                                    "confirmed_at": "2026-10-01T00:00:00+08:00",
                                    "evidence_ref": "t",
                                    "source_immutable_assertion": True},
            "files": [{"client_asset_id": "c", "relative_path": f"w15/{tag}.wsi",
                       "role": "primary", "size_bytes": len(payload),
                       "sha256": sha}]}
        acq, _ = service.register_acquisition(db, tenant_id, None, manifest)
        db.commit()
        asset = db.query(Asset).filter_by(acquisition_id=acq.id).first()
        s, _ = service.create_upload_session(db, tenant_id, None, acq.id, asset.id,
                                             len(payload), sha, len(payload),
                                             f"idem-w15-{tag}")
        db.commit()
        vol = VolumeHandle("vol-r", str(vol_root), 0)
        chunk = payload
        tmpf = vol.chunk_path(s.id, 0) + ".tmp"
        os.makedirs(os.path.dirname(tmpf), exist_ok=True)
        with open(tmpf, "wb") as f:
            f.write(chunk)
        service.commit_chunk(db, s, 0, hashlib.sha256(chunk).hexdigest(),
                             len(chunk), tmpf, vol)
        db.commit()
        service.complete_session(db, s)
        db.commit()
        result = service.verify_session(db, s)
        assets.append({"asset_id": asset.id, "sha": sha,
                       "blob_id": result["blob_id"]})
    # 交换库一批发布（供恢复后引用校验）
    from destinations.view_publication.exchange_db import ExchangeDb
    ex = ExchangeDb(str(ex_path))
    obj = str(vol_root / "objects" / assets[0]["sha"][:2] / assets[0]["blob_id"])
    b = ex.begin_batch("pub-w15", "gen-w15", "scope-w15", manifest_digest="d",
                       tenant_id=tenant_id)
    r1 = ex.add_asset_route(b, assets[0]["asset_id"], obj,
                            size_bytes=len(b"restore-A" * 512),
                            sha256=assets[0]["sha"],
                            media_type="application/octet-stream")
    ex.mark_asset_reachable(r1)
    ex.publish_batch(b)
    ex.close()
    yield {"root": str(root), "db": str(db_path), "ex": str(ex_path),
           "vol": str(vol_root), "sf": sf, "tenant_id": tenant_id,
           "assets": assets, "engine": engine, "route_id": r1}
    engine.dispose()


def test_w15_new_root_restore_remaps_and_reads_from_new_volume(env, tmp_path):
    from core_db.models import AssetLocation, StorageVolume, Blob, Asset
    from storage_backend import VolumeHandle, sha256_of_file

    # ---- 备份（一致快照）----
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    _sqlite_backup(env["db"], str(backup_dir / "core.sqlite3"))
    _sqlite_backup(env["ex"], str(backup_dir / "exchange.sqlite3"))
    shutil.copytree(env["vol"], str(backup_dir / "vol"),
                    ignore=shutil.ignore_patterns("staging", "assembling"))
    backup_core = str(backup_dir / "core.sqlite3")

    # ---- 恢复到新根 ----
    new_root = tmp_path / "newroot"
    new_root.mkdir()
    new_db = str(new_root / "core.sqlite3")
    shutil.copy2(backup_core, new_db)
    new_vol = str(new_root / "vol")
    shutil.copytree(str(backup_dir / "vol"), new_vol)
    VolumeHandle("vol-r", new_vol, 0).ensure_sentinel()  # 新根哨兵

    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{new_db}"
    os.environ["BLYB_EXCHANGE_DB"] = str(new_root / "exchange.sqlite3")
    shutil.copy2(str(backup_dir / "exchange.sqlite3"),
                 str(new_root / "exchange.sqlite3"))
    from core_db import make_engine, make_session_factory
    engine2 = make_engine()
    sf2 = make_session_factory(engine2)
    try:
        with sf2() as db:
            # 重映射：卷根 + 每条 AssetLocation.physical_key
            vol = db.get(StorageVolume, "vol-r")
            old_root = vol.root_ref
            vol.root_ref = new_vol
            remapped = 0
            for loc in db.query(AssetLocation).all():
                if loc.physical_key.startswith(old_root):
                    loc.physical_key = os.path.join(
                        new_vol, os.path.relpath(loc.physical_key, old_root))
                    remapped += 1
            db.commit()
            assert remapped >= 2, "至少两个资产位置需要重映射"

        # ---- 旧根不可访问（先释放旧库连接句柄）----
        env["engine"].dispose()
        quarantine = tmp_path / "quarantined-old-root"
        os.rename(env["root"], quarantine)

        # ---- 从新卷读取：字节/hash/状态一致 ----
        from apps.storage_service import service as svc
        for a in env["assets"]:
            with sf2() as db:
                got = svc.asset_content(db, env["tenant_id"], a["asset_id"])
                asset_obj, blob, path = got
                digest, size = sha256_of_file(path)
                assert digest == a["sha"], (digest, a["sha"])
                assert path.startswith(new_vol), path
                assert not path.startswith(str(quarantine))
                assert blob.state == "AVAILABLE"

        # ---- exchange 恢复一致性：generation/route 引用仍可解析 ----
        from destinations.view_publication.exchange_db import ExchangeDb
        ex2 = ExchangeDb(str(new_root / "exchange.sqlite3"))
        gen = ex2.current_generation("scope-w15", tenant_id=env["tenant_id"])
        assert gen is not None and gen["generation_id"] == "gen-w15"
        route = ex2.asset_route_by_id(env["route_id"])
        assert route is not None and route["batch_state"] == "PUBLISHED"
        ex2.close()

        # ---- 孤儿对象只登记不删除 ----
        orphan_path = os.path.join(new_vol, "objects", "zz", "orphan.bin")
        os.makedirs(os.path.dirname(orphan_path), exist_ok=True)
        with open(orphan_path, "wb") as f:
            f.write(b"orphan-not-in-db")
        # 恢复校验工具：登记孤儿（新卷对象未在DB）而不删除
        with sf2() as db:
            known = {os.path.relpath(l.physical_key, new_vol)
                     for l in db.query(AssetLocation).all()}
        orphans = []
        objects_dir = os.path.join(new_vol, "objects")
        for dirpath, _dirs, names in os.walk(objects_dir):
            for n in names:
                rel = os.path.relpath(os.path.join(dirpath, n), new_vol)
                if rel not in known:
                    orphans.append(rel)
        assert any("orphan.bin" in o for o in orphans)
        assert os.path.exists(orphan_path), "孤儿只登记，不得删除"
    finally:
        engine2.dispose()
