"""调度种子（LT-04 A 段；幂等只补缺，不覆盖任何已有字段/配置）。

R01：所有新调度（含三个系统任务）enabled=False；药品/传染病镜像
observe_only=True（服务端 OBSERVE_ONLY，enable/run-now/改类型全拒绝）。
系统任务会写库写文件并占用 worker——不是"只读所以自动启用"，仅独立
合成环境显式启用。
"""
from __future__ import annotations

from core_db.engine import utcnow
from core_db.models import Schedule

SYSTEM_SCHEDULES = [
    {"code": "system.alert_evaluate", "name": "告警评估（系统）",
     "target_code": "", "job_type": "system.alert_evaluate",
     "payload_template": {}, "cron": "*/5 * * * *", "enabled": False,
     "note": "系统任务：写库/写文件并占 worker；仅合成环境显式启用"},
    {"code": "system.alert_digest", "name": "告警晨报（系统）",
     "target_code": "", "job_type": "system.alert_digest",
     "payload_template": {}, "cron": "0 8 * * *", "enabled": False,
     "note": "系统任务：写库/写文件并占 worker；仅合成环境显式启用"},
    {"code": "system.run_guard", "name": "运行治理巡检（系统）",
     "target_code": "", "job_type": "system.run_guard",
     "payload_template": {}, "cron": "* * * * *", "enabled": False,
     "note": "系统任务：写库（观测状态+告警事件）；仅合成环境显式启用"},
]

# 镜像登记（observe_only）：现由宿主 timer/cron 执行，平台只展示未来状态
MIRROR_SCHEDULES = [
    {"code": "drug_trace.daily_mirror",
     "name": "药品追溯日批（镜像·仅观测）",
     "target_code": "drug_trace_alihealth", "job_type": "drug_trace.collect",
     "payload_template": {"business_date": "{fire_date_minus_1}"},
     "cron": "0 1 * * *", "enabled": False, "observe_only": True,
     "note": "镜像：现由宿主 blyb-daily-upload.timer 执行；切换须用户在场，"
             "平台内 enable/run-now 被服务端拒绝"},
    {"code": "inf.poll_mirror",
     "name": "传染病 */15 班次（镜像·仅观测）",
     "target_code": "inf_cdc_frontend", "job_type": "inf.collect",
     "payload_template": {}, "cron": "*/15 0-5,9-23 * * *", "enabled": False,
     "observe_only": True,
     "note": "镜像：现由 <worker-container> 容器 crontab（平台外 inf_pusher）执行"},
]


def seed_schedules(db, tenant_id: str, known_types: set[str] | None = None,
                   actor: str = "system") -> int:
    """幂等种子：按 code 补缺；job_type 未注册的镜像跳过创建（记日志）。"""
    import logging
    log = logging.getLogger("scheduling.seeds")
    existing = {s.code for s in db.query(Schedule)
                .filter_by(tenant_id=tenant_id).all()}
    added = 0
    from contracts import new_uuid
    for row in SYSTEM_SCHEDULES + MIRROR_SCHEDULES:
        if row["code"] in existing:
            continue
        if row.get("observe_only") and known_types is not None and \
                row["job_type"] not in known_types:
            log.info("seed skip %s: job_type %r 未注册（按任务书跳过创建）",
                     row["code"], row["job_type"])
            continue
        db.add(Schedule(id=new_uuid(), tenant_id=tenant_id,
                        created_at=utcnow(), updated_at=utcnow(),
                        updated_by=actor,
                        misfire_policy="SKIP", concurrency="FORBID",
                        max_runtime_seconds=3600, blackout=[],
                        observe_only=bool(row.get("observe_only", False)),
                        **{k: row[k] for k in
                           ("code", "name", "target_code", "job_type",
                            "payload_template", "cron", "enabled", "note")}))
        added += 1
    return added
