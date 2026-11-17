"""传染病钩子（LT-05 A5）：external_ledger 只读 summary + 台账检索。

data_source=external_ledger：数据来自导入的 state 副本——summary 同时输出
"观测截至"（最后尝试时间）与"导入时间"（最近批次），两者不混用；无任何
导入批次时 data_source="none"（不编造正常）。
"""
from __future__ import annotations

from datetime import timedelta

from contracts import REPORT_BUCKETS
from core_db.engine import utcnow
from core_db.models import ExternalImportBatch, ExternalSubmissionRecord

TARGET_CODE = "inf_cdc_frontend"

BUCKETS = {"ACCEPTED": "ACCEPTED", "REJECTED": "REJECTED",
           "CLIENT_INVALID": "REJECTED", "RETRYABLE": "UNKNOWN",
           "NETWORK_ERROR": "UNKNOWN", "MOCK": "NOT_REPORTABLE",
           # PT-03：等待父表受理单列（主子门禁保护中，归 PENDING 非 REJECTED）
           "PARENT_WAIT": "PENDING"}

# PT-03：13 张在推接口权威映射（编码=《03-接口总览与状态矩阵》；与
# importers/inf_pusher.INF_TABLES 同源对齐；旧映射系统性错位已修正）
DOC_TYPES = {
    "admission":      {"code": "09", "name": "住院病案首页", "endpoint": "emrAdmissionRecord",  "parent": ""},
    "admission_info": {"code": "06", "name": "入院记录",     "endpoint": "emrAdmissionInfo",    "parent": ""},
    "discharge_info": {"code": "10", "name": "出院记录",     "endpoint": "emrDischargeInfo",    "parent": ""},
    "death":          {"code": "17", "name": "死亡记录",     "endpoint": "emrDeathInfo",        "parent": ""},
    "first_course":   {"code": "07", "name": "首次病程记录", "endpoint": "emrFirstCourse",      "parent": ""},
    "daily_course":   {"code": "08", "name": "日常病程记录", "endpoint": "emrDailyCourse",      "parent": ""},
    "exam":           {"code": "11", "name": "检查报告主表", "endpoint": "emrExClinical",       "parent": ""},
    "exam_item":      {"code": "12", "name": "检查报告项目", "endpoint": "emrExClinicalItem",   "parent": "exam"},
    "ex_lab":         {"code": "13", "name": "检验报告主表", "endpoint": "emrExLab",            "parent": ""},
    "ex_lab_item":    {"code": "14", "name": "检验报告项目", "endpoint": "emrExLabItem",        "parent": "ex_lab"},
    "order":          {"code": "15", "name": "医嘱处方主表", "endpoint": "emrOrder",            "parent": ""},
    "order_item":     {"code": "16", "name": "医嘱药品明细", "endpoint": "emrOrderItem",        "parent": "order"},
    "vital_signs":    {"code": "18", "name": "生命体征记录", "endpoint": "emrVitalSignsRecord", "parent": ""},
}
_CODE_TO_TABLE = {v["code"]: k for k, v in DOC_TYPES.items()}


def target_summary(db, tenant_id: str, target_code: str, days: int):
    if target_code != TARGET_CODE:
        return None
    from plugins.domain_hooks import TargetSummary
    last_batch = (db.query(ExternalImportBatch)
                  .filter_by(tenant_id=tenant_id, target_code=TARGET_CODE,
                             dry_run=False)
                  .order_by(ExternalImportBatch.started_at.desc()).first())
    if last_batch is None:
        return None                      # 无导入批次 → data_source=none（路由层）
    since = utcnow() - timedelta(days=days)
    buckets = {b: 0 for b in REPORT_BUCKETS}
    daily: dict[str, dict[str, int]] = {}
    unresolved = ("UNKNOWN",)
    for r in (
            db.query(ExternalSubmissionRecord.last_attempt_at,
                     ExternalSubmissionRecord.bucket)
            .filter(ExternalSubmissionRecord.tenant_id == tenant_id,
                    ExternalSubmissionRecord.target_code == TARGET_CODE,
                    ExternalSubmissionRecord.last_attempt_at >= since)
            .all()):
        b = r[1] if r[1] in buckets else "UNKNOWN"
        buckets[b] += 1
        from datetime import timezone as _tz
        from workflow_core.cron import CST
        lat = r[0].replace(tzinfo=_tz.utc) if r[0] and r[0].tzinfo is None             else r[0]
        day = (lat.astimezone(CST) if lat else lat).date().isoformat()
        daily.setdefault(day, {})
        daily[day][b] = daily[day].get(b, 0) + 1
    backlog_total = (
        db.query(ExternalSubmissionRecord)
        .filter(ExternalSubmissionRecord.tenant_id == tenant_id,
                ExternalSubmissionRecord.target_code == TARGET_CODE,
                ExternalSubmissionRecord.bucket.in_(unresolved))
        .count() or 0)
    observed_until = (
        db.query(ExternalSubmissionRecord.last_attempt_at)
        .filter(ExternalSubmissionRecord.tenant_id == tenant_id,
                ExternalSubmissionRecord.target_code == TARGET_CODE,
                ExternalSubmissionRecord.last_attempt_at.isnot(None))
        .order_by(ExternalSubmissionRecord.last_attempt_at.desc()).first())
    # 外部台账无平台业务确认证据：last_success_at=None（ACCEPTED≠CONFIRMED）
    return TargetSummary(
        target_code=TARGET_CODE, window_days=days, buckets=buckets,
        daily=[{"date": d, **c} for d, c in sorted(daily.items())],
        last_success_at=None,
        backlog=buckets.get("UNKNOWN", 0), backlog_total=int(backlog_total),
        data_source="external_ledger",
        note=f"数据截至 {observed_until[0].isoformat() if observed_until and observed_until[0] else '未知'}"
             f"（副本观测，无平台业务确认）；最近导入"
             f" {last_batch.started_at.isoformat()}（{last_batch.source_label[:60]}，"
             f"tz={last_batch.source_tz}）",
        window_kind="rolling_days", quality_waiting=0, evidence_kind="real")


def ledger_search(db, tenant_id: str, target_code: str, filters,
                  cursor: str | None, limit: int):
    if target_code != TARGET_CODE:
        return None
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerFilter, LedgerPage, LedgerRow
    # 外部台账独立先筛后限（游标 v2 绑定上下文复用 RL 实现）
    items, next_cursor = _search(db, tenant_id, filters, cursor, limit)
    rows = [LedgerRow(id=r.id, target_code=TARGET_CODE,
                      business_key=r.record_key, bucket=r.bucket,
                      raw_state=r.last_result,
                      created_at=r.first_seen_at.isoformat(),
                      updated_at=(_sort_key(r).isoformat()),
                      attempts=r.attempts,
                      last_error_code=r.last_error_code or None,
                      last_error_message=r.last_error_message or "")
            for r in items]
    total = _total(db, tenant_id, filters)
    return LedgerPage(items=rows, next_cursor=next_cursor, total_estimate=total)


def _sort_key(r):
    """A3：sent-only 记录 last_attempt_at 未知（NULL）——列表统一
    COALESCE(last_attempt_at, first_seen_at) 稳定排序（不冒充尝试时间）。"""
    return r.last_attempt_at if r.last_attempt_at is not None \
        else r.first_seen_at


def ledger_detail(db, tenant_id: str, target_code: str, record_id: str):
    """A3：外部台账记录详情（HOOK_LEDGER_DETAIL；绑定 tenant+target+record）。

    只读；脱敏文书类型/业务键/源版本时间/实际尝试时间（未知如实为 None）/
    批次/次数下界/错误码/来源证据。外部历史观察**不开放**平台 UNKNOWN 手工
    重发（外部 record 不是 Submission，不能当 Submission 修改）。"""
    if target_code != TARGET_CODE:
        return None
    from core_db.models import ExternalImportBatch, ExternalSubmissionRecord
    r = (db.query(ExternalSubmissionRecord)
         .filter_by(id=record_id, tenant_id=tenant_id,
                    target_code=TARGET_CODE).first())
    if r is None:
        return None
    batch = (db.query(ExternalImportBatch)
             .filter_by(id=r.import_batch_id).first()) \
        if r.import_batch_id else None
    return {
        "kind": "external_record",
        "id": r.id,
        "target_code": TARGET_CODE,
        "record_key": r.record_key,                    # 表/记录ID（业务键）
        "business_type": r.business_type,
        "doc_type_code": r.doc_type_code or "",
        "doc_type_name": _DOC_TYPE_NAMES.get(r.doc_type_code, "其他/未分类"),
        "bucket": r.bucket,
        "raw_state": r.last_result,
        "evidence_level": r.evidence_level,
        # 源版本时间（源 opTime）≠ 实际尝试时间；未知保持 None（不冒充）
        "source_op_time": r.op_time.isoformat() if r.op_time else None,
        "last_attempt_at": r.last_attempt_at.isoformat()
        if r.last_attempt_at else None,
        "first_seen_at": r.first_seen_at.isoformat(),
        "attempts_lower_bound": int(r.attempts or 0),
        "last_error_code": r.last_error_code or None,
        "last_error_message": r.last_error_message or "",
        "source_instance": r.source_instance,
        "source_file": r.source_file,
        "import_batch_id": r.import_batch_id or None,
        "imported_at": batch.started_at.isoformat() if batch else None,
        "note": "外部副本观测记录（只读）：不开放平台 UNKNOWN 手工重发；"
                "attempts 为可见行数下界；last_attempt_at 未知=纯 sent 记账"
                "（SENT_LEDGER_ONLY）无回执时间",
    }


_DOC_TYPE_NAMES = {v["code"]: v["name"] for v in DOC_TYPES.values()}


def classification(db, tenant_id: str, target_code: str, days: int,
                   source_instance: str | None = None) -> dict | None:
    """A3：六类文书 × REPORT_BUCKETS 分类统计（只读，含零值行）。

    - 窗口按 COALESCE(last_attempt_at, first_seen_at)（与台账列表同一排序键
      口径对齐：同窗口台账总和=分类总和；last_attempt_at 未知的记录用
      first_seen_at 进窗并单列 unknown_time_records 计数）；
    - "其他/未分类"行收 5 新表（13-18）与非六类 business_type——保证总和
      可对齐且不静默丢弃；
    - 外部副本与平台影子 Mock 分栏（shadow_mock 单列，不合并为真实成功）。
    """
    if target_code != TARGET_CODE:
        return None
    from datetime import timedelta
    from sqlalchemy import func
    from contracts import REPORT_BUCKETS
    from core_db.models import (ExternalImportBatch,
                                ExternalSubmissionRecord,
                                InfectiousShadowRecord,
                                InfectiousTableTelemetry)
    sort_ts = func.coalesce(ExternalSubmissionRecord.last_attempt_at,
                            ExternalSubmissionRecord.first_seen_at)
    conds = [ExternalSubmissionRecord.tenant_id == tenant_id,
             ExternalSubmissionRecord.target_code == TARGET_CODE,
             sort_ts >= utcnow() - timedelta(days=days)]
    if source_instance:
        conds.append(ExternalSubmissionRecord.source_instance == source_instance)
    rows = (db.query(ExternalSubmissionRecord.doc_type_code,
                     ExternalSubmissionRecord.bucket,
                     func.count(ExternalSubmissionRecord.id))
            .filter(*conds).group_by(ExternalSubmissionRecord.doc_type_code,
                                     ExternalSubmissionRecord.bucket).all())
    by_type: dict[str, dict[str, int]] = {}
    sum_in_window = 0
    for doc_code, bucket, n in rows:
        t = by_type.setdefault(doc_code or "", {b: 0 for b in REPORT_BUCKETS})
        b = bucket if bucket in t else "UNKNOWN"
        t[b] += int(n)
        sum_in_window += int(n)
    # PT-03：逐表遥测（水位/滞后/最近班次/对账）——source_instance 过滤联动
    tconds = [InfectiousTableTelemetry.tenant_id == tenant_id,
              InfectiousTableTelemetry.target_code == TARGET_CODE]
    if source_instance:
        tconds.append(InfectiousTableTelemetry.source_instance == source_instance)
    tel_by_table = {t.table_key: t
                    for t in db.query(InfectiousTableTelemetry)
                    .filter(*tconds).all()}
    # PT-03：完整返回 13 张在推接口（含零值行）——消灭"其他/未分类"黑洞：
    # 每行附 parent_table/watermark/lag_hours/accepted/parent_waiting/
    # rejected/retryable/client_invalid 逐表计数；仅真正未登记的 14+ 表才进"其他"
    doc_types = []
    for table_key, meta in DOC_TYPES.items():
        code = meta["code"]
        counts = by_type.get(code) or {b: 0 for b in REPORT_BUCKETS}
        tel = tel_by_table.get(table_key)
        doc_types.append({
            "doc_type_code": code,
            "doc_type_name": meta["name"],
            "table_key": table_key,
            "endpoint": meta["endpoint"],
            "parent_table": meta["parent"],
            "buckets": {b: counts.get(b, 0) for b in REPORT_BUCKETS},
            "total": sum(counts.values()),
            # 逐表细分（监控矩阵直接消费）
            "accepted": counts.get("ACCEPTED", 0) + counts.get("CONFIRMED", 0),
            "parent_waiting": counts.get("PENDING", 0),
            "rejected": counts.get("REJECTED", 0),
            "retryable": counts.get("UNKNOWN", 0),
            "client_invalid": 0,     # 桶级并入 REJECTED（细节在台账检索按阶段过滤）
            "watermark": tel.watermark_op_time.isoformat()
            if tel and tel.watermark_op_time else None,
            "lag_hours": round(float(tel.watermark_lag_hours), 1)
            if tel and tel.watermark_lag_hours is not None else None,
            "last_shift_at": tel.last_shift_at.isoformat()
            if tel and tel.last_shift_at else None,
            "shift": {"rows": tel.shift_rows if tel else 0,
                      "sent": tel.shift_sent if tel else 0,
                      "parent_wait": tel.shift_parent_wait if tel else 0,
                      "skipped_invalid": tel.shift_skipped_invalid if tel else 0,
                      "failed": tel.shift_failed if tel else 0},
        })
    other = by_type.get("") or {b: 0 for b in REPORT_BUCKETS}
    known_codes = {m["code"] for m in DOC_TYPES.values()}
    for code, counts in by_type.items():
        if code and code not in known_codes:
            for b in REPORT_BUCKETS:
                other[b] += counts.get(b, 0)
    doc_types.append({"doc_type_code": "", "doc_type_name": "未登记的其他表"
                      "（14+ 非在推接口；正常应为 0）",
                      "buckets": {b: other.get(b, 0) for b in REPORT_BUCKETS},
                      "total": sum(other.values())})
    # 同窗口台账总和（同排序键口径）——对齐校验用
    ledger_total = (db.query(func.count(ExternalSubmissionRecord.id))
                    .filter(*conds).scalar() or 0)
    unknown_time = (db.query(func.count(ExternalSubmissionRecord.id))
                    .filter(ExternalSubmissionRecord.tenant_id == tenant_id,
                            ExternalSubmissionRecord.target_code == TARGET_CODE,
                            ExternalSubmissionRecord.last_attempt_at.is_(None))
                    .scalar() or 0)
    last_batch = (db.query(ExternalImportBatch)
                  .filter_by(tenant_id=tenant_id, target_code=TARGET_CODE,
                             dry_run=False)
                  .order_by(ExternalImportBatch.started_at.desc()).first())
    observed_until = (db.query(func.max(sort_ts))
                      .filter(ExternalSubmissionRecord.tenant_id == tenant_id,
                              ExternalSubmissionRecord.target_code == TARGET_CODE)
                      .scalar())
    instances = [r[0] for r in (
        db.query(ExternalSubmissionRecord.source_instance)
        .filter(ExternalSubmissionRecord.tenant_id == tenant_id,
                ExternalSubmissionRecord.target_code == TARGET_CODE)
        .distinct().all())]
    # 平台影子 Mock 分栏（infectious_shadow_record，mock 恒真；不并入外部桶）
    shadow_rows = (db.query(InfectiousShadowRecord.table_key,
                            InfectiousShadowRecord.state,
                            func.count(InfectiousShadowRecord.id))
                   .filter(InfectiousShadowRecord.tenant_id == tenant_id,
                           InfectiousShadowRecord.target_code == TARGET_CODE)
                   .group_by(InfectiousShadowRecord.table_key,
                             InfectiousShadowRecord.state).all())
    shadow_mock = [{"table": t, "state": s, "count": int(n)}
                   for t, s, n in shadow_rows]
    return {
        "target_code": TARGET_CODE,
        "window": {"days": days,
                   "note": "按 COALESCE(last_attempt_at, first_seen_at) 入窗"
                           "（与台账列表排序口径一致）"},
        "doc_types": doc_types,
        "sum_in_window": sum_in_window,
        "ledger_window_total": int(ledger_total),
        "unknown_time_records": int(unknown_time),
        "source_instances": sorted(i or "" for i in instances),
        "observed_until": observed_until.isoformat()
        if observed_until else None,
        "last_import_at": last_batch.started_at.isoformat()
        if last_batch else None,
        "shadow_mock": {"note": "平台本地影子（Mock 通道）单列，"
                         "不计入外部真实桶", "items": shadow_mock},
        "data_source": "external_ledger",
    }


def _conds(db, tenant_id, filters):
    from core_db.models import ExternalSubmissionRecord as R
    conds = [R.tenant_id == tenant_id, R.target_code == TARGET_CODE]
    from plugins.report_ledger import _day_window
    begin, end = _day_window(filters.date_from, filters.date_to)
    if begin:
        conds.append(_sort_col(R) >= begin)
    if end:
        conds.append(_sort_col(R) < end)
    if filters.bucket:
        conds.append(R.bucket == filters.bucket)   # 存的就是最终桶
    if filters.q:
        conds.append(R.record_key.like(f"{filters.q}%"))
    return conds


def _sort_col(R):
    from sqlalchemy import func
    return func.coalesce(R.last_attempt_at, R.first_seen_at)


def _search(db, tenant_id, filters, cursor, limit):
    from sqlalchemy import and_
    from core_db.models import ExternalSubmissionRecord as R
    from plugins import report_ledger as RL
    conds = _conds(db, tenant_id, filters)
    cur = RL.decode_cursor(cursor, RL.cursor_fingerprint(
        tenant_id, TARGET_CODE, filters, domain="external_ledger"))
    if cur:
        conds.append((_sort_col(R) < cur[0]) |
                     ((_sort_col(R) == cur[0]) & (R.id < cur[1])))
    order_col = _sort_col(R)
    rows = (db.query(R)
            .filter(and_(*conds))
            .order_by(order_col.desc(), R.id.desc())
            .limit(limit + 1).all())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = RL.encode_cursor(
            _sort_key(last), last.id,
            RL.cursor_fingerprint(tenant_id, TARGET_CODE, filters,
                                  domain="external_ledger"))
    return rows, next_cursor


def _total(db, tenant_id, filters):
    from sqlalchemy import and_, func
    from core_db.models import ExternalSubmissionRecord as R
    return (db.query(func.count(R.id))
            .filter(and_(*_conds(db, tenant_id, filters))).scalar() or 0)
