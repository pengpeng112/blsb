"""Regression for the real timer failure: no network or source connections."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import urllib.error
from types import SimpleNamespace
from datetime import date, timedelta

import pytest

DRIVER = Path(__file__).resolve().parents[3] / 'docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py'


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('public-test-token\n'))
    monkeypatch.setenv('BLYB_DAILY_REPORT_ENABLED', '0')
    spec = importlib.util.spec_from_file_location('repaired_daily_driver', DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize('status,data', [
    (401, {'error_code': 'UNAUTHORIZED'}),
    (403, {'error_code': 'FORBIDDEN'}),
    (500, {'error_code': 'INTERNAL'}),
    (200, {'items': []}), (200, ['bad-row']), (200, None),
])
def test_invalid_initial_list_stops_before_mutation(driver, capsys, status, data):
    calls = []
    def fake(method, path, body=None, timeout=90):
        calls.append((method, path))
        return status, data
    driver.call = fake
    assert driver.main() == 2
    result = json.loads(capsys.readouterr().out)
    assert result['fatal'] == 'collect_list_failed'
    assert result['outcome'] == 'FAILED' and len(result['window']) == 2
    assert result['candidate_count'] == 0 and result['send']['sent'] == 0
    assert all(method == 'GET' for method, _ in calls)
    assert 'public-test-token' not in json.dumps(result)


def test_poll_failure_keeps_window_and_does_not_send(driver, capsys):
    requested = []
    def fake(method, path, body=None, timeout=90):
        requested.append(method)
        if len(requested) == 1:
            return 200, []
        if method == 'POST':
            return 201, {'collection_run_id': 'test-run'}
        return 500, {'error_code': 'INTERNAL'}
    driver.call = fake
    assert driver.main('2026-10-24') == 3
    result = json.loads(capsys.readouterr().out)
    assert result['fatal'] == 'collect_poll_failed'
    assert result['window'] == ['2026-10-24T00:00:00', '2026-10-25T00:00:00']
    assert result['send']['sent'] == 0
    assert requested == ['GET', 'POST', 'GET']


@pytest.mark.parametrize('body', [b'not-json', b'[]', b'{"error":"secret-body"}'])
def test_malformed_success_envelope_is_safe(driver, monkeypatch, body):
    class Response(io.BytesIO):
        status = 200
    monkeypatch.setattr(driver.urllib.request, 'urlopen', lambda *a, **k: Response(body))
    status, data = driver.call('GET', '/test')
    assert status in (502, 599)
    assert data['error_code']
    assert 'secret-body' not in json.dumps(data)


def test_http_error_with_non_object_body_and_network_failure(driver, monkeypatch):
    def broken(*args, **kwargs):
        raise urllib.error.HTTPError('http://test', 401, 'No', {}, io.BytesIO(b'[]'))
    monkeypatch.setattr(driver.urllib.request, 'urlopen', broken)
    assert driver.call('GET', '/test') == (401, {'error_code': 'HTTP_ERROR'})
    def offline(*args, **kwargs):
        raise urllib.error.URLError('secret-body')
    monkeypatch.setattr(driver.urllib.request, 'urlopen', offline)
    status, data = driver.call('GET', '/test')
    assert status == 599 and 'secret-body' not in json.dumps(data)


def test_incomplete_date_cannot_create_collection(driver, capsys):
    driver.call = lambda *a, **k: pytest.fail('No API request permitted')
    assert driver.main(driver.datetime.now(driver.GMT8).date()) == 2
    assert json.loads(capsys.readouterr().out)['fatal'] == 'window_not_completed'


def test_unit_percent_specifiers_and_old_server_compatibility():
    unit = (DRIVER.parent / 'blyb-daily-upload.service').read_text('utf8')
    assert 'printf "%%s' in unit and 'printf "%s' not in unit
    assert '-e BLYB_DAILY_REPORT_ENABLED=1' in unit
    assert 'TimeoutStartSec=6h' in unit


@pytest.mark.parametrize('existing_state,worker_state,expected_sends,expected_queries,expected_exit', [
    ('UNKNOWN', 'ACCEPTED', 0, 1, 0), ('CONFIRMED', 'ACCEPTED', 0, 0, 0),
    ('REJECTED', 'ACCEPTED', 0, 0, 0), ('PREPARED', 'ACCEPTED', 1, 0, 0),
    ('PREPARED', 'UNKNOWN', 1, 0, 1), ('PREPARED', 'REJECTED', 1, 0, 1),
    ('PREPARED', 'PARTIAL', 1, 0, 1),
])
def test_state_guard_queries_unknown_and_only_resumes_prepared(
        driver, monkeypatch, capsys, existing_state, worker_state, expected_sends, expected_queries, expected_exit):
    import core_db
    class Result:
        def __init__(self, rows=(), scalar=None):self.rows=rows;self.value=scalar
        def fetchall(self):return self.rows
        def fetchone(self):return self.rows[0] if self.rows else None
        def scalar(self):return self.value
    class DB:
        def query(self, *a):return self
        def filter_by(self, **k):return self
        def one(self):return SimpleNamespace(id='test-tenant')
        def close(self):pass
        def execute(self, statement, parameters=None):
            sql=str(statement)
            if 'SELECT id FROM destination' in sql:return Result(scalar='test-destination')
            if 'SELECT b.id AS bill_id' in sql:return Result([('test-bill','test-revision')])
            if 'SELECT s.id, s.state' in sql:return Result([('test-submission',existing_state)])
            if 'AS submission_state' in sql:return Result([(worker_state,'SUCCEEDED')])
            if 'coalesce(q.polls,0)' in sql:
                return Result([('test-submission',0,0)] if existing_state=='UNKNOWN' else [])
            raise AssertionError('Unexpected database access')
    monkeypatch.setattr(core_db,'make_engine',lambda:SimpleNamespace(dispose=lambda:None))
    monkeypatch.setattr(core_db,'make_session_factory',lambda e:lambda:DB())
    monkeypatch.setattr(driver.time,'sleep',lambda seconds:None)
    posts=[]
    def fake(method,path,body=None,timeout=90):
        if method=='GET':
            return 200,[{'id':'test-run','state':'DONE','window':driver.receipt['window'],'summary':{}}]
        posts.append(path)
        return 200,{'job_id':'test-job'}
    driver.call=fake
    assert driver.main()==expected_exit
    result=json.loads(capsys.readouterr().out)
    assert sum(path.endswith('/send') for path in posts)==expected_sends
    assert sum(path.endswith('/query') for path in posts)==expected_queries
    assert result['send']['resumed']==expected_sends
    if expected_exit:
        assert result['outcome']=='PARTIAL_FAILURE'
        assert result['send']['errors'][0]['step']=='send_worker'


def test_large_failure_report_keeps_full_file_and_exact_total(driver, monkeypatch):
    monkeypatch.setenv('BLYB_DAILY_REPORT_ENABLED','1')
    errors=[{'bill':str(i),'step':'prepare','res':{'error_code':'DICTIONARY_UNMAPPED'}} for i in range(3000)]
    driver.receipt['send']['errors']=errors
    captured=[]
    def fake(method,path,body=None,timeout=90):
        captured.append(body)
        return 200,{}
    driver.call=fake
    assert driver.push_daily_report(date(2026,10,24),'PARTIAL_FAILURE')
    body=captured[0]
    assert body['send']['errors_total']==3000
    assert len(body['send']['errors'])==50
    assert body['send']['errors_truncated'] is True
    assert len(json.dumps(body)) < 64000
    assert len(driver.receipt['send']['errors'])==3000


def test_send_queued_164_seconds_does_not_timeout_or_repost(driver,monkeypatch):
    now=[0.0];posts=[]
    monkeypatch.setattr(driver.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(driver.time,'sleep',lambda n:now.__setitem__(0,now[0]+n))
    driver.call=lambda *a,**k:posts.append(a)
    class DB:
        def execute(self,sql,args):
            if now[0]<164:row=('PREPARED','PENDING')
            elif now[0]<166:row=('SENDING','LEASED')
            else:row=('ACCEPTED','SUCCEEDED')
            return SimpleNamespace(fetchone=lambda:row)
    assert driver.wait_for_send(DB(),'t','s','j')==('ACCEPTED',None)
    assert now[0]==166 and posts==[]


def test_proven_no_request_cooldown_does_not_spend_execution_timeout_or_repost(driver,monkeypatch):
    now=[0.0];posts=[]
    monkeypatch.setattr(driver.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(driver.time,'sleep',lambda n:now.__setitem__(0,now[0]+n))
    driver.call=lambda *a,**k:posts.append(a)
    class DB:
        def execute(self,sql,args):
            if now[0]<2:row=('PREPARED','LEASED',{})
            elif now[0]<604:row=('PREPARED','RETRY_WAIT',{'code':'PLATFORM_COOLDOWN'})
            elif now[0]<606:row=('SENDING','LEASED',{})
            else:row=('ACCEPTED','SUCCEEDED',{})
            return SimpleNamespace(fetchone=lambda:row)
    assert driver.wait_for_send(DB(),'t','s','j')==('ACCEPTED',None)
    assert now[0]==606 and posts==[]


@pytest.mark.parametrize('state,job,expected',[
    ('PREPARED','PENDING','SEND_QUEUE_TIMEOUT'),
    ('SENDING','LEASED','SEND_EXECUTION_TIMEOUT'),
    ('PREPARED','FAILED','SEND_JOB_FAILED'),
    ('UNKNOWN','SUCCEEDED','SEND_RESULT_UNKNOWN'),
])
def test_send_wait_preserves_state_and_reports_specific_stage(driver,monkeypatch,state,job,expected):
    now=[0.0]
    monkeypatch.setattr(driver.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(driver.time,'sleep',lambda n:now.__setitem__(0,now[0]+n))
    driver.SEND_QUEUE_WAIT_SECONDS=5;driver.SEND_EXECUTION_WAIT_SECONDS=7
    db=SimpleNamespace(execute=lambda *a,**k:SimpleNamespace(fetchone=lambda:(state,job)))
    assert driver.wait_for_send(db,'t','s','j')==(state,expected)


def test_query_budget_and_inflight_filter_do_not_reissue_247_closed_queries(driver):
    rows=[('old-'+str(i),12,0) for i in range(247)]+[('inflight',1,1),('fresh',1,0)]
    db=SimpleNamespace(execute=lambda *a,**k:SimpleNamespace(fetchall=lambda:rows))
    assert driver.eligible_queries(db,'tenant','dest')==['fresh']
    assert driver.receipt['query']['skipped_exhausted']==247
    assert driver.receipt['query']['skipped_inflight']==1
