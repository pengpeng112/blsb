"""告警评估器（LT-04 C 段；RT-03 S05/S06 深修；PT-02 升级穿透/恢复时长/风暴聚合/静默窗）。

去重/防轰（R05）：finding 指纹=tenant+rule+target+对象；同一未解决事件
occurrences 累加，通知按 cooldown 间隔防重；恢复**仅**在本次评估
HEALTHY 且证据完整时发生——数据缺失/过期副本/钩子异常（UNAVAILABLE/
STALE/ERROR）绝不恢复既有业务告警。ACKED 事件不再重复通知但恢复照发。
评估器单规则异常隔离：记 self 事件继续下一条。

RT-03 S05（self 监控与业务告警隔离）：
- self 指纹 = ``alerting.self:{tenant_id}:{rule.code}``（列长 ≤128 校验），
  A/B 规则、跨租户互不影响；
- self 恢复条件 = 该规则本轮评估**没有抛异常**（即使本轮仍发现业务问题，
  评估器自身已恢复，不得继续报评估器故障）；
- 业务恢复条件 = 无任何 finding（HEALTHY 且证据完整）——两套恢复独立，
  业务恢复循环显式排除 self 事件。

RT-03 S06（入库脱敏）：finding message/detail、持续更新、self、恢复通知、
NotificationLog.error/detail 全部过 sanitize/redact；last_notified_at 在
白名单内，冷却判定不受脱敏影响（不丢通知节流信息→防通知风暴）。

PT-02（企业微信升级配套）：
- **WARN→CRITICAL 升级穿透**：已有 OPEN/ACKED 告警本轮级别更高 → 立即更新
  severity 并**无视冷却期**外发【升级·严重】通知；
- **恢复通知含持续时长**：RESOLVED 且此前曾外发过 → 中文【已恢复】通知
  （含首次发现→恢复的持续分钟数）；
- **告警风暴聚合**：同一轮评估单通道待发 ALERT > 5 条 → 合并为 1 条
  【告警聚合】摘要（升级与恢复通知不受聚合影响，逐条即时送达）；
- **通道级夜间免打扰**：通道 config.quiet_hours 生效时，夜间窗口压制
  INFO/WARN 外发（CRITICAL 照常即时外发；file 通道照常落盘；压制不写
  last_notified_at——次日首轮评估自然补发），NotificationLog 如实记录。
"""
from __future__ import annotations

from datetime import datetime, timedelta, time as _dtime

from contracts import new_uuid
from core_db.engine import utcnow
from core_db.models import AlertEvent, AlertRule, NotificationChannel

from .channels import channel_quiet_hours, send as channel_send
from .redact import alert_text, redact_text, sanitize_detail
from .rules import Finding, evaluate_rule

# 观测状态：本规则本次评估是否"证据完整的健康"
HEALTHY, UNAVAILABLE, STALE, ERROR = "HEALTHY", "UNAVAILABLE", "STALE", "ERROR"

# RT-03 S05：fingerprint 列 String(128)；超长在构造期拒绝（不静默截断串租户）
_FINGERPRINT_MAX = 128

_SEV_RANK = {"info": 0, "warn": 1, "warning": 1, "critical": 2}
_SEV_CN = {"critical": "严重", "warn": "警告", "warning": "警告", "info": "提示"}

# 告警风暴聚合阈值（PT-02）：单通道单轮待发 ALERT 超过该数合并为 1 条摘要
_STORM_AGGREGATE_OVER = 5


def self_fingerprint(tenant_id: str, rule_code: str) -> str:
    fp = f"alerting.self:{tenant_id}:{rule_code}"
    if len(fp) > _FINGERPRINT_MAX:
        raise ValueError(
            f"self fingerprint 超出列长 {_FINGERPRINT_MAX}：{len(fp)} 字符"
            f"（rule_code 过长：{rule_code!r}）")
    return fp


def _norm_sev(sev: str) -> str:
    """severity 统一小写存储（沿旧列 'warning' 约定；展示层再大写化）。"""
    s = str(sev or "warn").lower()
    return s if s in ("info", "warn", "critical", "warning") else "warn"


def _sev_rank(sev: str) -> int:
    return _SEV_RANK.get(_norm_sev(sev), 1)


def _sev_title(sev: str, title: str) -> str:
    """新告警标题：【中文级别】+ 原标题。"""
    return f"【{_SEV_CN.get(_norm_sev(sev), '警告')}】{title}"


def _channels_for(db, tenant_id: str, rule) -> list:
    codes = list(rule.notify_channels or [])
    if not codes:
        codes = ["file_default"]
    rows = []
    for code in codes:
        ch = (db.query(NotificationChannel)
              .filter_by(tenant_id=tenant_id, code=code, enabled=True).first())
        if ch is not None:
            rows.append(ch)
    return rows


def _notify(db, tenant_id: str, channels, *, kind: str, title: str,
            detail: dict, rule_name: str, target_name: str,
            alert_event_id: str | None) -> None:
    """即时逐条外发（self/恢复等小流量路径与 RT-03 测试直接调用；业务告警经
    _flush_notifications 走聚合/静默）。签名保持稳定。"""
    from core_db.models import NotificationLog
    safe_detail = sanitize_detail(detail)
    text = alert_text(title, safe_detail, target_name=target_name,
                      rule_name=rule_name)
    for ch in channels:
        try:
            err = channel_send(ch, text, kind=kind)
            ok = err == ""
        except Exception as exc:  # noqa: BLE001 —— 通道失败不阻断评估
            ok, err = False, str(exc)[:200]
        db.add(NotificationLog(
            id=new_uuid(), tenant_id=tenant_id, channel_code=ch.code,
            alert_event_id=alert_event_id, sent_at=utcnow(), ok=bool(ok),
            error=redact_text(str(err or ""))[:500], kind=kind,
            detail={"title": redact_text(title[:120])}))


def _send_one(db, tenant_id: str, item: dict) -> tuple[bool, bool]:
    """发送单条待发项（返回 (通道成功?, 是否刷新 last_notified_at)）。"""
    from core_db.models import NotificationLog
    ch = item["ch"]
    safe_detail = sanitize_detail(item["detail"])
    text = alert_text(item["title"], safe_detail,
                      target_name=item["target_name"],
                      rule_name=item["rule_name"])
    try:
        err = channel_send(ch, text, kind=item["kind"])
        ok = err == ""
    except Exception as exc:  # noqa: BLE001
        ok, err = False, str(exc)[:200]
    db.add(NotificationLog(
        id=new_uuid(), tenant_id=tenant_id, channel_code=ch.code,
        alert_event_id=item["alert_event_id"], sent_at=utcnow(), ok=bool(ok),
        error=redact_text(str(err or ""))[:500], kind=item["kind"],
        detail={"title": redact_text(item["title"][:120])}))
    return ok, item["kind"] == "ALERT"


def _quiet_now(qh: dict, now) -> bool:
    """通道级夜间免打扰窗判定（支持跨午夜，如 23:00–07:30）。"""
    try:
        def _t(s, dft):
            hh, mm = str(s).split(":")
            return _dtime(int(hh), int(mm))
        start = _t(qh.get("start", "23:00"), _dtime(23, 0))
        end = _t(qh.get("end", "07:30"), _dtime(7, 30))
    except (ValueError, AttributeError):
        return False
    try:
        t = (now or utcnow()).time()
    except Exception:
        return False
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def _log_suppressed(db, tenant_id: str, item: dict) -> None:
    from core_db.models import NotificationLog
    db.add(NotificationLog(
        id=new_uuid(), tenant_id=tenant_id, channel_code=item["ch"].code,
        alert_event_id=item["alert_event_id"], sent_at=utcnow(),
        ok=False, error="夜间免打扰窗口已压制（次日首轮评估自动补发）",
        kind=item["kind"], detail={"title": redact_text(item["title"][:120])}))


def _flush_notifications(db, tenant_id: str, pending: list, *, now) -> dict:
    """PT-02 flush 阶段：通道级静默窗 → 风暴聚合 → 逐条外发。

    - file 通道照常落盘不受静默窗限制；外部通道 quiet_hours 生效时压制
      INFO/WARN（CRITICAL 即时放行），NotificationLog 如实记录压制；
    - 单通道待发 ALERT（不含升级）> 5 条 → 合并 1 条【告警聚合】摘要；
    - 升级/恢复通知始终逐条即时送达（升级受静默窗约束——CRITICAL 级别放行）。"""
    from core_db.models import NotificationLog
    out = {"sent": 0, "quiet_suppressed": 0, "aggregated": 0}
    by_channel: dict[str, list] = {}
    for item in pending:
        by_channel.setdefault(item["ch"].code, []).append(item)
    for code, items in by_channel.items():
        ch = items[0]["ch"]
        qh = channel_quiet_hours(ch)
        is_file = ch.type == "file"

        def _suppressed(it) -> bool:
            if is_file:
                return False                     # 本地通道照常落盘
            if qh and _quiet_now(qh, now) and it["sev_rank"] < 2:
                return True                      # 夜间压制 INFO/WARN
            return False

        regular = [it for it in items
                   if it["kind"] == "ALERT" and not it["escalate"]]
        immediate = [it for it in items
                     if it["kind"] != "ALERT" or it["escalate"]]
        # 1) 升级/恢复等即时项
        for it in immediate:
            if _suppressed(it):
                _log_suppressed(db, tenant_id, it)
                out["quiet_suppressed"] += 1
                continue
            ok, refresh = _send_one(db, tenant_id, it)
            if ok and refresh and it["ev"] is not None:
                it["ev"].detail = {**(it["ev"].detail or {}),
                                   "last_notified_at": now.isoformat()}
            out["sent"] += 1
        # 2) 常规 ALERT：风暴聚合（>5 条合并为 1 条摘要）
        if len(regular) > _STORM_AGGREGATE_OVER:
            crit = sum(1 for it in regular if it["sev_rank"] >= 2)
            warn = len(regular) - crit
            digest_title = (f"【告警聚合】本轮共触发 {len(regular)} 条告警"
                            f"（严重 {crit} / 警告 {warn}）")
            safe_detail = {"aggregated": len(regular), "critical": crit,
                           "warn": warn,
                           "top": redact_text("；".join(
                               it["title"][:60] for it in regular[:5]))}
            text = alert_text(digest_title, safe_detail,
                              target_name="", rule_name="")
            try:
                err = channel_send(ch, text, kind="ALERT")
                ok = err == ""
            except Exception as exc:  # noqa: BLE001
                ok, err = False, str(exc)[:200]
            db.add(NotificationLog(
                id=new_uuid(), tenant_id=tenant_id, channel_code=code,
                alert_event_id=None, sent_at=utcnow(), ok=bool(ok),
                error=redact_text(str(err or ""))[:500], kind="ALERT",
                detail={"title": redact_text(digest_title[:120]),
                        "aggregated": len(regular)}))
            for it in regular:                    # 聚合命中也刷新冷却（防重复轰炸）
                if ok and it["ev"] is not None:
                    it["ev"].detail = {**(it["ev"].detail or {}),
                                       "last_notified_at": now.isoformat()}
            out["aggregated"] += 1
            out["sent"] += 1
        else:
            for it in regular:
                if _suppressed(it):
                    _log_suppressed(db, tenant_id, it)
                    out["quiet_suppressed"] += 1
                    continue
                ok, refresh = _send_one(db, tenant_id, it)
                if ok and refresh and it["ev"] is not None:
                    it["ev"].detail = {**(it["ev"].detail or {}),
                                       "last_notified_at": now.isoformat()}
                out["sent"] += 1
    return out


def _enqueue_all(pending: list, channels, **common) -> None:
    """收集本轮待发通知（每通道一份；flush 阶段统一走聚合/静默/落日志）。"""
    for ch in channels:
        it = dict(common)
        it["ch"] = ch
        pending.append(it)


def _finding_is_data_gap(f: Finding) -> bool:
    return f.fingerprint.startswith("data_unavailable:")


def evaluate_all(db, tenant_id: str, now=None, *, registry=None,
                 notify: bool = True) -> dict:
    """评估全部 enabled 规则；返回统计。调用方 commit。

    PT-02：业务告警通知经 pending 缓冲统一 flush（升级穿透/风暴聚合/静默窗）；
    stats 新增 escalated/quiet_suppressed/aggregated。"""
    now = now or utcnow()
    stats = {"rules": 0, "new": 0, "re_notified": 0, "recovered": 0,
             "errors": 0, "self_recovered": 0, "escalated": 0,
             "quiet_suppressed": 0, "aggregated": 0}
    rules = (db.query(AlertRule)
             .filter_by(tenant_id=tenant_id, enabled=True).all())
    pending: list = []
    for rule in rules:
        stats["rules"] += 1
        try:
            self_fp = self_fingerprint(tenant_id, rule.code)
        except ValueError as exc:
            # 规则编码超长使 self 指纹超列宽：按规则异常隔离处理（不写库）
            stats["errors"] += 1
            _self_event(db, tenant_id, rule, str(exc), now)
            continue
        try:
            findings = evaluate_rule(db, tenant_id, rule, now, registry)
        except Exception as exc:  # noqa: BLE001 —— 单规则异常隔离
            stats["errors"] += 1
            _self_event(db, tenant_id, rule, str(exc), now, fp=self_fp)
            continue                      # 异常≠健康：不恢复、不中断
        # S05：self 恢复独立——该规则本轮没有抛异常即恢复其 self 事件，
        # 即使本轮仍发现业务问题（评估器故障与业务问题是两回事）
        stats["self_recovered"] += _recover_self(
            db, tenant_id, rule, self_fp, now, notify=notify)
        # 观测状态：存在 DATA_UNAVAILABLE 即证据不完整
        seen_fps = set()
        channels = _channels_for(db, tenant_id, rule)
        for f in findings:
            seen_fps.add(f.fingerprint)
            ev = (db.query(AlertEvent)
                  .filter_by(tenant_id=tenant_id, fingerprint=f.fingerprint,
                             resolved_at=None).first())
            if ev is None:
                ev = AlertEvent(
                    id=new_uuid(), tenant_id=tenant_id,
                    alert_type=rule.kind, severity=_norm_sev(f.severity),
                    message=redact_text(f.title)[:500],
                    target_ref=f.target_code or "-",
                    state="OPEN", created_at=now, fingerprint=f.fingerprint,
                    rule_code=rule.code, target_code=f.target_code,
                    first_seen_at=now, last_seen_at=now, occurrences=1,
                    detail=sanitize_detail(f.detail))
                db.add(ev)
                db.flush()
                stats["new"] += 1
                if notify:
                    _enqueue_all(pending, channels, kind="ALERT",
                                 title=_sev_title(f.severity, f.title),
                                 detail=f.detail, rule_name=rule.name,
                                 target_name=f.target_code,
                                 alert_event_id=ev.id,
                                 sev_rank=_sev_rank(f.severity),
                                 escalate=False, ev=ev)
            else:
                ev.last_seen_at = now
                ev.occurrences = int(ev.occurrences or 1) + 1
                # S06：持续更新同样过脱敏（非白名单键不透传进库）
                ev.detail = sanitize_detail({**(ev.detail or {}),
                                             **(f.detail or {})})
                # PT-02 升级穿透：本轮级别高于既有 → 立即更新并无视冷却外发
                if _sev_rank(f.severity) > _sev_rank(ev.severity):
                    ev.severity = _norm_sev(f.severity)
                    stats["escalated"] += 1
                    if notify:
                        _enqueue_all(pending, channels, kind="ALERT",
                                     title=f"【升级·{_SEV_CN.get(_norm_sev(f.severity), '严重')}】{f.title}",
                                     detail=f.detail, rule_name=rule.name,
                                     target_name=f.target_code,
                                     alert_event_id=ev.id,
                                     sev_rank=_sev_rank(f.severity),
                                     escalate=True, ev=ev)
                    continue
                cooldown = timedelta(minutes=int(rule.cooldown_minutes or 60))
                last_note = (ev.detail or {}).get("last_notified_at")
                notified_recently = False
                if last_note:
                    try:
                        ln = datetime.fromisoformat(str(last_note))
                        notified_recently = (now - ln) < cooldown
                    except ValueError:
                        notified_recently = False
                if notify and not ev.acked_by and not notified_recently:
                    _enqueue_all(pending, channels, kind="ALERT",
                                 title=f"【持续】{f.title}", detail=f.detail,
                                 rule_name=rule.name,
                                 target_name=f.target_code,
                                 alert_event_id=ev.id,
                                 sev_rank=_sev_rank(f.severity),
                                 escalate=False, ev=ev)
                    stats["re_notified"] += 1
        # 恢复：仅当本规则本次评估无任何 finding（含无 DATA_UNAVAILABLE）
        # ——即 HEALTHY 且证据完整——才恢复该 rule 的未解决**业务**事件；
        # self 事件显式排除（两套恢复条件独立，S05）。
        # PT-02：恢复通知含持续时长（首次发现→恢复分钟数）。
        if not findings:
            open_events = (db.query(AlertEvent)
                           .filter(AlertEvent.tenant_id == tenant_id,
                                   AlertEvent.rule_code == rule.code,
                                   AlertEvent.fingerprint != self_fp,
                                   AlertEvent.resolved_at.is_(None)).all())
            for ev in open_events:
                ev.resolved_at = now
                ev.state = "RESOLVED"
                if notify:
                    dur_min = 0
                    if ev.first_seen_at:
                        try:
                            dur_min = max(0, int((now - ev.first_seen_at)
                                                 .total_seconds() // 60))
                        except TypeError:
                            dur_min = 0
                    _enqueue_all(pending, channels, kind="RECOVERED",
                                 title=f"【已恢复】{ev.message[:200]}"
                                       f"（持续 {dur_min} 分钟）",
                                 detail={}, rule_name=rule.name,
                                 target_name=ev.target_code,
                                 alert_event_id=ev.id, sev_rank=0,
                                 escalate=False, ev=None)
                stats["recovered"] += 1
    if notify:
        flushed = _flush_notifications(db, tenant_id, pending, now=now)
        stats["quiet_suppressed"] += flushed.get("quiet_suppressed", 0)
        stats["aggregated"] += flushed.get("aggregated", 0)
    db.flush()
    return stats


def _self_event(db, tenant_id: str, rule, err: str, now, *,
                fp: str | None = None) -> None:
    """评估器自身异常：CRITICAL self 事件（不恢复任何业务告警）。

    S05：指纹按租户+规则隔离；S06：message/detail 过脱敏（err 原文可能含
    SQL/路径/敏感值——只留异常类型与打码文本）。"""
    fp = fp or self_fingerprint(tenant_id, rule.code)
    ev = (db.query(AlertEvent)
          .filter_by(tenant_id=tenant_id, fingerprint=fp,
                     resolved_at=None).first())
    if ev is None:
        db.add(AlertEvent(
            id=new_uuid(), tenant_id=tenant_id, alert_type="EVALUATOR_ERROR",
            severity="critical",
            message=redact_text(f"告警评估器规则异常（隔离）："
                                f"{type(err).__name__}: {err}")[:500],
            target_ref="-", state="OPEN", created_at=now, fingerprint=fp,
            rule_code=rule.code, first_seen_at=now, last_seen_at=now,
            occurrences=1,
            detail=sanitize_detail({"code": rule.code,
                                    "error_code": type(err).__name__})))
    else:
        ev.last_seen_at = now
        ev.occurrences = int(ev.occurrences or 1) + 1


def _recover_self(db, tenant_id: str, rule, self_fp: str, now, *,
                  notify: bool) -> int:
    """S05：规则本轮评估无异常 → 恢复该规则的 self 事件（不碰业务事件）。"""
    ev = (db.query(AlertEvent)
          .filter_by(tenant_id=tenant_id, fingerprint=self_fp,
                     resolved_at=None).first())
    if ev is None:
        return 0
    ev.resolved_at = now
    ev.state = "RESOLVED"
    if notify:
        channels = _channels_for(db, tenant_id, rule)
        for ch in channels:
            _send_one(db, tenant_id, {
                "ch": ch, "kind": "RECOVERED",
                "title": f"【已恢复】评估器规则恢复正常（规则 {rule.code}）",
                "detail": {}, "rule_name": rule.name, "target_name": "",
                "alert_event_id": ev.id, "sev_rank": 0,
                "escalate": False, "ev": None})
    return 1
