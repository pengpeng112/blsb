"""Read-only check: did today's scheduled push run normally? (2026-10-29)

User-authorized, SELECT/ls/cat only, via jump host -> <prod-host>:
- host clock, timer state, service state
- receipt files under /var/log/blyb/daily-upload/ (today + previous 2 days, capped)
- read-only Postgres snapshot (quota / collection runs / submissions by state+hour / jobs)

No writes, no API mutation, no sends, no config change.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import shlex
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


fb = load("chk_fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")

SQL = """
SELECT json_build_object(
 'now', now()::text,
 'quota', (SELECT json_agg(row_to_json(q)) FROM (
    SELECT evidence_id::text, used, max_bills, updated_at::text
    FROM drug_trace_send_quota) q),
 'runs', (SELECT json_agg(row_to_json(r)) FROM (
    SELECT state, window_begin::text, window_end::text, summary, created_at::text
    FROM drug_trace_collection_run ORDER BY created_at DESC LIMIT 4) r),
 'subs_by_state_3d', (SELECT json_agg(row_to_json(s)) FROM (
    SELECT state, count(*) n, min(created_at)::text lo, max(created_at)::text hi
    FROM submission WHERE created_at > now() - interval '3 days' GROUP BY state) s),
 'subs_by_hour_2d', (SELECT json_agg(row_to_json(s)) FROM (
    SELECT date_trunc('hour', created_at)::text h, state, count(*) n
    FROM submission WHERE created_at > now() - interval '2 days'
    GROUP BY 1,2 ORDER BY 1) s),
 'attempts_1d', (SELECT json_agg(row_to_json(a)) FROM (
    SELECT outcome, http_status, count(*) n
    FROM delivery_attempt WHERE started_at > now() - interval '1 day'
    GROUP BY 1,2) a),
 'jobs', (SELECT json_agg(row_to_json(j)) FROM (
    SELECT job_type, state, count(*) n FROM job GROUP BY job_type, state) j)
);
"""

CMD = r'''
echo "===HOST DATE==="; date '+%F %T %z'
echo "===TIMER==="; systemctl list-timers blyb-daily-upload.timer --no-pager 2>&1 || true
echo "===SERVICE==="; systemctl is-active blyb-daily-upload.service 2>&1 || true
echo "===RECEIPTS==="; ls -l /var/log/blyb/daily-upload/ 2>&1 || true
echo "===LAST-ERROR tail==="; tail -c 1500 /var/log/blyb/daily-upload/last-error.log 2>/dev/null || echo "(none)"
echo "===RECEIPTS CONTENT (10-27..10-29, capped)==="
for f in $(ls /var/log/blyb/daily-upload/2026102[789]-*.json 2>/dev/null); do
  echo "--- $f"; head -c 5000 "$f"; echo
done
echo "===DB SNAPSHOT==="
'''


def main():
    if '--authorized' not in sys.argv:
        raise SystemExit('Explicit user authorization required')
    jump = fb.deploy.connect_jump(fb.creds()['jump'])
    try:
        inner = ("{ " + CMD + " docker exec blyb-db-1 psql -U blyb -d blyb -At -c "
                 + shlex.quote(SQL) + "; }")
        rc, tr = fb.nested(jump, "bash -lc " + shlex.quote(inner), timeout=180)
        out = tr if isinstance(tr, str) else ''
        result = {'exit_code': rc, 'captured_at': datetime.now(timezone.utc).isoformat(),
                  'transcript': out[-20000:]}
        if rc == 0:
            marker = out.rfind('{')
            # psql JSON 是最后一个完整 json 对象（多行）——从 ===DB SNAPSHOT=== 之后解析
            seg = out.split('===DB SNAPSHOT===')[-1]
            s = seg.find('{')
            if s >= 0:
                depth = 0
                for i in range(s, len(seg)):
                    if seg[i] == '{':
                        depth += 1
                    elif seg[i] == '}':
                        depth -= 1
                        if depth == 0:
                            try:
                                result['db'] = json.loads(seg[s:i + 1])
                            except Exception:
                                result['db_raw'] = seg[s:i + 1][:4000]
                            break
        ev = HERE / 'evidence'
        ev.mkdir(exist_ok=True)
        name = 'check-today-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json'
        (ev / name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                               encoding='utf8')
        print(json.dumps({'file': name, 'exit_code': rc,
                          'has_db': 'db' in result}, ensure_ascii=False))
    finally:
        jump.close()


if __name__ == '__main__':
    main()
