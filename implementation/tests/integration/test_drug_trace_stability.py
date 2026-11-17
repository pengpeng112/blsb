"""Bounded collection/connection/lease tests. No hospital or platform I/O."""
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
import time
import pytest
from sqlalchemy import update
from core_db import utcnow
from core_db.models import Job,SourceCheckpoint,OutboxEvent
from workflow_core.jobs import enqueue_job,claim_job,renew_job,yield_job,JobLeaseLost,JobYield
from workflow_core.lease_guard import LeaseGuard,current_guard
from domains.drug_trace import service as S
from domains.drug_trace.models import DrugTraceBill,DrugTraceCollectionRun
from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
from test_drug_trace_fix3 import env,_connector_id,_bill_row,_code_row
from test_drug_trace_oracle_first_page import repository,KEY


def test_page_slices_resume_tied_cursor_without_rewinding_or_double_counting(env):
    cid=_connector_id(env);w=SyntheticDrugTraceWarehouse()
    for n in range(3):
        k='B'+str(n);w.add_bill(_bill_row(k));w.add_code(_code_row(k,record_id='R'+str(n)))
    with env['sf']() as db:
        kwargs=dict(window_begin=datetime(2026,10,12),window_end=datetime(2026,10,13),page_limit=2,max_pages=1,partition='slice')
        first=S.run_collection(db,env['tenant_id'],cid,w,**kwargs)
        assert first['state']=='RUNNING' and first['yielded'] and first['bills_seen']==2
        assert db.query(DrugTraceBill).count()==2
        cp=db.query(SourceCheckpoint).one();assert cp.cursor_json['completed_window'] is False
        second=S.run_collection(db,env['tenant_id'],cid,w,run_id=first['run_id'],**kwargs)
        assert second['state']=='DONE' and second['bills_seen']==3 and second['valid']==3
        assert second['slices_completed']==2 and db.query(DrugTraceBill).count()==3
        db.refresh(cp);assert cp.cursor_json['completed_window'] is True


def test_lost_lease_rolls_back_whole_page_and_does_not_fail_new_owners_run(env):
    cid=_connector_id(env);w=SyntheticDrugTraceWarehouse();w.add_bill(_bill_row());w.add_code(_code_row())
    class LostAtCommit:
        calls=0
        def check(self):pass
        def fence(self,db):
            self.calls+=1
            if self.calls==2:raise JobLeaseLost('synthetic takeover before page commit')
    with env['sf']() as db:
        with pytest.raises(JobLeaseLost):
            S.run_collection(db,env['tenant_id'],cid,w,window_begin=datetime(2026,10,12),window_end=datetime(2026,10,13),max_pages=1,execution_guard=LostAtCommit())
        assert db.query(DrugTraceBill).count()==0 and db.query(SourceCheckpoint).count()==0
        assert db.query(DrugTraceCollectionRun).one().state=='RUNNING'
        assert db.query(OutboxEvent).count()==0


def job_lease(env):
    with env['sf']() as db:
        j=enqueue_job(db,env['tenant_id'],'drug_trace.collect','synthetic-collect',{});db.commit()
        lease=claim_job(db,'first',['drug_trace.collect']);assert lease.job.id==j.id
        return lease


def test_same_job_yields_many_pages_without_spending_failure_attempts(env):
    lease=job_lease(env)
    with env['sf']() as db:
        for _ in range(8):
            assert yield_job(db,lease)
            j=db.get(Job,lease.job.id);db.refresh(j)
            assert j.attempts==0 and j.last_error['code']=='JOB_YIELDED'
            j.available_at=utcnow()-timedelta(seconds=1);db.commit()
            lease=claim_job(db,'next',['drug_trace.collect'])
        assert db.query(Job).count()==1 and lease.job.attempts==1


def test_renewal_and_page_fence_refuse_expiry_or_takeover(env,tmp_path):
    lease=job_lease(env);guard=LeaseGuard(env['sf'],lease,heartbeat=str(tmp_path/'health.json'))
    assert guard.pulse() and (tmp_path/'health.json').exists()
    with env['sf']() as db:
        j=db.get(Job,lease.job.id);j.lease_until=utcnow()-timedelta(seconds=1);db.commit()
        assert not renew_job(db,lease)
        new=claim_job(db,'winner',['drug_trace.collect']);assert new.epoch>lease.epoch
        with pytest.raises(JobLeaseLost):guard.fence(db)
        db.rollback();db.refresh(new.job);assert new.job.state=='LEASED'


def test_stalled_progress_does_not_refresh_health_or_renew_forever(env,tmp_path):
    lease=job_lease(env);path=tmp_path/'health'
    g=LeaseGuard(env['sf'],lease,heartbeat=str(path),stale_after=.01)
    g.last_progress=time.monotonic()-1
    assert g.pulse() is False and not path.exists()
    with pytest.raises(JobLeaseLost):g.check()


def test_guard_thread_renews_using_private_session_and_stops(env,tmp_path):
    lease=job_lease(env);path=tmp_path/'health'
    with LeaseGuard(env['sf'],lease,interval=.02,heartbeat=str(path)) as g:
        deadline=time.monotonic()+2
        while not path.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert path.exists() and not g.lost.is_set() and current_guard() is g
    assert not g.thread.is_alive() and current_guard() is None


def test_lost_guard_cannot_return_success_after_handler_returns(env):
    lease=job_lease(env)
    with pytest.raises(JobLeaseLost):
        with LeaseGuard(env['sf'],lease) as g:g.lost.set()
    assert current_guard() is None and not g.thread.is_alive()


def test_worker_does_not_finalize_operation_when_another_epoch_won(env,monkeypatch):
    from apps.worker.engine import WorkerEngine
    import workflow_core.jobs as J
    lease=job_lease(env)
    with env['sf']() as db:
        j=db.get(Job,lease.job.id);j.state='PENDING';db.commit()
    def takeover(db,job):
        with env['sf']() as winner:
            j=winner.get(Job,job.id);j.lease_epoch+=1;winner.commit()
    reg=SimpleNamespace(domain_job_handlers=lambda:{'drug_trace.collect':takeover})
    worker=WorkerEngine(env['sf'],registry=reg);finalized=[]
    monkeypatch.setattr(worker,'_finalize_operation',lambda *a,**k:finalized.append(1))
    with env['sf']() as db:worker._run_due_jobs(db,1)
    assert finalized==[]


def test_worker_yield_preserves_durable_progress_and_original_job(env):
    from apps.worker.engine import WorkerEngine
    lease=job_lease(env)
    with env['sf']() as db:
        j=db.get(Job,lease.job.id);j.state='PENDING';db.commit()
    def partial(db,job):
        job.payload={'page_done':1};current_guard().fence(db);db.commit()
        raise JobYield('page persisted')
    reg=SimpleNamespace(domain_job_handlers=lambda:{'drug_trace.collect':partial})
    worker=WorkerEngine(env['sf'],registry=reg)
    with env['sf']() as db:
        assert worker._run_due_jobs(db,1)==1
        db.expire_all();j=db.get(Job,lease.job.id)
        assert j.state=='RETRY_WAIT' and j.payload=={'page_done':1} and j.attempts==1


def test_oracle_collection_page_reuses_connection_and_keeps_standalone_reads_isolated(repository):
    wh,header,codes=repository
    factory=wh._conn_factory;opened=[]
    def connect():
        c=factory();opened.append(c);return c
    wh._conn_factory=connect
    with wh.collection_session():
        for _ in range(3):
            rows=wh.fetch_all('drugtrace.bill_codes',{**KEY,'after_record_id':None},2)
            assert len(rows)==2
        assert len(opened)==wh.metrics['connect_count']==1
    assert wh._session_connection is None
    wh.fetch_all('drugtrace.bill_codes',{**KEY,'after_record_id':None},2)
    assert len(opened)==2 and wh.metrics['query_count']==4


def test_oracle_failed_query_closes_reused_connection_without_retry(repository):
    wh,_,_=repository
    with wh.collection_session():
        wh.fetch_all('drugtrace.bill_codes',{**KEY,'after_record_id':None},2)
        conn=wh._session_connection;conn.conn.close()
        with pytest.raises(Exception):wh.fetch_all('drugtrace.bill_codes',{**KEY,'after_record_id':None},2)
        assert wh._session_connection is None and wh.metrics['connect_count']==1


def test_lease_lost_after_source_read_is_not_translated_into_database_error(repository):
    wh,_,_=repository;calls=[]
    def hook():
        calls.append(1)
        if len(calls)==2:raise JobLeaseLost('synthetic lost after read')
    wh.progress_hook=hook
    with wh.collection_session(),pytest.raises(JobLeaseLost):
        wh.fetch_all('drugtrace.bill_codes',{**KEY,'after_record_id':None},2)
    assert wh._session_connection is None
