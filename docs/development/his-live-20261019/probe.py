"""Task-scoped sjzc probes. No credential contents are read by this program.

Authorized by the user's follow-up request to connect now for bounded read-only
verification. This does not change the platform's medical_dict_push policy or
authorize production collection, DDL, patient exports, or real uploads.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE = "his_source_<his_host_id>"
SKILL = Path(r"C:\Users\Administrator\.zcode\skills\sjzc\scripts\sjzc_query.py")
VALIDATOR = Path(r"F:\python\数据资产\.agents\skills\hisuser-readonly-sql\scripts\validate_his_sql.py")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="basename of a reviewed SQL in this task's sql directory")
    parser.add_argument("--max-rows", type=int, default=50)
    args = parser.parse_args()
    if Path(args.query).name != args.query or not args.query.endswith(".sql"):
        parser.error("only a task SQL basename is accepted")
    if not 1 <= args.max_rows <= 100:
        parser.error("task row cap is 100")
    path = ROOT / "sql" / args.query
    text = path.read_text(encoding="utf-8")
    sql = "\n".join(line.split("--", 1)[0] for line in text.splitlines()).strip()
    errors = load_module("his_sql_validator", VALIDATOR).validate(sql)
    if errors:
        print(json.dumps({"ok": False, "static_errors": errors}))
        return 2
    sjzc = load_module("sjzc", SKILL)
    gate_sql = """SELECT row_to_json(t) FROM (
        SELECT source_code,target_host,port,service_name,service_mode,enabled,write_policy
        FROM asset.asset_data_sources WHERE source_code='his_source_<his_host_id>'
        LIMIT 1) t"""
    registration = json.loads(sjzc._ssh_psql(gate_sql))
    if (registration["target_host"] != "<his-host>" or registration["port"] != 1521
            or not registration["enabled"] or registration["service_mode"] != "service_name"
            or registration["service_name"] != "his"):
        print(json.dumps({"ok": False, "error": "registered endpoint changed; no live connection"}))
        return 2
    # Preserve the policy discrepancy; this user's task-specific authorization is
    # not an amendment to registration or a production readonly certification.
    capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(capture):
            sjzc.cmd_live(SOURCE, sql, args.max_rows)
        raw = capture.getvalue()
        result = json.loads(raw.splitlines()[-1])
        if not result.get("ok"):
            raise ValueError("unsuccessful connector response")
    except Exception:
        raw = capture.getvalue()
        result = {"ok": False, "error_codes": sorted(set(re.findall(r"(?:ORA|DPI|DPY)-\d+", raw))),
                  "error": "controlled connector failed; raw stderr withheld"}
    output = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "authorization": "current user follow-up: connect now for bounded read-only HIS verification",
        "registered_endpoint": registration,
        "channel": "sjzc/<relay-jump>/data-asset-api/execute_readonly",
        "query": args.query, "sql": sql, "sql_sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "readonly": True, "business_source_writes": 0,
        "client_credential_contents_read": False,
        "max_rows": args.max_rows,
        "result": result,
    }
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)
    target = evidence / (path.stem + ".json")
    # Completed probe evidence must not be overwritten accidentally.
    with target.open("x", encoding="utf-8") as file:
        file.write(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
