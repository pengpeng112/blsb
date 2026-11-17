"""Report actual collection progress on timeout; no production or network."""
from pathlib import Path
from datetime import timedelta
import importlib.util,json
from types import SimpleNamespace
import pytest
from test_drug_trace_daily_driver_repair import driver


def test_timeout_report_retains_running_collection_and_actual_progress(driver,monkeypatch,capsys):
    clock=[0.];reports=[]
    day=driver.datetime.now(driver.GMT8).date()-timedelta(days=1)
    window=[day.isoformat()+'T00:00:00',(day+timedelta(days=1)).isoformat()+'T00:00:00']
    def call(method,path,body=None,timeout=90):
        if method=='GET':return 200,[{'id':'run','state':'RUNNING','window':window,'summary':{'slices_completed':1,'bills_seen':25,'valid':20}}]
        assert path=='/drug-trace/daily-reports';reports.append(body);return 201,{}
    driver.call=call;driver.COLLECTION_WAIT_SECONDS=15
    monkeypatch.setenv('BLYB_DAILY_REPORT_ENABLED','1')
    monkeypatch.setattr(driver.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(driver.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    assert driver.main(day)==3
    result=json.loads(capsys.readouterr().out)
    assert result['fatal']=='collect_timeout'
    c=reports[0]['collection']
    assert c['state']=='RUNNING' and c['id']=='run' and c['summary']['bills_seen']==25
    assert c['error_code']=='collect_timeout' and c['candidate_count_known'] is False


def test_stalled_collection_stops_before_ceiling_without_upload(driver,monkeypatch,capsys):
    clock=[0.];day=driver.datetime.now(driver.GMT8).date()-timedelta(days=1)
    window=[day.isoformat()+'T00:00:00',(day+timedelta(days=1)).isoformat()+'T00:00:00']
    def call(method,path,body=None,timeout=90):
        assert method=='GET'
        return 200,[{'id':'run','state':'RUNNING','window':window,'summary':{'slices_completed':1,'bills_seen':25}}]
    driver.call=call;driver.COLLECTION_WAIT_SECONDS=100;driver.COLLECTION_STALL_SECONDS=5
    monkeypatch.setattr(driver.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(driver.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    assert driver.main(day)==3
    assert json.loads(capsys.readouterr().out)['fatal']=='collect_progress_stalled'
    assert clock[0]<100


def test_recovery_clone_forbids_source_recollection_and_preserves_timer_report():
    path=Path(__file__).resolve().parents[3]/'docs/operations/drug-trace-recovery-stability-20261027/recover_runner.py'
    spec=importlib.util.spec_from_file_location('stable_recovery',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    source="REPORT_SOURCE = 'timer'\ndef call(method, path, body=None, timeout=90):\n    return 200, {}\n"
    ns={};exec(m.recovery_source(source),ns)
    assert ns['REPORT_SOURCE']=='manual'
    assert ns['call']('POST','/drug-trace/collection-runs')[0]==412
    assert ns['call']('POST','/drug-trace/submissions/synthetic/send')[0]==200

@pytest.mark.parametrize('state,job,error',[
    ('PREPARED','PENDING',{}),
    ('PREPARED','RETRY_WAIT',{'code':'PLATFORM_COOLDOWN'}),
    ('SENDING','LEASED',{}),
])
def test_total_driver_budget_bounds_queue_cooldown_and_execution_without_repost(driver,monkeypatch,state,job,error):
    clock=[0.];calls=[]
    monkeypatch.setattr(driver.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(driver.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    driver.call=lambda *a,**kw:calls.append(a)
    db=SimpleNamespace(execute=lambda *a,**kw:SimpleNamespace(fetchone=lambda:(state,job,error)))
    assert driver.wait_for_send(db,'t','s','j',total_deadline=3)==(state,'RUN_TIME_BUDGET_EXHAUSTED')
    assert 3<=clock[0]<=4 and calls==[]

@pytest.mark.parametrize('state,error',[
    ('ACCEPTED',None),('CONFIRMED',None),('UNKNOWN','SEND_RESULT_UNKNOWN'),('REJECTED',None),
])
def test_observed_send_result_remains_authoritative_at_total_budget_boundary(driver,monkeypatch,state,error):
    monkeypatch.setattr(driver.time,'monotonic',lambda:10.)
    db=SimpleNamespace(execute=lambda *a,**kw:SimpleNamespace(fetchone=lambda:(state,'SUCCEEDED',{})))
    assert driver.wait_for_send(db,'t','s','j',total_deadline=10)==(state,error)
