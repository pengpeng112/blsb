"""任务内核：Job 租约队列 + Outbox + fencing（04 §3、05 §3）。

R08/R39 整改：
- 执行去重键 = 业务 dedup_key + payload 规范化摘要（事件代次）。同一事件重复
  摄入只产生一次执行；真正的新变更（payload不同）必须产生可执行的新任务。
- claim/finish 全部走数据库条件 UPDATE 并以 affected rows 判定，不依赖
  Session 缓存中的对象属性；租约被抢占后旧持有者 finish 被拒绝。
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.orm import Session

from contracts import JobState, new_uuid
from contracts.api import digest_of
from core_db.engine import utcnow
from core_db.models import Job, OutboxEvent, StepRun


def defer_job(db: Session, lease, retry_after: float) -> bool:
    """A pre-request cooldown releases the same fenced job, not a new attempt.

    One day without making progress is an explicit failure for human review.
    Query-generation rows and successful historical attempts are untouched.
    """
    now = utcnow()
    if lease.job.created_at and now - lease.job.created_at > timedelta(days=1):
        return finish_job(db, lease, success=False,
                          error={'code': 'RATE_LIMIT_WAIT_EXPIRED', 'message': 'shared cooldown exceeded one day'})
    result = db.execute(update(Job).where(
        Job.id == lease.job.id, Job.lease_epoch == lease.epoch,
        Job.state == JobState.LEASED).values(
            state=JobState.RETRY_WAIT, lease_owner='', lease_until=None,
            available_at=now + timedelta(seconds=max(1, retry_after)),
            attempts=Job.attempts - 1,
            last_error={'code': 'PLATFORM_COOLDOWN', 'message': 'waiting for shared platform request budget'}))
    db.commit()
    return result.rowcount == 1


def execution_key(dedup_key: str, payload: dict) -> str:
    """执行键：业务键 + 事件代次摘要（同payload幂等，新payload新任务）。"""
    return f"{dedup_key}#{digest_of(payload or {})[:16]}"


def enqueue_job(db: Session, tenant_id: str, job_type: str, dedup_key: str,
                payload: dict, priority: int = 100) -> Job:
    """同 (tenant, job_type, 执行键) 幂等：完成态复用、等待态复用、失败态重排。
    payload 变化 = 新事件代次 = 新执行键 = 新任务行（R08）。"""
    exec_key = execution_key(dedup_key, payload)
    existing = (db.query(Job)
                .filter_by(tenant_id=tenant_id, job_type=job_type, dedup_key=exec_key)
                .first())
    if existing is not None:
        if existing.state in (JobState.SUCCEEDED, JobState.PENDING,
                              JobState.LEASED, JobState.RETRY_WAIT):
            return existing
        # FAILED/CANCELLED 允许重新入队
        existing.state = JobState.PENDING
        existing.attempts = 0
        existing.available_at = utcnow()
        existing.last_error = {}
        return existing
    job = Job(id=new_uuid(), tenant_id=tenant_id, job_type=job_type, dedup_key=exec_key,
              payload=payload, priority=priority)
    db.add(job)
    db.flush()
    return job


@dataclass
class Lease:
    job: Job
    epoch: int


class JobLeaseLost(RuntimeError):
    """The current handler may no longer commit a page or complete this job."""


class JobYield(RuntimeError):
    """A durable partial job yields its slot; this says nothing about HTTP I/O."""


def renew_job(db: Session, lease: Lease, *, lease_seconds: int = 120) -> bool:
    now = utcnow()
    result = db.execute(update(Job).where(
        Job.id == lease.job.id, Job.lease_epoch == lease.epoch,
        Job.state == JobState.LEASED, Job.lease_until > now).values(
            lease_until=now + timedelta(seconds=lease_seconds)))
    db.commit()
    return result.rowcount == 1


def yield_job(db: Session, lease: Lease) -> bool:
    """Reuse the same fenced partial job; never consume failure attempts."""
    result = db.execute(update(Job).where(
        Job.id == lease.job.id, Job.lease_epoch == lease.epoch,
        Job.state == JobState.LEASED, Job.lease_until > utcnow()).values(
            state=JobState.RETRY_WAIT, lease_owner='', lease_until=None,
            available_at=utcnow() + timedelta(seconds=1),
            attempts=Job.attempts-1,
            last_error={'code':'JOB_YIELDED','message':'durable page completed; waiting for next slot'}))
    db.commit()
    return result.rowcount == 1


def claim_job(db: Session, owner: str, job_types: list[str], *,
              lease_seconds: int = 120) -> Lease | None:
    """条件UPDATE领取：优先级+时间排序；过期租约可被抢占（epoch递增做fencing）。
    UPDATE 的 WHERE 携带状态/时间前置条件，两个并发领取者只有一个 affected=1。"""
    now = utcnow()
    q = (db.query(Job)
         .filter(Job.job_type.in_(job_types),
                 Job.available_at <= now,
                 Job.state.in_([JobState.PENDING, JobState.RETRY_WAIT]))
         .order_by(Job.priority, Job.available_at))
    for job in q.all():
        res = db.execute(
            update(Job)
            .where(Job.id == job.id,
                   Job.state.in_([JobState.PENDING, JobState.RETRY_WAIT]))
            .values(state=JobState.LEASED, lease_owner=owner,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    lease_epoch=Job.lease_epoch + 1, attempts=Job.attempts + 1))
        db.commit()
        if res.rowcount == 1:
            db.refresh(job)
            return Lease(job=job, epoch=job.lease_epoch)
    # 抢占过期租约（条件：仍是租约态且已过期）
    q2 = (db.query(Job)
          .filter(Job.job_type.in_(job_types), Job.state == JobState.LEASED,
                  Job.lease_until < now)
          .order_by(Job.priority, Job.available_at))
    for job in q2.all():
        res = db.execute(
            update(Job)
            .where(Job.id == job.id,
                   Job.state == JobState.LEASED,
                   Job.lease_until < now)
            .values(lease_owner=owner,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    lease_epoch=Job.lease_epoch + 1, attempts=Job.attempts + 1))
        db.commit()
        if res.rowcount == 1:
            db.refresh(job)
            return Lease(job=job, epoch=job.lease_epoch)
    return None


def finish_job(db: Session, lease: Lease, *, success: bool, error: dict | None = None,
               retry_after_seconds: int | None = None) -> bool:
    """带 fencing 的完成：DB 条件 UPDATE（id+lease_epoch）的 affected rows
    判定。旧 Session 缓存过期值不影响判定（R39）。"""
    now = utcnow()
    cond = (Job.id == lease.job.id, Job.lease_epoch == lease.epoch)
    if success:
        res = db.execute(update(Job).where(*cond).values(
            state=JobState.SUCCEEDED, finished_at=now, last_error={}))
        db.commit()
        return res.rowcount == 1
    if retry_after_seconds is not None:
        res = db.execute(
            update(Job).where(*cond, Job.attempts < Job.max_attempts).values(
                state=JobState.RETRY_WAIT,
                available_at=now + timedelta(seconds=retry_after_seconds),
                last_error=error or {}))
        db.commit()
        if res.rowcount == 1:
            return True
    res = db.execute(update(Job).where(*cond).values(
        state=JobState.FAILED, finished_at=now, last_error=error or {}))
    db.commit()
    return res.rowcount == 1


def backoff_seconds(attempt: int, *, base: float = 1.0, cap: float = 60.0) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)) * (0.5 + random.random() / 2))


# ---------------------------------------------------------------- Outbox
def emit(db: Session, tenant_id: str, aggregate_ref: str, event_type: str,
         payload: dict) -> OutboxEvent:
    ev = OutboxEvent(id=new_uuid(), tenant_id=tenant_id, aggregate_ref=aggregate_ref,
                     event_type=event_type, payload=payload)
    db.add(ev)
    db.flush()
    return ev


def drain_outbox(db: Session, handler, limit: int = 100) -> int:
    """至少一次投递；handler 抛错则该事件保持未发布。"""
    events = (db.query(OutboxEvent)
              .filter(OutboxEvent.published_at.is_(None))
              .order_by(OutboxEvent.created_at).limit(limit).all())
    n = 0
    for ev in events:
        try:
            handler(db, ev)
            ev.published_at = utcnow()
            db.commit()
            n += 1
        except Exception:  # noqa: BLE001
            db.rollback()
    return n


# ---------------------------------------------------------------- StepRun 租约
def claim_step(db: Session, owner: str, *, lease_seconds: int = 300):
    now = utcnow()
    steps = (db.query(StepRun)
             .filter(StepRun.state == "READY", StepRun.lease_until.is_(None))
             .order_by(StepRun.updated_at).all())
    takeover = (db.query(StepRun)
                .filter(StepRun.state == "RUNNING", StepRun.lease_until < now).all())
    for step in steps + takeover:
        res = db.execute(
            update(StepRun)
            .where(StepRun.id == step.id,
                   ((StepRun.state == "READY") & (StepRun.lease_until.is_(None))) |
                   ((StepRun.state == "RUNNING") & (StepRun.lease_until < now)))
            .values(state="RUNNING", lease_owner=owner,
                    lease_until=now + timedelta(seconds=lease_seconds),
                    lease_epoch=StepRun.lease_epoch + 1,
                    attempts=StepRun.attempts + 1))
        db.commit()
        if res.rowcount == 1:
            db.refresh(step)
            return step, step.lease_epoch
    return None, 0


def finish_step(db: Session, step_id: str, lease_epoch: int, *, state: str,
                outputs: dict | None = None, wait_reason: str | None = None,
                error: dict | None = None) -> bool:
    """条件 UPDATE（id+epoch+RUNNING）判定，拒绝过期租约的完成提交。"""
    res = db.execute(
        update(StepRun)
        .where(StepRun.id == step_id, StepRun.lease_epoch == lease_epoch,
               StepRun.state == "RUNNING")
        .values(state=state,
                output_refs=outputs if outputs is not None else StepRun.output_refs,
                wait_reason=wait_reason, error=error or {}, lease_until=None,
                updated_at=utcnow()))
    db.commit()
    return res.rowcount == 1
