"""最小幂等生产初始化（execution-plan §5：缺独立bootstrap交付）。

只做三件事：必要租户、真实对象卷、令牌加固。复用 apps.seed 的卷注册
（_place_primary_volume）与 T14 令牌加固（_apply_token_hardening）逻辑，
**不**创建演示字典/HospitalPack/工作流/目的地/阻断，不激活演示源、
Mock 工作流或任何对外自动发送；也不批量读取患者资料。

必需环境变量（缺失或过短即退出非零，fail-closed）：
- BLYB_BOOTSTRAP_ADMIN_TOKEN  平台管理员引导令牌（高熵随机；重复运行=轮换同值幂等）
- BLYB_ENROLL_TOKEN           设备注册令牌（供 core_api enroll 校验；仅校验存在与长度，不落库）

可选：
- BLYB_VOLUME_ROOT  对象卷根（容器内 /data/volume1；未设时沿用 seed 的解析规则）
- BLYB_TENANT_CODE / BLYB_TENANT_NAME  租户编码/名称（默认 first-hospital / 首院）

幂等性：租户 get-or-create；卷登记/对齐沿用 seed 语义（已有对象位置时拒绝换根）；
令牌加固幂等（同值重复运行不产生重复主体）。退出码：0=成功，2=配置缺失，3=运行错误。
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_HERE, os.path.join(_HERE, "packages"), os.path.join(_HERE, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _bootstrap  # noqa: F401,E402

_MIN_TOKEN_LEN = 32  # 与 start.ps1 的引导令牌下限一致（T14）


def _check_env() -> list[str]:
    problems: list[str] = []
    for var in ("BLYB_BOOTSTRAP_ADMIN_TOKEN", "BLYB_ENROLL_TOKEN"):
        val = os.environ.get(var, "")
        if not val.strip():
            problems.append(f"{var} 未设置")
        elif len(val.strip()) < _MIN_TOKEN_LEN:
            problems.append(f"{var} 长度不足 {_MIN_TOKEN_LEN}（当前 {len(val.strip())}）")
    return problems


def main() -> int:
    import json
    import logging

    import _bootstrap  # noqa: F401
    logging.basicConfig(level=logging.INFO)
    problems = _check_env()
    if problems:
        for p in problems:
            print(f"bootstrap: 配置错误：{p}", file=sys.stderr)
        return 2
    # 生产形态强制禁用 dev 令牌（登录只认 ACTIVE；dev-* 主体即便存在也置 DISABLED）
    os.environ["BLYB_DISABLE_DEV_TOKENS"] = "1"

    from contracts import new_uuid
    from core_db.engine import make_engine, make_session_factory
    from core_db.models import Tenant
    from apps.seed import _apply_token_hardening, _place_primary_volume, resolve_volume_root

    engine = make_engine()
    sf = make_session_factory(engine)
    tenant_code = os.environ.get("BLYB_TENANT_CODE", "first-hospital").strip() or "first-hospital"
    tenant_name = os.environ.get("BLYB_TENANT_NAME", "首院").strip() or "首院"
    vol_root = os.environ.get("BLYB_VOLUME_ROOT", "").strip() or resolve_volume_root("volume1")
    result: dict = {"tenant_code": tenant_code, "volume_root": vol_root}

    try:
        with sf() as db:
            t = db.query(Tenant).filter_by(code=tenant_code).first()
            if t is None:
                t = Tenant(id=new_uuid(), code=tenant_code, name=tenant_name,
                           timezone="Asia/Shanghai")
                db.add(t)
                db.flush()
                result["tenant_created"] = True
            else:
                result["tenant_created"] = False
            tenant_id = t.id
            _place_primary_volume(db, tenant_id, vol_root)
            result.update(_apply_token_hardening(db, tenant_id))
            # LT-01：上报业务目录幂等种子（台账登记，不驱动任何链路；已存在的
            # code 不覆盖）。失败不阻断 bootstrap 主流程——目录缺失仅影响台账页。
            try:
                from plugins.report_targets import seed_default_targets
                result["report_targets_seeded"] = seed_default_targets(
                    db, tenant_id)
                # LT-04 种子（幂等只补缺）：调度（镜像 observe_only/系统任务
                # 默认停用）+ 告警规则 + file_default 通道
                from scheduling.seeds import seed_schedules
                from alerting.seeds import seed_alerting
                from scheduling.scheduler import known_job_types
                reg = None
                try:
                    import app_runtime
                    reg = app_runtime.load_registry()
                except Exception:  # noqa: BLE001
                    reg = None
                result["schedules_seeded"] = seed_schedules(
                    db, tenant_id, known_job_types(reg))
                result["alerting_seeded"] = seed_alerting(db, tenant_id)
                # LT-05 B4：传染病影子 Mock 目的地（幂等；loopback 占位端口，
                # 测试/演练用各自 Mock 服务地址发布新 revision）
                from core_db.models import ConfigRevision, Destination
                if db.query(Destination).filter_by(
                        tenant_id=tenant_id,
                        code="inf_frontend_mock").first() is None:
                    d = Destination(
                        id=new_uuid(), tenant_id=tenant_id,
                        code="inf_frontend_mock", name="前置机Mock（影子）",
                        profile_revision="destination.generic_http_json@1",
                        mode="PUSH", is_mock=True, status="ENABLED")
                    db.add(d)
                    db.flush()
                    cr = ConfigRevision(
                        id=new_uuid(), tenant_id=tenant_id,
                        config_type="destination", owner_id=d.id, revision=1,
                        content_json={
                            "base_url": "http://127.0.0.1:8090",
                            "allowed_hosts": ["127.0.0.1"],
                            "allowed_ports": [8090],
                            "allowed_schemes": ["http"],
                            "path_template": "/hclient/emr/receive/{__endpoint}",
                            "report_target_code": "inf_cdc_frontend"},
                        digest="seed-inf-mock", state="PUBLISHED")
                    db.add(cr)
                    db.flush()
                    d.config_revision = cr.id
                    result["inf_mock_dest_seeded"] = 1
            except Exception:  # noqa: BLE001 —— 台账种子失败不阻断初始化
                logging.getLogger("bootstrap").warning(
                    "report_targets seed failed", exc_info=True)
                result["report_targets_seeded"] = None
            db.commit()
    except Exception:  # noqa: BLE001
        logging.getLogger("bootstrap").exception("bootstrap failed")
        return 3
    finally:
        engine.dispose()
    # 不回显任何令牌值；bootstrap_admin 为主体ID（uuid），可审计
    print(json.dumps({"bootstrap": "ok", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
