"""Collection lease renewal and page-transaction fencing, without shared sessions."""
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
import json
import os
import threading
import time

from sqlalchemy import update
from core_db import utcnow
from core_db.models import Job
from contracts import JobState
from .jobs import JobLeaseLost, renew_job

_CURRENT=ContextVar('blyb_job_guard',default=None)


def current_guard():return _CURRENT.get()


class LeaseGuard:
    def __init__(self,session_factory,lease,*,interval=20,stale_after=300,heartbeat=None):
        self.sf=session_factory;self.lease=lease;self.interval=interval;self.stale_after=stale_after
        self.heartbeat=heartbeat or os.environ.get('BLYB_WORKER_HEARTBEAT','')
        self.last_progress=time.monotonic();self.lost=threading.Event();self.stop=threading.Event()
        self.thread=None;self.token=None

    def check(self):
        if self.lost.is_set():raise JobLeaseLost('collection lease unavailable or progress stale')
        self.last_progress=time.monotonic()

    def fence(self,db):
        """Lock the same epoch inside the page transaction before committing data."""
        self.check();now=utcnow()
        changed=db.execute(update(Job).where(
            Job.id==self.lease.job.id,Job.lease_epoch==self.lease.epoch,
            Job.state==JobState.LEASED,Job.lease_until>now).values(
                lease_until=now+timedelta(seconds=120)))
        if changed.rowcount!=1:
            self.lost.set();raise JobLeaseLost('collection page lost lease fence')

    def pulse(self):
        if time.monotonic()-self.last_progress>self.stale_after:
            self.lost.set();return False
        try:
            with self.sf() as db:
                if not renew_job(db,self.lease):self.lost.set();return False
        except Exception:
            self.lost.set();return False  # Fail closed; never pretend renewal succeeded.
        if self.heartbeat:
            p=Path(self.heartbeat);tmp=p.with_name(p.name+'.lease.'+str(id(self))+'.tmp')
            try:
                tmp.write_text(json.dumps({'ts':time.time(),'pid':os.getpid(),'phase':'collection','last_progress_age':time.monotonic()-self.last_progress}))
                os.replace(tmp,p)
            except OSError:pass  # Existing health check will report stale; no fake success.
        return True

    def _keepalive(self):
        while not self.stop.wait(self.interval):
            if not self.pulse():return

    def __enter__(self):
        self.token=_CURRENT.set(self)
        self.thread=threading.Thread(target=self._keepalive,name='blyb-collection-lease',daemon=True)
        self.thread.start();return self

    def __exit__(self,*exc):
        self.stop.set();self.thread.join(timeout=5)
        _CURRENT.reset(self.token)
        if exc[0] is None and self.lost.is_set():
            raise JobLeaseLost('collection guard lost lease before completion')
