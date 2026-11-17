"""济南医保影像云索引钩子（RT-06 F03）：summary/台账只认 medins_imaging_jn。

- 查询按 DataPackage.domain='medins_imaging' 绑定（tenant+日期窗口），不把
  其他业务记录合进来；
- Submission 桶映射按 SUBMISSION_STATE_TO_BUCKET；Publication 单独列
  publication_count（不与 Submission 双计数）；
- 无数据 data_source=none；Mock 目的地记录 evidence_kind=mock，不冒充
  CONFIRMED（last_success_at 仅取 CONFIRMED——Mock 回环通常无）。
"""
from __future__ import annotations

TARGET_CODE = "medins_imaging_jn"
DOMAIN = "medins_imaging"


def target_summary(db, tenant_id: str, target_code: str, days: int):
    if target_code != TARGET_CODE:
        return None
    from datetime import timedelta

    from contracts import REPORT_BUCKETS, SUBMISSION_STATE_TO_BUCKET
    from core_db.engine import utcnow
    from core_db.models import (DataPackage, Destination, PackageRevision,
                                Publication, Submission)
    from plugins.domain_hooks import TargetSummary

    since = utcnow() - timedelta(days=days)
    rows = (db.query(Submission)
            .join(PackageRevision,
                  PackageRevision.id == Submission.package_revision_id)
            .join(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(DataPackage.tenant_id == tenant_id,
                    DataPackage.domain == DOMAIN,
                    Submission.created_at >= since)
            .all())
    buckets = {b: 0 for b in REPORT_BUCKETS}
    daily: dict[str, dict[str, int]] = {}
    evidence = ""
    for s in rows:
        bucket = SUBMISSION_STATE_TO_BUCKET.get(s.state, "UNKNOWN")
        buckets[bucket] += 1
        day = s.created_at.date().isoformat()
        daily.setdefault(day, {})
        daily[day][bucket] = daily[day].get(bucket, 0) + 1
        dest = db.get(Destination, s.destination_id)
        if dest is not None and dest.is_mock:
            evidence = "mock"
    pubs = (db.query(Publication)
            .join(PackageRevision,
                  PackageRevision.id == Publication.package_revision_id)
            .join(DataPackage, DataPackage.id == PackageRevision.package_id)
            .filter(DataPackage.tenant_id == tenant_id,
                    DataPackage.domain == DOMAIN,
                    Publication.created_at >= since)
            .count())
    if not rows:
        return TargetSummary(
            target_code=TARGET_CODE, window_days=days,
            buckets={b: 0 for b in REPORT_BUCKETS}, data_source="none",
            note="无投递记录（本地示范投影未运行或窗口外）")
    return TargetSummary(
        target_code=TARGET_CODE, window_days=days, buckets=buckets,
        daily=[{"date": d, **c} for d, c in sorted(daily.items())],
        backlog=buckets.get("UNKNOWN", 0) + buckets.get("SENDING", 0),
        backlog_total=buckets.get("UNKNOWN", 0) + buckets.get("SENDING", 0),
        last_success_at=None,                    # Mock 回环无业务确认
        data_source="platform", evidence_kind=evidence or "mock",
        note=f"本地示范投影（Mock）；Publication {pubs} 条单列，不与 "
             "Submission 双计数", quality_waiting=0)


def ledger_search(db, tenant_id: str, target_code: str, filters,
                  cursor: str | None, limit: int):
    if target_code != TARGET_CODE:
        return None
    from plugins import report_ledger as RL
    from plugins.domain_hooks import LedgerPage, LedgerRow
    rows, next_cursor = RL.search(db, tenant_id, target_code, filters,
                                  cursor, limit, domain=DOMAIN)
    items = [LedgerRow(**{k: v for k, v in r.items()
                          if not k.startswith("_")})
             for r in rows]
    total = RL.total_estimate(db, tenant_id, target_code, filters,
                              domain=DOMAIN)
    return LedgerPage(items=items, next_cursor=next_cursor,
                      total_estimate=total)


def ledger_detail(db, tenant_id: str, target_code: str, submission_id: str):
    if target_code != TARGET_CODE:
        return None
    from plugins import report_ledger as RL
    return RL.submission_detail(db, tenant_id, submission_id, domain=DOMAIN)
