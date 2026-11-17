"""药品追溯整单抽取与事务化导入（W04 → FIX2-03/04 第二轮修订）。

采集算法（DEVELOPMENT_PLAN §5）：
1. 主单发现：poll_bills 半开窗口 (updated_at, 复合键含 source_system) keyset
   分页，固定本轮上界，可配置重叠回看（重扫幂等）。
2. 整单读取：每张主单的明细按 source_code_record_id keyset 翻页到短页；
   读完复核主单 source_revision，中途变化整单重取一次，再变则登记持久重取
   账本 DrugTraceRetryBill（FIX2-03：不再只依赖短回看跨过持续变更单）。
3. 单事务落库：快照 + 导入去重 + 码索引 + SourceRecord 血缘 + 质量项 +
   outbox + checkpoint 全部一个事务；任一步失败全部回滚（A09）。
4. checkpoint 只表示「已安全持久化」；FIX3-02 修订为**页级事务**：每页全部
   快照+outbox+推进到本页尾的 checkpoint 同一事务提交；页边界安全点（gate，
   独立只读会话读 run/connector/证据）暂停/撤销即停，不推进窗口末 checkpoint
   + 游标严格推进保护（防死循环）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from contracts import ApiError, new_uuid
from core_db.engine import utcnow
from core_db.models import (QualityIssue, SourceCheckpoint, SourceRecord,
                            SourceRecordRevision)
from workflow_core.jobs import emit as emit_outbox

from . import contracts as C
from . import quality as Q
from .business_rules import (branch_wait_issue, require_continuous_mode,
                             resolve_business_kind)
from .models import (DrugTraceBill, DrugTraceBillRevision, DrugTraceCodeIndex,
                     DrugTraceCollectionRun, DrugTraceRetryBill)

STREAM = "drugtrace.bills"
_DIGEST_EXCLUDE = ("__namespace", "__collected_at")
MAX_PAGES_PER_WINDOW = 10000      # 游标不推进/源错误保护：超限报错不死循环


def _norm_dt(v):
    """视图时间 → 朴素 datetime（带时区的先转 UTC 再去 tz，保证游标可比；
    原值字符串仍完整保留在快照 header/lines 中）。"""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _digest_norm(v):
    """摘要序列化规范化（R2-07）：datetime/date/Decimal → 确定字符串。"""
    if isinstance(v, datetime):
        return _norm_dt(v).isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def content_digest(header: dict, lines: list[dict]) -> str:
    def norm(v):
        if isinstance(v, dict):
            return {k: norm(val) for k, val in sorted(v.items())
                    if k not in _DIGEST_EXCLUDE}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return _digest_norm(v)
    blob = json.dumps({"header": norm(header), "lines": norm(lines)},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def composite_key(header: dict) -> str:
    return "|".join([str(header.get("source_system", "") or ""),
                     str(header.get("pharmacy_code", "") or ""),
                     str(header.get("business_kind", "") or ""),
                     str(header.get("source_bill_id", "") or "")])


# ---------------------------------------------------------------- 整单读取
def read_bill_whole(warehouse, header: dict, *, code_page_limit: int = 200):
    """读完整单：明细翻页到短页；复核主单版本。

    返回 (lines, final_header, reread)。版本变化重读一次；仍变化抛 VERSION_CONFLICT
    语义错误（ApiError VERSION_CONFLICT，调用方登记持久重取账本后跳过）。
    """
    def _fetch(h):
        lines: list[dict] = []
        after = ""
        pages = 0
        while True:
            page = warehouse.fetch_all(
                "drugtrace.bill_codes",
                {"source_system": h.get("source_system", ""),
                 "pharmacy_code": h.get("pharmacy_code", ""),
                 "business_kind": h.get("business_kind", ""),
                 "source_bill_id": h.get("source_bill_id", ""),
                 "after_record_id": after},
                code_page_limit)
            pages += 1
            lines.extend(page)
            if len(page) < code_page_limit:
                return lines, pages
            after = str(page[-1].get("source_code_record_id", ""))
    lines, pages = _fetch(header)
    # 复核版本（A09：源版本中途变化等待重取）
    recheck = warehouse.fetch_all(
        "drugtrace.bill_header",
        {"source_system": header.get("source_system", ""),
         "pharmacy_code": header.get("pharmacy_code", ""),
         "business_kind": header.get("business_kind", ""),
         "source_bill_id": header.get("source_bill_id", "")}, 2)
    if not recheck:
        raise ApiError("SOURCE_UNAVAILABLE", "bill header disappeared during read")
    if str(recheck[0].get("source_revision")) == str(header.get("source_revision")):
        return lines, recheck[0], False
    # 版本变化：整单重取一次
    header2 = recheck[0]
    lines2, pages2 = _fetch(header2)
    recheck2 = warehouse.fetch_all(
        "drugtrace.bill_header",
        {"source_system": header2.get("source_system", ""),
         "pharmacy_code": header2.get("pharmacy_code", ""),
         "business_kind": header2.get("business_kind", ""),
         "source_bill_id": header2.get("source_bill_id", "")}, 2)
    if not recheck2 or str(recheck2[0].get("source_revision")) != \
            str(header2.get("source_revision")):
        raise ApiError("VERSION_CONFLICT",
                       "bill revision kept changing during whole-bill read")
    return lines2, header2, True


# ---------------------------------------------------------------- 导入一单
def import_bill(db, tenant_id: str, connector_id: str, header: dict,
                lines: list[dict], *, profile: str | None,
                config_release_id: str | None = None) -> dict:
    """幂等导入一张整单；返回 {bill_id, revision_id, created, state, issues}。

    同 source_revision 重导：digest 一致 → no-op（但 FIN-04：业务分支批准
    规则登记后，重导会把此前 UNKNOWN 的 business_kind 重新解析并更新 bill
    实体状态——快照原文永不改）；不一致 → QD-REVISION-CONFLICT
    （不覆盖既有快照，新冲突行不落库、登记质量项待人工）。
    """
    for field in C.BILL_VIEW_REQUIRED:
        if field not in header:
            raise ApiError("REQUIRED_FIELD_MISSING",
                           f"bill view missing required field {field}")
    for field in C.CODE_VIEW_REQUIRED:
        for ln in lines:
            if field not in ln:
                raise ApiError(
                    "REQUIRED_FIELD_MISSING",
                    f"code view missing required field {field}")

    digest = content_digest(header, lines)
    comp = composite_key(header)
    bill_no = Q.as_original_str(header.get("source_bill_no")
                                or header.get("source_bill_id"))
    # 稳定身份 = 完整来源主键（source_system+药房+业务+source_bill_id）；
    # source_bill_no 仅展示（R2-08：不同 source_bill_id 不得因同展示号合并）。
    bill = (db.query(DrugTraceBill)
            .filter_by(tenant_id=tenant_id, connector_id=connector_id,
                       source_system=str(header.get("source_system", "") or ""),
                       pharmacy_code=str(header.get("pharmacy_code", "") or ""),
                       business_kind=str(header.get("business_kind", "") or ""),
                       source_bill_id=Q.as_original_str(
                           header.get("source_bill_id"))).first())
    src_rev = str(header.get("source_revision"))
    if bill is None:
        bill = DrugTraceBill(
            id=new_uuid(), tenant_id=tenant_id, connector_id=connector_id,
            source_system=str(header.get("source_system", "") or ""),
            pharmacy_code=str(header.get("pharmacy_code", "") or ""),
            warehouse_code=str(header.get("warehouse_code", "") or ""),
            business_kind=str(header.get("business_kind", "") or ""),
            source_bill_id=Q.as_original_str(header.get("source_bill_id")),
            source_bill_no=bill_no)
        db.add(bill)
        db.flush()
        created_bill = True
    else:
        created_bill = False

    existing = (db.query(DrugTraceBillRevision)
                .filter_by(bill_id=bill.id, source_revision=src_rev).first())
    # FIN-04：业务分支解析（快照原文不变；bill 实体状态可随批准规则更新）
    resolved_kind = resolve_business_kind(header)
    if resolved_kind and resolved_kind != "UNKNOWN" \
            and bill.business_kind != resolved_kind:
        bill.business_kind = resolved_kind
    if existing is not None:
        if existing.content_digest == digest:
            return {"bill_id": bill.id, "revision_id": existing.id,
                    "created": False, "state": existing.quality_state,
                    "issues": existing.quality_issues or [], "conflict": False}
        # 同版本内容冲突：登记质量项，不覆盖快照（A10）
        conflict_issue = {"rule": "QD-REVISION-CONFLICT", "severity": "BLOCKING",
                          "detail": {"source_revision": src_rev,
                                     "stored_digest": existing.content_digest,
                                     "new_digest": digest}}
        if all(i.get("rule") != "QD-REVISION-CONFLICT"
               for i in (existing.quality_issues or [])):
            existing.quality_issues = (existing.quality_issues or []) + [conflict_issue]
            existing.quality_state = C.BillQualityState.INVALID
            Q.persist_issues(db, tenant_id, existing, existing.quality_issues)
        return {"bill_id": bill.id, "revision_id": existing.id, "created": False,
                "state": existing.quality_state,
                "issues": existing.quality_issues or [], "conflict": True}

    guard = 0
    if profile and profile in C.PROFILES:
        guard = int(C.PROFILES[profile]["code_limit_guard"])
    eval_header = dict(header)
    if resolved_kind and resolved_kind != "UNKNOWN":
        eval_header["business_kind"] = resolved_kind
    state, issues = Q.evaluate_revision(eval_header, lines, profile=profile,
                                        code_limit_guard=guard)
    branch_issue = branch_wait_issue(header)
    if branch_issue is not None:
        issues = list(issues) + [branch_issue]
        if state == C.BillQualityState.VALID:
            state = C.BillQualityState.WAITING
    rev = DrugTraceBillRevision(
        id=new_uuid(), tenant_id=tenant_id, bill_id=bill.id,
        source_revision=src_rev, content_digest=digest,
        header=dict(header), lines=list(lines),
        quality_state=state, quality_issues=issues,
        event_time=_norm_dt(header.get("bill_time")) or utcnow(),
        source_evidence={"composite_key": comp},
        config_release_id=config_release_id)
    db.add(rev)
    db.flush()
    # 码索引（原值字符串；重复 record_id 由唯一约束兜底——视图契约违规已在质量层报）
    seen_rids: set[str] = set()
    for ln in lines:
        rid = str(ln.get("source_code_record_id", "") or "")
        if rid in seen_rids:
            continue  # 重复来源行：质量项已登记 INVALID，这里不重复建索引
        seen_rids.add(rid)
        db.add(DrugTraceCodeIndex(
            id=new_uuid(), tenant_id=tenant_id, bill_id=bill.id,
            bill_revision_id=rev.id,
            source_line_id=Q.as_original_str(ln.get("source_line_id")),
            source_code_record_id=rid,
            trace_code=Q.as_original_str(ln.get("trace_code")),
            code_level=str(ln.get("code_level", "") or ""),
            parent_code=Q.as_original_str(ln.get("parent_code")),
            quantity="" if ln.get("quantity") in (None, "")
            else Q.as_original_str(ln.get("quantity")),
            unit=str(ln.get("unit", "") or ""),
            is_deleted=Q.as_bool(ln.get("is_deleted"), "is_deleted"),
            updated_at=_norm_dt(ln.get("updated_at"))))
    # 当前态指针 + SourceRecord 血缘（布尔字段经 as_bool：Oracle '0' 不再误判真）
    bill.current_source_revision = src_rev
    bill.current_revision_id = rev.id
    bill.source_updated_at = _norm_dt(header.get("updated_at"))
    bill.bill_time = _norm_dt(header.get("bill_time"))
    bill.source_event_seq = str(header.get("source_event_seq", "") or "")
    bill.finalized = Q.as_bool(header.get("finalized"), "finalized")
    bill.is_void = Q.as_bool(header.get("is_void"), "is_void")
    bill.source_status = "VOIDED" if bill.is_void else "ACTIVE"
    bill.warehouse_code = str(header.get("warehouse_code", "") or "")
    rec = (db.query(SourceRecord)
           .filter_by(tenant_id=tenant_id,
                      namespace=str(header.get("source_system", "") or "HIS"),
                      entity_type="drug_trace_bill",
                      external_id=comp).first())
    if rec is None:
        rec = SourceRecord(id=new_uuid(), tenant_id=tenant_id,
                           namespace=str(header.get("source_system", "") or "HIS"),
                           entity_type="drug_trace_bill", external_id=comp)
        db.add(rec)
        db.flush()
    rec.current_revision = rev.id
    db.add(SourceRecordRevision(
        id=new_uuid(), tenant_id=tenant_id, record_id=rec.id,
        source_version=src_rev, source_change_time=bill.source_updated_at,
        raw_payload={"header": header, "lines": lines}, digest=digest,
        observed_by="drugtrace_window_scan"))
    Q.persist_issues(db, tenant_id, rev, issues)
    emit_outbox(db, tenant_id, f"drug_trace_bill:{comp}",
                C.DomainEvent.BILL_INGESTED,
                {"bill_id": bill.id, "revision_id": rev.id,
                 "quality_state": state, "business_kind": bill.business_kind,
                 "composite_key": comp})
    return {"bill_id": bill.id, "revision_id": rev.id, "created": True,
            "state": state, "issues": issues, "conflict": False}


# ---------------------------------------------------------------- 持久重取账本
def _record_retry(db, tenant_id: str, connector_id: str, header: dict,
                  reason: str) -> None:
    """持续变更/读取失败单登记持久重取（FIX2-03：不只依赖短回看跨过）。"""
    key = {"tenant_id": tenant_id, "connector_id": connector_id,
           "source_system": str(header.get("source_system", "") or ""),
           "pharmacy_code": str(header.get("pharmacy_code", "") or ""),
           "business_kind": str(header.get("business_kind", "") or ""),
           "source_bill_id": str(header.get("source_bill_id", "") or "")}
    row = db.query(DrugTraceRetryBill).filter_by(**key).first()
    if row is None:
        db.add(DrugTraceRetryBill(
            id=new_uuid(), reason=reason, attempts=1,
            first_seen_at=utcnow(), last_seen_at=utcnow(), **key))
    else:
        row.reason = reason
        row.attempts = (row.attempts or 0) + 1
        row.last_seen_at = utcnow()


def _drain_retry_ledger(db, tenant_id: str, connector_id: str, warehouse,
                        summary: dict, *, code_page_limit: int) -> None:
    """窗口扫描前先重取账本中的失败单（成功即出账；失败保留计数）。"""
    rows = (db.query(DrugTraceRetryBill)
            .filter_by(tenant_id=tenant_id, connector_id=connector_id).all())
    for row in rows:
        headers = warehouse.fetch_all(
            "drugtrace.bill_header",
            {"source_system": row.source_system,
             "pharmacy_code": row.pharmacy_code,
             "business_kind": row.business_kind,
             "source_bill_id": row.source_bill_id}, 2)
        if not headers:
            row.reason = "header-missing-on-retry"
            row.attempts = (row.attempts or 0) + 1
            row.last_seen_at = utcnow()
            continue
        try:
            lines, final_header, reread = read_bill_whole(
                warehouse, headers[0], code_page_limit=code_page_limit)
            profile = C.profile_for_kind(
                resolve_business_kind(final_header))
            import_bill(db, tenant_id, connector_id, final_header, lines,
                        profile=profile, config_release_id=None)
        except ApiError as exc:
            row.reason = f"retry-failed:{exc.code}"
            row.attempts = (row.attempts or 0) + 1
            row.last_seen_at = utcnow()
            summary["retried_failed"] = summary.get("retried_failed", 0) + 1
            continue
        db.delete(row)
        summary["retried_ok"] = summary.get("retried_ok", 0) + 1


# ---------------------------------------------------------------- 窗口采集
def key_parts(header: dict) -> list[str]:
    """游标复合键的逐字段表示（与 SQL 谓词/ORDER BY 同一契约；FIX3-02）。"""
    return [str(header.get("source_system", "") or ""),
            str(header.get("pharmacy_code", "") or ""),
            str(header.get("business_kind", "") or ""),
            str(header.get("source_bill_id", "") or "")]


def _cursor_key_parts(cursor: dict) -> list[str]:
    """读游标键。旧（FIX2 及之前）竖线拼接字符串键不可安全拆分——视为无键，
    从 since_time 起整刻重扫（导入幂等，宁可重扫不可漏单）。"""
    raw = cursor.get("since_key")
    if isinstance(raw, list) and len(raw) == 4:
        return [str(x) for x in raw]
    return ["", "", "", ""]


def collect_window(db, tenant_id: str, connector_id: str, warehouse, *,
                   window_begin, window_end, run: DrugTraceCollectionRun,
                   page_limit: int = 100, code_page_limit: int = 200,
                   lookback: timedelta | None = None,
                   gate=None, partition: str = "0") -> dict:
    """窗口采集：**每页一个事务**（FIX3-02/R3-05 ADR）。

    事务边界：一页内全部整单快照 + 去重导入 + 码索引 + 血缘 + 质量项 + outbox +
    「推进到本页尾的 checkpoint」在同一事务提交；任一步失败整页回滚，checkpoint
    停在上一已提交页尾。重启后从该 checkpoint 续跑（重扫页幂等），不漏不重。
    旧实现把页边界 db.commit() 当安全点，会在无 checkpoint 时隐式提交整页数据
    （R3-05-atomic 反例：1 单持久化、checkpoint=0）；本 ADR 明确弃用整窗大事务
    （长窗锁持有与中断恢复代价高），改为页级原子 + 安全点串联。

    gate（页边界安全点，service 注入）：返回 None 继续；返回停止原因
    （"paused"/"cancelled"/"blocked:<detail>"）即停——检查发生在独立只读会话，
    能看到其他事务提交的 PAUSED/CANCELLED；重取账本与首页之前也先过安全点
    （FIX3-02：重取账本不再先于安全点执行）。

    window_begin/window_end：datetime（半开 [begin, end)）。lookback 回看由
    调用方传参；回看会同时清掉键游标（时间被拉回后旧键下界会导致漏单）。
    partition：checkpoint 分区（增量默认 "0"；历史补报用独立分区，G3-06）。
    """
    cp = (db.query(SourceCheckpoint)
          .filter_by(tenant_id=tenant_id, connector_id=connector_id,
                     stream=STREAM, partition=partition).first())
    cursor = dict(cp.cursor_json or {}) if cp is not None else {}
    summary = {"bills_seen": 0, "revisions_created": 0, "idempotent": 0,
               "waiting": 0, "valid": 0, "invalid": 0, "conflicts": 0,
               "version_rereads": 0, "skipped_version_changing": 0,
               "retried_ok": 0, "retried_failed": 0}

    def _write_checkpoint(c: dict) -> None:
        nonlocal cp
        if cp is None:
            cp = SourceCheckpoint(
                id=new_uuid(), tenant_id=tenant_id, connector_id=connector_id,
                stream=STREAM, partition=partition, cursor_json=c)
            db.add(cp)
        else:
            cp.cursor_json = c
            cp.row_version += 1

    # 首个安全点：重取账本与首页之前先复核 run/connector/证据（FIX3-02）
    if gate is not None:
        cause = gate()
        if cause:
            summary["stopped"] = cause
            return summary
    _drain_retry_ledger(db, tenant_id, connector_id, warehouse, summary,
                        code_page_limit=code_page_limit)
    db.commit()   # 重取账本独立提交（幂等；不与首页事务捆绑）
    # 上次窗口未完成：从上次 cursor 续跑；重叠回看（可配置）把 cursor 向前拉，
    # 重新扫描临近边界（重扫幂等；A08 迟到码/回补）
    since_time = _norm_dt(cursor.get("since_time")) or window_begin
    since_parts = _cursor_key_parts(cursor)
    if cursor.get("completed_window"):
        since_time = window_begin        # 上一窗完整结束：新窗从下界起
        since_parts = ["", "", "", ""]
    if lookback is not None and since_time > window_begin:
        since_time = max(since_time - lookback, window_begin)
        since_parts = ["", "", "", ""]   # 时间回拉后旧键下界不再适用（防漏）
    pages = 0
    while True:
        page = warehouse.fetch_all(
            "drugtrace.poll_bills",
            {"begin_time": since_time, "end_time": window_end,
             "since_time": since_time,
             "since_ss": since_parts[0], "since_pc": since_parts[1],
             "since_bk": since_parts[2], "since_sb": since_parts[3]},
            page_limit)
        pages += 1
        if pages > MAX_PAGES_PER_WINDOW:
            raise ApiError("SOURCE_LOOP_SUSPECTED",
                           f"pagination exceeded {MAX_PAGES_PER_WINDOW} pages "
                           "without advancing; aborting (source error guard)")
        for header in page:
            summary["bills_seen"] += 1
            try:
                lines, final_header, reread = read_bill_whole(
                    warehouse, header, code_page_limit=code_page_limit)
            except ApiError as exc:
                if exc.code == "VERSION_CONFLICT":
                    summary["skipped_version_changing"] += 1
                    _record_retry(db, tenant_id, connector_id, header,
                                  "version-conflict")
                    continue
                raise
            if reread:
                summary["version_rereads"] += 1
            profile = C.profile_for_kind(resolve_business_kind(final_header))
            result = import_bill(db, tenant_id, connector_id, final_header,
                                 lines, profile=profile,
                                 config_release_id=None)
            if result["created"]:
                summary["revisions_created"] += 1
            elif result.get("conflict"):
                summary["conflicts"] += 1
            else:
                summary["idempotent"] += 1
            if result["state"] == C.BillQualityState.VALID:
                summary["valid"] += 1
            elif result["state"] == C.BillQualityState.WAITING:
                summary["waiting"] += 1
            else:
                summary["invalid"] += 1
        # ---- 页级事务提交：本页全部快照 + 推进到本页尾的 checkpoint 原子落库
        if len(page) < page_limit:
            _write_checkpoint({"since_time": window_end.isoformat(),
                               "since_key": ["", "", "", ""],
                               "window_end": window_end.isoformat(),
                               "completed_window": True})
            db.commit()
            break
        tail = page[-1]
        new_time = _norm_dt(tail.get("updated_at"))
        new_parts = key_parts(tail)
        if new_time == since_time and new_parts == since_parts:
            raise ApiError(
                "SOURCE_LOOP_SUSPECTED",
                f"pagination cursor did not advance at ({since_time}, "
                f"{new_parts}); aborting (duplicate-page guard)")
        since_time, since_parts = new_time, new_parts
        _write_checkpoint({
            "since_time": since_time.isoformat() if since_time
            else window_begin.isoformat(),
            "since_key": list(since_parts),
            "window_end": window_end.isoformat(),
            "completed_window": False})
        db.commit()
        if gate is not None:
            cause = gate()
            if cause:
                summary["stopped"] = cause
                break
    return summary
