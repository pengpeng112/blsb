"""调度与告警 API（LT-04 A5/C7；RT-03 S08 真实 keyset 分页）。

权限：读=任意登录；调度写/规则写/通道写=platform_admin（enable/disable 与
run-now 另放 operator）；通知日志=platform_admin/auditor。所有写操作落
AuditEvent（服务层或路由层）。

RT-03 S08：四个列表接口走 contracts.api 的严格 keyset 分页（kc1 版本游标，
绑定资源/租户/筛选上下文；坏游标 400 INVALID_CURSOR、换上下文 400
CURSOR_CONTEXT_MISMATCH，不回退首页）。查询顺序：租户/scope/筛选 → 游标
条件 → order_by → limit+1；next_cursor 只由最后一条实际返回记录生成。
分页不是安全授权：解码后仍按租户与 RT-02 范围防线过滤。
alerts2 为**实时列表分页**：last_seen_at 会变化，不承诺并发更新期间的
数据库快照；前端按 ID 合并去重、刷新/改筛选重置游标（数据静止时严格
无重无漏）。severity 按 lower 值归一返回 INFO/WARN/CRITICAL（warning→
WARN）；旧 /api/v1/alerts 保持数组契约（app.py，共享本文件的过滤/归一/
范围防线助手）。
"""
from __future__ import annotations

from fastapi import Depends

from contracts import ok
from contracts.api import (ROLE_BUSINESS_REVIEWER, ROLE_CONFIG_EDITOR,  # noqa: F401
                           ROLE_PLATFORM_ADMIN, audit, keyset_condition,
                           keyset_cursor_decode, keyset_cursor_encode,
                           keyset_next_cursor, normalize_severity,
                           severity_filter_set)
from core_db.engine import utcnow
from core_db.models import (AlertEvent, AlertRule, NotificationChannel,
                            NotificationLog, Schedule, ScheduleRun)
from plugins import report_targets as RT
from workflow_core.cron import CronExpr


def _require_scope_or_global(p, target_code: str) -> None:
    """RT-02：对象级范围校验——系统级对象（target_code 为空）要求全局范围，
    业务对象要求该业务授权。机器主体与 platform_admin 不受限（语义同 R08）。"""
    if target_code:
        p.require_target_scope(target_code)
        return
    sc = p.target_scopes_effective()
    if sc is not None and "*" not in sc:
        from contracts import ApiError
        raise ApiError("FORBIDDEN", "系统级对象仅全局范围账号可操作")


def _sched_json(s: Schedule) -> dict:
    return {
        "id": s.id, "code": s.code, "name": s.name, "target_code": s.target_code,
        "job_type": s.job_type, "payload_template": s.payload_template or {},
        "cron": s.cron, "enabled": bool(s.enabled),
        "observe_only": bool(s.observe_only),
        "misfire_policy": s.misfire_policy, "concurrency": s.concurrency,
        "max_runtime_seconds": s.max_runtime_seconds, "blackout": s.blackout or [],
        "next_fire_at": s.next_fire_at.isoformat() if s.next_fire_at else None,
        "last_fire_at": s.last_fire_at.isoformat() if s.last_fire_at else None,
        "revision": s.revision, "note": s.note,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "updated_by": s.updated_by,
    }


def _run_json(r: ScheduleRun) -> dict:
    return {
        "id": r.id, "schedule_id": r.schedule_id, "run_key": r.run_key,
        "fire_time": r.fire_time.isoformat() if r.fire_time else None,
        "state": r.state, "job_id": r.job_id,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "ended_at": r.ended_at.isoformat() if r.ended_at else None,
        "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None,
        "duration_seconds": r.duration_seconds,
        "result_summary": r.result_summary or {},
        "error_code": r.error_code, "error_message": r.error_message,
        "trigger": r.trigger, "triggered_by": r.triggered_by,
    }


def alerts_base_query(db, principal, *, state: str = "", severity: str = "",
                      target: str = ""):
    """RT-03 S08：alerts2 与旧 /api/v1/alerts（app.py）共享的过滤+范围防线。

    severity 按 lower 值归一集合（WARN 覆盖 warn/warning 存储）；受限账号
    不见未授权业务与系统级 target_code=''（RT-02 防线）；机器 token、
    ["*"] 全局范围与 platform_admin 不收缩（含系统级——与旧端点 100% 一致）。"""
    q = db.query(AlertEvent).filter_by(tenant_id=principal.tenant_id)
    if state:
        q = q.filter(AlertEvent.state == state)
    sev_set = severity_filter_set(severity)
    if sev_set:
        # lower 归一匹配：旧库存在 'Warning'/'WARN' 大小写混合存储（S08）
        from sqlalchemy import func
        q = q.filter(func.lower(AlertEvent.severity).in_(sev_set))
    sc = principal.target_scopes_effective()
    if target:
        principal.require_target_scope(target)
        q = q.filter(AlertEvent.target_code == target)
    elif sc is not None and "*" not in sc:
        # R08：受限用户不见未授权业务（含系统级 target_code="" 告警）
        allowed = [t.code for t in RT.list_targets(db, principal.tenant_id)
                   if t.code in sc]
        q = q.filter(AlertEvent.target_code.in_(allowed or ["__none__"]))
    return q


def register(app, sf, auth, registry):
    from fastapi import Body, Query
    from scheduling import scheduler as SCH

    # ================================================================ 调度
    @app.get("/api/v1/schedules/meta")
    def schedules_meta(ctx=Depends(auth)):
        """RT-05 U03：调度表单元数据（只读）——job 类型=真实 registry
        已注册集合（不展示未注册类型让用户提交后才发现无处理器）。"""
        p = ctx.require_principal()
        return ok({
            "job_types": sorted(SCH.known_job_types(registry)),
            "misfire_policies": ["SKIP", "RUN_ONCE"],
            "concurrency_modes": ["FORBID", "ALLOW"],
        }, ctx.request_id)

    @app.get("/api/v1/schedules")
    def list_schedules(target: str = Query(""), ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(Schedule).filter_by(tenant_id=p.tenant_id)
            if target:
                p.require_target_scope(target)
                q = q.filter_by(target_code=target)
            elif p.target_scopes_effective() is not None:
                # R08：受限用户只见授权业务的调度（系统调度 target_code=""
                # 为平台全局 → 不返回）
                sc = p.target_scopes_effective()
                allowed = [t.code for t in RT.list_targets(db, p.tenant_id)
                           if "*" in sc or t.code in sc]
                q = q.filter(Schedule.target_code.in_(allowed or ["__none__"]))
            return ok([_sched_json(s) for s in q.order_by(Schedule.code).all()],
                      ctx.request_id)

    @app.post("/api/v1/schedules")
    def create_schedule(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        from contracts import ApiError
        with sf() as db:
            existing = db.query(Schedule).filter_by(
                tenant_id=p.tenant_id, code=str(body.get("code", ""))).first()
            if existing is not None:
                raise ApiError("CONFLICT", "schedule code already exists")
            s = SCH.upsert_schedule(db, p.tenant_id, body,
                                    actor=p.subject_code, registry=registry)
            db.commit()
            return ok(_sched_json(s), ctx.request_id)

    @app.put("/api/v1/schedules/{schedule_id}")
    def update_schedule(schedule_id: str, body: dict = Body(...),
                        ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        from contracts import ApiError
        if "revision" not in body:
            raise ApiError("VALIDATION_FAILED",
                           "body must carry current revision (optimistic lock)")
        body = dict(body)
        body["code"] = db_code_of(sf, p.tenant_id, schedule_id)
        with sf() as db:
            s = SCH.upsert_schedule(db, p.tenant_id, body,
                                    actor=p.subject_code,
                                    expected_revision=body.get("revision"),
                                    registry=registry)
            db.commit()
            return ok(_sched_json(s), ctx.request_id)

    @app.post("/api/v1/schedules/{schedule_id}/enable")
    def enable_schedule(schedule_id: str, body: dict = Body(...),
                        ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN, "operator")
        with sf() as db:
            _sched = db.get(Schedule, schedule_id)
            if _sched is not None:
                _require_scope_or_global(p, _sched.target_code or "")  # RT-02
            s = SCH.set_enabled(db, p.tenant_id, schedule_id, enabled=True,
                                actor=p.subject_code,
                                reason=str(body.get("reason", "")))
            db.commit()
            return ok(_sched_json(s), ctx.request_id)

    @app.post("/api/v1/schedules/{schedule_id}/disable")
    def disable_schedule(schedule_id: str, body: dict = Body(...),
                         ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN, "operator")
        with sf() as db:
            _sched = db.get(Schedule, schedule_id)
            if _sched is not None:
                _require_scope_or_global(p, _sched.target_code or "")  # RT-02
            s = SCH.set_enabled(db, p.tenant_id, schedule_id, enabled=False,
                                actor=p.subject_code,
                                reason=str(body.get("reason", "")))
            db.commit()
            return ok(_sched_json(s), ctx.request_id)

    @app.post("/api/v1/schedules/{schedule_id}/run-now")
    def run_now(schedule_id: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN, "operator")
        with sf() as db:
            _sched = db.get(Schedule, schedule_id)
            if _sched is not None:
                _require_scope_or_global(p, _sched.target_code or "")  # RT-02
            run = SCH.run_now(db, p.tenant_id, schedule_id,
                              actor=p.subject_code,
                              reason=str(body.get("reason", "")),
                              idempotency_key=str(body.get("idempotency_key",
                                                           "")),
                              registry=registry)
            db.commit()
            return ok(_run_json(run), ctx.request_id)

    @app.get("/api/v1/schedules/{schedule_id}/runs")
    def list_runs(schedule_id: str, limit: int = Query(50),
                  cursor: str = Query(""), ctx=Depends(auth)):
        """运行记录（RT-03 S08：fire_time DESC, id DESC 真实 keyset；
        保留 schedule/items 形状，增加 next_cursor）。"""
        p = ctx.require_principal()
        with sf() as db:
            s = db.get(Schedule, schedule_id)
            if s is None or s.tenant_id != p.tenant_id:
                from contracts import ApiError
                raise ApiError("NOT_FOUND", "schedule not found")
            _require_scope_or_global(p, s.target_code or "")  # RT-02：系统级同拦
            context = {"schedule_id": schedule_id,
                       "target": s.target_code or ""}
            cur = None
            if cursor:
                cur = keyset_cursor_decode(
                    cursor, resource="schedule_runs", tenant_id=p.tenant_id,
                    context=context)
            q = db.query(ScheduleRun).filter_by(schedule_id=schedule_id)
            if cur:
                q = q.filter(keyset_condition(
                    ScheduleRun.fire_time, ScheduleRun.id, cur[0], cur[1]))
            lim = min(200, max(1, limit))
            rows = (q.order_by(ScheduleRun.fire_time.desc(), ScheduleRun.id.desc())
                    .limit(lim + 1).all())
            has_more = len(rows) > lim
            rows = rows[:lim]
            return ok({"schedule": s.code,
                       "items": [_run_json(r) for r in rows],
                       "next_cursor": keyset_next_cursor(
                           rows, has_more, resource="schedule_runs",
                           tenant_id=p.tenant_id, context=context,
                           ts_attr="fire_time")}, ctx.request_id)

    @app.get("/api/v1/schedules/preview")
    def preview_cron(cron: str = Query(...), count: int = Query(5),
                     ctx=Depends(auth)):
        p = ctx.require_principal()
        from contracts import ApiError
        from workflow_core.cron import CST
        try:
            expr = CronExpr(cron)
            nxt = expr.preview(utcnow().replace(
                tzinfo=__import__("datetime").timezone.utc),
                count=min(10, max(1, count)))
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED", f"cron 非法：{exc}")
        return ok({"cron": cron,
                   "next": [t.astimezone(CST).isoformat() for t in nxt]},
                  ctx.request_id)

    # ================================================================ 告警
    @app.get("/api/v1/alerts2")
    def list_alerts(state: str = Query(""), severity: str = Query(""),
                    target: str = Query(""), limit: int = Query(50),
                    cursor: str = Query(""), ctx=Depends(auth)):
        """告警聚合视图（RT-03 S08：有效时间 COALESCE(last_seen_at, created_at)
        DESC, id DESC 真实 keyset；数组改 items/next_cursor 分页对象）。

        实时列表分页：last_seen_at 随评估变化，不承诺并发更新期间的数据库
        快照；前端按 ID 合并去重、刷新/改筛选重置游标；数据静止时严格
        无重无漏。severity 归一返回 INFO/WARN/CRITICAL。"""
        p = ctx.require_principal()
        with sf() as db:
            q = alerts_base_query(db, p, state=state, severity=severity,
                                  target=target)
            context = {"state": state,
                       "severity": (normalize_severity(severity)
                                    if str(severity).strip() else ""),
                       "target": target}
            cur = None
            if cursor:
                cur = keyset_cursor_decode(
                    cursor, resource="alerts2", tenant_id=p.tenant_id,
                    context=context)
            from sqlalchemy import func
            eff = func.coalesce(AlertEvent.last_seen_at, AlertEvent.created_at)
            if cur:
                q = q.filter(keyset_condition(eff, AlertEvent.id,
                                              cur[0], cur[1]))
            lim = min(200, max(1, limit))
            rows = (q.order_by(eff.desc(), AlertEvent.id.desc())
                    .limit(lim + 1).all())
            has_more = len(rows) > lim
            rows = rows[:lim]
            next_cursor = None
            if has_more and rows:
                last = rows[-1]
                next_cursor = keyset_cursor_encode(
                    resource="alerts2", tenant_id=p.tenant_id,
                    context=context,
                    ts=last.last_seen_at or last.created_at,
                    row_id=last.id)
            return ok({"items": [_alert_json(e) for e in rows],
                       "next_cursor": next_cursor}, ctx.request_id)

    @app.post("/api/v1/alerts2/{alert_id}/ack")
    def ack_alert(alert_id: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role("operator", ROLE_BUSINESS_REVIEWER,
                           ROLE_PLATFORM_ADMIN)
        from contracts import ApiError
        with sf() as db:
            ev = db.get(AlertEvent, alert_id)
            if ev is None or ev.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "alert not found")
            _require_scope_or_global(p, ev.target_code or "")  # RT-02：系统级同拦
            if ev.resolved_at is not None:
                raise ApiError("CONFLICT", "alert already resolved")
            ev.acked_by = p.subject_code
            ev.acked_at = utcnow()
            ev.detail = {**(ev.detail or {}),
                         "ack_reason": str(body.get("reason", ""))[:200]}
            audit(db, p, "alert.ack", f"alert_event:{ev.id}",      # RT-02 审计
                  new_ref={"state": ev.state, "severity": ev.severity,
                           "target_code": ev.target_code or "",
                           "ack_reason": str(body.get("reason", ""))[:200]},
                  trace_id=ctx.request_id)
            db.commit()
            return ok(_alert_json(ev), ctx.request_id)

    @app.post("/api/v1/alerts2/{alert_id}/resolve")
    def resolve_alert(alert_id: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role("operator", ROLE_BUSINESS_REVIEWER,
                           ROLE_PLATFORM_ADMIN)
        from contracts import ApiError
        with sf() as db:
            ev = db.get(AlertEvent, alert_id)
            if ev is None or ev.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "alert not found")
            _require_scope_or_global(p, ev.target_code or "")  # RT-02：系统级同拦
            if ev.resolved_at is not None:
                raise ApiError("CONFLICT", "alert already resolved")
            # 人工类（DATE_EXPIRY 等）允许人工关闭；业务类必须由评估器恢复——
            # 人工关闭业务告警会掩盖持续异常，本批限制为 kind 白名单
            if ev.alert_type not in ("DATE_EXPIRY", "EVALUATOR_ERROR"):
                raise ApiError("VALIDATION_FAILED",
                               "仅 DATE_EXPIRY/评估器异常类允许人工关闭；"
                               "业务告警由评估器证据完整恢复")
            ev.resolved_at = utcnow()
            ev.state = "RESOLVED"
            ev.detail = {**(ev.detail or {}),
                         "resolve_reason": str(body.get("reason", ""))[:200],
                         "resolved_by": p.subject_code}
            audit(db, p, "alert.resolve", f"alert_event:{ev.id}",  # RT-02 审计
                  new_ref={"alert_type": ev.alert_type, "state": ev.state,
                           "target_code": ev.target_code or "",
                           "resolve_reason": str(body.get("reason", ""))[:200]},
                  trace_id=ctx.request_id)
            db.commit()
            return ok(_alert_json(ev), ctx.request_id)

    @app.get("/api/v1/alert-rules")
    def list_rules(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(AlertRule).filter_by(tenant_id=p.tenant_id)
                    .order_by(AlertRule.code).all())
            sc = p.target_scopes_effective()
            if sc is not None:
                # R08：受限用户只见授权业务的规则（无 target 的系统规则不返回）
                rows = [r for r in rows
                        if r.target_code and ("*" in sc or r.target_code in sc)]
            return ok([_rule_json(r) for r in rows], ctx.request_id)

    @app.post("/api/v1/alert-rules")
    def create_rule(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        from contracts import ApiError, new_uuid
        from alerting.rules import KINDS
        # RT-02：新建规则的范围校验——系统级规则（target_code 空）要求全局
        # 范围，业务规则要求该业务授权
        _require_scope_or_global(p, str(body.get("target_code", "") or ""))
        with sf() as db:
            code = str(body.get("code", "") or "")
            if not code:
                raise ApiError("VALIDATION_FAILED", "code 必填")
            if body.get("kind") not in KINDS:
                raise ApiError("VALIDATION_FAILED",
                               f"kind 必须是 {'/'.join(KINDS)}")
            if db.query(AlertRule).filter_by(
                    tenant_id=p.tenant_id, code=code).first() is not None:
                raise ApiError("CONFLICT", "rule code already exists")
            r = AlertRule(id=new_uuid(), tenant_id=p.tenant_id, code=code,
                          updated_by=p.subject_code, updated_at=utcnow(),
                          created_at=utcnow(), cooldown_minutes=60,
                          notify_channels=["file_default"],
                          **{k: body[k] for k in
                             ("name", "target_code", "kind", "params",
                              "severity", "enabled") if k in body})
            db.add(r)
            audit(db, p, "alert_rule.create", f"alert_rule:{code}",  # RT-02 审计
                  new_ref={"code": code, "kind": r.kind,
                           "target_code": r.target_code or "",
                           "severity": r.severity, "enabled": bool(r.enabled)},
                  trace_id=ctx.request_id)
            db.commit()
            return ok(_rule_json(r), ctx.request_id)

    @app.put("/api/v1/alert-rules/{code}")
    def update_rule(code: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN, ROLE_CONFIG_EDITOR)
        from contracts import ApiError
        with sf() as db:
            r = db.query(AlertRule).filter_by(
                tenant_id=p.tenant_id, code=code).first()
            if r is None:
                raise ApiError("NOT_FOUND", "rule not found")
            # RT-02：原规则与新 target_code 都要过范围校验——系统级（空
            # target_code）要求全局范围，受限 config_editor 不得篡改他人或
            # 全局规则，也不得把规则改挂到未授权业务
            _require_scope_or_global(p, str(r.target_code or ""))
            if "target_code" in body:
                _require_scope_or_global(p, str(body.get("target_code") or ""))
            if "revision" not in body or int(body["revision"]) != int(r.revision):
                raise ApiError("CONFLICT", "revision mismatch（乐观锁）")
            _old = {"code": r.code, "name": r.name,
                    "target_code": r.target_code or "", "kind": r.kind,
                    "severity": r.severity, "enabled": bool(r.enabled),
                    "revision": int(r.revision)}
            for k in ("name", "target_code", "params", "severity", "enabled",
                      "cooldown_minutes", "notify_channels"):
                if k in body:
                    setattr(r, k, body[k])
            r.revision = int(r.revision) + 1
            r.updated_by = p.subject_code
            r.updated_at = utcnow()
            audit(db, p, "alert_rule.update", f"alert_rule:{code}",  # RT-02 审计
                  old_ref=_old,
                  new_ref={"code": r.code, "name": r.name,
                           "target_code": r.target_code or "", "kind": r.kind,
                           "severity": r.severity, "enabled": bool(r.enabled),
                           "revision": int(r.revision)},
                  trace_id=ctx.request_id)
            db.commit()
            return ok(_rule_json(r), ctx.request_id)

    # ================================================================ 通道/日志
    @app.get("/api/v1/notification-channels")
    def list_channels(ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            rows = (db.query(NotificationChannel)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(NotificationChannel.code).all())
            return ok([_channel_json(c) for c in rows], ctx.request_id)

    @app.post("/api/v1/notification-channels")
    def create_channel(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        from contracts import ApiError, new_uuid
        from alerting.channels import (assert_channel_change_allowed,
                                       validate_channel_config)
        with sf() as db:
            code = str(body.get("code", "") or "")
            ctype = str(body.get("type", "") or "")
            config = body.get("config") or {}
            if not code:
                raise ApiError("VALIDATION_FAILED", "code 必填")
            if db.query(NotificationChannel).filter_by(
                    tenant_id=p.tenant_id, code=code).first() is not None:
                raise ApiError("CONFLICT", "channel code already exists")
            validate_channel_config(ctype, config)
            enabling = bool(body.get("enabled", False))
            assert_channel_change_allowed(ctype, enabling=enabling)
            c = NotificationChannel(id=new_uuid(), tenant_id=p.tenant_id,
                                    code=code, type=ctype, config=config,
                                    enabled=enabling, updated_by=p.subject_code,
                                    updated_at=utcnow())
            db.add(c)
            audit(db, p, "notification_channel.create",           # RT-02 审计
                  f"notification_channel:{code}",
                  new_ref={"code": code, "type": ctype,
                           "enabled": enabling},   # config 不入审计（防敏感值）
                  trace_id=ctx.request_id)
            db.commit()
            return ok(_channel_json(c), ctx.request_id)

    @app.put("/api/v1/notification-channels/{code}")
    def update_channel(code: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        from contracts import ApiError
        from alerting.channels import (assert_channel_change_allowed,
                                       validate_channel_config)
        with sf() as db:
            c = db.query(NotificationChannel).filter_by(
                tenant_id=p.tenant_id, code=code).first()
            if c is None:
                raise ApiError("NOT_FOUND", "channel not found")
            _old = {"code": c.code, "type": c.type, "enabled": bool(c.enabled)}
            if "config" in body:
                validate_channel_config(c.type, body["config"])
                c.config = body["config"]
            if "enabled" in body:
                assert_channel_change_allowed(c.type,
                                              enabling=bool(body["enabled"]))
                c.enabled = bool(body["enabled"])
            c.updated_by = p.subject_code
            c.updated_at = utcnow()
            audit(db, p, "notification_channel.update",           # RT-02 审计
                  f"notification_channel:{code}", old_ref=_old,
                  new_ref={"code": c.code, "type": c.type,
                           "enabled": bool(c.enabled)},
                  trace_id=ctx.request_id)
            db.commit()
            return ok(_channel_json(c), ctx.request_id)

    @app.post("/api/v1/notification-channels/{code}/test")
    def test_channel(code: str, dry_run: bool = Query(False),
                     probe: bool = Query(False), ctx=Depends(auth)):
        """通道验证（PT-02 三态）：
        - dry_run=true：零出网渲染预览（无条件开放，写 DRY_RUN_OK 日志+审计）；
        - probe=true：网关真达探测（仅 wecom_relay；管理员 + 外发开关）；
        - 缺省：实发测试（smtp/webhook 拒绝；wecom 须 BLYB_ALLOW_EXTERNAL_NOTIFY=1）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        from contracts import ApiError, new_uuid
        from alerting.channels import (assert_channel_change_allowed,
                                       external_notify_allowed, probe_relay,
                                       render_preview, send)
        with sf() as db:
            c = db.query(NotificationChannel).filter_by(
                tenant_id=p.tenant_id, code=code).first()
            if c is None:
                raise ApiError("NOT_FOUND", "channel not found")
            if dry_run:
                # PT-02：零出网预览（本地/未授权环境验证配置的主路径）
                sample = ("【严重】示例业务 · 示例规则\n"
                          "所属业务：inf_cdc_frontend\n触发规则：示例规则\n"
                          "当前现象：count=3（演示数据）\n"
                          "首次发现：刚刚 / 持续 0 分钟\n"
                          "处置指引：本条为预览渲染，不会向任何企微用户发送。")
                preview = render_preview(c, sample, kind="TEST")
                db.add(NotificationLog(
                    id=new_uuid(), tenant_id=p.tenant_id, channel_code=c.code,
                    alert_event_id=None, sent_at=utcnow(), ok=True,
                    error="", kind="DRY_RUN_OK",
                    detail={"title": redact_text(sample[:60]),
                            "type": c.type}))
                audit(db, p, "notification_channel.dry_run",
                      f"notification_channel:{code}",
                      new_ref={"type": c.type}, trace_id=ctx.request_id)
                db.commit()
                return ok({"ok": True, "dry_run": True, "preview": preview},
                          ctx.request_id)
            if probe:
                if c.type != "wecom_relay":
                    raise ApiError("VALIDATION_FAILED",
                                   "仅 wecom_relay 支持网关真达探测（无签名空包校验 401）")
                if not external_notify_allowed():
                    raise ApiError("EXTERNAL_NOTIFY_DISABLED",
                                   "连通性探测会真实访问网关地址，需开启外发开关"
                                   "（BLYB_ALLOW_EXTERNAL_NOTIFY=1）后使用")
                ok_p, detail_p = probe_relay(c)
                db.add(NotificationLog(
                    id=new_uuid(), tenant_id=p.tenant_id, channel_code=c.code,
                    alert_event_id=None, sent_at=utcnow(), ok=bool(ok_p),
                    error="" if ok_p else detail_p[:500], kind="TEST",
                    detail={"probe": True}))
                db.commit()
                return ok({"ok": bool(ok_p), "probe": True,
                           "detail": detail_p}, ctx.request_id)
            # 实发测试（本批 smtp/webhook 拒绝；wecom 须外发开关）
            assert_channel_change_allowed(c.type, enabling=False, testing=True)
            err = send(c, "BLYB 告警通道测试", kind="TEST")
            db.add(NotificationLog(
                id=new_uuid(), tenant_id=p.tenant_id, channel_code=c.code,
                alert_event_id=None, sent_at=utcnow(), ok=(err == ""),
                error=err[:500], kind="TEST", detail={}))
            db.commit()
            return ok({"ok": err == "", "error": err}, ctx.request_id)

    @app.get("/api/v1/notification-logs")
    def list_notify_logs(kind: str = Query(""), limit: int = Query(50),
                         cursor: str = Query(""), ctx=Depends(auth)):
        """通知日志（RT-03 S08：sent_at DESC, id DESC 真实 keyset；补 cursor
        参数，数组改 items/next_cursor 分页对象；行内补 id 供前端稳定 key）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN, "auditor")
        with sf() as db:
            q = db.query(NotificationLog).filter_by(tenant_id=p.tenant_id)
            if kind:
                q = q.filter_by(kind=kind)
            context = {"kind": kind}
            cur = None
            if cursor:
                cur = keyset_cursor_decode(
                    cursor, resource="notify_logs", tenant_id=p.tenant_id,
                    context=context)
            if cur:
                q = q.filter(keyset_condition(
                    NotificationLog.sent_at, NotificationLog.id,
                    cur[0], cur[1]))
            lim = min(200, max(1, limit))
            rows = (q.order_by(NotificationLog.sent_at.desc(),
                               NotificationLog.id.desc())
                    .limit(lim + 1).all())
            has_more = len(rows) > lim
            rows = rows[:lim]
            return ok({"items": [{"id": r.id, "channel": r.channel_code,
                                  "ok": bool(r.ok), "error": r.error,
                                  "kind": r.kind,
                                  "sent_at": r.sent_at.isoformat()}
                                 for r in rows],
                       "next_cursor": keyset_next_cursor(
                           rows, has_more, resource="notify_logs",
                           tenant_id=p.tenant_id, context=context,
                           ts_attr="sent_at")}, ctx.request_id)

    @app.get("/api/v1/alerts/digest/latest")
    def latest_digest(ctx=Depends(auth)):
        p = ctx.require_principal()
        if p.target_scopes_effective() is not None:
            # R08：晨报为全院聚合文本（含未授权业务名）→ 受限用户不返回
            from contracts import ApiError
            raise ApiError("FORBIDDEN",
                           "晨报为全院聚合内容，当前账号业务范围不包含全部业务")
        with sf() as db:
            row = (db.query(NotificationLog)
                   .filter_by(tenant_id=p.tenant_id, kind="DIGEST",
                              ok=True)
                   .order_by(NotificationLog.sent_at.desc()).first())
            if row is None:
                return ok({"text": None, "sent_at": None}, ctx.request_id)
            return ok({"text": (row.detail or {}).get("text"),
                       "sent_at": row.sent_at.isoformat()}, ctx.request_id)


def db_code_of(sf, tenant_id: str, schedule_id: str) -> str:
    with sf() as db:
        s = db.get(Schedule, schedule_id)
        if s is None or s.tenant_id != tenant_id:
            from contracts import ApiError
            raise ApiError("NOT_FOUND", "schedule not found")
        return s.code


def _alert_json(e: AlertEvent) -> dict:
    return {
        "id": e.id, "alert_type": e.alert_type,
        "severity": normalize_severity(e.severity),   # RT-03：归一 INFO/WARN/CRITICAL
        "message": e.message,
        "target_ref": e.target_ref, "state": e.state,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "fingerprint": e.fingerprint, "rule_code": e.rule_code,
        "target_code": e.target_code,
        "first_seen_at": e.first_seen_at.isoformat() if e.first_seen_at else None,
        "last_seen_at": e.last_seen_at.isoformat() if e.last_seen_at else None,
        "occurrences": e.occurrences, "acked_by": e.acked_by,
        "acked_at": e.acked_at.isoformat() if e.acked_at else None,
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "detail": e.detail or {},
    }


def _rule_json(r: AlertRule) -> dict:
    return {"code": r.code, "name": r.name, "target_code": r.target_code,
            "kind": r.kind, "params": r.params or {},
            "severity": r.severity, "enabled": bool(r.enabled),
            "cooldown_minutes": r.cooldown_minutes,
            "notify_channels": r.notify_channels or [],
            "revision": r.revision,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _channel_json(c: NotificationChannel) -> dict:
    cfg = dict(c.config or {})
    # *_env 只回显变量名（本就无值）；再兜底剔除任何秘密语义键的值
    out_cfg = {k: v for k, v in cfg.items()
               if not any(s in str(k).lower() for s in
                          ("password", "secret", "token"))}
    return {"code": c.code, "type": c.type, "config": out_cfg,
            "enabled": bool(c.enabled),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}
