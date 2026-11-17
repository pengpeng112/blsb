"""Core-side audited API setup/collection; private inputs arrive through stdin."""
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

token = sys.stdin.readline().strip()
args = json.loads(sys.stdin.readline())
candidate = json.loads(sys.stdin.read())
out = {"ok": False, "phase": args["phase"], "uploads": 0, "source_writes": 0, "calls": []}
base = "http://localhost:8000/api/v1"


def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            status, data = resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        status, data = exc.code, json.loads(exc.read())
    out["calls"].append({"method": method, "path": path, "http_status": status})
    if status not in (200, 201):
        raise RuntimeError("API operation failed; recorded HTTP status")
    return data["data"]


try:
    connectors = call("GET", "/connectors")
    sources = [c for c in connectors if c["plugin_id"] == "sources.db_view.drug_trace"]
    assert len(sources) == 1
    cid = sources[0]["id"]
    if args["phase"] == "setup":
        existing = call("GET", "/dictionaries")
        for ns, target in (("drugtrace.bill_type", "321"),
                           ("drugtrace.query_bill_type", "A"),
                           ("drugtrace.ref_ent_id", args["ref_ent_id"])):
            # New immutable revisions retain the actual previously approved items.
            items_by_code = {}
            for rev in existing:
                if rev["namespace"] == ns and rev.get("approved_by"):
                    for item in rev.get("items") or []:
                        code = str(item["code"])
                        if code in items_by_code:
                            assert str(items_by_code[code]["target"]) == str(item["target"])
                        items_by_code[code] = item
            source = candidate["pharmacy_code"] if ns == "drugtrace.ref_ent_id" else "PRESC_SOURCE=0;FLAG=1"
            items_by_code[source] = {"code": source, "target": target,
                "label": "r10 verified normal outpatient mapping; official platform dictionary governs numeric values",
                "evidence": "docs/development/drug-trace-r10-readiness-20261022/README.md; "
                            "docs/development/drug-trace-firstbill-exec-20261021/reference/official-guide-202601.md"}
            items = [items_by_code[k] for k in sorted(items_by_code)]
            version = "r10-normal-20261022-r1"
            prior = [d for d in existing if d["namespace"] == ns and d["version"] == version]
            if prior:
                assert len(prior) == 1 and prior[0]["items"] == items and prior[0]["approved_by"]
            else:
                call("POST", "/dictionaries", {"namespace": ns, "version": version,
                                               "items": items, "approve": True})
        call("POST", "/drug-trace/evidence", {
            "profile": "alihealth_yljg_retail_v1", "capability": "collect",
            "items": [
                {"key": "EXT-01", "evidence_ref": "r10 reader live SELECT verified: service his, CREATE SESSION only, two non-grantable view SELECT grants; worker verification receipt",
                 "provider": "user-executed DBA SQL and live dedicated-reader verification"},
                {"key": "EXT-02", "evidence_ref": "user executed r10 views; exact view SQL verified; single normal completed outpatient header and whole code set VALID. Batch only, unknown branches wait; no continuous/return/split approval",
                 "provider": "user-executed r10 contract; docs/development/drug-trace-r10-readiness-20261022/README.md"}],
            "valid_until": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()})
        cfg = {"content": candidate["source_config"], "publish": True, "enable": True}
        out["source"] = call("POST", f"/drug-trace/sources/{cid}/config-revisions", cfg)
        out["dictionary_revisions"] = {"version": version, "namespaces": 3,
                                       "prior_revisions_preserved": True}
    elif args["phase"] == "collect":
        # A narrow one-second window was proven to contain exactly one source bill.
        wb, we = candidate["window_begin"], candidate["window_end"]
        assert (datetime.fromisoformat(we) - datetime.fromisoformat(wb)).total_seconds() == 1
        # Existing run check makes operator re-execution safe after a lost receipt.
        runs = call("GET", "/drug-trace/collection-runs?limit=200")
        matches = [r for r in runs if r.get("window") == [wb, we]]
        if matches:
            assert len(matches) == 1, "ambiguous prior run; inspect instead of repeating"
            out["collection"] = {"existing": True, **matches[0]}
        else:
            out["collection"] = call("POST", "/drug-trace/collection-runs", {
                "connector_id": cid, "window_begin": wb, "window_end": we,
                "lookback_seconds": 0, "mode": "oracle", "profile": "alihealth_yljg_retail_v1",
                "checkpoint_partition": "r10-batch-first-20261022"})
    elif args["phase"] == "prepare":
        from core_db import make_engine, make_session_factory
        from core_db.models import Tenant, Destination
        from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
        engine = make_engine()
        with make_session_factory(engine)() as db:
            tenant = db.query(Tenant).filter_by(code="first-hospital").one()
            bill = db.query(DrugTraceBill).filter_by(
                tenant_id=tenant.id, connector_id=cid, **candidate["key"]).one()
            revision = db.get(DrugTraceBillRevision, bill.current_revision_id)
            assert revision.quality_state == "VALID"
            dest = db.query(Destination).filter_by(tenant_id=tenant.id,
                code="alihealth-drugtrace-retail").one()
            bid, did = bill.id, dest.id
        engine.dispose()
        out["prepared"] = call("POST", f"/drug-trace/bills/{bid}/prepare", {
            "profile": "alihealth_yljg_retail_v1", "destination_id": did})
    else:
        raise RuntimeError("unsupported phase")
    out["ok"] = True
except Exception as exc:
    out["error_type"] = type(exc).__name__
print("__SOURCE_RESULT__" + json.dumps(out, ensure_ascii=False) + "__END__")
if not out["ok"]:
    raise SystemExit(1)
