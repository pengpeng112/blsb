"""传染病全维监控只读路由（PT-03）：domain.routes 通用挂载 + target_scopes 鉴权。

- GET  /api/v1/infectious/monitor/overview?days=&source_instance=   总览指标+趋势+错误码分布
- GET  /api/v1/infectious/monitor/tables?days=&source_instance=     13 接口全景矩阵
- GET  /api/v1/infectious/monitor/watchdog?source_instance=         守护 C1–C11+晨报+告警流水
- GET  /api/v1/infectious/monitor/reconcile?days=&source_instance=  日终对账+影子 7 类差异
- POST /api/v1/infectious/monitor/sync                              触发只读增量同步
     （operator/platform_admin；未配置目录温和提示不 500）

全部只读（sync 只写平台自有库表，对源目录 100% O_RDONLY 零副作用）；
数据全部为聚合计数/表名/时间戳——零患者字段。
"""
from __future__ import annotations

from datetime import timedelta

from contracts import ok
from contracts.api import ROLE_OPERATOR, ROLE_PLATFORM_ADMIN
from core_db.engine import utcnow

TARGET = "inf_cdc_frontend"

# 13 接口 6 大临床域分组（监控矩阵展示顺序）
DOMAIN_GROUPS = [
    ("住院与出入院", ["admission", "admission_info", "discharge_info", "death"]),
    ("病程记录", ["first_course", "daily_course"]),
    ("检查", ["exam", "exam_item"]),
    ("检验", ["ex_lab", "ex_lab_item"]),
    ("医嘱", ["order", "order_item"]),
    ("生命体征", ["vital_signs"]),
]

_ERROR_CODE_CN = {
    "00": "成功受理",
    "01": "平台服务异常（自动重试）",
    "02": "字典/值域不符（需核对字段口径）",
    "03": "数据格式错误（需核对日期/编码格式）",
    "04": "平台必填项校验未过（需核查源病历或视图字段）",
    "05": "时间格式无法解析（需核对源时间字段）",
    "99": "平台未知异常（自动重试）",
}

_STAGE_CN = {
    "parent-wait": "等待父表受理（父单成功后下一班自动补发）",
    "client-validate": "本地必填预检未过（进入质量等待，不发废数据）",
    "network": "网络/连接异常（自动重试）",
    "row-error": "单行处理异常（已隔离并记录）",
    "wait-persist-failed": "等待队列落盘失败（按失败处理不推水位）",
}


def register_routes(app, sf, auth, registry):
    from fastapi import Depends, Query

    from contracts import ApiError

    def _principal(ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_target_scope(TARGET)          # 先范围后存在性（R08）
        return p

    def _tables_meta():
        from plugins.external_ledger.importers.inf_pusher import INF_TABLES
        return INF_TABLES

    @app.get("/api/v1/infectious/monitor/overview")
    def infectious_overview(days: int = Query(7), source_instance: str = Query(""),
                            ctx=Depends(auth)):
        p = _principal(ctx)
        from sqlalchemy import func
        from core_db.models import (ExternalSubmissionRecord,
                                    InfectiousTableTelemetry,
                                    InfectiousWatchdogSnapshot)
        with sf() as db:
            since = utcnow() - timedelta(days=max(1, min(90, int(days))))
            conds = [ExternalSubmissionRecord.tenant_id == p.tenant_id,
                     ExternalSubmissionRecord.target_code == TARGET,
                     func.coalesce(ExternalSubmissionRecord.last_attempt_at,
                                   ExternalSubmissionRecord.first_seen_at) >= since]
            if source_instance:
                conds.append(ExternalSubmissionRecord.source_instance
                             == source_instance)
            buckets = {b: 0 for b in ("PENDING", "ACCEPTED", "CONFIRMED",
                                      "REJECTED", "UNKNOWN", "NOT_REPORTABLE")}
            codes: dict[str, int] = {}
            daily: dict[str, dict[str, int]] = {}
            rows = (db.query(
                ExternalSubmissionRecord.bucket,
                ExternalSubmissionRecord.last_error_code,
                func.coalesce(ExternalSubmissionRecord.last_attempt_at,
                              ExternalSubmissionRecord.first_seen_at),
                func.count(ExternalSubmissionRecord.id))
                .filter(*conds)
                .group_by(ExternalSubmissionRecord.bucket,
                          ExternalSubmissionRecord.last_error_code,
                          func.coalesce(
                              ExternalSubmissionRecord.last_attempt_at,
                              ExternalSubmissionRecord.first_seen_at)).all())
            for bucket, code, ts, n in rows:
                n = int(n)
                if bucket in buckets:
                    buckets[bucket] += n
                ck = str(code or "").strip()
                if ck:
                    codes[ck] = codes.get(ck, 0) + n
                d = str(ts)[:10] if ts is not None else "unknown"
                dd = daily.setdefault(d, {"ACCEPTED": 0, "REJECTED": 0,
                                          "UNKNOWN": 0, "PENDING": 0})
                if bucket in dd:
                    dd[bucket] += n
            # 遥测：水位最大滞后/最近班次
            tconds = [InfectiousTableTelemetry.tenant_id == p.tenant_id,
                      InfectiousTableTelemetry.target_code == TARGET]
            if source_instance:
                tconds.append(InfectiousTableTelemetry.source_instance
                              == source_instance)
            last_shift = (db.query(func.max(
                InfectiousTableTelemetry.last_shift_at)).filter(*tconds).scalar())
            max_lag = (db.query(func.max(
                InfectiousTableTelemetry.watermark_lag_hours))
                .filter(*tconds).scalar())
            wd = (db.query(InfectiousWatchdogSnapshot)
                  .filter(InfectiousWatchdogSnapshot.tenant_id == p.tenant_id,
                          InfectiousWatchdogSnapshot.target_code == TARGET)
                  .order_by(InfectiousWatchdogSnapshot.updated_at.desc())
                  .first())
            from domains.infectious import monitor_sync
            return ok({
                "days": int(days),
                "buckets": buckets,
                "error_codes": [{"code": k, "count": v,
                                 "meaning": _ERROR_CODE_CN.get(k, "未知错误码")}
                                for k, v in sorted(
                                    codes.items(), key=lambda kv: -kv[1])],
                "daily": [{"date": d, **counts} for d, counts in sorted(
                    daily.items())],
                "observed_until": max((str(r[0]) for r in rows
                                       if r[2] is not None), default=None),
                "last_shift_at": last_shift.isoformat() if last_shift else None,
                "max_watermark_lag_hours": round(float(max_lag), 1) \
                    if max_lag is not None else None,
                "watchdog_level": wd.overall_level if wd else None,
                "watchdog_checked_at": wd.checked_at.isoformat()
                if wd and wd.checked_at else None,
                "wecom_relay_ok": wd.relay_reachable if wd else None,
                "sync_configured": monitor_sync.sync_configured(),
                "source_instance": source_instance or "(全部)",
            }, ctx.request_id)

    @app.get("/api/v1/infectious/monitor/tables")
    def infectious_tables(days: int = Query(7), source_instance: str = Query(""),
                          ctx=Depends(auth)):
        p = _principal(ctx)
        from sqlalchemy import func
        from core_db.models import (ExternalSubmissionRecord,
                                    InfectiousTableTelemetry)
        meta = _tables_meta()
        with sf() as db:
            since = utcnow() - timedelta(days=max(1, min(90, int(days))))
            groups = []
            for group_name, tables in DOMAIN_GROUPS:
                rows = []
                for tkey in tables:
                    meta_t = meta[tkey]
                    tconds = [InfectiousTableTelemetry.tenant_id == p.tenant_id,
                              InfectiousTableTelemetry.target_code == TARGET,
                              InfectiousTableTelemetry.table_key == tkey]
                    if source_instance:
                        tconds.append(InfectiousTableTelemetry.source_instance
                                      == source_instance)
                    tel = (db.query(InfectiousTableTelemetry)
                           .filter(*tconds).first())
                    bconds = [ExternalSubmissionRecord.tenant_id == p.tenant_id,
                              ExternalSubmissionRecord.target_code == TARGET,
                              ExternalSubmissionRecord.business_type == tkey,
                              func.coalesce(
                                  ExternalSubmissionRecord.last_attempt_at,
                                  ExternalSubmissionRecord.first_seen_at) >= since]
                    if source_instance:
                        bconds.append(ExternalSubmissionRecord.source_instance
                                      == source_instance)
                    brows = (db.query(ExternalSubmissionRecord.bucket,
                                      func.count(ExternalSubmissionRecord.id))
                             .filter(*bconds)
                             .group_by(ExternalSubmissionRecord.bucket).all())
                    bc = {b: int(n) for b, n in brows}
                    rows.append({
                        "table": tkey,
                        "doc_type_code": meta_t["code"],
                        "doc_type_name": meta_t["name"],
                        "endpoint": meta_t["endpoint"],
                        "parent_table": meta_t["parent"],
                        "parent_name": meta[meta_t["parent"]]["name"]
                        if meta_t["parent"] else "",
                        "watermark": tel.watermark_op_time.isoformat()
                        if tel and tel.watermark_op_time else None,
                        "lag_hours": round(float(tel.watermark_lag_hours), 1)
                        if tel and tel.watermark_lag_hours is not None else None,
                        "last_shift_at": tel.last_shift_at.isoformat()
                        if tel and tel.last_shift_at else None,
                        "shift": {"rows": tel.shift_rows if tel else 0,
                                  "sent": tel.shift_sent if tel else 0,
                                  "parent_wait": tel.shift_parent_wait
                                  if tel else 0,
                                  "skipped_invalid": tel.shift_skipped_invalid
                                  if tel else 0,
                                  "failed": tel.shift_failed if tel else 0,
                                  "error": (tel.shift_error if tel else "")
                                  or ""},
                        "reconcile": {"day": tel.reconcile_day if tel else "",
                                      "repushed": tel.reconcile_repushed
                                      if tel else 0,
                                      "parent_wait": tel.reconcile_parent_wait
                                      if tel else 0,
                                      "failed": tel.reconcile_failed
                                      if tel else 0},
                        "accepted": bc.get("ACCEPTED", 0)
                        + bc.get("CONFIRMED", 0),
                        "parent_waiting": bc.get("PENDING", 0),
                        "rejected": bc.get("REJECTED", 0),
                        "unknown": bc.get("UNKNOWN", 0),
                        "total": sum(bc.values()),
                    })
                groups.append({"group": group_name, "tables": rows})
            return ok({"days": int(days), "groups": groups,
                       "total_tables": len(meta)}, ctx.request_id)

    @app.get("/api/v1/infectious/monitor/watchdog")
    def infectious_watchdog(source_instance: str = Query(""),
                            ctx=Depends(auth)):
        p = _principal(ctx)
        from core_db.models import InfectiousWatchdogSnapshot
        with sf() as db:
            q = (db.query(InfectiousWatchdogSnapshot)
                 .filter(InfectiousWatchdogSnapshot.tenant_id == p.tenant_id,
                         InfectiousWatchdogSnapshot.target_code == TARGET))
            if source_instance:
                q = q.filter(InfectiousWatchdogSnapshot.source_instance
                             == source_instance)
            snap = q.order_by(
                InfectiousWatchdogSnapshot.updated_at.desc()).first()
            if snap is None:
                return ok({"present": False,
                           "note": "尚无守护快照——导入副本或配置只读目录同步后"
                                   "自动生成"}, ctx.request_id)
            return ok({"present": True,
                       "checked_at": snap.checked_at.isoformat()
                       if snap.checked_at else None,
                       "host_label": snap.host_label,
                       "overall_level": snap.overall_level,
                       "relay_reachable": snap.relay_reachable,
                       "checks": snap.checks_json or {},
                       "driver_errors": snap.driver_errors_json or {},
                       "daily": {"ymd": snap.daily_ymd,
                                 "text": snap.daily_text or None},
                       "recent_alerts": (snap.recent_alerts_json or {})
                       .get("items", [])}, ctx.request_id)

    @app.get("/api/v1/infectious/monitor/reconcile")
    def infectious_reconcile(days: int = Query(7), source_instance: str = Query(""),
                             ctx=Depends(auth)):
        p = _principal(ctx)
        from sqlalchemy import func
        from core_db.models import (ExternalSubmissionRecord,
                                    InfectiousTableTelemetry)
        meta = _tables_meta()
        with sf() as db:
            tconds = [InfectiousTableTelemetry.tenant_id == p.tenant_id,
                      InfectiousTableTelemetry.target_code == TARGET,
                      InfectiousTableTelemetry.reconcile_day != ""]
            if source_instance:
                tconds.append(InfectiousTableTelemetry.source_instance
                              == source_instance)
            tels = (db.query(InfectiousTableTelemetry)
                    .filter(*tconds)
                    .order_by(InfectiousTableTelemetry.reconcile_day.desc())
                    .limit(40).all())
            reconcile = [{
                "table": t.table_key,
                "doc_type_name": meta.get(t.table_key, {}).get("name",
                                                               t.table_key),
                "day": t.reconcile_day,
                "repushed": t.reconcile_repushed,
                "parent_wait": t.reconcile_parent_wait,
                "failed": t.reconcile_failed,
            } for t in tels]
            # 影子 vs 外部台账 7 类差异对比（只读复用 reconcile_shadow 汇总）
            from domains.infectious import reconcile_shadow
            try:
                diff = reconcile_shadow.compare(db, p.tenant_id, TARGET,
                                                source_instance=source_instance
                                                or None)
            except Exception as exc:  # noqa: BLE001 —— 差异对比失败不阻断对账历史
                diff = {"error": str(exc)[:200]}
            return ok({"days": int(days), "reconcile": reconcile,
                       "shadow_diff": diff}, ctx.request_id)

    @app.post("/api/v1/infectious/monitor/sync")
    def infectious_sync(ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        p.require_target_scope(TARGET)
        from domains.infectious import monitor_sync
        with sf() as db:
            out = monitor_sync.sync_status(db, p.tenant_id)
            db.commit()
        return ok(out, ctx.request_id)
