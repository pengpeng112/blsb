"""Actual isolated PostgreSQL fencing for pre-request cooldown deferral."""
from datetime import timedelta
from test_drug_trace_pg_fix3 import pg, fresh_db
from core_db import make_engine,make_session_factory,run_migrations,utcnow
from core_db.models import Job,Tenant
from workflow_core.jobs import enqueue_job,claim_job,defer_job


def test_postgres_cooldown_deferral_fences_old_owner_and_keeps_query_job(fresh_db):
    import app_runtime
    engine=make_engine(fresh_db[0]);run_migrations(engine,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
    sf=make_session_factory(engine)
    try:
        with sf() as db:
            db.add(Tenant(id='flow-tenant',code='flow',name='synthetic'));db.commit()
            job=enqueue_job(db,'flow-tenant','drug_trace.query_status','q:synthetic:1',{'submission_id':'synthetic'});db.commit()
            old=claim_job(db,'old',['drug_trace.query_status'])
            job.lease_until=utcnow()-timedelta(seconds=1);db.commit()
            with sf() as fresh:
                current=claim_job(fresh,'new',['drug_trace.query_status'])
                assert current.epoch>old.epoch
                assert not defer_job(db,old,601)
                assert defer_job(fresh,current,601)
                fresh.refresh(current.job)
                assert current.job.state=='RETRY_WAIT' and current.job.attempts==1
                assert (current.job.available_at-utcnow()).total_seconds()>590
                assert fresh.query(Job).count()==1
    finally:engine.dispose()
