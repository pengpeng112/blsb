"""Run in deployed worker. Bounded source SELECTs, aggregate-only receipt."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta

CFG = {
    "mode": "oracle", "source_profile": "blyb_drugtrace_views@20261022-r10",
    "dsn_env": "BLYB_DRUGTRACE_ORACLE_DSN", "collection_mode": "batch",
    "objects": {"bill_view": "PHARMACY.V_DRUG_TRACE_BILL",
                "code_view": "PHARMACY.V_DRUG_TRACE_CODE"},
}
out = {"ok": False, "source_writes": 0, "uploads": 0}
conn = None
try:
    from domains.drug_trace import service as S, quality as Q
    from domains.drug_trace.ingest import read_bill_whole, composite_key
    from sources.db_view.drug_trace import normalize_row
    wh = S.make_warehouse(CFG)
    import oracledb
    conn = oracledb.connect(os.environ[CFG["dsn_env"]])
    conn.call_timeout = 45000

    def select(sql, binds=None):
        with conn.cursor() as cur:
            cur.execute(sql, binds or {})
            cols = [d[0] for d in cur.description]
            return [normalize_row(dict(zip(cols, row))) for row in cur.fetchall()]

    identity = select("SELECT USER AS login_user, SYS_CONTEXT('USERENV','SERVICE_NAME') AS service_name FROM DUAL")[0]
    grants = select("SELECT OWNER,TABLE_NAME,PRIVILEGE,GRANTABLE FROM USER_TAB_PRIVS_RECD ORDER BY OWNER,TABLE_NAME,PRIVILEGE")
    privileges = select("SELECT PRIVILEGE FROM SESSION_PRIVS ORDER BY PRIVILEGE")
    roles = select("SELECT ROLE FROM SESSION_ROLES")
    assert identity == {"login_user": "BLYB_DRUGTRACE", "service_name": "his"}
    assert len(grants) == 2 and all(r["owner"] == "PHARMACY" and r["privilege"] == "SELECT"
                                         and r["grantable"] == "NO" for r in grants)
    assert {r["table_name"] for r in grants} == {"V_DRUG_TRACE_BILL", "V_DRUG_TRACE_CODE"}
    assert privileges == [{"privilege": "CREATE SESSION"}] and not roles
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    seeds = select("SELECT source_system,source_bill_id,pharmacy_code,business_kind,bill_time "
                   "FROM PHARMACY.V_DRUG_TRACE_CODE WHERE bill_time >= :b AND bill_time < :e "
                   "AND business_kind='PATIENT_DISPENSE' AND ROWNUM<=100",
                   {"b": today, "e": today + timedelta(days=1)})
    attempted = set()
    candidate = None
    for seed in seeds:
        key = {k: seed[k] for k in ("source_system", "pharmacy_code", "business_kind", "source_bill_id")}
        hashed = hashlib.sha256(composite_key(key).encode()).hexdigest()
        if hashed in attempted:
            continue
        attempted.add(hashed)
        if len(attempted) > 8:
            break
        headers = wh.fetch_all("drugtrace.bill_header", key, 2)
        if len(headers) != 1:
            continue
        header = headers[0]
        begin = datetime.fromisoformat(header["updated_at"])
        end = begin + timedelta(seconds=1)
        cursor = {"begin_time": begin, "end_time": end, "since_time": begin,
                  "since_ss": "", "since_pc": "", "since_bk": "", "since_sb": ""}
        page = wh.fetch_all("drugtrace.poll_bills", cursor, 2)
        if len(page) != 1 or composite_key(page[0]) != composite_key(header):
            continue
        lines, final_header, reread = read_bill_whole(wh, header, code_page_limit=2)
        state, issues = Q.evaluate_revision(final_header, lines,
            profile="alihealth_yljg_retail_v1", code_limit_guard=3500)
        if state != "VALID":
            continue
        candidate = {"key": key, "key_sha256": hashed,
                     "window_begin": begin.isoformat(), "window_end": end.isoformat(),
                     "pharmacy_code": key["pharmacy_code"], "source_config": CFG,
                     "quality_state": state, "code_count": len(lines),
                     "rules": [i["rule"] for i in issues],
                     "prior_upload_reconciled": False, "send_authorized": False}
        break
    assert candidate is not None, "no single-bill VALID window found within bounded probe"
    path = "/tmp/srcdeploy-candidate.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(candidate, f)
    out.update(ok=True, identity=identity, restricted_grants=True,
               privileges=privileges, role_count=len(roles),
               candidate={k: candidate[k] for k in ("key_sha256", "window_begin", "window_end",
                            "quality_state", "code_count", "rules", "prior_upload_reconciled")},
               boundary_poll_rows=1, code_page_size=2,
               secret_present=bool(os.environ.get("BLYB_ALIHEALTH_APP_SECRET")),
               driver_mode="thick" if not oracledb.is_thin_mode() else "thin")
except Exception as exc:
    out.update(error_type=type(exc).__name__,
               oracle_codes=sorted(set(re.findall(r"(?:ORA|DPI|DPY)-\d+", str(exc)))))
finally:
    if conn is not None:
        conn.close()
print("__SOURCE_RESULT__" + json.dumps(out, ensure_ascii=False) + "__END__")
if not out["ok"]:
    raise SystemExit(1)
