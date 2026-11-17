"""Verify this task's public evidence without networking or exposing credentials."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    secret = os.environ.get("BLYB_ALIHEALTH_APP_SECRET")
    if not secret:
        raise SystemExit("Use the private credential wrapper; no secret arguments.")
    spec = importlib.util.spec_from_file_location("project_docs", ROOT / "scripts/project_docs.py")
    docs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs)
    all_links = docs.broken_links(ROOT)
    relevant_prefixes = ("docs/development/platform-live-test-20261020/", "docs/ai/", "implementation/docs/")
    changed_docs = {"docs/README.md", "docs/development/platform-credentials-20261020/README.md",
                    "docs/development/drug-trace-20261016/EXTERNAL_INPUTS.md"}
    task_links = [l for l in all_links if l["file"].startswith(relevant_prefixes) or l["file"] in changed_docs]
    public_files = list(HERE.rglob("*")) + [ROOT / x for x in changed_docs]
    public_files += list((ROOT / "docs/ai").rglob("*.md")) + list((ROOT / "implementation/docs").glob("*.md"))
    # Scan in memory and publish paths/counts only. Never log the actual secret.
    secret_hits = [p.relative_to(ROOT).as_posix() for p in public_files if p.is_file()
                   and p.suffix not in {".pyc"} and secret.encode() in p.read_bytes()]
    xml = ET.parse(ROOT / "implementation/var/platform-live-20261020.xml")
    cases = xml.findall(".//testcase")
    suites = xml.findall(".//testsuite")
    query = json.loads((HERE / "evidence/query-probe-receipt.json").read_text("utf-8"))
    enterprise = json.loads((HERE / "evidence/enterprise-probe-receipt.json").read_text("utf-8"))
    for receipt in (query, enterprise):
        target = (ROOT / receipt["encrypted_raw_response"]).resolve()
        if not target.is_relative_to((ROOT / ".ai/private/alihealth-live-test-20261020").resolve()):
            raise SystemExit("Private evidence path outside task directory")
        if not target.is_file():
            raise SystemExit("Missing encrypted response")
    diff = subprocess.run(["git", "diff", "--check"], cwd=ROOT, capture_output=True)
    ignored = subprocess.run(["git", "check-ignore", ".ai/private/alihealth-live-test-20261020"],
                             cwd=ROOT, capture_output=True)
    result = {
        "secret_plaintext_matches": secret_hits, "task_broken_links": task_links,
        "all_repository_broken_links": len(all_links),
        "source_or_archive_hash_errors": docs.source_errors(ROOT),
        "git_diff_check_exit": diff.returncode, "private_directory_ignored": ignored.returncode == 0,
        "pytest_cases": len(cases),
        "pytest_failures": sum(int(s.get("failures", "0")) for s in suites),
        "pytest_errors": sum(int(s.get("errors", "0")) for s in suites),
        "pytest_skipped": sum(int(s.get("skipped", "0")) for s in suites),
        "real_query_count": query["request_count"] + enterprise["request_count"],
        "real_upload_count": query["real_uploads"] + enterprise["real_uploads"],
        "query_success": query["response_success"] is True,
        "hospital_directory_matches": enterprise["ref_ent_id_matches_supplied"] is True
                                      and enterprise["hospital_name_matches_supplied"] is True,
        "network_or_business_requests_in_this_verification": 0,
    }
    output = HERE / "evidence/verification.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(secret_hits or task_links or result["source_or_archive_hash_errors"] or diff.returncode
                    or ignored.returncode or result["pytest_failures"] or result["pytest_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
