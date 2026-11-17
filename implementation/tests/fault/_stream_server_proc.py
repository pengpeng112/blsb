"""W08 真实流测试：独立进程启动 storage service（真实 uvicorn + 真实 socket）。

用法：python tests/fault/_stream_server_proc.py <tmp_dir> <file_mb>
stdout 逐行输出：DB_URL= / VOL_ROOT= / ASSET_ID= / SHA256= / TOTAL= / PORT=
"""
import hashlib
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401

print("BOOT=1", flush=True)
tmp = sys.argv[1]
file_mb = int(sys.argv[2]) if len(sys.argv) > 2 else 24
os.makedirs(tmp, exist_ok=True)
db_path = os.path.join(tmp, "core.sqlite3")
url = f"sqlite:///{db_path}"
os.environ["BLYB_DATABASE_URL"] = url
os.environ["BLYB_EXCHANGE_DB"] = os.path.join(tmp, "exchange.sqlite3")

from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
import plugin_registry  # noqa: E402
from apps.seed import seed  # noqa: E402
from core_db.models import StorageVolume, Tenant  # noqa: E402

engine = make_engine(url)
print("ENGINE=1", flush=True)
run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
print("MIGRATED=1", flush=True)
sf = make_session_factory(engine)
seed(engine, sf, with_volume=False)
print("SEEDED=1", flush=True)
vol_root = os.path.join(tmp, "vol")
os.makedirs(vol_root, exist_ok=True)
from storage_backend import VolumeHandle  # noqa: E402

with sf() as db:
    t = db.query(Tenant).filter_by(code="first-hospital").first()
    if db.query(StorageVolume).filter_by(id="vol-w8").first() is None:
        db.add(StorageVolume(id="vol-w8", tenant_id=t.id, backend="filesystem",
                             name="vol1", root_ref=vol_root,
                             safety_margin_bytes=256 * 1024 * 1024,
                             state="ACTIVE", health="OK"))
        db.commit()
    tenant_id = t.id
VolumeHandle("vol-w8", vol_root, 0).ensure_layout()
VolumeHandle("vol-w8", vol_root, 0).ensure_sentinel()

# 构造大文件资产：确定性伪随机内容（4MB×N 分片）
from apps.storage_service import service  # noqa: E402
from core_db.models import Asset  # noqa: E402

chunk_size = 4 * 1024 * 1024
n_chunks = file_mb // 4
blocks = []
h = hashlib.sha256()
for i in range(n_chunks):
    block = bytes(((j * 131 + i * 7) % 251) for j in range(chunk_size))
    blocks.append(block)
    h.update(block)
total = chunk_size * n_chunks
sha = h.hexdigest()
manifest = {
    "schema_version": "1.0", "event_id": "ev-w08-stream", "agent_id": None,
    "source_system_code": "scanner-simulator", "root_id": "t",
    "captured_at": "2026-10-01T08:00:00+08:00", "source_scan_id": "scan-w08",
    "source_revision": "1", "identity_hints": {"barcode_raw": "900001"},
    "completion_evidence": {"method": "vendor_manifest",
                            "confirmed_at": "2026-10-01T08:00:00+08:00",
                            "evidence_ref": "t", "source_immutable_assertion": True},
    "files": [{"client_asset_id": "c1", "relative_path": "w08/f.wsi", "role": "primary",
               "size_bytes": total, "sha256": sha}]}
print("VOLSET=1", flush=True)
with sf() as db:
    acq, _ = service.register_acquisition(db, tenant_id, None, manifest)
    print("ACQ=1", flush=True)
    db.commit()
    asset = db.query(Asset).filter_by(acquisition_id=acq.id).first()
    s, _ = service.create_upload_session(db, tenant_id, None, acq.id, asset.id,
                                         total, sha, chunk_size, "idem-w08")
    db.commit()
    vol_row = db.get(StorageVolume, s.volume_id)
    vol = VolumeHandle(vol_row.id, vol_root, 0)
    for i in range(s.chunk_count):
        tmpf = vol.chunk_path(s.id, i) + ".tmp"
        os.makedirs(os.path.dirname(tmpf), exist_ok=True)
        with open(tmpf, "wb") as f:
            f.write(blocks[i])
        service.commit_chunk(db, s, i, hashlib.sha256(blocks[i]).hexdigest(),
                             len(blocks[i]), tmpf, vol)
        db.commit()
    op = service.complete_session(db, s)
    db.commit()
    result = service.verify_session(db, s)
    assert result["asset_id"] == asset.id
    asset_id = asset.id

s2 = socket.socket()
s2.bind(("127.0.0.1", 0))
port = s2.getsockname()[1]
s2.close()

import uvicorn  # noqa: E402
from apps.storage_service.app import create_app  # noqa: E402

app = create_app(session_factory=sf, engine=engine, embedded_worker=False)
print(f"DB_URL={url}", flush=True)
print(f"VOL_ROOT={vol_root}", flush=True)
print(f"ASSET_ID={asset_id}", flush=True)
print(f"SHA256={sha}", flush=True)
print(f"TOTAL={total}", flush=True)
print(f"PORT={port}", flush=True)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="error", timeout_keep_alive=65)
