"""Offline review counterexamples. No live source, network, secrets or code edits."""
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import importlib.util
import json
import os
import socket
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[5]
IMPL = ROOT / 'implementation'
sys.path.insert(0, str(IMPL))
import _bootstrap
from domains.drug_trace import service as S, ingest as I, contracts as C
from destinations.alihealth_drugtrace import protocol as P
from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
from destinations.alihealth_drugtrace.transport import RealTransport, TransportResult
from sources.db_view.drug_trace import OracleDrugTraceWarehouse, TEMPLATES

observations = []
def note(key, reproduced, detail):
    observations.append({'id': key, 'issue_reproduced': bool(reproduced), 'detail': detail})

def run_pure():
    payload = {'profile': C.PROFILE_INOUT, 'bill_code': 'SYN-B1', 'bill_type': '102',
               'bill_time': '2026-10-12 08:00:00', 'ref_ent_id': 'SYN-HOSP',
               'codes': [{'trace_code': '000123'}], 'warehouse_code': 'W1',
               'supplier_ref': 'SYN-SUPPLIER'}
    biz = S.build_biz_params(payload)
    required = {'bill_code', 'bill_time', 'bill_type', 'physic_type',
                'ref_user_id', 'from_user_id', 'to_user_id', 'trace_codes', 'client_type'}
    missing = sorted(required - set(biz))
    note('R2-01', bool(missing), {'missing_inout_parameters': missing, 'unexpected_doc_parameter': '_doc' in biz})

    method = C.METHOD_UPLOAD_RETAIL
    accepted = {P.method_response_key(method): {'response_success': True, 'model': 'SYN-QUEUE'}}
    result = P.parse_response(method, 200, json.dumps(accepted).encode())
    note('R2-02-upload', result['outcome'] != 'ACCEPTED', {'official_shape_outcome': result['outcome']})
    entry = {'bill_code': 'SYN-B1', 'result_type': '3', 'sub_process_flag': '51'}
    response = {P.method_response_key('alibaba.alihealth.drugtrace.top.yljg.query.billstatus'):
                {'result': {'response_success': True, 'model': {'total_num': 1,
                 'result_list': {'bill_deal_status_search_do': [entry]}}}}}
    parsed = P.parse_query_response(200, json.dumps(response).encode())
    classified = S._classify_query(parsed, 'SYN-B1')
    note('R2-02-query', classified['state'] != 'CONFIRMED', {'official_shape_classification': classified})
    partial = S._classify_query({'found': True, 'bills': {'list': [dict(entry, sub_process_flag='52')]}}, 'SYN-B1')
    note('R2-03', partial['state'] == 'CONFIRMED', {'partial_success_classification': partial})

    # Real transport type, but stub the method so no outbound call is possible.
    transport = RealTransport('https://invalid.example')
    calls = []
    def fake_post(body, ctype):
        calls.append(body)
        return TransportResult(http_status=200, body=json.dumps({P.method_response_key(method): {'success': True}}).encode())
    transport.post = fake_post
    client = AlihealthDrugTraceClient(transport, app_key='synthetic', app_secret_ref='mock:offline',
                                      resolve_secret=lambda ref: '__mock__offline')
    try:
        client.upload_retail({'bill_code': 'SYN-B1'})
        escaped = bool(calls)
    except Exception:
        escaped = False
    note('R2-04-signature', escaped, {'signature_verified_flag': P.SIGNATURE_VERIFIED,
                                    'real_transport_stub_called': bool(calls), 'network_used': False})

    ev = SimpleNamespace(id='E1', required_items=S.REQUIRED_EVIDENCE['send'],
        provided_items=[{'key': x['key']} for x in S.REQUIRED_EVIDENCE['send']],
        revoked_at=None, valid_until=None, status='APPROVED', destination_id='D1',
        config_release_id='OLD', approved_by='synthetic')
    class Query:
        def filter_by(self, **kw): return self
        def first(self): return ev
    class FakeDB:
        def query(self, model): return Query()
    state = S.evidence_state(FakeDB(), 'T1', profile=C.PROFILE_RETAIL, capability='send',
                             destination_id='D1', config_release_id='NEW')
    note('R2-05-config', state['status'] == 'APPROVED', {'mismatched_release_status': state['status'],
         'send_evidence_keys': [x['key'] for x in S.REQUIRED_EVIDENCE['send']]})

    class Cursor:
        description = [('SOURCE_BILL_ID',), ('BILL_TIME',), ('FINALIZED',), ('IS_VOID',)]
        def execute(self, *args): pass
        def fetchall(self): return [('SYN-B1', datetime(2026, 10, 12, 8), '1', '0')]
        def close(self): pass
    class Conn:
        def cursor(self): return Cursor()
        def close(self): pass
    row = OracleDrugTraceWarehouse(connection_factory=Conn).fetch_all('drugtrace.bill_header',
        {'pharmacy_code': 'YF1', 'business_kind': 'PATIENT_DISPENSE', 'source_bill_id': 'SYN-B1'}, 2)[0]
    note('R2-07-case', 'source_bill_id' not in row, {'oracle_column_keys': sorted(row)})
    try:
        I.content_digest({'bill_time': row['BILL_TIME']}, [])
        date_error = None
    except Exception as exc:
        date_error = type(exc).__name__
    note('R2-07-date', date_error == 'TypeError', {'datetime_snapshot_error': date_error})
    note('R2-07-bool', bool(row['IS_VOID']), {'bool_of_oracle_zero_string': bool(row['IS_VOID'])})
    tail = {'source_system': 'HIS', 'pharmacy_code': 'YF01', 'business_kind': 'PATIENT_DISPENSE', 'source_bill_id': 'B1'}
    cursor = I.composite_key(tail)
    sql_key = '|'.join([tail['pharmacy_code'], tail['business_kind'], tail['source_bill_id']])
    note('R2-08-cursor', sql_key > cursor, {'sql_key': sql_key, 'python_cursor': cursor,
        'same_tail_row_qualifies_again': sql_key > cursor, 'scope': 'same_timestamp_SQL_predicate_counterexample'})

def run_database(tmp):
    spec = importlib.util.spec_from_file_location('review_fixture', IMPL / 'tests/integration/test_drug_trace_domain.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    gen = fixture.env.__wrapped__(Path(tmp))
    env = next(gen)
    try:
        from core_db.models import Submission, DeliveryAttempt, Destination, Job, ReceiptEvent
        from domains.drug_trace.models import DrugTraceProductionEvidence, DrugTraceCollectionRun
        from domains.drug_trace import plugin
        from contracts import new_uuid, SubmissionState
        f = fixture._frozen_retail(env, Path(tmp))
        sid = f['submission']['submission_id']
        with env['sf']() as db:
            sub = db.get(Submission, sid)
            sub.state = SubmissionState.UNKNOWN
            db.commit()
            result = S.perform_send(db, env['tenant_id'], sub)
            note('R2-06-unknown', result.get('outcome') == 'ACCEPTED',
                 {'direct_UNKNOWN_resend_outcome': result.get('outcome'), 'transport': 'mock'})
            before = db.query(DeliveryAttempt).filter_by(submission_id=sid).count()
            sub.state = SubmissionState.CONFIRMED
            db.commit()
            S.perform_send(db, env['tenant_id'], sub, dry_run=True)
            after = db.query(DeliveryAttempt).filter_by(submission_id=sid).count()
            note('R2-06-dryrun', after > before or sub.state != SubmissionState.CONFIRMED,
                 {'attempts_before': before, 'attempts_after': after, 'confirmed_state_after_dryrun': sub.state})
            # The existing reconciliation procedure must not call a platform at all.
            dest = db.get(Destination, sub.destination_id)
            S.approve_evidence(db, env['tenant_id'], profile=C.PROFILE_RETAIL, capability='reconcile',
                destination_id=dest.id, provided_items=[{'key': 'EXT-09'}], approved_by='synthetic')
            sub.state = SubmissionState.CONFIRMED
            db.commit()
            calls = []
            with patch.object(S, 'build_client', side_effect=lambda *a: calls.append(a)):
                res = S.run_reconciliation(db, env['tenant_id'], destination_id=dest.id, profile=C.PROFILE_RETAIL,
                    window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
            note('R2-10-reconcile', not calls and res['summary']['matched'] > 0,
                 {'platform_client_calls': len(calls), 'summary': res['summary']})
            # A queued collection whose evidence was revoked and state paused.
            connector = fixture._connector_id(env)
            run = DrugTraceCollectionRun(id=new_uuid(), tenant_id=env['tenant_id'], connector_id=connector,
                stream=I.STREAM, profile=C.PROFILE_RETAIL, state='PAUSED',
                window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
            db.add(run); db.commit()
            job = SimpleNamespace(tenant_id=env['tenant_id'], payload={'connector_id': connector,
                'collection_run_id': run.id, 'connector_config': {'mode': 'oracle'},
                'profile': C.PROFILE_RETAIL, 'window_begin': '2026-10-12T00:00:00',
                'window_end': '2026-10-13T00:00:00'})
            with patch.object(S, 'make_warehouse', return_value=fixture._wh_with([], [])):
                plugin._j_collect(db, job)
            note('R2-09-stop', run.state == 'DONE', {'paused_run_no_collect_evidence_after_worker': run.state,
                                                   'source_used': 'empty_synthetic_stub_not_HIS'})
            from workflow_core.jobs import enqueue_job
            from contracts import JobState
            one = enqueue_job(db, env['tenant_id'], C.DomainJobType.QUERY, f'q:{sid}', {'submission_id': sid})
            one.state = JobState.SUCCEEDED
            db.commit()
            two = enqueue_job(db, env['tenant_id'], C.DomainJobType.QUERY, f'q:{sid}', {'submission_id': sid})
            note('R2-06-poll', two.id == one.id and two.state == JobState.SUCCEEDED,
                 {'query_job_reused_as_completed': two.id == one.id, 'state_after_manual_requery': two.state})
            class FakeClient:
                def __init__(self): self.result_type = '1'
                def query_bill_status(self, biz):
                    return {'found': True, 'unknown': False, 'bills': {'list': [
                        {'bill_code': self.bill_code, 'result_type': self.result_type, 'sub_process_flag': '51'}]}}
            fc = FakeClient()
            from core_db.models import PackageRevision
            fc.bill_code = db.get(PackageRevision, sub.package_revision_id).payload['bill_code']
            with patch.object(S, 'build_client', return_value=fc):
                S.perform_query(db, env['tenant_id'], sub)
                fc.result_type = '3'
                S.perform_query(db, env['tenant_id'], sub)
            receipt = db.query(ReceiptEvent).filter_by(submission_ref=sid).one()
            note('R2-06-receipt', receipt.parsed_result.get('state') != sub.state,
                 {'stored_first_receipt': receipt.parsed_result, 'current_submission_state': sub.state,
                  'receipt_rows': db.query(ReceiptEvent).filter_by(submission_ref=sid).count()})
    finally:
        try: next(gen)
        except StopIteration: pass

def main():
    # Defense in depth: any accidental real network call in these probes fails.
    with patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden in offline review')):
        run_pure()
        with tempfile.TemporaryDirectory(prefix='blyb-review2-') as tmp:
            run_database(tmp)
    result = {'at_utc': datetime.now(timezone.utc).isoformat(), 'mode': 'offline_counterexamples',
              'remote_changes': False, 'business_uploads': 0, 'observations': observations}
    Path(__file__).with_name('probe-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if all(x['issue_reproduced'] for x in observations) else 2

if __name__ == '__main__':
    raise SystemExit(main())
