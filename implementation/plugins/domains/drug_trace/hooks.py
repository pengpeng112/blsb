"""药品追溯领域钩子（LT-01）：上报业务摘要（只读统计，复用既有统计口径）。

- target_summary(drug_trace_alihealth)：把药品域 Submission 状态映射到统一
  REPORT_BUCKETS；统计口径与 routes.py::upload_stats / overview 完全一致
  （按 DataPackage.domain='drug_trace' 圈定药品域提交，不混计其他领域，
  R2-16）。**严禁**调用任何写库/入队/触网函数——本模块只做 SELECT。
- NOT_REPORTABLE（K1 终态）与 WAITING（质量等待）为药品自有状态，映射到
  同名桶 / PENDING；未映射状态计入 UNKNOWN 并在 unmapped_states 列出。
"""
from __future__ import annotations

from datetime import timedelta

from contracts import REPORT_BUCKETS, SUBMISSION_STATE_TO_BUCKET
from core_db.engine import utcnow
from core_db.models import DataPackage, PackageRevision, Submission

TARGET_CODE = "drug_trace_alihealth"

# 药品域 Submission 状态 → 统一桶（通用 7 态沿用公共映射；
# 另补药品自有终态与领域等待态）
DRUG_TRACE_STATE_TO_BUCKET: dict[str, str] = {
    **SUBMISSION_STATE_TO_BUCKET,
    "NOT_REPORTABLE": "NOT_REPORTABLE",   # K1 终态（QD-NO-CODES-FINAL 等）
    "WAITING": "PENDING",                 # 质量等待（未生成提交前的领域状态）
}


def target_summary(db, tenant_id: str, target_code: str, days: int, *,
                   window=None):
    """码上放心业务摘要（只读；LT-03-R1 口径分离）。

    - buckets/daily：**滚动 N 天窗**内 Submission 计数（created_at ≥ since；
      daily 按存储日期分组）；
    - RT-03 S04 兼容扩展：window=(begin, end) 显式 UTC 半开区间（早报自然日
      窗口）——给定时按区间过滤并返回 window_kind="hospital_day"；不给时
      滚动语义与旧行为完全一致（签名仅新增关键字参数，旧调用零变化）；
    - backlog_total：**全量**未解决（PENDING/SENDING/UNKNOWN）——窗外 UNKNOWN
      不被窗口隐藏；
    - quality_waiting：尚无 Submission 的质量等待单据（当前版本
      quality_state=WAITING 的 DrugTraceBill 数）；
    - last_success_at：全量最近一次 CONFIRMED（业务确认，与窗口无关）。
    三组数字不得相加成"全院实时成功率"。"""
    if target_code != TARGET_CODE:
        return None
    from sqlalchemy import func
    from plugins.domain_hooks import TargetSummary
    since = utcnow() - timedelta(days=days)
    window_kind = "rolling_days"
    conds = [Submission.tenant_id == tenant_id,
             Submission.created_at >= since]
    if window is not None:
        begin, end = window
        conds = [Submission.tenant_id == tenant_id,
                 Submission.created_at >= begin,
                 Submission.created_at < end]
        window_kind = "hospital_day"
    buckets = {b: 0 for b in REPORT_BUCKETS}
    unmapped: dict[str, int] = {}
    daily_rows: dict[str, dict[str, int]] = {}
    for d, state, n in (
            db.query(func.date(Submission.created_at), Submission.state,
                     func.count())
            .join(PackageRevision,
                  PackageRevision.id == Submission.package_revision_id)
            .join(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(*conds)
            .group_by(func.date(Submission.created_at), Submission.state)
            .all()):
        bucket = DRUG_TRACE_STATE_TO_BUCKET.get(str(state))
        if bucket is None:
            unmapped[str(state)] = unmapped.get(str(state), 0) + n
            bucket = "UNKNOWN"
        buckets[bucket] += n
        day = daily_rows.setdefault(str(d), {})
        day[bucket] = day.get(bucket, 0) + n
    # 全量未解决积压（不受窗口裁剪；窗外 UNKNOWN 计入）
    unresolved = ("PENDING", "SENDING", "UNKNOWN")
    backlog_total = (db.query(func.count(Submission.id))
                     .join(PackageRevision,
                           PackageRevision.id == Submission.package_revision_id)
                     .join(DataPackage, DataPackage.id == PackageRevision.package_id)
                     .filter(Submission.tenant_id == tenant_id,
                             DataPackage.domain == "drug_trace",
                             Submission.state.in_(unresolved))
                     .scalar() or 0)
    # 无 Submission 的质量等待单据（当前版本 quality_state=WAITING）
    quality_waiting = 0
    try:
        from domains.drug_trace.models import DrugTraceBill, \
            DrugTraceBillRevision
        quality_waiting = (db.query(func.count(DrugTraceBill.id))
                           .join(DrugTraceBillRevision,
                                 DrugTraceBillRevision.id ==
                                 DrugTraceBill.current_revision_id)
                           .filter(DrugTraceBill.tenant_id == tenant_id,
                                   DrugTraceBillRevision.quality_state ==
                                   "WAITING")
                           .scalar() or 0)
    except Exception:  # noqa: BLE001 —— 模型未加载（metrics-only 进程）不计
        quality_waiting = 0
    last = (db.query(Submission.updated_at)
            .join(PackageRevision,
                  PackageRevision.id == Submission.package_revision_id)
            .join(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(Submission.tenant_id == tenant_id,
                    DataPackage.domain == "drug_trace",
                    Submission.state == "CONFIRMED")
            .order_by(Submission.updated_at.desc())
            .first())
    last_success_at = last[0] if last is not None and last[0] is not None else None
    daily = [{"date": d, **counts} for d, counts in sorted(daily_rows.items())]
    return TargetSummary(
        target_code=target_code,
        window_days=days,
        buckets=buckets,
        daily=daily,
        last_success_at=last_success_at.isoformat() if last_success_at else None,
        backlog=buckets.get("PENDING", 0) + buckets.get("SENDING", 0)
        + buckets.get("UNKNOWN", 0),
        unmapped_states=unmapped,
        data_source="platform",
        note="生产链路：宿主驱动逐窗发送；台账 sole_pusher 登记不影响链路；"
             "buckets/backlog=窗口计数（window_kind 标注滚动/院内自然日），"
             "backlog_total=全量未解决积压（含窗外 UNKNOWN），"
             "quality_waiting=无提交的质量等待单据，daily 按存储日期(UTC)分组",
        window_kind=window_kind,
        backlog_total=int(backlog_total),
        quality_waiting=int(quality_waiting),
        evidence_kind="real",
    )


# ---------------------------------------------------------------- 统一台账（LT-03）
def ledger_search(db, tenant_id: str, target_code: str, filters,
                  cursor: str | None, limit: int):
    """药品台账（只读）：按 DataPackage.domain='drug_trace' 先筛后限。

    不复用/不改 routes.py 既有查询（API 层带鉴权与响应包装，形状不同）；
    这里是纯只读查询，bucket 用 DRUG_TRACE_STATE_TO_BUCKET 反查（ Submission
    的状态是通用状态；药品的 NOT_REPORTABLE/WAITING 是领域单据态，不出现在
    Submission 上）。detail_route=/drugtrace。
    """
    if target_code != TARGET_CODE:
        return None
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerPage, LedgerRow
    rows, next_cursor = RL.search(db, tenant_id, target_code, filters,
                                  cursor, limit, domain="drug_trace")
    items = [LedgerRow(**{k: v for k, v in r.items() if not k.startswith("_")})
             for r in rows]
    total = RL.total_estimate(db, tenant_id, target_code, filters,
                              domain="drug_trace")
    return LedgerPage(items=items, next_cursor=next_cursor,
                      total_estimate=total)
