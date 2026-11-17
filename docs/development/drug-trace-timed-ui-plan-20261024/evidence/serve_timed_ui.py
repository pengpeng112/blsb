"""TUI-05/06 浏览器验证栈（仅合成数据，2026-10-24）。

复用 tests/e2e/serve_drug_trace_ui.py 的合成栈（新 SQLite + Mock 目的地 + worker），
启动前追加种子：
- send 证据（APPROVED + EXT-12 scope + 配额台账 used=2/80000）→ 授权行有值；
- 日报两行（D-1 OK、D-3 OK；D-2 故意缺失）→ FIX-05 断档演示：
  连续正常=1 天（最新 D-1 OK，D-2 缺整天即停，不再累计 D-3）；
- 合成提交三笔（CONFIRMED/UNKNOWN 异常/PREPARED 未发）+ delivery_attempt →
  每日实际（含「其他」列=1，PREPARED）与异常提交两区可验证。

用法：python serve_timed_ui.py [port]   （UI: http://127.0.0.1:<port>/ui/）
令牌：ui-admin-token-0123456789abcdef（admin 具备 operator/platform_admin 角色）
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_IMPL = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..",
                                     "implementation"))
sys.path.insert(0, os.path.join(_IMPL, "tests", "e2e"))
sys.path.insert(0, _IMPL)

import serve_drug_trace_ui as srv  # noqa: E402  合成栈整体初始化（新库+worker）

from datetime import date, datetime, timedelta  # noqa: E402

from contracts import new_uuid  # noqa: E402
from core_db.models import (DataPackage, DeliveryAttempt, Destination,  # noqa: E402
                            PackageRevision, Submission, Tenant)
from domains.drug_trace import service  # noqa: E402
from domains.drug_trace.models import DrugTraceDailyReport  # noqa: E402

RETAIL = "alihealth_yljg_retail_v1"
_keys = ["EXT-03", "EXT-04", "EXT-05", "EXT-06", "EXT-07", "EXT-08",
         "EXT-10", "EXT-12"]

with srv.sf() as db:
    t = db.query(Tenant).filter_by(code="first-hospital").one()
    dest = db.query(Destination).first()
    # send 证据 + 配额（used=2 演示递增后的真实台账读数）
    ev = service.approve_evidence(
        db, t.id, profile=RETAIL, capability="send", destination_id=dest.id,
        provided_items=[{"key": k, "evidence_ref": f"doc:{k}"} for k in _keys],
        approved_by="seed-admin", valid_until=datetime(2026, 11, 23),
        scope={"window_begin": "2026-10-24T00:00:00",
               "window_end": "2026-11-23T00:00:00",
               "pharmacies": ["050121", "050102", "050101", "050116", "050117"],
               "max_bills": 80000})
    from domains.drug_trace.models import DrugTraceSendQuota
    db.flush()
    quota = (db.query(DrugTraceSendQuota)
             .filter_by(tenant_id=t.id, evidence_id=ev.id).first())
    quota.used = 2
    # 日报两行（D-1 OK、D-3 OK；D-2 缺失 → FIX-05 断档：连续正常=1 天）
    today = date.today()
    for offset, outcome, send in (
            (1, "OK", {"sent": 1780, "prepared_new": 1775, "resumed": 3,
                       "skipped_terminal": 2, "errors": [],
                       "aborted_reason": ""}),
            (3, "OK",
             {"sent": 1740, "prepared_new": 1735, "resumed": 0,
              "skipped_terminal": 1, "errors": [],
              "aborted_reason": ""})):
        d = today - timedelta(days=offset)
        db.add(DrugTraceDailyReport(
            id=new_uuid(), tenant_id=t.id, report_date=d, source="timer",
            window_begin=datetime(d.year, d.month, d.day) - timedelta(days=1),
            window_end=datetime(d.year, d.month, d.day),
            candidate_count=1782 - offset, 
            collection={"id": f"run-seed-{offset}", "state": "DONE",
                        "summary": {"bills_seen": 3500 - offset,
                                    "revisions_created": 3400,
                                    "valid": 1782 - offset,
                                    "waiting": 1600 + offset}},
            send=send,
            query_stats={"queried": 120, "errors": []},
            outcome=outcome, elapsed_seconds=4480.2 - offset * 10))
    # 合成提交：CONFIRMED / UNKNOWN(异常) / PREPARED(未发)
    y = datetime.now() - timedelta(days=1)
    for state, attempts in (
            ("CONFIRMED", [("ACCEPTED", 200)]),
            ("UNKNOWN", [("UNKNOWN", 500)]),
            ("PREPARED", [])):
        pkg = DataPackage(id=new_uuid(), tenant_id=t.id, domain="drug_trace",
                          business_key=f"drug_trace|seed|{state}")
        pr = PackageRevision(id=new_uuid(), tenant_id=t.id,
                             package_id=pkg.id, revision=1,
                             schema_ref={"profile": RETAIL},
                             payload={"bill_code": "DTRT-SEED-" + state[:4]},
                             provenance={}, digest="d" * 8, state="FROZEN")
        sub = Submission(id=new_uuid(), tenant_id=t.id,
                         package_revision_id=pr.id, destination_id=dest.id,
                         state=state, idempotency_key=f"seed-{state}",
                         created_at=y, updated_at=y)
        db.add_all([pkg, pr, sub])
        db.flush()
        for i, (outcome, http) in enumerate(attempts, start=1):
            db.add(DeliveryAttempt(
                id=new_uuid(), tenant_id=t.id, submission_id=sub.id,
                attempt_no=i, outcome=outcome, http_status=http,
                started_at=y, ended_at=y))
    db.commit()
    print("seeded: send evidence + quota(used=2), 2 daily reports"
          " (D-1 OK, D-3 OK, D-2 gap), 3 subs")

if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
    print(f"UI: http://127.0.0.1:{port}/ui/  admin token: "
          "ui-admin-token-0123456789abcdef")
    uvicorn.run(srv.app, host="127.0.0.1", port=port, log_level="warning")
