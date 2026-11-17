"""Synthetic tests of maintenance authority boundaries; no network or production DB."""
from pathlib import Path
from urllib.parse import parse_qs
import ast,importlib.util,json
import pytest
from types import SimpleNamespace
from unittest.mock import patch
import sys

HERE=Path(__file__).resolve().parent

def query_only():
    tree=ast.parse((HERE/'old41_container.py').read_text(encoding='utf8'))
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='QueryOnly')
    namespace={'parse_qs':parse_qs,'QUERY_METHOD':'alibaba.alihealth.drugtrace.top.yljg.query.billstatus'}
    exec(compile(ast.Module(body=[cls],type_ignores=[]),'isolated-query-guard','exec'),namespace)
    return namespace['QueryOnly']

@pytest.mark.parametrize('method',['uploadretail','uploadinoutbill'])
def test_old_confirmation_tool_cannot_upload(method):
    calls=[]
    class Wire:
        def post(self,*args):calls.append(args)
    transport=query_only()(Wire())
    with pytest.raises(AssertionError,match='UPLOAD_BLOCKED'):
        transport.post(('method=alibaba.alihealth.drugtrace.top.yljg.'+method).encode(),'form')
    assert calls==[]

@pytest.mark.parametrize('wire_error',[False,True])
def test_query_network_attempt_budget_includes_uncertain_wire_errors(wire_error):
    calls=[]
    class Wire:
        def post(self,*args):
            calls.append(args)
            if wire_error:raise OSError('synthetic interrupted response')
            return 'ok'
    transport=query_only()(Wire());body=b'method=alibaba.alihealth.drugtrace.top.yljg.query.billstatus'
    for _ in range(4):
        if wire_error:
            with pytest.raises(OSError):transport.post(body,'form')
        else:assert transport.post(body,'form')=='ok'
    with pytest.raises(AssertionError):transport.post(body,'form')
    assert len(calls)==4

@pytest.fixture
def watcher(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('test_watch_completion',HERE/'watch_completion.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'evidence').mkdir();module.HERE=tmp_path;module.STATE=tmp_path/'evidence/state.json'
    monkeypatch.setattr(module.os,'umask',lambda n:0);monkeypatch.setattr(module,'sync',lambda:None)
    return module

def completed_progress(**changes):
    result={'outcome':'ALL_CONFIRMED','snapshot':{'window_states':{'CONFIRMED':1995}},'old_246_unchanged':True,'checkpoint_unchanged':True}
    result.update(changes);return {'final.json':result}

@pytest.mark.parametrize('changes,exception',[
    ({'outcome':'BUSINESS_REVIEW_REQUIRED'},RuntimeError),
    ({'old_246_unchanged':False},AssertionError),
    ({'checkpoint_unchanged':False},AssertionError),
])
def test_watcher_never_launches_old_confirmation_before_guarded_completion(watcher,changes,exception):
    calls=[]
    def remote(phase):calls.append(phase);return completed_progress(**changes)
    watcher.remote=remote
    with pytest.raises(exception):watcher.main()
    assert calls==['resumed_status']

def test_watcher_launches_exactly_one_query_only_confirmation_after_1995_confirmed(watcher):
    calls=[]
    fresh={'old_205':{'n':205,'hash':watcher.EXPECTED_205},'window_states':{'CONFIRMED':1995},
           'states':{'CONFIRMED':4613,'UNKNOWN':205},'inflight':0,'quota':[{'used':4820,'max_bills':80000}]}
    def remote(phase):
        calls.append(phase)
        if phase=='resumed_status':return completed_progress()
        if phase=='fresh':return fresh
        if phase=='old41_status':return {'result.json':{'result':{'outcome':'ALL_41_CONFIRMED','rows_count':41,
            'row_states':{'CONFIRMED':41},'query_generations_unchanged':True,'before':{'attempts':4820},'after':{'attempts':4820}}}}
        assert phase in ('old41_launch','detail','runtime');return {}
    watcher.remote=remote;watcher.main()
    assert calls.count('old41_launch')==1
    assert calls.index('old41_launch')>calls.index('fresh')>calls.index('resumed_status')
    assert json.loads(watcher.STATE.read_text(encoding='utf8'))['outcome']=='RECOVERY_AND_OLD41_COMPLETE'

def test_used_watcher_cannot_restart_operations(watcher):
    (watcher.HERE/'evidence/completion-watch.claimed').write_text('used')
    watcher.remote=lambda phase:pytest.fail('used watcher reached a remote operation')
    with pytest.raises(FileExistsError):watcher.main()

@pytest.mark.parametrize('recovery,old41,driver,active_jobs',[
    ('RUNNING','ALL_41_CONFIRMED','inactive',0),
    ('ALL_CONFIRMED','STOPPED_FOR_REVIEW','inactive',0),
    ('ALL_CONFIRMED','ALL_41_CONFIRMED','active',0),
    ('ALL_CONFIRMED','ALL_41_CONFIRMED','inactive',1),
])
def test_ui_upgrade_cannot_pause_live_recovery_and_restores_timer_on_failed_drain(recovery,old41,driver,active_jobs):
    source=(HERE/'phase_pause_remote.py').read_text(encoding='utf8');calls=[];writes=[]
    class FakePath:
        def __init__(self,path):self.path=str(path)
        def resolve(self):return SimpleNamespace(name='stability-20261027T030000Z-r2')
        def read_text(self):
            if 'old41-confirmations' in self.path:return json.dumps({'exit_code':0,'result':{'outcome':old41}})
            assert self.path.endswith('/final.json');return json.dumps({'outcome':recovery})
        def mkdir(self,**kwargs):pass
        def __truediv__(self,name):return FakePath(self.path+'/'+name)
        def write_text(self,text):writes.append(self.path)
    def run(args,**kwargs):
        calls.append(args)
        if args[:2]==['systemctl','show']:value=driver
        elif args[0]=='docker':value=json.dumps({'states':{'CONFIRMED':4613,'UNKNOWN':205},'active_jobs':active_jobs,
            'inflight':0,'protected_old_hash':'7a691905901917167cf999c3d8f4c202'})
        else:value=''
        return SimpleNamespace(returncode=0,stdout=value)
    with patch('pathlib.Path',FakePath),patch('subprocess.run',run):
        with pytest.raises(AssertionError):exec(compile(source,'isolated-phase-guard','exec'),{})
    assert writes==[]
    mutations=[args[1:3] for args in calls if args[0]=='systemctl' and args[1] in ('stop','start')]
    if active_jobs:
        assert mutations==[['stop','blyb-daily-upload.timer'],['start','blyb-daily-upload.timer']]
    else:assert mutations==[]

def test_existing_query_ledger_count_is_distinct_from_next_receipt_generation(tmp_path):
    sys.path.insert(0,str(HERE.parents[2]/'implementation'))
    import _bootstrap,app_runtime
    from core_db import make_engine,make_session_factory,run_migrations
    from core_db.models import Tenant
    from workflow_core.jobs import enqueue_job
    from domains.drug_trace import service as S
    tree=ast.parse((HERE/'old41_container.py').read_text(encoding='utf8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='existing_query_count')
    namespace={'S':S};exec(compile(ast.Module(body=[fn],type_ignores=[]),'ledger-count-guard','exec'),namespace)
    engine=make_engine('sqlite:///'+str(tmp_path/'ledger.sqlite'))
    run_migrations(engine,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
    try:
        with make_session_factory(engine)() as db:
            db.add(Tenant(id='ledger-guard',code='ledger-guard',name='synthetic'));db.commit()
            for n in range(1,13):enqueue_job(db,'ledger-guard',S.C.DomainJobType.QUERY,'q:target:'+str(n),{'submission_id':'target'})
            enqueue_job(db,'ledger-guard',S.C.DomainJobType.QUERY,'q:other:1',{'submission_id':'other'});db.commit()
            assert S._query_generation(db,'ledger-guard','target')==13
            assert namespace['existing_query_count'](db,'ledger-guard','target')==12
            assert namespace['existing_query_count'](db,'ledger-guard','absent')==0
    finally:engine.dispose()
