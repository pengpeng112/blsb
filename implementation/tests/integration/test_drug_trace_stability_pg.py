"""Real isolated PG lease renew/page rollback; no hospital endpoints."""
from datetime import timedelta
import pytest
from test_drug_trace_pg_fix3 import pg,fresh_db
from core_db import make_engine,make_session_factory,run_migrations,utcnow
from core_db.models import Job,Tenant
from workflow_core.jobs import enqueue_job,claim_job,renew_job,JobLeaseLost
from workflow_core.lease_guard import LeaseGuard


def test_pg_takeover_fences_old_transaction_and_renewal(fresh_db):
    import app_runtime
    engine=make_engine(fresh_db[0]);run_migrations(engine,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
    sf=make_session_factory(engine)
    try:
        with sf() as db:
            db.add(Tenant(id='stability-pg',code='stability-pg',name='synthetic'));db.commit()
            j=enqueue_job(db,'stability-pg','drug_trace.collect','collect',{});db.commit()
            old=claim_job(db,'old',['drug_trace.collect']);assert renew_job(db,old)
            j.lease_until=utcnow()-timedelta(seconds=1);db.commit()
            with sf() as winner:
                new=claim_job(winner,'winner',['drug_trace.collect'])
                assert new.epoch>old.epoch and not renew_job(db,old)
                j.payload={'uncommitted':'must rollback'}
                with pytest.raises(JobLeaseLost):LeaseGuard(sf,old).fence(db)
                db.rollback()
                winner.refresh(new.job);assert new.job.payload=={} and new.job.state=='LEASED'
                assert renew_job(winner,new)
    finally:engine.dispose()
