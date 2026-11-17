"""晨报（LT-04 C5；RT-03 S04 时区/自然日窗口深修）。

昨日各业务 summary + 告警统计 + 调度计划；写 kind=DIGEST 的 notification_log
（detail 存正文供 /alerts/digest/latest）；发往 enabled 通道。全部只读统计+
写日志文件。

RT-03 S04 硬约定：
- 时间一律经 to_aware_utc 规范（utcnow() 的 naive UTC 直接 astimezone(CST)
  会被当宿主本地时区——错 8 小时的根因）；不改宿主 OS 时区，不引入 tzdata；
- "昨日"= 院内（UTC+8）昨天 00:00 到今天 00:00 的自然日半开区间，换算 UTC
  后查询；滚动 24h 数字单独标"近24小时"，不与"昨日"混称；
- 摘要层经 target_summary(..., window=...) 显式区间（钩子声明 window= 才
  收到；不支持自然日的来源显示"仅近24h"，无源数据继续 unavailable/none，
  不把滚动数字塞进昨日栏；ACCEPTED 不升级 CONFIRMED）；
- 未解决告警总数与"展示前10条"分开，排序用统一有效时间
  COALESCE(last_seen_at, created_at)；
- cron 错误分类（语法非法/范围内无触发），下一次在明天时如实写日期；
  不用 broad except 隐藏编程错误；
- 落库与文件正文同样过脱敏（redact_text）。
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

from contracts import new_uuid
from core_db.engine import utcnow
from core_db.models import (AlertEvent, NotificationChannel, NotificationLog,
                            Schedule)
from workflow_core.cron import (CST, CronExpr, CronSyntaxError,
                                NextFireOutOfRange, to_aware_utc)

from .channels import send as channel_send
from .redact import redact_text


def _hospital_day_window(now_aware) -> tuple[str, str, object, object]:
    """院内昨天自然日 [昨00:00, 今00:00)（CST）→ naive UTC 半开区间。

    返回 (昨日日期串, 今日日期串, begin_naive, end_naive)。"""
    now_cst = to_aware_utc(now_aware).astimezone(CST)
    today = now_cst.date()
    yesterday = today - timedelta(days=1)
    begin = to_aware_utc(datetime.combine(yesterday, time.min, tzinfo=CST))
    end = to_aware_utc(datetime.combine(today, time.min, tzinfo=CST))
    return yesterday.isoformat(), today.isoformat(), \
        begin.replace(tzinfo=None), end.replace(tzinfo=None)


def build_digest(db, tenant_id: str, now=None, *, registry=None,
                 target_rows=None) -> str:
    now = to_aware_utc(now or utcnow())
    yesterday, _today, w_begin, w_end = _hospital_day_window(now)
    now_cst = now.astimezone(CST)
    lines = [f"BLYB 晨报（生成 {now_cst.isoformat()}）",
             f"—— 昨日（院内自然日 {yesterday}）各业务 ——"]
    targets = target_rows if target_rows is not None else []
    if registry is not None:
        for t in targets:
            s = None
            try:
                s = registry.target_summary(db, tenant_id, t.code, 1,
                                            window=(w_begin, w_end))
            except TypeError:
                # 该 registry/钩子不支持显式区间（旧装配）→ 降级滚动窗口，
                # 下方按"仅近24h"标注（不把滚动数字冒充昨日）
                try:
                    s = registry.target_summary(db, tenant_id, t.code, 1)
                except Exception:  # noqa: BLE001 —— 摘要查询异常单独标识
                    lines.append(f"{t.name}: 摘要查询异常")
                    continue
            except Exception:  # noqa: BLE001 —— 摘要查询异常单独标识
                lines.append(f"{t.name}: 摘要查询异常")
                continue
            if s is None:
                continue
            if s.data_source == "none":
                lines.append(f"{t.name}: 无源数据（unavailable/none）")
                continue
            b = s.buckets or {}
            base = (f"{t.name}: CONFIRMED={b.get('CONFIRMED', 0)} "
                    f"REJECTED={b.get('REJECTED', 0)} "
                    f"UNKNOWN={b.get('UNKNOWN', 0)} backlog={s.backlog}"
                    f"{'/' + str(s.backlog_total) if s.backlog_total is not None else ''} "
                    f"last_success={s.last_success_at or '无'}"
                    f"{' [Mock]' if s.evidence_kind == 'mock' else ''}")
            if getattr(s, "window_kind", "") == "hospital_day":
                lines.append(f"{base}（自然日窗口）")
            else:
                # 钩子不支持自然日：滚动数字不冒充昨日，明确标注口径
                lines.append(f"{base}（窗口不可用，仅近24h）")
    # ---- 告警统计：昨日自然日窗口 + 近24小时分开标注（不混称）
    new_yday = (db.query(AlertEvent)
                .filter(AlertEvent.tenant_id == tenant_id,
                        AlertEvent.created_at >= w_begin,
                        AlertEvent.created_at < w_end).count())
    rec_yday = (db.query(AlertEvent)
                .filter(AlertEvent.tenant_id == tenant_id,
                        AlertEvent.resolved_at.isnot(None),
                        AlertEvent.resolved_at >= w_begin,
                        AlertEvent.resolved_at < w_end).count())
    since24 = now.replace(tzinfo=None) - timedelta(days=1)
    new_24 = (db.query(AlertEvent)
              .filter(AlertEvent.tenant_id == tenant_id,
                      AlertEvent.created_at >= since24).count())
    rec_24 = (db.query(AlertEvent)
              .filter(AlertEvent.tenant_id == tenant_id,
                      AlertEvent.resolved_at.isnot(None),
                      AlertEvent.resolved_at >= since24).count())
    # 未解决总数与"展示前10条"分开；排序用统一有效时间
    from sqlalchemy import func
    eff = func.coalesce(AlertEvent.last_seen_at, AlertEvent.created_at)
    open_total = (db.query(func.count(AlertEvent.id))
                  .filter(AlertEvent.tenant_id == tenant_id,
                          AlertEvent.resolved_at.is_(None)).scalar() or 0)
    open_rows = (db.query(AlertEvent)
                 .filter(AlertEvent.tenant_id == tenant_id,
                         AlertEvent.resolved_at.is_(None))
                 .order_by(eff.desc()).limit(10).all())
    lines.append(f"—— 告警：昨日新增 {new_yday} / 恢复 {rec_yday}；"
                 f"近24小时新增 {new_24} / 恢复 {rec_24}；"
                 f"仍未解决 {open_total}（展示前10条，按有效时间排序） ——")
    for ev in open_rows:
        eff_ts = (ev.last_seen_at or ev.created_at)
        lines.append(f"  [{(ev.severity or '').upper()}] "
                     f"{redact_text(ev.message)[:80]}"
                     f"（有效时间 {eff_ts.isoformat() if eff_ts else '未知'}）")
    lines.append("—— 调度计划（enabled，下次触发如实标注日期） ——")
    for sch in (db.query(Schedule)
                .filter_by(tenant_id=tenant_id, enabled=True).all()):
        try:
            expr = CronExpr(sch.cron)
            nxt = expr.next_after(now).astimezone(CST)
            nxt_day = "（明天）" if nxt.date() > now_cst.date() else ""
            lines.append(f"  {sch.code}: 下次 {nxt.isoformat()}{nxt_day}")
        except CronSyntaxError:
            lines.append(f"  {sch.code}: 表达式非法（待核）")
        except NextFireOutOfRange:
            lines.append(f"  {sch.code}: 表达式有效但 366 天内无触发")
    return "\n".join(lines)


def run_digest(db, tenant_id: str, now=None, *, registry=None) -> dict:
    """晨报任务体：生成→file_default 落盘→写 DIGEST 日志。调用方 commit。

    正文与落库 detail 均经 redact_text（长数字/证件号打码，S04）。"""
    from plugins.report_targets import list_targets
    now = to_aware_utc(now or utcnow())
    targets = list_targets(db, tenant_id)
    text = redact_text(build_digest(db, tenant_id, now, registry=registry,
                                    target_rows=targets))
    channels = (db.query(NotificationChannel)
                .filter_by(tenant_id=tenant_id, enabled=True).all())
    sent_to = []
    for ch in channels:
        err = channel_send(ch, text, kind="DIGEST")
        db.add(NotificationLog(
            id=new_uuid(), tenant_id=tenant_id, channel_code=ch.code,
            alert_event_id=None, sent_at=now.replace(tzinfo=None),
            ok=(err == ""),
            error=redact_text(str(err or ""))[:500], kind="DIGEST",
            detail={"text": text[:20000]}))
        if err == "":
            sent_to.append(ch.code)
    return {"channels": sent_to, "length": len(text)}
