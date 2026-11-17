"""Post-collection/freeze evidence; no upload call exists here."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone

out = {"ok": False, "source_writes": 0, "uploads": 0}
try:
    from core_db import make_engine, make_session_factory
    from core_db.models import Tenant, ConnectorInstance, ConfigRevision, Destination, Submission, DeliveryAttempt, PackageRevision
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision, DrugTraceCollectionRun, DrugTraceSendQuota
    from domains.drug_trace import contracts as C, service as S
    from domains.drug_trace.payload import build_payload
    candidate = json.load(open("/tmp/srcdeploy-candidate.json"))
    engine = make_engine()
    with make_session_factory(engine)() as db:
        tenant = db.query(Tenant).filter_by(code="first-hospital").one()
        connector = db.query(ConnectorInstance).filter_by(tenant_id=tenant.id,
            plugin_id="sources.db_view.drug_trace").one()
        cfg = db.get(ConfigRevision, connector.active_config_revision)
        assert cfg.state == "PUBLISHED" and connector.status == "ENABLED"
        assert cfg.content_json == candidate["source_config"]
        bill = db.query(DrugTraceBill).filter_by(tenant_id=tenant.id,
            connector_id=connector.id, **candidate["key"]).one()
        rev = db.get(DrugTraceBillRevision, bill.current_revision_id)
        assert rev.quality_state == "VALID" and len(rev.lines) == candidate["code_count"]
        payload = build_payload(db, tenant.id, rev, bill, profile=C.PROFILE_RETAIL)
        # build_payload should find the existing stable bill code after freeze;
        # all reads are rolled back below (no new mapping can be committed).
        dest = db.query(Destination).filter_by(tenant_id=tenant.id,
            code="alihealth-drugtrace-retail").one()
        packages = []
        for sub in db.query(Submission).filter_by(tenant_id=tenant.id, destination_id=dest.id):
            pr = db.get(PackageRevision, sub.package_revision_id)
            if (pr.provenance or {}).get("bill_revision_id") == rev.id:
                assert sub.state == "PREPARED"
                assert pr.payload == payload
                packages.append({"submission_id": sub.id, "state": sub.state,
                                 "package_revision_id": pr.id, "digest": pr.digest})
        assert len(packages) == 1
        out.update(source_status=connector.status, config_state=cfg.state,
                   source_profile=cfg.content_json["source_profile"],
                   collection_runs=[{"id": r.id, "state": r.state, "summary": r.summary}
                        for r in db.query(DrugTraceCollectionRun).filter_by(tenant_id=tenant.id,
                                                                           connector_id=connector.id)],
                   bill_count=db.query(DrugTraceBill).filter_by(tenant_id=tenant.id).count(),
                   candidate_sha256=candidate["key_sha256"], quality_state=rev.quality_state,
                   code_count=len(rev.lines), quality_rules=[i["rule"] for i in rev.quality_issues],
                   prepared=packages, method=payload["method"],
                   bill_type=payload.get("bill_type"), dictionary_revisions={
                       k:{f:v.get(f) for f in ("revision_id","version")}
                       for k,v in payload.get("dict_revisions",{}).items()},
                   ref_user_id_matches_ref_ent_id=payload.get("ref_user_id") == payload.get("ref_ent_id"),
                   evidence={cap:S.evidence_state(db,tenant.id,profile=C.PROFILE_RETAIL,
                       capability=cap,destination_id=dest.id if cap != "collect" else "")["status"]
                       for cap in ("collect","send","query","reconcile")},
                   destination_status=dest.status,
                   delivery_attempt_count=db.query(DeliveryAttempt).filter_by(tenant_id=tenant.id).count(),
                   quota_count=db.query(DrugTraceSendQuota).filter_by(tenant_id=tenant.id).count())
        assert out["delivery_attempt_count"] == 0 and out["quota_count"] == 0
        # Actual existing query-only permission: recheck the current full route.
        client = S.build_client(db, tenant.id, dest, purpose="query")
        day = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        result = client.query_bill_status({"ref_ent_id": payload["ref_ent_id"],
            "bill_code": payload["bill_code"], "bill_type": payload["query_bill_type"],
            "begin_date": day, "end_date": day, "page": "1", "page_size": "20"})
        out["platform_query"] = {k: result.get(k) for k in ("found", "total_num", "unknown", "error")}
        out["platform_query"]["scope"] = "new local stable platform bill code only; not legacy trace-level reconciliation"
        db.rollback()
    engine.dispose()
    out["ok"] = True
except Exception as exc:
    out.update(error_type=type(exc).__name__,
               oracle_codes=sorted(set(re.findall(r"(?:ORA|DPI|DPY)-\d+", str(exc)))))
print("__SOURCE_RESULT__" + json.dumps(out, ensure_ascii=False) + "__END__")
if not out["ok"]:
    raise SystemExit(1)
