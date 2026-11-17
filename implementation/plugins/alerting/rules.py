"""告警规则（LT-04 C 段）：9 种业务 kind + DATA_UNAVAILABLE 另列。

每类 evaluate(db, tenant_id, rule, now) -> list[Finding]；无问题返回空。
观测状态（R05）：钩子不可用/数据缺失/过期副本 → DATA_UNAVAILABLE finding，
**不产生"正常"结论**，也不据此恢复既有业务告警。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from core_db.engine import utcnow


def _aware(dt):
    """评估时刻统一 aware（naive 视为 UTC——storage utcnow() 语义）。"""
    from datetime import timezone
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class Finding:
    fingerprint: str
    severity: str                    # INFO/WARN/CRITICAL
    title: str
    detail: dict = field(default_factory=dict)
    target_code: str = ""


KINDS = ("JOB_FAILED", "RUN_TIMEOUT", "RUN_STALLED", "SCHEDULE_SKIPPED",
         "NO_SUCCESS_WITHIN", "BACKLOG_OVER", "UNKNOWN_OVER", "REJECT_RATE_OVER",
         "DATE_EXPIRY", "WATERMARK_STALE", "PARENT_WAIT_OVER", "WATCHDOG_STALE")


def _summary_for(db, tenant_id: str, target_code: str, registry):
    """经 HOOK_TARGET_SUMMARY 取 summary；registry 缺失/无钩子→(None, "none")。"""
    if registry is None:
        return None, "none"
    try:
        s = registry.target_summary(db, tenant_id, target_code, 7)
    except Exception:  # noqa: BLE001 —— 钩子异常按不可用处理（评估器另记）
        return None, "error"
    if s is None:
        return None, "none"
    return s, "platform"


# ---------------------------------------------------------------- schedule 系
def _eval_job_failed(db, tenant_id, rule, now) -> list[Finding]:
    code = str((rule.params or {}).get("schedule_code", ""))
    if not code:
        return []
    from core_db.models import Schedule, ScheduleRun
    sched = (db.query(Schedule)
             .filter_by(tenant_id=tenant_id, code=code).first())
    if sched is None:
        return [Finding(f"job_failed:{code}", "INFO",
                        "调度不存在（规则待核）", {"schedule_code": code})]
    last = (db.query(ScheduleRun)
            .filter_by(schedule_id=sched.id)
            .order_by(ScheduleRun.fire_time.desc()).first())
    if last is not None and last.state == "FAILED":
        return [Finding(
            f"job_failed:{code}", rule.severity or "WARN",
            f"调度 {code} 最近一次运行 FAILED",
            {"schedule_code": code, "state": last.state,
             "run_key": last.run_key,
             "error_code": last.error_code or "-"})]
    return []


def _eval_run_state(db, tenant_id, rule, now, state: str) -> list[Finding]:
    from core_db.models import ScheduleRun
    rows = (db.query(ScheduleRun)
            .filter(ScheduleRun.tenant_id == tenant_id,
                    ScheduleRun.state == state).all())
    out = []
    for r in rows:
        out.append(Finding(
            f"run.{state.lower()}:{r.schedule_id}:{r.run_key}",
            rule.severity or "CRITICAL",
            f"运行{ '超时' if state == 'TIMED_OUT' else '停滞' }未恢复"
            f"（观测异常，Job 未被终止）",
            {"run_key": r.run_key,
             "deadline_at": r.deadline_at.isoformat()
             if r.deadline_at else None}))
    return out


def _eval_schedule_skipped(db, tenant_id, rule, now) -> list[Finding]:
    code = str((rule.params or {}).get("schedule_code", ""))
    need = int((rule.params or {}).get("consecutive", 3))
    if not code:
        return []
    from core_db.models import Schedule, ScheduleRun
    sched = (db.query(Schedule)
             .filter_by(tenant_id=tenant_id, code=code).first())
    if sched is None:
        return []
    runs = (db.query(ScheduleRun)
            .filter(ScheduleRun.schedule_id == sched.id,
                    ScheduleRun.state.in_(("SKIPPED_OVERLAP", "MISSED",
                                           "SKIPPED_BLACKOUT", "QUEUED",
                                           "RUNNING", "SUCCEEDED", "FAILED",
                                           "TIMED_OUT", "STALLED",
                                           "CANCELLED")))
            .order_by(ScheduleRun.fire_time.desc()).limit(need).all())
    if len(runs) >= need and all(
            r.state in ("SKIPPED_OVERLAP", "MISSED", "SKIPPED_BLACKOUT")
            for r in runs):
        return [Finding(
            f"skipped:{code}", rule.severity or "CRITICAL",
            f"调度 {code} 连续 {need} 次被跳过（对应'班次全被跳过无人知晓'）",
            {"schedule_code": code, "consecutive": need,
             "state": runs[0].state})]
    return []


# ---------------------------------------------------------------- summary 系
def _summary_findings(db, tenant_id, rule, now, registry):
    """summary 类规则共用：不可用→DATA_UNAVAILABLE INFO（不当'正常'）。"""
    target = rule.target_code or ""
    s, source = _summary_for(db, tenant_id, target, registry)
    if s is None or source == "none":
        return [Finding(f"data_unavailable:{target}", "INFO",
                        f"业务 {target} 无可用摘要数据（DATA_UNAVAILABLE）",
                        {"target": target, "state": source})], None
    return [], s


def _eval_no_success(db, tenant_id, rule, now, registry):
    miss, s = _summary_findings(db, tenant_id, rule, now, registry)
    if s is None:
        return miss
    hours = float((rule.params or {}).get("hours", 26))
    # PT-02 修复传染病 ACCEPTED 业务永久误报：summary 只有 last_success_at
    # （CONFIRMED 口径），而前置机 result=true 映射为 ACCEPTED——默认参数
    # accept_counts_as_activity=true 时回退读取 last_accepted_at（受理也算活动）。
    accept = bool((rule.params or {}).get("accept_counts_as_activity", False))
    last = getattr(s, "last_success_at", None)
    if not last and accept:
        last = getattr(s, "last_accepted_at", None)
    if not last:
        return [Finding(f"no_success:{rule.target_code}", rule.severity or "WARN",
                        f"业务 {rule.target_code} 从未有业务确认（CONFIRMED）",
                        {"hours": hours})]
    from datetime import datetime
    try:
        last_dt = datetime.fromisoformat(str(last))
    except ValueError:
        return miss
    if last_dt.tzinfo is None:
        from workflow_core.cron import CST
        last_dt = last_dt.replace(tzinfo=CST)
    age = (_aware(now) - last_dt).total_seconds() / 3600
    if age > hours:
        scope = "受理/确认" if (accept and str(last) == str(
            getattr(s, "last_accepted_at", "") or "")) else "业务确认"
        return [Finding(
            f"no_success:{rule.target_code}", rule.severity or "WARN",
            f"业务 {rule.target_code} 距最近{scope} {age:.0f}h（阈值 {hours}h）",
            {"hours": hours, "last_success_at": str(last)})]
    return []


def _eval_backlog(db, tenant_id, rule, now, registry):
    miss, s = _summary_findings(db, tenant_id, rule, now, registry)
    if s is None:
        return miss
    threshold = int((rule.params or {}).get("threshold", 500))
    backlog = s.backlog_total if s.backlog_total is not None else s.backlog
    if backlog > threshold:
        return [Finding(
            f"backlog:{rule.target_code}", rule.severity or "WARN",
            f"业务 {rule.target_code} 全量未解决积压 {backlog}（阈值 {threshold}）",
            {"threshold": threshold, "backlog_total": backlog,
             "backlog": s.backlog})]
    return []


def _eval_unknown(db, tenant_id, rule, now, registry):
    miss, s = _summary_findings(db, tenant_id, rule, now, registry)
    if s is None:
        return miss
    threshold = int((rule.params or {}).get("threshold", 1))
    n = int((s.buckets or {}).get("UNKNOWN", 0))
    if n >= threshold:
        return [Finding(
            f"unknown:{rule.target_code}", rule.severity or "WARN",
            f"业务 {rule.target_code} 窗口内 UNKNOWN {n}（阈值 {threshold}）"
            f"（evidence_kind={s.evidence_kind or 'unknown'}）",
            {"threshold": threshold, "count": n})]
    return []


def _eval_reject_rate(db, tenant_id, rule, now, registry):
    miss, s = _summary_findings(db, tenant_id, rule, now, registry)
    if s is None:
        return miss
    p = rule.params or {}
    rate = float(p.get("rate", 0.05))
    min_total = int(p.get("min_total", 50))
    b = s.buckets or {}
    total = sum(v for k, v in b.items() if k != "PENDING")
    if total < min_total:
        return []                       # 总数不足不判（宁缺勿误报）
    rejected = int(b.get("REJECTED", 0))
    if total > 0 and rejected / total > rate:
        return [Finding(
            f"reject_rate:{rule.target_code}", rule.severity or "WARN",
            f"业务 {rule.target_code} 拒绝率 {rejected}/{total} 超过 {rate}",
            {"rate": rate, "min_total": min_total, "count": rejected})]
    return []


def _eval_date_expiry(db, tenant_id, rule, now) -> list[Finding]:
    """DATE_EXPIRY：从**实际登记元数据**读有效期（不硬编码 11-22）。

    source=drug_trace.production_evidence：读最新 APPROVED 未撤销 send 证据的
    valid_until（scope.window_end 兜底），存 naive 按 CST 解释；无证据→
    INFO 待核（不算正常也不告警升级）。
    """
    from datetime import datetime
    from workflow_core.cron import CST
    p = rule.params or {}
    source = str(p.get("source", ""))
    label = str(p.get("label", "授权/证书"))
    warn_days = int(p.get("warn_days", 7))
    expires: datetime | None = None
    ev_id = ""
    if source == "drug_trace.production_evidence":
        try:
            from domains.drug_trace.models import \
                DrugTraceProductionEvidence as EVID
            ev = (db.query(EVID)
                  .filter(EVID.tenant_id == tenant_id,
                          EVID.capability == "send",
                          EVID.status == "APPROVED",
                          EVID.revoked_at.is_(None),
                          EVID.valid_until.isnot(None))
                  .order_by(EVID.valid_until.desc()).first())
            if ev is not None:
                expires = ev.valid_until
                ev_id = ev.id
        except Exception:  # noqa: BLE001 —— 模型未加载（非药品部署）
            return [Finding(f"date_expiry:{label}", "INFO",
                            f"{label}有效期待核（证据源不可用）",
                            {"label": label})]
    else:
        d = p.get("date")
        if d:
            try:
                expires = datetime.fromisoformat(str(d))
            except ValueError:
                return []
    if expires is None:
        return [Finding(f"date_expiry:{label}", "INFO",
                        f"{label}有效期无登记证据（待核，不算正常）",
                        {"label": label})]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=CST)
    days = (expires - _aware(now)).total_seconds() / 86400
    if days < 0:
        return [Finding(f"date_expiry:{label}", "CRITICAL",
                        f"{label}已过期 {-days:.1f} 天",
                        {"label": label, "days": round(days, 1),
                         "evidence_id": ev_id})]
    if days <= warn_days:
        return [Finding(f"date_expiry:{label}", "WARN",
                        f"{label}将在 {days:.1f} 天后到期",
                        {"label": label, "days": round(days, 1),
                         "evidence_id": ev_id})]
    return []


# ---------------------------------------------------------------- PT-02 新增 3 类
def _eval_watermark_stale(db, tenant_id, rule, now, registry):
    """WATERMARK_STALE：影子水位（InfectiousShadowCursor.source_cursor）滞后超 N 小时。

    params: {table: "ex_lab_item", hours: 6}；target_code=业务编码（inf_cdc_frontend）。
    水位行缺失 → DATA_UNAVAILABLE INFO（不算正常也不误报）；解析失败同。"""
    p = rule.params or {}
    table = str(p.get("table", "") or "")
    hours = float(p.get("hours", 6))
    if not table:
        return [Finding("watermark_stale:unconfigured", "INFO",
                        "水位滞后规则未配置 table 参数（规则待核）",
                        {"table": "", "hours": hours})]
    from core_db.models import InfectiousShadowCursor
    row = (db.query(InfectiousShadowCursor)
           .filter_by(tenant_id=tenant_id, target_code=rule.target_code,
                      stream_key=table).first())
    fp = f"watermark_stale:{rule.target_code}:{table}"
    if row is None or not row.source_cursor:
        return [Finding(fp, "INFO",
                        f"业务 {rule.target_code} 表 {table} 无影子水位记录"
                        "（DATA_UNAVAILABLE，不判正常）",
                        {"table": table, "hours": hours})]
    from datetime import datetime
    try:
        cur = datetime.fromisoformat(str(row.source_cursor))
    except ValueError:
        return [Finding(fp, "INFO",
                        f"表 {table} 水位格式不可解析（DATA_UNAVAILABLE）",
                        {"table": table, "observed": str(row.source_cursor)[:40]})]
    # 存储语义与 _aware 一致：naive 视为 UTC（core_db utcnow 口径）——
    # 误按 CST 解释会把 UTC 时间戳虚增 8 小时滞后（PT-02 单测锁定）
    cur = _aware(cur)
    age_h = (_aware(now) - cur).total_seconds() / 3600.0
    if age_h > hours:
        return [Finding(fp, rule.severity or "WARN",
                        f"业务 {rule.target_code} 表 {table} 水位滞后 {age_h:.1f}h"
                        f"（阈值 {hours}h）——子表采集停滞或源端无新数据",
                        {"table": table, "hours": hours,
                         "observed": str(row.source_cursor)})]
    return []


def _eval_parent_wait(db, tenant_id, rule, now, registry):
    """PARENT_WAIT_OVER：待父受理积压（影子 QUALITY_WAIT 且 last_error 为
    PARENT_WAIT 前缀）超 N 条——与 inf_watchdog c5_wait 形成平台侧交叉守护。"""
    threshold = int((rule.params or {}).get("threshold", 300))
    from core_db.models import InfectiousShadowRecord
    q = (db.query(InfectiousShadowRecord)
         .filter(InfectiousShadowRecord.tenant_id == tenant_id,
                 InfectiousShadowRecord.target_code == rule.target_code,
                 InfectiousShadowRecord.state == "QUALITY_WAIT",
                 InfectiousShadowRecord.last_error.like("PARENT_WAIT%")))
    n = q.count()
    if n >= threshold:
        return [Finding(f"parent_wait:{rule.target_code}", rule.severity or "WARN",
                        f"业务 {rule.target_code} 待父受理积压 {n} 条"
                        f"（阈值 {threshold}）——父单补录受理后自动补发",
                        {"threshold": threshold, "count": n})]
    return []


def _eval_watchdog_stale(db, tenant_id, rule, now, registry):
    """WATCHDOG_STALE：外部守护 status.json 超 N 分钟未刷新或 overall 为 CRITICAL
    ——平台与 inf_watchdog 双向交叉守护（守护自身停摆平台必须能看见）。

    params: {status_path: "<prod-path>/exchange/inf-watchdog/state/status.json",
             minutes: 30}；文件缺失 → DATA_UNAVAILABLE INFO。"""
    from datetime import datetime, timedelta
    from workflow_core.cron import CST
    p = rule.params or {}
    path = str(p.get("status_path", "") or "")
    minutes = float(p.get("minutes", 30))
    fp = "watchdog_stale:inf"
    if not path:
        return [Finding(fp, "INFO",
                        "外部守护交叉监控规则未配置 status_path 参数（规则待核）",
                        {"minutes": minutes})]
    try:
        import json as _json
        with open(path, "r", encoding="utf-8") as fh:
            st = _json.load(fh)
    except OSError:
        return [Finding(fp, "INFO",
                        "外部守护状态文件不可读（DATA_UNAVAILABLE——守护未部署或路径待核）",
                        {"minutes": minutes, "source_ref": path})]
    except ValueError:
        return [Finding(fp, "WARN", "外部守护状态文件解析失败（status.json 非法 JSON）",
                        {"minutes": minutes, "source_ref": path})]
    ts = str(st.get("ts", "") or "")
    findings = []
    overall = str(st.get("level", "") or "").upper()
    if overall == "CRITICAL":
        findings.append(Finding(fp, "CRITICAL",
                                "外部守护 inf_watchdog 自身报告 CRITICAL——推送链路故障"
                                "（平台侧交叉守护）",
                                {"minutes": minutes, "observed": overall,
                                 "source_ref": path}))
    if ts:
        try:
            ts_dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CST)
            age_min = (_aware(now) - ts_dt).total_seconds() / 60.0
            if age_min > minutes:
                findings.append(Finding(
                    fp, rule.severity or "WARN",
                    f"外部守护状态已 {age_min:.0f} 分钟未刷新（阈值 {minutes} 分钟）"
                    "——守护自身停摆（timer/服务异常）",
                    {"minutes": minutes, "observed": ts}))
        except ValueError:
            findings.append(Finding(fp, "INFO",
                                    "外部守护状态时间戳不可解析（DATA_UNAVAILABLE）",
                                    {"observed": ts[:40]}))
    return findings


_EVALUATORS = {
    "JOB_FAILED": lambda db, t, r, now, reg: _eval_job_failed(db, t, r, now),
    "RUN_TIMEOUT": lambda db, t, r, now, reg: _eval_run_state(
        db, t, r, now, "TIMED_OUT"),
    "RUN_STALLED": lambda db, t, r, now, reg: _eval_run_state(
        db, t, r, now, "STALLED"),
    "SCHEDULE_SKIPPED": lambda db, t, r, now, reg: _eval_schedule_skipped(
        db, t, r, now),
    "NO_SUCCESS_WITHIN": _eval_no_success,
    "BACKLOG_OVER": _eval_backlog,
    "UNKNOWN_OVER": _eval_unknown,
    "REJECT_RATE_OVER": _eval_reject_rate,
    "DATE_EXPIRY": lambda db, t, r, now, reg: _eval_date_expiry(db, t, r, now),
    "WATERMARK_STALE": _eval_watermark_stale,
    "PARENT_WAIT_OVER": _eval_parent_wait,
    "WATCHDOG_STALE": _eval_watchdog_stale,
}


def evaluate_rule(db, tenant_id: str, rule, now, registry=None) -> list[Finding]:
    fn = _EVALUATORS.get(rule.kind)
    if fn is None:
        raise ValueError(f"unknown rule kind {rule.kind!r}")
    return fn(db, tenant_id, rule, now, registry)
