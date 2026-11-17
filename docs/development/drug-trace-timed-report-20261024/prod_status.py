"""Read-only production status check on <prod-host> via jump host (user-authorized).

Queries the production Postgres through docker exec psql (SELECT only):
destinations, evidence + send quota, collection runs, bills since 10-22,
submissions/attempts, jobs. No config change, no API mutation, no sends.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fb = load("timed_fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")

SQL = """
SELECT json_build_object(
 'now', now()::text,
 'destinations', (SELECT json_agg(row_to_json(d)) FROM (
    SELECT id::text, code, profile_revision, mode, config_revision::text, is_mock, status, created_at::text FROM destination ORDER BY created_at DESC) d),
 'evidence', (SELECT json_agg(row_to_json(e)) FROM (
    SELECT id::text, capability, status, approved_by, valid_until::text, scope, created_at::text
    FROM drug_trace_production_evidence ORDER BY created_at) e),
 'send_quota', (SELECT json_agg(row_to_json(q)) FROM (
    SELECT evidence_id::text, used, max_bills, updated_at::text FROM drug_trace_send_quota) q),
 'collection_runs', (SELECT json_agg(row_to_json(r)) FROM (
    SELECT id::text, state, window_begin::text, window_end::text, summary, created_at::text
    FROM drug_trace_collection_run ORDER BY created_at DESC LIMIT 10) r),
 'bills_total', (SELECT count(*) FROM drug_trace_bill),
 'bills_by_state', (SELECT json_agg(row_to_json(s)) FROM (
    SELECT business_kind, count(*) n FROM drug_trace_bill GROUP BY business_kind) s),
 'revisions_by_quality', (SELECT json_agg(row_to_json(s)) FROM (
    SELECT quality_state, count(*) n FROM drug_trace_bill_revision GROUP BY quality_state) s),
 'submissions', (SELECT json_agg(row_to_json(s)) FROM (
    SELECT id::text, state, created_at::text FROM submission ORDER BY created_at DESC LIMIT 10) s),
 'attempts', (SELECT count(*) FROM delivery_attempt),
 'jobs_recent', (SELECT json_agg(row_to_json(j)) FROM (
    SELECT job_type, state, count(*) n FROM job GROUP BY job_type, state) j),
 'checkpoints', (SELECT json_agg(row_to_json(c)) FROM (
    SELECT connector_id::text, stream, partition, cursor_json, row_version
    FROM source_checkpoint LIMIT 5) c)
);
"""


def main():
    if '--authorized' not in sys.argv:
        raise SystemExit('Explicit user authorization required')
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        import shlex
        inner = ("docker exec blyb-db-1 psql -U blyb -d blyb -At -c " + shlex.quote(SQL))
        rc, tr = fb.nested(jump, "bash -lc " + shlex.quote(inner), timeout=120)
        out = tr if isinstance(tr, str) else ''
        result = {'exit_code': rc, 'captured_at': datetime.now(timezone.utc).isoformat(),
                  'transcript_tail': str(tr)[-2500:]}
        if rc == 0 and out.strip():
            marker = out.find('{')
            if marker >= 0:
                try:
                    result['status'] = json.loads(out[marker:].strip())
                except Exception:
                    result['raw'] = out[marker:]
            else:
                result['raw'] = out[:4000]
        ev = HERE / 'evidence'
        ev.mkdir(exist_ok=True)
        name = 'prod-status-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'
        (ev / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
        print(json.dumps({'file': name, 'exit_code': rc,
                          'has_status': 'status' in result,
                          'stderr': result['stderr_tail'][:200]}, ensure_ascii=False))
    finally:
        jump.close()


if __name__ == '__main__':
    main()
