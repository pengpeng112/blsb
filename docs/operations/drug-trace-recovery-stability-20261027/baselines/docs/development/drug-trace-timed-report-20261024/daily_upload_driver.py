#!/usr/bin/env python3
"""blyb 码上放心定时上报驱动 v2（在 blyb-core-1 容器内执行；stdin=bootstrap token).

每日流程（处理北京时间昨天窗口）：
 1) 幂等创建/复用采集运行并等待完成；
 2) 对窗口内 VALID+finalized+未作废的门诊单：无提交则 prepare，PREPARED 则续发
    send(confirm)，逐单串行（另有传输层统一限制上传/每页查询共40次/分）；
 3) 对非终态提交做查询补进（query）；
 4) 输出 JSON 回执（宿主 unit 重定向到 /var/log/blyb/daily-upload/）；
 5) v2（DTR-TIMED-UI-20261024/TUI-04）：跑完把日报 POST
    /api/v1/drug-trace/daily-reports 入库（幂等 upsert，source=timer）。

安全规则：CONFIRMED/REJECTED 永不重发；单条失败仅记录不盲目重试；
PROFILE_UNAPPROVED（范围/配额）立即中止后续发送；每日发送上限 MAX_SENDS。

v2 兼容性：日报推送为单次 POST、90s 超时、失败即放弃——文件回执照写、
发送与退出码语义与 v1 完全一致；回执仅新增 report_push 字段。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, time as dtime, timedelta, timezone

TOKEN = sys.stdin.readline().strip()
BASE = 'http://localhost:8000/api/v1'
CONNECTOR_ID = '7d51abd7-3f32-4c8e-b2fa-9b652a511808'
PROFILE = 'alihealth_yljg_retail_v1'
DEST_CODE = 'alihealth-drugtrace-retail'
PARTITION = 'r10-batch-daily'
REPORT_SOURCE = 'timer'
GMT8 = timezone(timedelta(hours=8))
PACE_SECONDS = 2.2  # Upload plus its automatic status query share DMZ's 60/min budget.
MAX_SENDS = 3000
MAX_QUERY = 400
COLLECTION_WAIT_SECONDS = 3 * 60 * 60
SEND_QUEUE_WAIT_SECONDS = 30 * 60
SEND_EXECUTION_WAIT_SECONDS = 180
TERMINAL = {'CONFIRMED', 'REJECTED', 'PARTIAL'}

receipt = {'window': None, 'collection': None, 'send': {'sent': 0, 'skipped_terminal': 0,
           'resumed': 0, 'prepared_new': 0, 'errors': [], 'aborted_reason': ''},
           'query': {'queried': 0, 'errors': []}, 'candidate_count': 0}


def call(method, path, body=None, timeout=90):
    req = urllib.request.Request(BASE + path, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            result = json.load(r)
            if not isinstance(result, dict) or 'data' not in result:
                return 502, {'error_code': 'INVALID_API_ENVELOPE'}
            return r.status, result['data']
    except urllib.error.HTTPError as exc:
        try:
            e = json.load(exc)
        except Exception:
            e = {}
        err = e.get('error') if isinstance(e, dict) else None
        err = err if isinstance(err, dict) else {}
        return exc.code, {'error_code': err.get('code') or 'HTTP_ERROR'}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # No raw exception text: it can contain URLs, response bodies or tokens.
        return 599, {'error_code': 'API_TRANSPORT_OR_JSON_ERROR',
                     'error_type': type(exc).__name__}


def collection_rows(status, data):
    return (status == 200 and isinstance(data, list)
            and all(isinstance(row, dict) and isinstance(row.get('id'), str)
                    and isinstance(row.get('state'), str) for row in data))


def push_daily_report(day, outcome):
    """TUI-04：跑完把日报 POST 入库（幂等 upsert by report_date+source）。

    单次 POST、90s 超时、任何失败仅记 receipt['report_push'] 后放弃——
    绝不影响文件回执内容（除新增 report_push 字段）与退出码。"""
    if os.environ.get('BLYB_DAILY_REPORT_ENABLED', '1') == '0':
        receipt['report_push'] = {'skipped': True, 'reason': 'DAILY_REPORT_NOT_DEPLOYED'}
        return False
    window = receipt.get('window') or [None, None]
    body = {
        'report_date': day.isoformat(), 'source': REPORT_SOURCE,
        'window_begin': window[0], 'window_end': window[1],
        'candidate_count': receipt.get('candidate_count', 0),
        'collection': receipt.get('collection') or {},
        'send': compact_report_errors(receipt.get('send') or {}),
        'query_stats': compact_report_errors(receipt.get('query') or {}),
        'outcome': outcome,
        'elapsed_seconds': receipt.get('elapsed_seconds', 0.0)}
    try:
        st, res = call('POST', '/drug-trace/daily-reports', body, timeout=90)
        if st in (200, 201):
            receipt['report_push'] = {'ok': True}
        else:
            receipt['report_push'] = {'ok': False, 'status': st, 'detail': res}
    except Exception as exc:  # noqa: BLE001 —— 上报失败即放弃（不重试）
        receipt['report_push'] = {'ok': False, 'error': str(exc)[:200]}
    return receipt['report_push']['ok']


def compact_report_errors(section):
    """API gets bounded samples and exact totals; full file stays intact."""
    result = dict(section)
    errors = list(result.get('errors') or [])
    if len(errors) <= 50:
        return result
    result['errors_total'] = len(errors)
    result['errors'] = errors[:50]
    result['errors_truncated'] = len(errors) > 50
    return result


def wait_for_send(db, tenant_id, sid, job_id):
    """Wait on the original job only; queue time is not network failure."""
    from sqlalchemy import text
    queue_deadline = time.monotonic() + SEND_QUEUE_WAIT_SECONDS
    execution_deadline = None
    while True:
        row = db.execute(text('''SELECT s.state AS submission_state, j.state AS job_state, j.last_error
            FROM submission s LEFT JOIN job j ON j.id=:j AND j.tenant_id=s.tenant_id
            WHERE s.id=:s AND s.tenant_id=:t'''), {'t':tenant_id,'s':sid,'j':job_id}).fetchone()
        if row is None:
            return None, 'SEND_SUBMISSION_MISSING'
        state, job_state = row[0], row[1]
        if state not in ('PREPARED', 'SENDING'):
            return state, 'SEND_RESULT_UNKNOWN' if state == 'UNKNOWN' else None
        if job_state in (None, 'FAILED', 'CANCELLED', 'SUCCEEDED'):
            return state, 'SEND_JOB_' + (job_state or 'MISSING')
        now = time.monotonic()
        deferred = row[2] if len(row) > 2 else {}
        if isinstance(deferred, str):
            try: deferred = json.loads(deferred)
            except ValueError: deferred = {}
        if state == 'PREPARED' and job_state == 'RETRY_WAIT' and isinstance(deferred, dict) and deferred.get('code') == 'PLATFORM_COOLDOWN':
            # The worker explicitly proved no request occurred and released
            # this same job. Keep the original overall queue deadline.
            execution_deadline = None
            if now >= queue_deadline:
                return state, 'SEND_QUEUE_TIMEOUT'
            time.sleep(2)
            continue
        if state == 'SENDING' or job_state == 'LEASED':
            if execution_deadline is None:
                execution_deadline = now + SEND_EXECUTION_WAIT_SECONDS
            if now >= execution_deadline:
                return state, 'SEND_EXECUTION_TIMEOUT'
        elif execution_deadline is not None:
            # Never reset an execution deadline when a job changes state.
            if now >= execution_deadline:
                return state, 'SEND_EXECUTION_TIMEOUT'
        elif now >= queue_deadline:
            return state, 'SEND_QUEUE_TIMEOUT'
        time.sleep(1.0)


def eligible_queries(db, tenant_id, dest_id):
    from sqlalchemy import text
    from domains.drug_trace.service import QUERY_MAX_POLLS
    rows = db.execute(text('''SELECT s.id, coalesce(q.polls,0), coalesce(q.inflight,0)
        FROM submission s LEFT JOIN (
          SELECT tenant_id, payload->>'submission_id' sid, count(*) polls,
            max(CASE WHEN state IN ('PENDING','LEASED','RETRY_WAIT') THEN 1 ELSE 0 END) inflight
          FROM job WHERE tenant_id=:t AND job_type='drug_trace.query_status'
            AND dedup_key LIKE ('q:' || (payload->>'submission_id') || ':%')
          GROUP BY tenant_id,payload->>'submission_id'
        ) q ON q.tenant_id=s.tenant_id AND q.sid=s.id
        WHERE s.tenant_id=:t AND s.destination_id=:d
          AND s.state IN ('ACCEPTED','UNKNOWN') AND s.created_at > now() - interval '7 days'
        ORDER BY s.created_at'''), {'t':tenant_id,'d':dest_id}).fetchall()
    eligible = []
    for sid, polls, inflight in rows:
        if polls >= QUERY_MAX_POLLS:
            receipt['query']['skipped_exhausted'] = receipt['query'].get('skipped_exhausted',0) + 1
        elif inflight:
            receipt['query']['skipped_inflight'] = receipt['query'].get('skipped_inflight',0) + 1
        elif len(eligible) < MAX_QUERY:
            eligible.append(sid)
        else:
            receipt['query']['skipped_limit'] = receipt['query'].get('skipped_limit',0) + 1
    return eligible


def main(report_date=None):
    started = time.monotonic()
    today = datetime.now(GMT8).date()
    day = date.fromisoformat(report_date) if isinstance(report_date, str) else report_date
    day = day if day is not None else today - timedelta(days=1)
    wb = datetime.combine(day, dtime())  # naive GMT+8 (system convention)
    we = wb + timedelta(days=1)
    receipt['window'] = [wb.isoformat(), we.isoformat()]

    def fail(code, key, detail=None):
        """v1 语义保持：fatal 回执+退出码不变；v2 仅 best-effort 推送 FAILED 日报。"""
        receipt.update({'fatal': key, 'outcome': 'FAILED',
                        'elapsed_seconds': round(time.monotonic() - started, 1)})
        if detail is not None:
            receipt['detail'] = detail
        push_daily_report(day, 'FAILED')
        print(json.dumps(receipt, ensure_ascii=False, default=str))
        return code

    if day >= today:
        return fail(2, 'window_not_completed')
    if not TOKEN:
        return fail(2, 'missing_token')

    # ---- 1. collection (idempotent by window) ----
    st, runs = call('GET', '/drug-trace/collection-runs?limit=200')
    if not collection_rows(st, runs):
        return fail(2, 'collect_list_failed', {'status': st,
                    'error_code': runs.get('error_code') if isinstance(runs, dict) else 'INVALID_COLLECTION_LIST'})
    run = next((r for r in (runs or []) if r.get('window') == [wb.isoformat(), we.isoformat()]), None)
    if run is None:
        st, res = call('POST', '/drug-trace/collection-runs', {
            'connector_id': CONNECTOR_ID, 'window_begin': wb.isoformat(),
            'window_end': we.isoformat(), 'lookback_seconds': 600,
            'mode': 'oracle', 'profile': PROFILE, 'checkpoint_partition': PARTITION})
        if st not in (200, 201) or not isinstance(res, dict) or not res.get('collection_run_id'):
            return fail(2, 'collect_create_failed', {'status': st,
                        'error_code': res.get('error_code') if isinstance(res, dict) else 'INVALID_CREATE_RESPONSE'})
        run_id = res['collection_run_id']
    else:
        run_id = run['id']
    deadline = time.monotonic() + COLLECTION_WAIT_SECONDS
    while time.monotonic() < deadline:
        st, runs = call('GET', '/drug-trace/collection-runs?limit=200')
        if not collection_rows(st, runs):
            return fail(3, 'collect_poll_failed', {'status': st,
                        'error_code': runs.get('error_code') if isinstance(runs, dict) else 'INVALID_COLLECTION_LIST'})
        cur = next((r for r in (runs or []) if r['id'] == run_id), None)
        if cur and cur['state'] in ('DONE', 'FAILED', 'CANCELLED'):
            receipt['collection'] = {'id': run_id, 'state': cur['state'], 'summary': cur.get('summary')}
            break
        time.sleep(10)
    else:
        return fail(3, 'collect_timeout', {'run_id': run_id})
    if receipt['collection']['state'] != 'DONE':
        return fail(3, 'collect_not_done', receipt['collection'])

    # ---- 2. candidates via read-only DB ----
    from core_db import make_engine, make_session_factory
    from core_db.models import Tenant, Submission
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    from sqlalchemy import text as sql_text
    engine = make_engine()
    db = make_session_factory(engine)()
    try:
        tenant = db.query(Tenant).filter_by(code='first-hospital').one()
        dest_id = db.execute(sql_text(
            "SELECT id FROM destination WHERE tenant_id=:t AND code=:c"),
            {'t': tenant.id, 'c': DEST_CODE}).scalar()
        rows = db.execute(sql_text("""
            SELECT b.id AS bill_id, r.id AS rev_id
            FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
            WHERE b.tenant_id=:t AND b.connector_id=:cid
              AND b.bill_time>=:wb AND b.bill_time<:we
              AND b.business_kind='PATIENT_DISPENSE' AND b.finalized=true AND b.is_void=false
              AND r.quality_state='VALID'
            ORDER BY b.bill_time"""), {'t': tenant.id, 'cid': CONNECTOR_ID,
                                       'wb': wb, 'we': we}).fetchall()
        receipt['candidate_count'] = len(rows)

        def existing_submission(rev_id):
            return db.execute(sql_text("""
                SELECT s.id, s.state FROM submission s
                JOIN package_revision pr ON pr.id=s.package_revision_id
                WHERE s.tenant_id=:t AND s.destination_id=:d
                  AND pr.provenance->>'bill_revision_id'=:r
                ORDER BY s.created_at DESC LIMIT 1"""),
                {'t': tenant.id, 'd': dest_id, 'r': rev_id}).fetchone()

        for bill_id, rev_id in rows:
            if receipt['send']['sent'] >= MAX_SENDS:
                receipt['send']['aborted_reason'] = 'MAX_SENDS reached'
                break
            if receipt['send']['aborted_reason']:
                break
            ex = existing_submission(rev_id)
            if ex is not None:
                sid, state = ex[0], ex[1]
                if state in TERMINAL or state in ('ACCEPTED', 'SENDING', 'UNKNOWN'):
                    receipt['send']['skipped_terminal'] += 1
                    continue
                if state != 'PREPARED':
                    receipt['send']['errors'].append({'submission': sid, 'step': 'state',
                                                     'res': {'error_code': 'NOT_SENDABLE'}})
                    continue
                receipt['send']['resumed'] += 1
            else:
                st, res = call('POST', f'/drug-trace/bills/{bill_id}/prepare',
                               {'profile': PROFILE, 'destination_id': dest_id})
                if st not in (200, 201):
                    code = (res or {}).get('error_code')
                    if code == 'CONFLICT':
                        receipt['send']['skipped_terminal'] += 1
                        continue
                    receipt['send']['errors'].append({'bill': bill_id, 'step': 'prepare', 'res': res})
                    continue
                sid = res['submission_id']
                receipt['send']['prepared_new'] += 1
            st, res = call('POST', f'/drug-trace/submissions/{sid}/send', {'confirm': True})
            if st not in (200, 201):
                code = (res or {}).get('error_code')
                receipt['send']['errors'].append({'submission': sid, 'step': 'send', 'res': res})
                if code == 'PROFILE_UNAPPROVED':
                    receipt['send']['aborted_reason'] = 'PROFILE_UNAPPROVED (scope/quota/evidence)'
                    break
                time.sleep(PACE_SECONDS)
                continue
            receipt['send']['sent'] += 1
            job_id = res.get('job_id') if isinstance(res, dict) else None
            if not job_id:
                state, wait_error = None, 'SEND_JOB_ID_MISSING'
            else:
                state, wait_error = wait_for_send(db, tenant.id, sid, job_id)
            if wait_error:
                receipt['send']['errors'].append({'submission': sid, 'step': 'send_worker',
                    'res': {'error_code': wait_error}})
                receipt['send']['aborted_reason'] = wait_error
                break
            if state in ('REJECTED', 'PARTIAL'):
                receipt['send']['errors'].append({'submission': sid, 'step': 'send_worker',
                    'res': {'error_code': 'BUSINESS_' + state}})
            time.sleep(PACE_SECONDS)

        # ---- 3. query catch-up (non-terminal, last 7 days, capped) ----
        for sid in eligible_queries(db, tenant.id, dest_id):
            st, res = call('POST', f'/drug-trace/submissions/{sid}/query', {})
            if st not in (200, 201):
                receipt['query']['errors'].append({'submission': sid, 'res': res})
            else:
                receipt['query']['queried'] += 1
            time.sleep(PACE_SECONDS)
    finally:
        db.close()
        engine.dispose()

    receipt['elapsed_seconds'] = round(time.monotonic() - started, 1)
    failed = bool(receipt['send']['aborted_reason'] or receipt['send']['errors']
                  or receipt['query']['errors'])
    receipt['outcome'] = 'OK' if not failed else 'PARTIAL_FAILURE'
    # TUI-04：日报入库（best-effort；失败只记 report_push，不改下方回执/退出码）
    push_daily_report(day, receipt['outcome'])
    print(json.dumps(receipt, ensure_ascii=False, default=str))
    return 0 if not failed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=date.fromisoformat,
                        help='Explicit completed GMT+8 business date; existing scope still enforced')
    args = parser.parse_args()
    try:
        exit_code = main(args.date)
    except Exception as exc:
        receipt.update({'outcome': 'FAILED', 'fatal': 'unhandled_driver_error',
                        'error_type': type(exc).__name__})
        # A final receipt must survive unexpected DB/import/response failures.
        print(json.dumps(receipt, ensure_ascii=False, default=str))
        exit_code = 1
    sys.exit(exit_code)
