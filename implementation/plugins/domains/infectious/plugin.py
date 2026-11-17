"""传染病影子插件注册（LT-05 B1；RT-04 A1 状态存储改造）。

只读外部台账钩子 + inf.collect/inf.send 影子 job（只能 Mock）。
生产推送仍在平台外（deploy/inf_pusher + crontab），本插件不接管任何生产链路。

A1：影子运行状态（水位/已发账）存独立状态表 infectious_shadow_cursor /
infectious_shadow_record（迁移021）——**不再写 PUBLISHED ConfigRevision**
（旧配置只读兼容：首次使用时 migrate_legacy_config 一次性迁入，不改写）。
"""
from __future__ import annotations

from contracts import new_uuid
from core_db.engine import utcnow

JOB_HANDLERS = {}


def _j_inf_collect(db, job):
    """影子采集：主入口走真实 SQL 路径（collect.collect_job——连接配置
    缺失时显式能力不可用）；payload['rows_by_table'] 仅为显式测试注入入口
    （缺连接配置时同样报能力不可用，不静默当作真实采集）。"""
    from domains.infectious import collect as C
    payload = dict(job.payload or {})
    rows_by_table = payload.get("rows_by_table")
    if rows_by_table:
        result = C.collect_rows(db, job.tenant_id,
                                source_instance=payload.get(
                                    "source_instance", "default"),
                                rows_by_table=rows_by_table)
    else:
        result = C.collect_job(db, job.tenant_id, payload)
    job.payload = {**payload, **result}


def _j_inf_send(db, job):
    """影子发送：只能对 is_mock=True 目的地（send_batch 强制）。"""
    from domains.infectious import send as S
    payload = job.payload or {}
    dest_code = str(payload.get("destination_code", "inf_frontend_mock"))
    from core_db.models import Destination
    dest = (db.query(Destination)
            .filter_by(tenant_id=job.tenant_id, code=dest_code).first())
    stats = S.send_batch(
        db, job.tenant_id, dest, payload.get("rows_by_table") or {},
        source_instance=payload.get("source_instance", "default"),
        now_cst=payload.get("now_cst") or utcnow())
    job.payload = {**payload, "stats": stats}


JOB_HANDLERS["inf.collect"] = _j_inf_collect
JOB_HANDLERS["inf.send"] = _j_inf_send


def register(reg):
    from domains.infectious import hooks as _hooks
    from plugins.domain_hooks import (HOOK_LEDGER_DETAIL,
                                      HOOK_LEDGER_SEARCH,
                                      HOOK_TARGET_CLASSIFICATION,
                                      HOOK_TARGET_SUMMARY)
    reg.attach(HOOK_TARGET_SUMMARY, _hooks.target_summary)
    reg.attach(HOOK_LEDGER_SEARCH, _hooks.ledger_search)
    reg.attach(HOOK_LEDGER_DETAIL, _hooks.ledger_detail)
    reg.attach(HOOK_TARGET_CLASSIFICATION, _hooks.classification)
    reg.attach("worker.job_handlers", dict(JOB_HANDLERS))
    # PT-03：传染病全维监控只读路由（/api/v1/infectious/monitor/*，
    # target_scopes 鉴权；domain.routes 通用挂载，核心 app 零特判）
    from domains.infectious import routes as _routes
    reg.attach("domain.routes", _routes.register_routes)
