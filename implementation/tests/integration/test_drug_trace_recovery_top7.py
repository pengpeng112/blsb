"""Finite maintenance retry guards; synthetic data, no production connection."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import importlib.util
import pytest

path=Path(__file__).resolve().parents[3]/'docs/operations/drug-trace-recovery-stability-20261027/top7_container.py'
spec=importlib.util.spec_from_file_location('history_trial',path)
trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)


def valid():
    c=dict(submission_id=trial.TARGET,state='UNKNOWN',revision=3,attempts=1,last_http_status=200,top_code='7',sub_code='accesscontrol.limited-by-api-access-count',
           bill_hash=trial.BILL_HASH,codes_count=1,same_config=True,same_source=True,same_tenant=True,
           snapshot_matches=True,destination_enabled=True,real_destination=True,active_jobs=0,inflight=0,
           send_approved=True,in_scope=True,profile='alihealth_yljg_retail_v1',bill_type='321',query_bill_type='A')
    q=dict(submission_id=trial.TARGET,bill_hash=trial.BILL_HASH,state='NOT_FOUND',query_status='OK',checked_utc=datetime.now(timezone.utc).isoformat())
    return c,q


@pytest.mark.parametrize('changes',[
    {'state':'CONFIRMED'},{'state':'ACCEPTED'},{'revision':4},{'same_tenant':False},
    {'same_config':False},{'same_source':False},{'snapshot_matches':False},
    {'active_jobs':1},{'inflight':1},{'send_approved':False},{'last_http_status':429},{'top_code':'5'},{'sub_code':'other'},
    {'submission_id':'other'},{'bill_hash':'other'},
])
def test_recovery_refuses_changed_or_already_accepted_target(changes):
    c,q=valid();c.update(changes)
    with pytest.raises(RuntimeError,match='CANDIDATE_GUARD_FAILED'):trial.validate(c,q)


@pytest.mark.parametrize('changes',[
    {'state':'CONFIRMED'},{'state':'PROCESSING'},{'query_status':'ERROR'},
    {'query_status':'RATE_LIMITED'},{'submission_id':'other'},{'bill_hash':'other'},
])
def test_recovery_requires_complete_absence_for_same_identity(changes):
    c,q=valid();q.update(changes)
    with pytest.raises(RuntimeError,match='PLATFORM_NOT_VERIFIED_ABSENT'):trial.validate(c,q)


def test_valid_absence_then_expired_evidence():
    c,q=valid();trial.validate(c,q)
    q['checked_utc']=(datetime.now(timezone.utc)-timedelta(seconds=121)).isoformat()
    with pytest.raises(RuntimeError,match='PLATFORM_LOOKUP_STALE'):trial.validate(c,q)


@pytest.mark.parametrize('wire_failure',[False,True])
def test_one_post_budget_survives_ambiguous_wire_failure(wire_failure):
    calls=[]
    class Wire:
        def post(self,*a):
            calls.append(a)
            if wire_failure:raise OSError('synthetic interrupted response')
            return 'accepted'
    t=trial.OneUploadTransport(Wire())
    body=b'method=alibaba.alihealth.drugtrace.top.yljg.uploadretail'
    if wire_failure:
        with pytest.raises(OSError):t.post(body,'form')
    else:assert t.post(body,'form')=='accepted'
    with pytest.raises(RuntimeError,match='SECOND_UPLOAD_BLOCKED'):t.post(body,'form')
    assert len(calls)==t.calls==1


def test_wrong_api_never_reaches_wire():
    class Wire:
        def post(self,*a):pytest.fail('wrong method touched network')
    t=trial.OneUploadTransport(Wire())
    with pytest.raises(RuntimeError,match='NON_RETAIL_UPLOAD_BLOCKED'):
        t.post(b'method=alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill','form')
    assert t.calls==0


def test_historical_query_count_uses_registered_domain_type(tmp_path):
    import app_runtime
    from core_db import make_engine,make_session_factory,run_migrations
    from core_db.models import Job
    from domains.drug_trace.contracts import DomainJobType
    from workflow_core.jobs import enqueue_job
    engine=make_engine('sqlite:///'+str(tmp_path/'history.sqlite'))
    run_migrations(engine,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
    try:
        with make_session_factory(engine)() as db:
            for n in [1,2]:enqueue_job(db,'test',DomainJobType.QUERY,'q:'+trial.TARGET+':'+str(n),{'submission_id':trial.TARGET})
            enqueue_job(db,'test',DomainJobType.SEND,'send:'+trial.TARGET,{'submission_id':trial.TARGET})
            enqueue_job(db,'test',DomainJobType.QUERY,'q:other:1',{'submission_id':'other'})
            db.commit()
            assert trial.query_count(db)==2
    finally:engine.dispose()
