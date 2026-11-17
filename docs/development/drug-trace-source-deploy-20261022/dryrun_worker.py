"""Verify actual prepared business parameters without constructing a client."""
import json
import re
from core_db import make_engine, make_session_factory
from core_db.models import Tenant, Submission, PackageRevision, DeliveryAttempt, Job
from domains.drug_trace.models import DrugTraceSendQuota
from domains.drug_trace import service as S, contracts as C

out = {"ok": False, "uploads": 0, "source_writes": 0}
engine = make_engine()
try:
    with make_session_factory(engine)() as db:
        tenant = db.query(Tenant).filter_by(code="first-hospital").one()
        sub = db.get(Submission, "7ed95aaa-ee66-4510-b80d-630f6c9dc381")
        assert sub and sub.tenant_id == tenant.id and sub.state == "PREPARED"
        pr = db.get(PackageRevision, sub.package_revision_id)
        before = (db.query(DeliveryAttempt).filter_by(tenant_id=tenant.id).count(),
                  db.query(DrugTraceSendQuota).filter_by(tenant_id=tenant.id).count(),
                  db.query(Job).filter_by(tenant_id=tenant.id, job_type=C.DomainJobType.SEND).count())
        result = S.perform_send(db, tenant.id, sub, dry_run=True)
        biz = result["biz_params"]
        assert set(biz) == {"bill_code", "bill_time", "bill_type", "ref_user_id", "trace_codes"}
        assert biz["bill_type"] == "321" and biz["ref_user_id"] == pr.payload["ref_ent_id"]
        assert biz["bill_code"] == pr.payload["bill_code"]
        codes = biz["trace_codes"].split(",")
        assert codes == [r["trace_code"] for r in pr.payload["codes"]]
        assert len(codes) == 4 and all(re.fullmatch(r"\d{20}", c) for c in codes)
        after = (db.query(DeliveryAttempt).filter_by(tenant_id=tenant.id).count(),
                 db.query(DrugTraceSendQuota).filter_by(tenant_id=tenant.id).count(),
                 db.query(Job).filter_by(tenant_id=tenant.id, job_type=C.DomainJobType.SEND).count())
        assert before == after == (0, 0, 0) and sub.state == "PREPARED"
        out.update(ok=True, outcome=result["outcome"], mandatory_fields=sorted(biz),
                   code_count=len(codes), hospital_id_matches=True, original_codes_preserved=True,
                   submission_state=sub.state, delivery_attempt_count=after[0],
                   quota_count=after[1], send_job_count=after[2])
        db.rollback()
except Exception as exc:
    out["error_type"] = type(exc).__name__
finally:
    engine.dispose()
print("__SOURCE_RESULT__" + json.dumps(out) + "__END__")
if not out["ok"]:
    raise SystemExit(1)
