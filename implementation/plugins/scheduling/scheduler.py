"""调度 tick 与服务层（LT-04 A 段；RT-03 深修）。

事务模型（R04）：next_fire 推进（条件 UPDATE 抢占）→ schedule_run 插入 →
Job 入队三步在**同一事务**内提交；任一步失败整体回滚——不丢触发（下轮重试）
也不重复入队（执行键幂等）。进程内节流不是锁，抢占以数据库条件 UPDATE 为准。

FORBID 并发（R04）：检查同 schedule 的**真实非终态**——ScheduleRun 非
QUEUED/RUNNING 即终态的观测集之外，TIMED_OUT/STALLED 观测状态**不算终态**
（原 Job 可能仍在运行），并直接检查 Job 的 PENDING/LEASED/RETRY_WAIT
非终态行（dedup_key 前缀关联）。

misfire（RT-03 S03 语义）：到期即把 next_fire 一次推进到严格大于 now 的
下一槽位（future_next），积压多班合并处理——超 grace(120s) 的 SKIP 只记
一条 MISSED（result_summary 注明被折叠区间，不制造 96 条历史）；RUN_ONCE
只补 [旧 next_fire_at, now] 内**最近一个合法槽位**（CronExpr.
previous_at_or_before，业务日期={fire_date_minus_1} 语义正确），不拿 now
冒充计划时间。blackout 先按选中槽位做计划避让；补跑时实际 now 也在维护
窗口则不启动补跑，只记一条 SKIPPED_BLACKOUT（维护窗口不是延后反复补跑
机制）。输入 datetime 一律先规范为 aware UTC（naive 按 UTC 解释，带 offset
先 astimezone(utc) 再落 naive UTC 列，不直接剥离 +08:00）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from contracts import ApiError, new_uuid
from core_db.engine import utcnow
from core_db.models import AuditEvent, Job, Schedule, ScheduleRun
from workflow_core.cron import (CST, CronExpr, NextFireOutOfRange,
                                in_blackout, render_payload, to_aware_utc)
from workflow_core.jobs import enqueue_job

MISFIRE_GRACE_SECONDS = 120
# 系统任务 job 类型（engine 侧注册处理器；镜像/领域类型经 registry 附件）
SYSTEM_JOB_TYPES = ("system.alert_evaluate", "system.alert_digest",
                    "system.run_guard")
NON_TERMINAL_RUN_STATES = ("QUEUED", "RUNNING", "TIMED_OUT", "STALLED")
NON_TERMINAL_JOB_STATES = ("PENDING", "LEASED", "RETRY_WAIT")


def _sched_dedup_prefix(schedule_id: str) -> str:
    return f"sched:{schedule_id}:"


def _has_active_execution(db, schedule_id: str) -> bool:
    """FORBID 判定：真实非终态 Job（不以观测状态放行）或非终态 run。"""
    prefix = _sched_dedup_prefix(schedule_id)
    job_alive = (db.query(Job.id)
                 .filter(Job.dedup_key.like(f"{prefix}%"),
                         Job.state.in_(NON_TERMINAL_JOB_STATES))
                 .first() is not None)
    if job_alive:
        return True
    return (db.query(ScheduleRun.id)
            .filter(ScheduleRun.schedule_id == schedule_id,
                    ScheduleRun.state.in_(NON_TERMINAL_RUN_STATES))
            .first() is not None)


def _record_run(db, tenant_id: str, sched: Schedule, fire: datetime,
                state: str, *, trigger: str = "CRON", triggered_by: str = "",
                run_key: str | None = None, job_id: str | None = None,
                error_code: str = "", error_message: str = "",
                now: datetime | None = None,
                result_summary: dict | None = None) -> ScheduleRun:
    """入队时刻的 run 行（S01/S03）：started_at 为空、保留 fire_time；
    deadline 暂按**入队时刻 now** 起算（排队中也有观测锚点，run_guard 不以
    它把 QUEUED 排队标超时）；首次有效开跑由 mark_run_started 重置为
    start + max_runtime_seconds 的执行 deadline。"""
    fire_aware = to_aware_utc(fire)
    fire_naive = fire_aware.replace(tzinfo=None)
    now_naive = to_aware_utc(now or utcnow()).replace(tzinfo=None)
    run = ScheduleRun(
        id=new_uuid(), tenant_id=tenant_id, schedule_id=sched.id,
        run_key=run_key or f"cron:{fire_naive.isoformat()}",
        fire_time=fire_naive, state=state, job_id=job_id,
        deadline_at=now_naive + timedelta(
            seconds=int(sched.max_runtime_seconds or 3600)),
        trigger=trigger, triggered_by=triggered_by,
        error_code=error_code, error_message=error_message[:500],
        result_summary=result_summary or {})
    db.add(run)
    return run


def tick(db, now_utc: datetime, *, registry=None) -> dict:
    """调度触发主循环（worker 每 tick 调一次，进程内节流在 engine 侧）。

    返回 {"fired": n, "skipped": {state: n}, "scheduled": 计算了 next_fire 的数}。
    RT-03 S03：now 一律先规范 aware UTC；到期时 next_fire 一次推进到严格大于
    now 的 future_next（积压槽位合并），抢占/记录/入队同事务，任一步异常
    rollback 后 re-raise——下轮 tick 以同一到期指针重试（不丢不重）。
    """
    now_utc = to_aware_utc(now_utc)
    now_naive = now_utc.replace(tzinfo=None)
    fired = 0
    skipped: dict[str, int] = {}
    scheduled = 0
    # 1) enabled 且未观测的镜像限制在查询层：observe_only 永不触发（R01）
    rows = (db.query(Schedule)
            .filter(Schedule.enabled.is_(True),
                    Schedule.observe_only.is_(False))
            .all())
    for sched in rows:
        try:
            expr = CronExpr(sched.cron)
        except ValueError:
            # 配置坏表达式：不触发不推进，留告警/日志（不猜测）
            skipped["CRON_INVALID"] = skipped.get("CRON_INVALID", 0) + 1
            continue
        if sched.next_fire_at is None:
            sched.next_fire_at = expr.next_after(now_utc).replace(tzinfo=None)
            db.commit()                   # 只保存触发指针，不触发
            scheduled += 1
            continue
        if sched.next_fire_at > now_naive:
            continue
        old_fire_naive = sched.next_fire_at
        fire_utc = to_aware_utc(old_fire_naive)
        try:
            future_next = expr.next_after(now_utc).replace(tzinfo=None)
        except NextFireOutOfRange:
            # 到期槽位之后的 366 天内再无触发：指针推进到远未来锚点（不触发）
            future_next = (now_utc + timedelta(days=366)).replace(tzinfo=None)
        # 2) 条件 UPDATE 抢占（与 run/Job 同事务；0 行=他 worker 已抢）。
        #    last_fire_at 记**原到期槽位**（准点/补跑共用它渲染业务日期）。
        from sqlalchemy import update
        res = db.execute(
            update(Schedule)
            .where(Schedule.id == sched.id,
                   Schedule.next_fire_at == old_fire_naive,
                   Schedule.revision == sched.revision)
            .values(next_fire_at=future_next, last_fire_at=old_fire_naive,
                    revision=Schedule.revision + 1))
        if res.rowcount != 1:
            db.rollback()                 # 他人已处理或并发修改：让位
            continue
        db.expire(sched)                  # 抢占后重读，避免 Session 陈值
        try:
            # ---- 槽位选择：未超 grace=准点执行原 due 槽位；超 grace 合并积压
            late = (now_utc - fire_utc).total_seconds() > MISFIRE_GRACE_SECONDS
            if not late:
                fire = fire_utc
            elif sched.misfire_policy == "RUN_ONCE":
                # RUN_ONCE 只补 [旧 next_fire_at, now] 内最近合法槽位
                # （>= 旧槽位由"旧槽位本身合法且 <=now"保证）
                fire = expr.previous_at_or_before(now_utc)
            else:
                fire = fire_utc
            # ---- blackout 判定顺序（S03）：先按选中槽位做计划避让；补跑时
            # 实际 now 也在维护窗口 → 不启动补跑（只记一条，不延后反复补跑）
            in_blackout_plan = in_blackout(fire, sched.blackout)
            in_blackout_now = late and in_blackout(now_utc, sched.blackout)
            if in_blackout_plan or in_blackout_now:
                _record_run(db, sched.tenant_id, sched, fire,
                            "SKIPPED_BLACKOUT", error_code="BLACKOUT",
                            now=now_utc)
                skipped["SKIPPED_BLACKOUT"] = \
                    skipped.get("SKIPPED_BLACKOUT", 0) + 1
            elif late and sched.misfire_policy == "SKIP":
                # 一条 MISSED + 被折叠区间（旧到期槽位 → now 前最近合法槽位）
                try:
                    collapsed_to = expr.previous_at_or_before(
                        now_utc).replace(tzinfo=None).isoformat()
                except NextFireOutOfRange:
                    collapsed_to = now_naive.isoformat()
                _record_run(db, sched.tenant_id, sched, fire, "MISSED",
                            error_code="MISFIRE_SKIP", now=now_utc,
                            result_summary={
                                "collapsed_from": old_fire_naive.isoformat(),
                                "collapsed_to": collapsed_to})
                skipped["MISSED"] = skipped.get("MISSED", 0) + 1
            elif sched.concurrency == "FORBID" and _has_active_execution(
                    db, sched.id):
                _record_run(db, sched.tenant_id, sched, fire, "SKIPPED_OVERLAP",
                            error_code="OVERLAP_FORBID", now=now_utc)
                skipped["SKIPPED_OVERLAP"] = \
                    skipped.get("SKIPPED_OVERLAP", 0) + 1
            else:
                # 3) run + Job 入队（同事务；入队异常整体回滚→下轮重试）
                run = _record_run(db, sched.tenant_id, sched, fire, "QUEUED",
                                  now=now_utc)
                payload = render_payload(sched.payload_template or {}, fire)
                payload["schedule_run_id"] = run.id
                payload["schedule_id"] = sched.id
                payload["fire_time"] = fire.replace(tzinfo=None).isoformat()
                job = enqueue_job(
                    db, sched.tenant_id, sched.job_type,
                    f"{_sched_dedup_prefix(sched.id)}"
                    f"{fire.replace(tzinfo=None).isoformat()}",
                    payload)
                run.job_id = job.id
                db.flush()
                fired += 1
            db.commit()
        except Exception:
            # 整体回滚（不依赖 Session close 兜底；PG 上必须显式 rollback），
            # re-raise 交由调用方（engine 侧 log 后继续下一轮 tick 重试）
            db.rollback()
            raise
    return {"fired": fired, "skipped": skipped, "scheduled": scheduled}


def mark_run_started(db, job: Job, now: datetime | None = None) -> ScheduleRun | None:
    """RT-03 S02：worker 成功 claim 后、调领域 handler 前的运行开始回写。

    只处理 payload 带 schedule_run_id 的 Job。绑定校验：run 必须属于本租户、
    本 Job（run.job_id == job.id）与本调度（payload.schedule_id）——错绑定
    不写他人记录（返回 None）。幂等：仅**首次有效开跑**写 started_at 并把
    执行 deadline 固定为 start + max_runtime_seconds（重试/租约重新领取保留
    首次 started_at 与 deadline，总运行预算不无限延长）；观测异常
    （TIMED_OUT/STALLED）不洗回 RUNNING（S01：不得把已观测异常洗回正常）。
    """
    payload = job.payload or {}
    run_id = payload.get("schedule_run_id")
    if not run_id:
        return None
    run = db.get(ScheduleRun, run_id)
    if run is None or run.tenant_id != job.tenant_id or run.job_id != job.id:
        return None
    sched_id = payload.get("schedule_id")
    if sched_id and run.schedule_id != sched_id:
        return None
    from sqlalchemy import update as sa_update
    now_naive = to_aware_utc(now or utcnow()).replace(tzinfo=None)
    if run.started_at is None:
        sched = db.get(Schedule, run.schedule_id)
        max_rt = int(sched.max_runtime_seconds or 3600) if sched else 3600
        res = db.execute(
            sa_update(ScheduleRun)
            .where(ScheduleRun.id == run.id,
                   ScheduleRun.tenant_id == job.tenant_id,
                   ScheduleRun.started_at.is_(None))
            .values(state="RUNNING", started_at=now_naive,
                    deadline_at=now_naive + timedelta(seconds=max_rt)))
        if res.rowcount == 1:
            db.flush()
            db.refresh(run)
            return run
        db.refresh(run)          # 并发首次开跑已由他人写入：走下方幂等分支
    if run.state in ("QUEUED", "RUNNING"):
        run.state = "RUNNING"    # 保留首次 started_at/deadline（不重置预算）
        db.flush()
    return run


# ---------------------------------------------------------------- 服务层（API 用）
def _get_schedule(db, tenant_id: str, schedule_id: str) -> Schedule:
    s = db.get(Schedule, schedule_id)
    if s is None or s.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "schedule not found")
    return s


def _refuse_observe_only(sched: Schedule, action: str) -> None:
    if sched.observe_only:
        raise ApiError(
            "OBSERVE_ONLY_REJECTED",
            f"镜像调度 {sched.code!r} 服务端 OBSERVE_ONLY：{action} 被拒绝"
            f"（生产由宿主 timer/cron 执行，切换须用户在场，UI 确认不能授权）")


def known_job_types(registry=None) -> set[str]:
    """已注册 job 类型集合（engine 基线 + 领域附件；创建/改调度时校验）。"""
    types = set(SYSTEM_JOB_TYPES)
    types.update({"pathology.resolve", "pathology.freeze_attempt",
                  "pathology.resolve_waiting", "destination.poll_receipts",
                  "destination.query_unknown", "destination.push_manual",
                  "metrics.run", "workflow.launch", "pis.poll_created",
                  "pis.partition_reconcile", "storage.verify_session",
                  "storage.verify_asset", "storage.migrate_asset"})
    if registry is not None:
        types.update(registry.capability_merged("worker.job_handlers").keys())
    return types


def validate_schedule_payload(data: dict, registry=None) -> None:
    code = str(data.get("code", "") or "")
    if not code or len(code) > 64:
        raise ApiError("VALIDATION_FAILED", "code 必填（≤64）")
    cron = str(data.get("cron", "") or "")
    try:
        CronExpr(cron)
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", f"cron 非法：{exc}")
    job_type = str(data.get("job_type", "") or "")
    if job_type not in known_job_types(registry):
        raise ApiError("JOB_TYPE_UNKNOWN",
                       f"job_type {job_type!r} 未注册（已注册类型才允许创建）")
    if data.get("misfire_policy") not in (None, "SKIP", "RUN_ONCE"):
        raise ApiError("VALIDATION_FAILED", "misfire_policy 只允许 SKIP/RUN_ONCE")
    if data.get("concurrency") not in (None, "FORBID", "ALLOW"):
        raise ApiError("VALIDATION_FAILED", "concurrency 只允许 FORBID/ALLOW")
    for win in data.get("blackout") or []:
        for k in ("start", "end"):
            v = str(win.get(k, ""))
            hh, mm = (v.split(":") + [""])[:2]
            if not (hh.isdigit() and mm.isdigit() and 0 <= int(hh) <= 23
                    and 0 <= int(mm) <= 59):
                raise ApiError("VALIDATION_FAILED",
                               f"blackout {k} 须为 HH:MM，当前 {v!r}")


def upsert_schedule(db, tenant_id: str, data: dict, actor: str,
                    expected_revision: int | None = None,
                    registry=None) -> Schedule:
    from sqlalchemy import update as sa_update
    code = str(data.get("code", "") or "")
    sched = db.query(Schedule).filter_by(tenant_id=tenant_id, code=code).first()
    if sched is None:
        if not str(data.get("name", "") or "").strip():
            raise ApiError("VALIDATION_FAILED", "新建调度 name 必填")
        validate_schedule_payload(data, registry)
        # observe_only 只能由种子（镜像登记）创建，API 新建一律 False（R01）；
        # enabled 强制 False——启停必须走 enable/disable 端点（reason+审计）
        sched = Schedule(id=new_uuid(), tenant_id=tenant_id, code=code,
                         revision=1, updated_by=actor,
                         **{k: data[k] for k in
                            ("name", "target_code", "job_type",
                             "payload_template", "cron",
                             "misfire_policy", "concurrency",
                             "max_runtime_seconds", "blackout", "note")
                            if k in data})
        if data.get("observe_only"):
            raise ApiError("VALIDATION_FAILED",
                           "observe_only 镜像只能由系统种子创建，API 不接受")
        db.add(sched)
        db.flush()
    else:
        if expected_revision is not None and \
                int(expected_revision) != int(sched.revision):
            raise ApiError("CONFLICT", "revision mismatch（乐观锁）")
        _refuse_observe_only(sched, "修改 job_type/payload/enabled")
        for k in ("name", "target_code", "cron", "misfire_policy",
                  "concurrency", "max_runtime_seconds", "blackout", "note"):
            if k in data:
                setattr(sched, k, data[k])
        if "job_type" in data or "payload_template" in data or "cron" in data:
            merged = {"code": code,
                      "cron": data.get("cron", sched.cron),
                      "job_type": data.get("job_type", sched.job_type)}
            validate_schedule_payload(merged, registry)
            if "job_type" in data:
                sched.job_type = data["job_type"]
            if "payload_template" in data:
                sched.payload_template = data["payload_template"]
        # enabled 不经 PUT 修改（须走 enable/disable 端点留 reason+审计；R01）
        # 推进指针重算（cron 变更后旧 next_fire 语义失效）
        sched.next_fire_at = None
        sched.revision = int(sched.revision) + 1
        sched.updated_by = actor
    db.add(AuditEvent(
        id=new_uuid(), tenant_id=tenant_id, actor=actor or "",
        action="schedule.upsert", target=f"schedule:{code}",
        new_ref={"revision": int(sched.revision),
                 "enabled": bool(sched.enabled),
                 "observe_only": bool(sched.observe_only)}))
    db.flush()
    return sched


def _set_enabled_checked(sched: Schedule, enabled: bool) -> None:
    if enabled:
        _refuse_observe_only(sched, "enable")
    sched.enabled = enabled


def set_enabled(db, tenant_id: str, schedule_id: str, *, enabled: bool,
                actor: str, reason: str) -> Schedule:
    sched = _get_schedule(db, tenant_id, schedule_id)
    if len((reason or "").strip()) < 5:
        raise ApiError("VALIDATION_FAILED", "reason 必须至少 5 个字符")
    _set_enabled_checked(sched, enabled)
    sched.revision = int(sched.revision) + 1
    sched.updated_by = actor
    db.add(AuditEvent(
        id=new_uuid(), tenant_id=tenant_id, actor=actor,
        action="schedule.enable" if enabled else "schedule.disable",
        target=f"schedule:{sched.code}",
        new_ref={"reason": reason.strip(), "revision": int(sched.revision)}))
    db.flush()
    return sched


def run_now(db, tenant_id: str, schedule_id: str, *, actor: str,
            reason: str, idempotency_key: str = "",
            registry=None) -> ScheduleRun:
    """手动立即运行：遵守 concurrency；默认也遵守 blackout（本批无绕过参数）。"""
    sched = _get_schedule(db, tenant_id, schedule_id)
    _refuse_observe_only(sched, "run-now")
    if len((reason or "").strip()) < 5:
        raise ApiError("VALIDATION_FAILED", "reason 必须至少 5 个字符")
    run_key = f"manual:{idempotency_key or new_uuid()[:12]}"
    existing = (db.query(ScheduleRun)
                .filter_by(tenant_id=tenant_id, schedule_id=sched.id,
                           run_key=run_key).first())
    if existing is not None:
        return existing                     # 手动幂等键重放（非新执行，先于FORBID）
    now = utcnow().replace(tzinfo=timezone.utc)
    if in_blackout(now, sched.blackout):
        raise ApiError("BLACKOUT", "当前处于维护窗口（blackout），run-now 拒绝"
                      "（本批不提供绕过；绕过须独立维护策略）")
    if sched.concurrency == "FORBID" and _has_active_execution(db, sched.id):
        raise ApiError("CONFLICT", "上一轮仍在执行（FORBID），run-now 拒绝")
    fire = now
    run = _record_run(db, tenant_id, sched, fire, "QUEUED", trigger="MANUAL",
                      triggered_by=actor, run_key=run_key,
                      job_id=None)
    payload = render_payload(sched.payload_template or {}, fire)
    payload.update({"schedule_run_id": run.id, "schedule_id": sched.id,
                    "fire_time": fire.isoformat(), "manual": True})
    job = enqueue_job(db, tenant_id, sched.job_type,
                      f"{_sched_dedup_prefix(sched.id)}manual:"
                      f"{idempotency_key or run.id}", payload)
    run.job_id = job.id
    db.add(AuditEvent(
        id=new_uuid(), tenant_id=tenant_id, actor=actor,
        action="schedule.run_now", target=f"schedule:{sched.code}",
        new_ref={"run_id": run.id, "reason": reason.strip()}))
    db.flush()
    return run


def finalize_run(db, job: Job, state: str, error: dict | None = None) -> None:
    """Job 终态回写 run（engine _finalize_operation 调用；幂等、保留迟到痕迹）。

    RT-03 S01/S02 语义：
    - 耗时 = 首次执行至最终结束的墙钟秒数（含重试等待，不含排队等待）；
      不足 1 秒的真实任务允许为 0。
    - was_timed_out / was_stalled 分别保留——只写实际观察到的历史，不能对仅
      STALLED 的情况无依据地写超时史（反之亦然）；late 统一保留。
    - 历史缺 started_at 的旧记录不得伪造 started_at=ended_at 再宣称真实耗时：
      保留 None 并在 result_summary 标 started_at_unknown（本轮只处理正常新
      运行与安全兼容，不批量改历史账）。
    - 终态幂等：已是终态（含观测后已回写的行）不再改写。
    """
    payload = job.payload or {}
    run_id = payload.get("schedule_run_id")
    if not run_id:
        return
    run = db.get(ScheduleRun, run_id)
    if run is None or run.state in ("SUCCEEDED", "FAILED", "CANCELLED",
                                    "MISSED", "SKIPPED_OVERLAP",
                                    "SKIPPED_BLACKOUT"):
        return
    summary = dict(run.result_summary or {})
    observed_hist = str(summary.get("observed") or "")
    was_timed_out = run.state == "TIMED_OUT" or observed_hist == "TIMED_OUT"
    was_stalled = run.state == "STALLED" or observed_hist == "STALLED"
    late = run.state in ("TIMED_OUT", "STALLED")
    now = utcnow()
    run.state = "SUCCEEDED" if state == "SUCCEEDED" else "FAILED"
    run.ended_at = now
    if run.started_at is not None:
        run.duration_seconds = max(
            0, int((now - run.started_at).total_seconds()))
    else:
        # 历史行缺首次开跑时间：duration 未知（不伪造 started_at=ended_at）
        run.duration_seconds = None
        summary["started_at_unknown"] = True
    if late:
        summary["late"] = True
    if was_timed_out:
        summary["was_timed_out"] = True
    if was_stalled:
        summary["was_stalled"] = True
    if error:
        run.error_code = str(error.get("code", ""))[:64]
        run.error_message = str(error.get("message", ""))[:500]
        summary["error"] = {"code": run.error_code}
    run.result_summary = summary
