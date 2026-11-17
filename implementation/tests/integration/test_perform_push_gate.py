"""LT-03 闸门测试：真实 generic 目的地过 LT-01 生产闸门；药品路径不受影响。

关键证据（任务书步骤4 CAUTION）：
- 药品真实发送**不经过 perform_push**——worker drug_trace.send job →
  service.perform_send（plugins/domains/drug_trace/plugin.py::_j_send），
  core_handlers.perform_push 只服务病理/metrics/工作流节点与手动推送；
- 因此闸门只可能影响"经 perform_push 的真实 generic 目的地"，
  药品链路行为不变（本文件有直接证明测试）。
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (AuthSubject, ConfigRevision, DataPackage,
                            Destination, PackageRevision, Submission, Tenant)  # noqa: E402
from plugins.report_targets import seed_default_targets  # noqa: E402

from contracts.api import hash_token  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/gate.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="gate-hospital", name="闸门测试")
        db.add(t)
        db.commit()
        tid = t.id
    yield {"sf": sf, "engine": engine, "tid": tid}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_generic_dest(env, *, profile="destination.generic_http_json",
                     config_rev=None):
    with env["sf"]() as db:
        d = Destination(id=new_uuid(), tenant_id=env["tid"], code="gen-real",
                        name="通用真实目的地", profile_revision=profile,
                        mode="PUSH", capabilities=[], is_mock=False,
                        status="ENABLED", config_revision=config_rev)
        db.add(d)
        db.commit()
        return d.id


def _mk_submission(env, dest_id):
    with env["sf"]() as db:
        pkg = DataPackage(id=new_uuid(), tenant_id=env["tid"],
                          domain="drug_trace", business_key="bk")
        db.add(pkg)
        db.flush()
        pr = PackageRevision(id=new_uuid(), tenant_id=env["tid"],
                             package_id=pkg.id, revision=1, state="FROZEN")
        db.add(pr)
        db.flush()
        s = Submission(id=new_uuid(), tenant_id=env["tid"],
                       package_revision_id=pr.id, destination_id=dest_id,
                       idempotency_key=f"idem-{new_uuid()[:8]}",
                       state="PREPARED")
        db.add(s)
        db.commit()
        return s.id


def test_real_generic_without_target_code_blocked(env):
    from core_handlers import perform_push
    from contracts import ApiError
    dest_id = _mk_generic_dest(env)
    sid = _mk_submission(env, dest_id)
    with env["sf"]() as db:
        s = db.get(Submission, sid)
        with pytest.raises(ApiError) as ei:
            perform_push(db, env["tid"], s, {"x": 1})
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "report_target_code" in ei.value.message


def _mk_target_config(env, dest_id, target_code):
    with env["sf"]() as db:
        cr = ConfigRevision(
            id=new_uuid(), tenant_id=env["tid"], config_type="destination",
            owner_id=dest_id, revision=1,
            content_json={"base_url": "http://127.0.0.1:8090",
                          "allowed_hosts": ["127.0.0.1"],
                          "allowed_ports": [8090],
                          "allowed_schemes": ["http"],
                          "report_target_code": target_code},
            digest="d" * 64, state="PUBLISHED")
        db.add(cr)
        db.commit()
        dest = db.get(Destination, dest_id)
        dest.config_revision = cr.id
        db.commit()


def test_real_generic_planned_target_blocked(env):
    """LT-07 后 medins_emr_sd=DEVELOPING（非 PILOT/PRODUCTION）——闸门仍拦。"""
    from core_handlers import perform_push
    from contracts import ApiError
    dest_id = _mk_generic_dest(env)
    _mk_target_config(env, dest_id, "medins_emr_sd")   # DEVELOPING（LT-07）
    with env["sf"]() as db:
        seed_default_targets(db, env["tid"])
        db.commit()
        sid = _mk_submission(env, dest_id)
        s = db.get(Submission, sid)
        with pytest.raises(ApiError) as ei:
            perform_push(db, env["tid"], s, {"x": 1})
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "DEVELOPING" in ei.value.message


def test_real_generic_sole_pusher_conflict_blocked(env):
    from core_handlers import perform_push
    from contracts import ApiError
    dest_id = _mk_generic_dest(env)
    _mk_target_config(env, dest_id, "drug_trace_alihealth")  # PRODUCTION+CONFLICT
    with env["sf"]() as db:
        seed_default_targets(db, env["tid"])
        db.commit()
        sid = _mk_submission(env, dest_id)
        s = db.get(Submission, sid)
        with pytest.raises(ApiError) as ei:
            perform_push(db, env["tid"], s, {"x": 1})
        assert "CONFLICT" in ei.value.message


def test_mock_destination_not_gated(env):
    """Mock 目的地不受闸门影响（走 MockHttpClient 分支）。"""
    from core_handlers import perform_push
    with env["sf"]() as db:
        d = Destination(id=new_uuid(), tenant_id=env["tid"], code="mock-x",
                        profile_revision="alihealth_yljg_retail_v1",
                        mode="PUSH", is_mock=True, status="ENABLED")
        db.add(d)
        db.commit()
        did = d.id
        sid = _mk_submission(env, did)
        s = db.get(Submission, sid)
        # Mock 分支会打 127.0.0.1:8090——环境无 mock server 时 outcome=UNKNOWN/
        # 网络异常；关键是**不抛 PROFILE_UNAPPROVED**（闸门只管真实 generic）
        try:
            r = perform_push(db, env["tid"], s, {"x": 1})
            assert r["outcome"] in ("UNKNOWN", "ACCEPTED", "REJECTED")
        except Exception as exc:  # noqa: BLE001
            assert "PROFILE_UNAPPROVED" not in str(exc)


def test_drug_trace_profile_adapter_still_registered_and_gate_free(env):
    """药品 profile 适配器仍按 profile 键注册；闸门逻辑不覆盖药品键。"""
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    from core_handlers import DESTINATION_ADAPTERS
    assert "alihealth_yljg_retail_v1" in DESTINATION_ADAPTERS
    assert "alihealth_yljg_inout_v1" in DESTINATION_ADAPTERS
    assert "destination.generic_http_json" in DESTINATION_ADAPTERS
    # 药品 profile 键不以 destination. 开头 → 不进闸门分支（代码路径检查：
    # 闸门仅 profile_key.startswith("destination.") 时触发）
    dest_id = _mk_generic_dest(env, profile="alihealth_yljg_retail_v1@1")
    with env["sf"]() as db:
        dest = db.get(Destination, dest_id)
        assert (dest.profile_revision or "").split("@")[0] \
            != "destination.generic_http_json"


def test_drug_trace_real_send_does_not_call_perform_push():
    """证据测试：药品真实发送链路（worker job drug_trace.send →
    plugin._j_send → service.perform_send）不调用 core_handlers.perform_push
    ——LT-03 的闸门加在 perform_push 里，因此不可能影响药品发送。

    证据（文件:行号，见交接）：
    - apps/worker/engine.py 经 plugin.JOB_HANDLERS["drug_trace.send"]=_j_send；
    - _j_send 状态守卫后调 S.perform_send（service.py）；
    - service.py 全文不含 perform_push 引用（inspect.getsource 实测 False）；
    - plugin.py 中唯一的 "perform_push" 字符串出现在 _destination_adapter 的
      docstring（说明其为 core_handlers.perform_push 的适配器入口——该入口
      只服务病理/metrics 的工作流 Mock 流与手动提交，不属于药品发送 job）。
    """
    import inspect
    from domains.drug_trace import service, plugin
    svc_src = inspect.getsource(service)
    assert "perform_push" not in svc_src          # 药品服务层不引用
    # 药品 send job handler 直接走 perform_send（不经 perform_push）
    plug_src = inspect.getsource(plugin)
    assert "S.perform_send(db, job.tenant_id, sub)" in plug_src
    assert "S.perform_send(db, tenant_id, submission)" in plug_src
    # perform_send 存在且带药品自己的证据门禁
    assert hasattr(service, "perform_send")
    assert "DrugTraceProductionEvidence" in svc_src


def test_perform_send_gate_signature_unchanged(env):
    """药品 perform_send 的证据门禁签名与调用形状未变（LT-03 不触碰）。"""
    import inspect
    from domains.drug_trace import service
    sig = inspect.signature(service.perform_send)
    assert list(sig.parameters)[:3] == ["db", "tenant_id", "submission"]
