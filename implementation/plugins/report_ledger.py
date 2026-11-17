"""统一提交台账（LT-03）：通用 Submission 台账查询 + UNKNOWN 受控人工处置。

先筛后限硬规则：过滤条件全部进 SQL WHERE，再 ORDER BY updated_at DESC, id DESC
LIMIT n+1；游标 = base64("updated_at|id")。根治 2026-10-26 "先 limit 200 再筛选
漏掉旧 UNKNOWN" 的历史 bug。

人工处置（manual-resolution）：
- 仅通用 Submission 且当前 state=UNKNOWN；
- 条件 UPDATE（WHERE id AND state='UNKNOWN'）CAS：影响 0 行 → 冲突；
- CONFIRMED/REJECTED 直接置状态；RESEND_ALLOWED 只把状态改回 PREPARED，
  **不自动发送**；
- 写 ManualDecision + AuditEvent。
"""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

from contracts import REPORT_BUCKETS, SUBMISSION_STATE_TO_BUCKET
from core_db.engine import utcnow

# 院内日期（UTC+8）→ UTC datetime 的窗口换算
_HOSPITAL_OFFSET = timezone(timedelta(hours=8))


def _day_window(date_from: str | None, date_to: str | None):
    """'YYYY-MM-DD'（院内日界）→ [begin, end) UTC naive datetime 对。"""
    begin = end = None
    if date_from:
        d = datetime.strptime(date_from, "%Y-%m-%d")
        begin = d.replace(tzinfo=_HOSPITAL_OFFSET).astimezone(
            timezone.utc).replace(tzinfo=None)
    if date_to:
        d = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
        end = d.replace(tzinfo=_HOSPITAL_OFFSET).astimezone(
            timezone.utc).replace(tzinfo=None)
    return begin, end


def encode_cursor(updated_at, row_id: str, fingerprint: str = "") -> str:
    """v2 游标：base64("v2|<fingerprint>|<ts>|<id>")。

    fingerprint 绑定租户/target/筛选/排序上下文（LT-03-R1/R02）：换条件后
    旧游标不可用（decode 时校验，不匹配抛 CURSOR_CONTEXT_MISMATCH）——
    否则跨条件复用游标会静默漏行/串页。fingerprint 为空时退化为 v1 形状
    （仅同上下文内部使用）。"""
    ts = updated_at.isoformat() if isinstance(updated_at, datetime) \
        else str(updated_at)
    return base64.urlsafe_b64encode(
        f"v2|{fingerprint}|{ts}|{row_id}".encode()).decode()


def cursor_fingerprint(tenant_id: str, target_code: str, filters,
                       domain: str | None = None) -> str:
    """游标上下文指纹：tenant+target+domain+全部筛选条件（排序固定，不入指纹）。"""
    import hashlib as _hashlib
    parts = [str(tenant_id), str(target_code), str(domain or ""),
             str(getattr(filters, "bucket", None) or ""),
             str(getattr(filters, "date_from", None) or ""),
             str(getattr(filters, "date_to", None) or ""),
             str(getattr(filters, "q", None) or "")]
    return _hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def decode_cursor(cursor: str | None,
                  fingerprint: str | None = None) -> tuple[datetime, str] | None:
    """游标 → (datetime, id)。ts 必须解析为 datetime 对象传给 ORM——
    SQLite DateTime 列存储为 'YYYY-MM-DD HH:MM:SS.ffffff'（空格分隔），
    与 ISO 'T' 格式字符串直接比较会因字符序（T>空格）恒 False（分页失效）。

    LT-03-R1：给定 fingerprint 时校验游标上下文——不匹配抛
    ApiError(CURSOR_CONTEXT_MISMATCH)（换筛选条件须丢弃旧游标重新首页）。
    v1 旧形状（无 "v2|" 前缀）在要求指纹时视为不匹配。"""
    if not cursor:
        return None
    from contracts import ApiError
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        if raw.startswith("v2|"):
            _, fp, ts, rid = raw.split("|", 3)
            if fingerprint is not None and fp != fingerprint:
                raise ApiError(
                    "CURSOR_CONTEXT_MISMATCH",
                    "游标与当前筛选上下文不匹配（tenant/target/筛选变化后"
                    "须丢弃旧游标从首页重新查询）")
            return datetime.fromisoformat(ts), rid
        if fingerprint is not None:
            # v1 游标不携带上下文指纹：要求绑定时按不匹配拒绝
            raise ApiError(
                "CURSOR_CONTEXT_MISMATCH",
                "旧版游标不携带筛选上下文指纹，须从首页重新查询")
        ts, rid = raw.rsplit("|", 1)
        return datetime.fromisoformat(ts), rid
    except ApiError:
        raise
    except Exception:  # noqa: BLE001 —— 坏游标按无游标处理（首页）
        return None


def target_destination_ids(db, tenant_id: str, target_code: str) -> list[str]:
    """ReportTarget.destination_id 指定的目的地（可空）；空=该 target 无绑定。"""
    from core_db.models import ReportTarget
    t = (db.query(ReportTarget)
         .filter_by(tenant_id=tenant_id, code=target_code).first())
    if t is None or not t.destination_id:
        return []
    return [t.destination_id]


def _state_set_for_bucket(bucket: str) -> list[str]:
    """桶 → 原始状态集合（反查 SUBMISSION_STATE_TO_BUCKET）。"""
    return [s for s, b in SUBMISSION_STATE_TO_BUCKET.items() if b == bucket]


def search(db, tenant_id: str, target_code: str, filters,
           cursor: str | None, limit: int, *, domain: str | None = None):
    """台账查询（先筛后限）。domain=None 用通用 Submission（按 target 绑定的
    destination 过滤）；domain='drug_trace' 由药品钩子实现（本函数不处理）。

    返回 (rows[list[dict]], next_cursor)。行含原始 ORM 对象键 + bucket。
    """
    from sqlalchemy import and_, func
    from core_db.models import DataPackage, DeliveryAttempt, PackageRevision, \
        Submission

    states = (_state_set_for_bucket(filters.bucket)
              if filters.bucket else list(SUBMISSION_STATE_TO_BUCKET.keys()))
    begin, end = _day_window(filters.date_from, filters.date_to)
    fp = cursor_fingerprint(tenant_id, target_code, filters, domain)
    cur = decode_cursor(cursor, fp)
    dest_ids = target_destination_ids(db, tenant_id, target_code)

    conds = [Submission.tenant_id == tenant_id,
             Submission.state.in_(states)]
    if domain is None:
        # 通用：按 target 绑定的目的地圈定（无绑定 → 空页）
        if not dest_ids:
            return [], None
        conds.append(Submission.destination_id.in_(dest_ids))
    else:
        conds.append(DataPackage.domain == domain)
    if begin:
        conds.append(Submission.created_at >= begin)
    if end:
        conds.append(Submission.created_at < end)
    if filters.q:
        conds.append(Submission.id.like(f"{filters.q}%"))
    if cur:
        conds.append((Submission.updated_at < cur[0]) |
                     ((Submission.updated_at == cur[0]) &
                      (Submission.id < cur[1])))

    rows = (db.query(Submission)
            .outerjoin(PackageRevision,
                       PackageRevision.id == Submission.package_revision_id)
            .outerjoin(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(and_(*conds))
            .order_by(Submission.updated_at.desc(), Submission.id.desc())
            .limit(limit + 1)
            .all())

    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        last = rows[-1]
        next_cursor = encode_cursor(last.updated_at, last.id, fp)

    # 批量取每 submission 的 attempt 计数与最新一次（避免 N+1）
    att_rows = []
    if rows:
        ids = [r.id for r in rows]
        from sqlalchemy import func
        att_rows = (db.query(
            DeliveryAttempt.submission_id,
            func.count(DeliveryAttempt.id).label("n"))
            .filter(DeliveryAttempt.submission_id.in_(ids))
            .group_by(DeliveryAttempt.submission_id).all())
        counts = {sid: n for sid, n in att_rows}
        rn = func.row_number().over(
            partition_by=DeliveryAttempt.submission_id,
            order_by=DeliveryAttempt.attempt_no.desc()).label("rn")
        latest_q = (db.query(DeliveryAttempt.submission_id,
                             DeliveryAttempt.response_snapshot, rn)
                    .filter(DeliveryAttempt.submission_id.in_(ids)).subquery())
        latest = {sid: snap for sid, snap, rn_ in
                  db.query(latest_q).filter(latest_q.c.rn == 1).all()}
    else:
        counts, latest = {}, {}
    out = []
    for s in rows:
        n_att = counts.get(s.id, 0)
        snap = latest.get(s.id) or {}
        out.append({
            "id": s.id, "target_code": target_code,
            "business_key": s.idempotency_key or s.id,
            "bucket": SUBMISSION_STATE_TO_BUCKET.get(s.state, "UNKNOWN"),
            "raw_state": s.state,
            "created_at": s.created_at.isoformat() if s.created_at else "",
            "updated_at": s.updated_at.isoformat() if s.updated_at else "",
            "attempts": n_att,
            "last_error_code": snap.get("error_code"),
            "last_error_message": snap.get("error_message", ""),
            "detail_route": "/drugtrace" if domain == "drug_trace" else None,
            "_updated_at": s.updated_at,
        })
    return out, next_cursor


def total_estimate(db, tenant_id: str, target_code: str, filters,
                   *, domain: str | None = None) -> int | None:
    """同条件的总数估算（独立 COUNT；与页查询同 WHERE）。"""
    from sqlalchemy import and_, func
    from core_db.models import DataPackage, PackageRevision, Submission
    states = (_state_set_for_bucket(filters.bucket)
              if filters.bucket else list(SUBMISSION_STATE_TO_BUCKET.keys()))
    begin, end = _day_window(filters.date_from, filters.date_to)
    dest_ids = target_destination_ids(db, tenant_id, target_code)
    conds = [Submission.tenant_id == tenant_id, Submission.state.in_(states)]
    if domain is None:
        if not dest_ids:
            return 0
        conds.append(Submission.destination_id.in_(dest_ids))
    else:
        conds.append(DataPackage.domain == domain)
    if begin:
        conds.append(Submission.created_at >= begin)
    if end:
        conds.append(Submission.created_at < end)
    if filters.q:
        conds.append(Submission.id.like(f"{filters.q}%"))
    return (db.query(func.count(Submission.id))
            .outerjoin(PackageRevision,
                       PackageRevision.id == Submission.package_revision_id)
            .outerjoin(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(and_(*conds)).scalar() or 0)


# ---------------------------------------------------------------- 详情/处置
def submission_detail(db, tenant_id: str, submission_id: str, *,
                      target_code: str | None = None,
                      domain: str | None = None) -> dict | None:
    """通用 Submission 详情：基础字段 + attempts + receipts（时间序）。

    LT-03-R1（R02）：**必须**按 tenant+target+submission 绑定关系取数——
    target_code 与 domain 至少给一个：
    - domain 给定（领域认领）：Submission→PackageRevision→DataPackage.domain
      必须等于 domain，否则按不存在处理；
    - target_code 给定：target 绑定的 destination 集合非空且
      s.destination_id ∈ 集合，否则按不存在处理（target 未绑定目的地=
      无法证明归属，不得返回）。
    路由层校验 target 存在不能证明 submission 归属该 target。"""
    from core_db.models import DeliveryAttempt, ReceiptEvent, Submission
    if target_code is None and domain is None:
        raise ValueError("submission_detail requires target_code or domain "
                         "(tenant+target binding check, LT-03-R1)")
    s = (db.query(Submission)
         .filter_by(tenant_id=tenant_id, id=submission_id).first())
    if s is None:
        return None
    if domain is not None:
        from core_db.models import DataPackage, PackageRevision
        pkg_domain = (db.query(DataPackage.domain)
                      .join(PackageRevision,
                            PackageRevision.package_id == DataPackage.id)
                      .filter(PackageRevision.id == s.package_revision_id)
                      .scalar())
        if pkg_domain != domain:
            return None
    else:
        dest_ids = target_destination_ids(db, tenant_id, target_code)
        if not dest_ids or s.destination_id not in dest_ids:
            return None
    attempts = (db.query(DeliveryAttempt)
                .filter_by(submission_id=s.id)
                .order_by(DeliveryAttempt.attempt_no).all())
    receipts = (db.query(ReceiptEvent)
                .filter_by(submission_ref=s.id)
                .order_by(ReceiptEvent.id).all())
    return {
        "id": s.id, "state": s.state,
        "destination_id": s.destination_id,
        "package_revision_id": s.package_revision_id,
        "idempotency_key": s.idempotency_key,
        "snapshot_digest": s.snapshot_digest,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
        "attempts": [{
            "attempt_no": a.attempt_no, "outcome": a.outcome,
            "http_status": a.http_status,
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "response_snapshot": a.response_snapshot or {},
        } for a in attempts],
        "receipts": [{
            "external_receipt_id": r.external_receipt_id,
            "parsed_result": r.parsed_result or {},
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
        } for r in receipts],
    }


def manual_resolution(db, tenant_id: str, target_code: str, submission_id: str,
                      *, resolution: str, reason: str, evidence_ref: str,
                      expected_state: str, actor: str) -> dict:
    """UNKNOWN 受控人工处置：CAS + ManualDecision + AuditEvent。

    - 仅通用 Submission（药品走自己的受控流程，路由层已按 target 拒绝）；
    - 归属校验（LT-03-R1/R02）：target 绑定目的地非空且 submission 归属
      之，否则 NOT_FOUND——target 无绑定=无法证明归属，不得处置；
    - 处置前提（R02）：submission 有冻结请求摘要（snapshot_digest）且
      已有可审计尝试（≥1 行 DeliveryAttempt）——无冻结请求或零尝试的
      UNKNOWN 不满足"核对冻结请求与证据"的前提，拒绝；
    - 条件 UPDATE WHERE id AND state=expected_state；0 行 → CONFLICT；
    - RESEND_ALLOWED 只改回 PREPARED，**不自动发送**（不入队任何 job；
      worker 只执行显式入队任务，须整个调用链测试证明）。
    调用方 commit。
    """
    from sqlalchemy import and_
    from contracts import ApiError, new_uuid
    from core_db.models import (AuditEvent, DeliveryAttempt, ManualDecision,
                                Submission)
    from core_db.engine import utcnow

    if resolution not in ("CONFIRMED", "REJECTED", "RESEND_ALLOWED"):
        raise ApiError("VALIDATION_FAILED",
                       "resolution 只允许 CONFIRMED/REJECTED/RESEND_ALLOWED")
    if len((reason or "").strip()) < 5:
        raise ApiError("VALIDATION_FAILED", "reason 必须至少 5 个字符")
    if expected_state != "UNKNOWN":
        raise ApiError("VALIDATION_FAILED",
                       'expected_state 必须为 "UNKNOWN"（CAS 基准）')
    dest_ids = target_destination_ids(db, tenant_id, target_code)
    s = (db.query(Submission)
         .filter_by(tenant_id=tenant_id, id=submission_id).first())
    if s is None:
        raise ApiError("NOT_FOUND", "submission not found")
    if not dest_ids or s.destination_id not in dest_ids:
        # LT-03-R1：target 无绑定（dest_ids 空）同样拒绝——空绑定不能放过
        # 任意租户内 Submission（旧逻辑只在 dest_ids 非空时校验）
        raise ApiError("NOT_FOUND",
                       "submission not in this target's destinations "
                       "(target without destination binding cannot resolve)")
    if not (s.snapshot_digest or "").strip():
        raise ApiError("VALIDATION_FAILED",
                       "submission has no frozen snapshot_digest; cannot "
                       "resolve without frozen request (LT-03-R1)")
    n_attempts = (db.query(DeliveryAttempt)
                  .filter_by(submission_id=s.id).count())
    if n_attempts < 1:
        raise ApiError("VALIDATION_FAILED",
                       "submission has no auditable DeliveryAttempt; cannot "
                       "resolve without attempt evidence (LT-03-R1)")

    new_state = {"CONFIRMED": "CONFIRMED", "REJECTED": "REJECTED",
                 "RESEND_ALLOWED": "PREPARED"}[resolution]
    # 条件 UPDATE CAS：仍为 UNKNOWN 才允许
    from sqlalchemy import update
    res = db.execute(
        update(Submission)
        .where(and_(Submission.id == s.id,
                    Submission.state == "UNKNOWN"))
        .values(state=new_state, updated_at=utcnow()))
    if res.rowcount != 1:
        raise ApiError("CONFLICT",
                       f"submission state is no longer UNKNOWN "
                       f"(expected_state={expected_state})")

    db.add(ManualDecision(
        id=new_uuid(), tenant_id=tenant_id,
        entity_ref=f"submission:{s.id}",
        action=f"manual_resolution:{resolution}",
        before_ref={"state": "UNKNOWN", "target_code": target_code},
        after_ref={"state": new_state, "evidence_ref": evidence_ref or ""},
        reason=reason.strip(), actor=actor))
    db.add(AuditEvent(
        id=new_uuid(), tenant_id=tenant_id, actor=actor,
        action="report_ledger.manual_resolution",
        target=f"submission:{s.id}",
        new_ref={"resolution": resolution, "from_state": "UNKNOWN",
                 "to_state": new_state, "reason": reason.strip(),
                 "evidence_ref": evidence_ref or "",
                 "target_code": target_code}))
    return {"id": s.id, "from_state": "UNKNOWN", "to_state": new_state,
            "resolution": resolution}
