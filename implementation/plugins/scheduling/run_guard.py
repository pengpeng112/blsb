"""运行治理（LT-04 B 段）：只标记、只告警，不 kill、不释放租约、不改业务表。

药品 collect 长任务有自己的续租/进度心跳逻辑（DTR-RECOVERY-STABILITY），
误杀/误放行都会造成重复采集或双发。TIMED_OUT/STALLED 是**观测异常**：
原 Job 可能仍在运行，FORBID 并发保护不因观测状态放行第二个 Job（R04）；
迟到成功由 scheduler.finalize_run 回写并保留 late 痕迹。
"""
from __future__ import annotations

from datetime import timedelta

from core_db.engine import utcnow
from core_db.models import Job, ScheduleRun


def scan(db, tenant_id: str) -> dict:
    """检测超 deadline/停滞的运行并写观测状态 + RUN_TIMEOUT/RUN_STALLED 事件。

    RT-03 S02：尚未开跑的 QUEUED 排队任务不按运行时限标超时（排队积压是
    另一个观测口径）；deadline 检测只对**已首次开跑**（started_at 非空）的
    RUNNING 运行生效。返回 {"timed_out": n, "stalled": n}。
    """
    from core_db.models import AlertEvent, Schedule
    from contracts import new_uuid
    now = utcnow()
    timed_out = 0
    stalled = 0
    runs = (db.query(ScheduleRun)
            .filter(ScheduleRun.tenant_id == tenant_id,
                    ScheduleRun.state.in_(("QUEUED", "RUNNING")))
            .all())
    for run in runs:
        if run.state == "QUEUED" or run.started_at is None:
            continue                 # 未开跑：排队积压不按执行超时口径（S02）
        sched = db.get(Schedule, run.schedule_id)
        max_rt = int(sched.max_runtime_seconds or 3600) if sched else 3600
        over_deadline = run.deadline_at is not None and now > run.deadline_at
        if not over_deadline:
            continue
        if run.state != "TIMED_OUT":
            run.state = "TIMED_OUT"
            run.result_summary = {**(run.result_summary or {}),
                                  "observed": "TIMED_OUT",
                                  "observed_at": now.isoformat()}
            _emit_guard_event(db, tenant_id, run, "RUN_TIMEOUT",
                              "CRITICAL" if now > run.deadline_at + timedelta(
                                  seconds=max(300, max_rt)) else "WARN")
            timed_out += 1
        # STALLED：run 已超时且关联 Job 租约过期超过阈值（心跳不可用时以
        # lease_until 为代理；不改 Job 行——只读其状态）
        job = db.get(Job, run.job_id) if run.job_id else None
        if job is not None and job.state == "LEASED" and \
                job.lease_until is not None:
            stall_after = timedelta(seconds=max(300, int(max_rt * 0.2)))
            if now > job.lease_until + stall_after:
                run.state = "STALLED"       # 覆盖 TIMED_OUT（更严重观测）
                run.result_summary = {**(run.result_summary or {}),
                                      "observed": "STALLED",
                                      "observed_at": now.isoformat()}
                _emit_guard_event(db, tenant_id, run, "RUN_STALLED", "CRITICAL")
                stalled += 1
    db.flush()
    return {"timed_out": timed_out, "stalled": stalled}


def _emit_guard_event(db, tenant_id: str, run: ScheduleRun, kind: str,
                      severity: str) -> None:
    """写告警事件（评估器去重接管后续通知；指纹含 schedule+run 使每班独立）。"""
    from core_db.models import AlertEvent
    from contracts import new_uuid
    fp = f"run.{kind.lower()}:{run.schedule_id}:{run.run_key}"
    existing = (db.query(AlertEvent)
                .filter_by(tenant_id=tenant_id, fingerprint=fp,
                           resolved_at=None).first())
    now = utcnow()
    if existing is not None:
        existing.last_seen_at = now
        existing.occurrences = int(existing.occurrences or 1) + 1
        return
    db.add(AlertEvent(
        id=new_uuid(), tenant_id=tenant_id, alert_type=kind,
        severity=severity.lower(), message=f"{kind}: run {run.run_key} "
        f"超过 deadline（观测异常，Job 未被终止）",
        target_ref=f"schedule_run:{run.id}", state="OPEN",
        created_at=now, fingerprint=fp, rule_code=f"sys.{kind.lower()}",
        target_code="", first_seen_at=now, last_seen_at=now, occurrences=1,
        detail={"schedule_id": run.schedule_id, "run_key": run.run_key,
                "deadline_at": run.deadline_at.isoformat()
                if run.deadline_at else None}))
