"""告警种子（LT-04 C 段；幂等只补缺）。

通道：file_default（enabled=True，用户已确认的 file-only 决定保留）；
smtp/webhook 不建（本批服务端强制停用）。
规则 enabled=True 只表示"参与评估"；评估本身由 system.alert_evaluate
调度驱动，该调度默认停用（R01）——生产未启用前这些规则不产生任何外发。
"""
from __future__ import annotations

from contracts import new_uuid
from core_db.engine import utcnow
from core_db.models import AlertRule, NotificationChannel

DEFAULT_RULES = [
    {"code": "dt.no_success", "name": "药品 30h 无业务确认",
     "target_code": "drug_trace_alihealth", "kind": "NO_SUCCESS_WITHIN",
     "params": {"hours": 30}, "severity": "WARN", "enabled": True},
    {"code": "dt.unknown", "name": "药品存在 UNKNOWN",
     "target_code": "drug_trace_alihealth", "kind": "UNKNOWN_OVER",
     "params": {"threshold": 1}, "severity": "WARN", "enabled": True},
    {"code": "dt.backlog", "name": "药品积压超阈",
     "target_code": "drug_trace_alihealth", "kind": "BACKLOG_OVER",
     "params": {"threshold": 500}, "severity": "WARN", "enabled": True},
    {"code": "dt.auth_expiry", "name": "药品发送授权到期",
     "target_code": "drug_trace_alihealth", "kind": "DATE_EXPIRY",
     "params": {"source": "drug_trace.production_evidence",
                "label": "药品发送授权", "warn_days": 7},
     "severity": "WARN", "enabled": True},
    {"code": "inf.no_success", "name": "传染病 26h 无受理/确认活动",
     "target_code": "inf_cdc_frontend", "kind": "NO_SUCCESS_WITHIN",
     # PT-02 修复 ACCEPTED 业务永久误报：前置机 result=true 映射为 ACCEPTED
     # （无 CONFIRMED 记录）——accept_counts_as_activity=true 让受理也算活动。
     "params": {"hours": 26, "accept_counts_as_activity": True},
     "severity": "WARN", "enabled": True},
    {"code": "inf.watermark_stale", "name": "传染病检验明细水位滞后",
     "target_code": "inf_cdc_frontend", "kind": "WATERMARK_STALE",
     "params": {"table": "ex_lab_item", "hours": 6},
     "severity": "WARN", "enabled": True},
    {"code": "inf.parent_wait", "name": "传染病待父受理积压",
     "target_code": "inf_cdc_frontend", "kind": "PARENT_WAIT_OVER",
     "params": {"threshold": 300}, "severity": "WARN", "enabled": True},
    {"code": "sys.run_timeout", "name": "运行超时", "target_code": "",
     "kind": "RUN_TIMEOUT", "params": {}, "severity": "CRITICAL",
     "enabled": True},
    {"code": "sys.run_stalled", "name": "运行停滞", "target_code": "",
     "kind": "RUN_STALLED", "params": {}, "severity": "CRITICAL",
     "enabled": True},
    # 平台 × inf_watchdog 双向交叉守护：守护自身停摆平台必须能看见
    # （status_path 生产值 = <prod-path>/exchange/inf-watchdog/state/status.json，
    # 需挂载共享；未配置时规则产出 DATA_UNAVAILABLE 提示，不误报）
    {"code": "sys.watchdog_stale", "name": "外部守护停摆交叉监控",
     "target_code": "", "kind": "WATCHDOG_STALE",
     "params": {"status_path": "", "minutes": 30},
     "severity": "WARN", "enabled": True},
]


def seed_alerting(db, tenant_id: str, actor: str = "system") -> int:
    added = 0
    existing_ch = {c.code for c in db.query(NotificationChannel)
                   .filter_by(tenant_id=tenant_id).all()}
    if "file_default" not in existing_ch:
        db.add(NotificationChannel(
            id=new_uuid(), tenant_id=tenant_id, code="file_default",
            type="file", config={}, enabled=True,   # 默认 var/alerts/alerts-YYYYMMDD.jsonl
            updated_by=actor, updated_at=utcnow()))
        added += 1
    # PT-02：会话为 autoflush=False（core_db 约定）——查重前先落盘 pending，
    # 保证同事务内连续调用 seed_alerting 时第二次的查重能看到第一次的 INSERT
    # （否则重复 add 同一 code，commit 时撞 UNIQUE；幂等语义对齐"逐条补缺"）
    db.flush()
    existing_r = {r.code for r in db.query(AlertRule)
                  .filter_by(tenant_id=tenant_id).all()}
    for row in DEFAULT_RULES:
        if row["code"] in existing_r:
            continue
        db.add(AlertRule(
            id=new_uuid(), tenant_id=tenant_id, updated_by=actor,
            cooldown_minutes=60, notify_channels=["file_default"],
            updated_at=utcnow(), created_at=utcnow(),
            **{k: row[k] for k in ("code", "name", "target_code", "kind",
                                   "params", "severity", "enabled")}))
        added += 1
    return added
