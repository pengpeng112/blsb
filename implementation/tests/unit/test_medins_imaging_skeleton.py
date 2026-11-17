"""济南医保影像云索引 骨架测试（脚手架生成）：manifest 可发现/钩子 data_source=none/
真实发送被闸门拒绝。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401


def test_manifest_discovered():
    import app_runtime
    manifests = app_runtime.PLUGIN_MANIFESTS
    assert "medins_imaging" in manifests
    assert manifests["medins_imaging"]["module"] == "domains.medins_imaging.plugin"


def test_summary_data_source_none():
    from contracts import new_uuid
    from core_db import make_engine, make_session_factory, run_migrations
    import app_runtime
    reg = app_runtime.load_registry(["medins_imaging"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from core_db.models import Tenant
    with sf() as db:
        t = Tenant(id=new_uuid(), code=f"sk-mi-{new_uuid()[:8]}", name="骨架")
        db.add(t); db.flush(); tid = t.id
    with sf() as db:
        s = reg.target_summary(db, tid, "medins_imaging_jn", 7)
        assert s is not None and s.data_source == "none"
        assert s.buckets.get("CONFIRMED", 0) == 0   # 不伪造数据
    engine.dispose()


def test_real_send_blocked_by_gate():
    """lifecycle=PLANNED → assert_target_can_send_real 拒绝（骨架无真实发送）。"""
    from plugins.report_targets import assert_target_can_send_real, seed_default_targets
    from contracts import ApiError, new_uuid
    from core_db import make_engine, make_session_factory, run_migrations
    import app_runtime
    reg = app_runtime.load_registry([])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    from core_db.models import Tenant
    with sf() as db:
        t = Tenant(id=new_uuid(), code=f"sk-gate-mi-{new_uuid()[:8]}", name="骨架闸门")
        db.add(t); db.flush(); tid = t.id
        seed_default_targets(db, tid)
        db.commit()
    with sf() as db:
        with pytest.raises(ApiError):
            assert_target_can_send_real(db, tid, "medins_imaging_jn")
    engine.dispose()
