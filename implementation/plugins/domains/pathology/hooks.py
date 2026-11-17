"""病理领域钩子（LT-01）：overview 贡献 + 上报业务摘要（全部只读）。

- overview_contrib：core_api /admin/overview 中原本硬编码的 cases /
  cases_clinically_ready 两个键（自 app.py 原样搬入，键名/口径不变）。
- target_summary：pathology_cloud 业务的统一状态桶摘要，基于通用 Submission
  表按 DataPackage.domain='pathology' 统计（与药品同构；Mock 目的地上
  note='Mock 目的地数据'）。
"""
from __future__ import annotations

from datetime import timedelta

from contracts import REPORT_BUCKETS, SUBMISSION_STATE_TO_BUCKET
from core_db.engine import utcnow
from core_db.models import DataPackage, PackageRevision, Submission

TARGET_CODE = "pathology_cloud"


def overview_contrib(db, tenant_id: str) -> dict:
    """/admin/overview 的病理贡献键（与改前 app.py 硬编码完全同口径）。"""
    from domains.pathology.models import PathologyCase
    cases = db.query(PathologyCase).filter_by(tenant_id=tenant_id)
    return {
        "cases": cases.count(),
        "cases_clinically_ready": cases.filter_by(
            clinically_ready=True).count(),
    }


def target_summary(db, tenant_id: str, target_code: str, days: int):
    """pathology_cloud 摘要：通用 Submission 按病理域统计（只读）。

    LT-03-R1（R02）：应用 days 滚动窗口（旧实现统计全量、忽略 days）；
    窗口计数与全量未解决积压分开；最近业务确认时间全量口径。

    非本插件 target_code 返回 None（由注册顺序中第一个认领的插件响应）。
    """
    if target_code != TARGET_CODE:
        return None
    from plugins.domain_hooks import TargetSummary
    since = utcnow() - timedelta(days=days)
    buckets = {b: 0 for b in REPORT_BUCKETS}
    unmapped: dict[str, int] = {}
    rows = (db.query(Submission.state,
                     Submission.updated_at)
            .join(PackageRevision,
                  PackageRevision.id == Submission.package_revision_id)
            .join(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(Submission.tenant_id == tenant_id,
                    DataPackage.domain == "pathology",
                    Submission.updated_at >= since)
            .all())
    last_success_at = None
    for state, updated_at in rows:
        bucket = SUBMISSION_STATE_TO_BUCKET.get(state)
        if bucket is None:
            unmapped[state] = unmapped.get(state, 0) + 1
            bucket = "UNKNOWN"
        buckets[bucket] += 1
        if state == "CONFIRMED" and updated_at is not None:
            if last_success_at is None or updated_at > last_success_at:
                last_success_at = updated_at
    # 全量未解决积压（不受窗口裁剪）与全量最近确认
    unresolved = ("PENDING", "SENDING", "UNKNOWN")
    backlog_total = (db.query(Submission.id)
                     .join(PackageRevision,
                           PackageRevision.id == Submission.package_revision_id)
                     .join(DataPackage, DataPackage.id == PackageRevision.package_id)
                     .filter(Submission.tenant_id == tenant_id,
                             DataPackage.domain == "pathology",
                             Submission.state.in_(unresolved))
                     .count() or 0)
    last_all = (db.query(Submission.updated_at)
                .join(PackageRevision,
                      PackageRevision.id == Submission.package_revision_id)
                .join(DataPackage, DataPackage.id == PackageRevision.package_id)
                .filter(Submission.tenant_id == tenant_id,
                        DataPackage.domain == "pathology",
                        Submission.state == "CONFIRMED")
                .order_by(Submission.updated_at.desc())
                .first())
    if last_all is not None and last_all[0] is not None:
        last_success_at = last_all[0]
    # 病理链路当前全 Mock：数据仍是平台内真实 Submission 统计，但须标注来源形态
    return TargetSummary(
        target_code=target_code,
        window_days=days,
        buckets=buckets,
        daily=[],
        last_success_at=last_success_at.isoformat() if last_success_at else None,
        backlog=buckets.get("PENDING", 0) + buckets.get("SENDING", 0)
        + buckets.get("UNKNOWN", 0),
        unmapped_states=unmapped,
        data_source="platform",
        note="Mock 目的地数据（evidence_kind=mock：受理/确认均为本地 Mock 证据，"
             "不得当真实上报成功）；buckets=滚动窗口计数，backlog_total=全量"
             "未解决积压，last_success_at=全量最近业务确认",
        window_kind="rolling_days",
        backlog_total=int(backlog_total),
        quality_waiting=0,
        evidence_kind="mock",
    )


# ---------------------------------------------------------------- 统一台账（LT-03）
def ledger_search(db, tenant_id: str, target_code: str, filters,
                  cursor: str | None, limit: int):
    """病理台账（只读）：通用 Submission 按 target 绑定目的地先筛后限。"""
    if target_code != TARGET_CODE:
        return None
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerPage, LedgerRow
    rows, next_cursor = RL.search(db, tenant_id, target_code, filters,
                                  cursor, limit, domain="pathology")
    items = [LedgerRow(**{k: v for k, v in r.items() if not k.startswith("_")})
             for r in rows]
    total = RL.total_estimate(db, tenant_id, target_code, filters,
                              domain="pathology")
    return LedgerPage(items=items, next_cursor=next_cursor,
                      total_estimate=total)


def ledger_detail(db, tenant_id: str, target_code: str, submission_id: str):
    """病理台账详情（LT-03-R1）：按 domain='pathology' 绑定关系校验归属。"""
    if target_code != TARGET_CODE:
        return None
    from plugins import report_ledger as RL
    return RL.submission_detail(db, tenant_id, submission_id,
                                domain="pathology")
