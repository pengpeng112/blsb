"""Synthetic flow/queue/cross-process regression; no hospital or platform I/O."""
from datetime import datetime,timedelta
from types import SimpleNamespace
import json,subprocess,sys
import pytest
from connector_sdk import RequestDeferred
from core_db import make_engine,make_session_factory,run_migrations
from core_db.models import Job,DeliveryAttempt,ReceiptEvent
from workflow_core.jobs import enqueue_job,claim_job,defer_job
from destinations.alihealth_drugtrace.rate_control import SharedRequestPacer,rate_limit_info,platform_error,retry_seconds
from destinations.alihealth_drugtrace.protocol import parse_query_response,parse_response,QUERY_METHOD
from domains.drug_trace import service as S

TOP7={'error_response':{'code':7,'msg':'App Call Limited','sub_code':'accesscontrol.limited-by-api-access-count',
 'sub_msg':'This ban will last for 601 more seconds','request_id':'synthetic-request'}}

@pytest.mark.parametrize('sub,scope',[
 ('accesscontrol.limited-by-api-access-count','method'),
 ('accesscontrol.limited-by-app-access-count','app'),
 ('accesscontrol.limited-by-app-api-access-count','app_method')])
def test_top7_fields_and_supplied_wait_retained(sub,scope):
    payload=json.loads(json.dumps(TOP7));payload['error_response']['sub_code']=sub
    body=json.dumps(payload).encode();info=rate_limit_info(200,body,'120')
    assert info['retry_after']==601 and info['scope']==scope
    assert info['sub_code']==sub and info['request_id']=='synthetic-request'
    assert parse_query_response(200,body)['error']['sub_code']==sub
    upload=parse_response('synthetic.upload',200,body)
    assert upload['outcome']=='UNKNOWN' and upload['error']['sub_msg'].endswith('601 more seconds')

def test_retry_after_date_invalid_values_and_redaction():
    assert retry_seconds('Thu, 01 Jan 1970 00:02:00 GMT',now=100)==20
    assert retry_seconds('nan') is None and retry_seconds('-1') is None
    assert rate_limit_info(200,b'{"ok":true}') is None
    assert rate_limit_info(429,retry_after='90')['retry_after']==90
    error=platform_error({'msg':'trace_codes=12345678901234567890 app_secret=secret session=token','sub_msg':'card 123456789012345678'})
    assert 'token' not in error['msg'] and '123456789012345678' not in str(error)

def test_shared_spacing_and_method_cooldown_survive_new_instances(tmp_path):
    now=[1000.];waits=[]
    def sleep(n):waits.append(n);now[0]+=n
    def pacer():return SharedRequestPacer(tmp_path,'https://dmz',clock=lambda:now[0],sleep=sleep,jitter=lambda:0)
    a=pacer();a.acquire('app','query.billstatus');pacer().acquire('app','query.billstatus')
    assert waits==[4.5]
    assert a.cool_down(rate_limit_info(200,json.dumps(TOP7).encode()),'app','query.billstatus')==601
    with pytest.raises(RequestDeferred) as exc:pacer().acquire('other-app','query.billstatus')
    assert exc.value.retry_after==601
    pacer().acquire('app','uploadretail')
    now[0]+=602;pacer().preflight('app','query.billstatus')
    assert all('app_key' not in p.read_text() for p in tmp_path.glob('*.json'))

def test_fallback_backoff_persists_and_never_shortens_supplied_wait(tmp_path):
    now=[1000.]
    def pacer():return SharedRequestPacer(tmp_path,'https://dmz',clock=lambda:now[0],jitter=lambda:0)
    info=rate_limit_info(200,b'{"error_response":{"code":7}}')
    assert pacer().cool_down(info,'a',QUERY_METHOD)==180
    now[0]+=200
    assert pacer().cool_down(info,'a',QUERY_METHOD)==360
    info['retry_after']=7200
    assert pacer().cool_down(info,'a',QUERY_METHOD)==7200

def test_corrupt_shared_state_fails_closed(tmp_path):
    a=SharedRequestPacer(tmp_path,'https://dmz');a.path.write_text('bad-json')
    with pytest.raises(RequestDeferred):a.acquire('a',QUERY_METHOD)
    assert a.path.read_text()=='bad-json'

def test_real_processes_share_spacing(tmp_path):
    script='''import sys,time
import _bootstrap
from destinations.alihealth_drugtrace.rate_control import SharedRequestPacer
from connector_sdk import RequestDeferred
p=SharedRequestPacer(sys.argv[1],'test-origin',interval=.12,query_interval=.12)
for i in range(3):
 while True:
  try:p.acquire('a','query.billstatus');break
  except RequestDeferred:time.sleep(.02)
 print(time.time(),flush=True)
'''
    processes=[subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
    stamps=[]
    for p in processes:
        output,error=p.communicate(timeout=20);assert p.returncode==0,error
        stamps.extend(map(float,output.splitlines()))
    stamps.sort();assert len(stamps)==6
    assert all(b-a>=.10 for a,b in zip(stamps,stamps[1:]))

@pytest.fixture
def queue(tmp_path):
    import app_runtime
    engine=make_engine('sqlite:///'+str(tmp_path/'queue.sqlite'))
    run_migrations(engine,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
    yield make_session_factory(engine)
    engine.dispose()

def test_deferral_preserves_job_attempt_generation_and_fencing(queue):
    with queue() as db:
        job=enqueue_job(db,'synthetic','drug_trace.query_status','q:one:1',{'submission_id':'one'});db.commit()
        lease=claim_job(db,'worker',['drug_trace.query_status']);assert lease.job.attempts==1
        assert defer_job(db,lease,601);db.refresh(job)
        assert job.state=='RETRY_WAIT' and job.attempts==0
        from core_db import utcnow
        assert (job.available_at-utcnow()).total_seconds()>595
        assert db.query(Job).count()==1 and db.query(ReceiptEvent).count()==0
        assert not defer_job(db,lease,10)
        job.available_at=utcnow()-timedelta(seconds=1);db.commit()
        newer=claim_job(db,'other',['drug_trace.query_status']);assert newer.epoch>lease.epoch
        assert not defer_job(db,lease,10)

def test_expired_deferral_fails_explicitly(queue):
    from core_db import utcnow
    with queue() as db:
        job=enqueue_job(db,'synthetic','drug_trace.query_status','q:one:1',{})
        job.created_at=utcnow()-timedelta(days=2);db.commit()
        lease=claim_job(db,'worker',['drug_trace.query_status']);assert defer_job(db,lease,100)
        db.refresh(job);assert job.state=='FAILED' and job.last_error['code']=='RATE_LIMIT_WAIT_EXPIRED'

def test_worker_defers_without_business_side_effect(queue):
    from apps.worker.engine import WorkerEngine
    class Registry:
        def domain_job_handlers(self):return {'synthetic.request':lambda db,job:(_ for _ in ()).throw(RequestDeferred(300))}
    with queue() as db:
        enqueue_job(db,'synthetic','synthetic.request','one',{});db.commit()
        assert WorkerEngine(queue,registry=Registry())._run_due_jobs(db,1)==1
        job=db.query(Job).one();assert job.state=='RETRY_WAIT' and job.attempts==0
        assert db.query(DeliveryAttempt).count()==0

@pytest.mark.parametrize('state',['CONFIRMED','REJECTED','PARTIAL','SUPERSEDED'])
def test_terminal_query_never_calls_platform(state,monkeypatch):
    from domains.drug_trace.plugin import _j_query
    monkeypatch.setattr(S,'perform_query',lambda *a:pytest.fail('terminal query contacted platform'))
    db=SimpleNamespace(get=lambda *a:SimpleNamespace(tenant_id='t',state=state))
    assert _j_query(db,SimpleNamespace(tenant_id='t',payload={'submission_id':'s'}))['skipped']=='terminal state'

def test_overlapping_dates_merge_disjoint_dates_stay():
    assert S._query_windows({'bill_time':'2026-10-24T08:00:00'},datetime(2026,10,25))==[('2026-10-23','2026-10-26')]
    assert S._query_windows({'bill_time':'2026-07-01T08:00:00'},datetime(2026,10,25))==[('2026-06-30','2026-07-02'),('2026-10-24','2026-10-26')]

def test_deferred_read_is_not_query_failure():
    class Client:
        def query_bill_status(self,params):raise RequestDeferred(300)
    with pytest.raises(RequestDeferred):S._platform_query_full(Client(),{'bill_code':'synthetic'})

def test_top7_query_is_error_with_hint_not_not_found():
    class Client:
        def query_bill_status(self,params):return {'error':platform_error(TOP7['error_response']),'rate_limit':rate_limit_info(200,json.dumps(TOP7).encode())}
    full=S._platform_query_full(Client(),{'bill_code':'synthetic'})
    classified=S._classify_query(full,'synthetic')
    assert classified['state']=='QUERY_ERROR' and classified['rate_limit']['retry_after']==601
    assert full['status']=='RATE_LIMITED'


@pytest.mark.parametrize('persistence_failure',[False,True])
def test_transport_top7_one_request_and_no_upload_replay(tmp_path,monkeypatch,persistence_failure):
    import io
    from destinations.alihealth_drugtrace import transport as T
    hits=[]
    class Response:
        status=200
        def __init__(self):self.stream=io.BytesIO(json.dumps(TOP7).encode())
        def getheader(self,name,default=None):return default
        def read1(self,n):return self.stream.read(n)
    class Connection:
        sock=None
        def __init__(self,*a,**k):pass
        def request(self,*a,**k):hits.append(a)
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr(T.ssl,'create_default_context',lambda **k:None)
    monkeypatch.setattr(T.http.client,'HTTPSConnection',Connection)
    monkeypatch.setenv('BLYB_DRUGTRACE_RATE_STATE_DIR',str(tmp_path))
    t=T.RealTransport('https://synthetic-dmz')
    if persistence_failure:
        monkeypatch.setattr(t._shared_pacer,'cool_down',lambda *a:(_ for _ in ()).throw(RequestDeferred(2)))
    result=t.post(b'app_key=synthetic&method=synthetic.upload','application/x-www-form-urlencoded')
    assert len(hits)==1 and result.http_status==200
    assert result.rate_limit['retry_after']>=601
    if persistence_failure:
        assert result.rate_limit['shared_state_unavailable']
    else:
        with pytest.raises(RequestDeferred):t.post(b'app_key=synthetic&method=synthetic.upload','form')
        assert len(hits)==1


def test_cooldown_preflight_occurs_before_send_quota_or_attempt(tmp_path,monkeypatch):
    from test_drug_trace_fix3 import _frozen
    import app_runtime
    from core_db.models import Tenant,Submission,Destination
    from contracts import new_uuid
    engine=make_engine('sqlite:///'+str(tmp_path/'preflight.sqlite'))
    run_migrations(engine,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
    sf=make_session_factory(engine)
    with sf() as db:
        tenant=Tenant(id=new_uuid(),code='synthetic',name='test');db.add(tenant);db.commit();tenant_id=tenant.id
    f=_frozen({'sf':sf,'tenant_id':tenant_id},bill_id='preflight')
    monkeypatch.setenv('BLYB_DRUGTRACE_RATE_STATE_DIR',str(tmp_path/'rate'))
    cfg={'dmz_base_url':'https://synthetic-dmz','app_key':'synthetic'}
    SharedRequestPacer(tmp_path/'rate',cfg['dmz_base_url'],jitter=lambda:0).cool_down(
        {'scope':'app','source':'top7','retry_after':601},'synthetic','irrelevant')
    monkeypatch.setattr(S,'_load_config_revision',lambda *a,**k:SimpleNamespace(content_json=cfg))
    monkeypatch.setattr(S,'evidence_state',lambda *a,**k:{'evidence_id':'test','scope':{'window_begin':'2026-10-01T00:00:00','window_end':'2026-11-01T00:00:00','pharmacies':['YF01'],'max_bills':10}})
    monkeypatch.setattr(S,'require_production_evidence',lambda *a,**k:None)
    monkeypatch.setattr(S,'_reserve_send_quota',lambda *a:pytest.fail('cooldown spent quota'))
    try:
        with sf() as db:
            dest=db.get(Destination,f['dest']);dest.is_mock=False;db.commit()
            sub=db.get(Submission,f['submission']['submission_id'])
            with pytest.raises(RequestDeferred):S.perform_send(db,tenant_id,sub)
            assert sub.state=='PREPARED' and db.query(DeliveryAttempt).count()==0
    finally:engine.dispose()
