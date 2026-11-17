"""性能测试用：独立进程启动 storage service（便于测量服务端RSS）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401

import socket
import tempfile

tmp = tempfile.mkdtemp(prefix="blyb-perf-")
os.environ.setdefault("BLYB_DATABASE_URL", f"sqlite:///{tmp}/p.sqlite3")

from core_db import make_engine, make_session_factory, run_migrations
import plugin_registry
from apps.seed import seed
from core_db.models import StorageVolume, Tenant

engine = make_engine()
run_migrations(engine, extra_migrations=plugin_registry.MIGRATIONS)
sf = make_session_factory(engine)
seed(engine, sf, with_volume=False)
vol = os.path.join(tmp, "vol")
os.makedirs(vol, exist_ok=True)
with sf() as db:
    t = db.query(Tenant).filter_by(code="first-hospital").first()
    db.add(StorageVolume(id="vol-p", tenant_id=t.id, backend="filesystem", name="vol1",
                         root_ref=vol, safety_margin_bytes=256 * 1024 * 1024,
                         state="ACTIVE", health="OK"))
    db.commit()

# RT-01 修复：手工登记的卷此前从未写挂载哨兵/目录布局，W07 写路径 mount_ok()
# 校验（service.py VOLUME_OFFLINE）必然拒绝——对齐正常注册路径
# （storage_service/app.py register：ensure_layout + ensure_sentinel）。
from packages.storage_backend import VolumeHandle as _VH
_vh = _VH("vol-p", vol, 256 * 1024 * 1024)
_vh.ensure_layout()
_vh.ensure_sentinel()

from core_db.models import Agent
with sf() as db:
    agent_id = db.query(Agent).filter_by(device_code="SCAN-WS-01").first().id

s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()

import uvicorn
from apps.storage_service.app import create_app

app = create_app(session_factory=sf, engine=engine, embedded_worker=True)
print(f"PERF_SERVER_PORT={port}", flush=True)
print(f"PERF_AGENT_ID={agent_id}", flush=True)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="error")
