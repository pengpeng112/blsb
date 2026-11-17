"""Worker 引擎：outbox路由、Job执行、DAG步骤推进、连接器轮询、等待重评。

单线程确定性 tick()：测试可直接调用；生产为循环进程。
"""
from __future__ import annotations

import logging
import os

from contracts import new_uuid
from connector_sdk import RequestDeferred
from core_db.engine import make_engine, make_session_factory, utcnow
from core_db.models import (Acquisition, Asset, ConfigRelease, ConfigRevision, Job,
                            SourceCheckpoint, StepRun, Submission, WorkflowRevision,
                            WorkflowRun)
from workflow_core import dag as dag_mod
from workflow_core import jobs as jobs_mod

log = logging.getLogger("worker")

TRANSFERABLE_KEYS = ("case_id", "package_revision_id", "payload", "validated",
                     "submission_id", "rows", "acquisition_id", "import_ref")


class WorkerEngine:
    def __init__(self, session_factory, *, registry=None):
        self.sf = session_factory
        self.registry = registry or _default_registry()
        self._last_sched_tick = 0.0        # LT-04：调度 tick 进程内节流（非锁）

    # ------------------------------------------------------------ tick
    def tick(self, *, max_jobs: int = 20, max_steps: int = 20,
             retry_wait_seconds: float | None = None) -> dict:
        import time as _time
        sched = None
        # LT-04：调度 tick 进程内节流（距上次 <10s 不调）；多 worker 安全由
        # scheduler.tick 的数据库条件 UPDATE 抢占保证（节流不是锁）。
        if _time.monotonic() - self._last_sched_tick >= 10:
            self._last_sched_tick = _time.monotonic()
            from scheduling.scheduler import tick as sched_tick
            from datetime import timezone as _tz
            from core_db.engine import utcnow as _utcnow
            with self.sf() as sdb:
                try:
                    sched = sched_tick(sdb, _utcnow().replace(tzinfo=_tz.utc),
                                       registry=self.registry)
                except Exception as exc:  # noqa: BLE001 —— 调度故障不阻断worker
                    log.warning("scheduler.tick failed: %s", exc)
        with self.sf() as db:
            outboxed = jobs_mod.drain_outbox(db, self._route_event)
            jobs_done = self._run_due_jobs(db, max_jobs)
            self._rearm_waiting(db, retry_wait_seconds)
            steps_done = self._advance_workflows(db, max_steps)
            polls = self._connector_poll(db)
            return {"outbox": outboxed, "jobs": jobs_done, "steps": steps_done,
                    "polls": polls, "scheduled": sched}

    # ------------------------------------------------------------ outbox 路由
    def _route_event(self, db, ev) -> None:
        # 模拟事件隔离：dry-run 产生的事件只允许 dry-run 消费者处理（R34）。
        # 普通路由直接忽略，防止模拟链路间接触发真实发布/发送。
        if (ev.event_type or "").startswith("dry."):
            return
        tenant = ev.tenant_id
        p = ev.payload or {}
        if ev.event_type == "asset.available":
            asset_id = p.get("asset_id")
            asset = db.get(Asset, asset_id) if asset_id else None  # RT-02 空主键守卫
            if asset is None:
                return
            acq = db.get(Acquisition, asset.acquisition_id)
            if acq is not None and acq.tenant_id == tenant and \
                    acq.state == "FILES_COMPLETE":
                self.registry.enqueue(db, tenant, "workflow.launch",
                                      f"push:{acq.id}", {"acquisition_id": acq.id})
        elif ev.event_type == "pathology.case.updated":
            self.registry.enqueue(db, tenant, "pathology.freeze_attempt",
                                  f"case:{p.get('case_id')}", {"case_id": p.get("case_id")})
        elif ev.event_type == "package.frozen" and p.get("domain") == "metrics":
            self.registry.enqueue(db, tenant, "metrics.run", p["package_id"],
                                  {"package_id": p["package_id"],
                                   "rows": p.get("rows")})
        elif ev.event_type == "package.frozen" and p.get("domain") == "pathology":
            # 冻结包 → 视图发布工作流（与PUSH互不覆盖，T10）
            self.registry.enqueue(db, tenant, "workflow.launch",
                                  f"publish:{p['package_revision_id']}",
                                  {"case_id": p.get("case_id"),
                                   "package_revision_id": p["package_revision_id"]})
        elif ev.event_type == "submission.confirmed":
            # 交换网关收到的CONFIRMED回执 → 统一业务落地（submission+病例分列）
            sub_id = p.get("submission_id")
            if sub_id:
                self.registry.apply_business_confirmation(db, tenant, sub_id)
                db.commit()
        elif ev.event_type == "pis.source_changed":
            barcode = p.get("barcode")
            if barcode:
                self.registry.enqueue(db, tenant, "pathology.resolve_waiting",
                                      f"barcode:{barcode}", {"barcode": barcode})
        elif ev.event_type == "pathology.case.refresh":
            # 分区盘点发现内容修订 → 定位病例并触发重新冻结（新包候选）
            from domains.pathology.models import PathologyCase
            case = (db.query(PathologyCase)
                    .filter_by(tenant_id=tenant,
                               pathology_no_raw=p.get("pathology_no", ""))
                    .order_by(PathologyCase.first_seen_at)
                    .first())
            if case is not None:
                self.registry.enqueue(db, tenant, "pathology.freeze_attempt",
                                      f"case:{case.id}", {"case_id": case.id})
        else:
            # 领域插件事件路由（DTR-BASE-02：drug_trace 等注册表路由合并；
            # 未匹配保持不发布，不猜测）
            for router in self.registry.event_routers():
                if router(db, ev):
                    return

    # ------------------------------------------------------------ jobs
    def _run_due_jobs(self, db, max_jobs: int) -> int:
        handlers = {
            "pathology.resolve": self._j_pathology_resolve,
            "pathology.freeze_attempt": self._j_pathology_freeze,
            "pathology.resolve_waiting": self._j_pathology_resolve_waiting,
            "destination.poll_receipts": self._j_poll_receipts,
            "destination.query_unknown": self._j_query_unknown,
            "destination.push_manual": self._j_push_manual,
            "metrics.run": self._j_metrics_run,
            "workflow.launch": self._j_workflow_launch,
            "pis.poll_created": self._j_pis_poll,
            "pis.partition_reconcile": self._j_pis_partition,
            "storage.verify_session": self._j_storage_verify_session,
            "storage.verify_asset": self._j_storage_verify_asset,
            "storage.migrate_asset": self._j_storage_migrate_asset,
            # LT-04 系统任务（会写库/写文件并占 worker；调度默认停用）
            "system.alert_evaluate": self._j_alert_evaluate,
            "system.alert_digest": self._j_alert_digest,
            "system.run_guard": self._j_run_guard,
            # 领域插件 job（drug_trace 等；DTR-BASE-02：注册表合并，不各自硬编码）
            **self.registry.domain_job_handlers(),
        }
        n = 0
        while n < max_jobs:
            # At least one in five slots remains available to preparation and
            # other domains while drug-trace send/query priorities drain.
            # All claims still use the existing lease/fencing implementation.
            background = [key for key in handlers if key not in (
                "drug_trace.send", "drug_trace.query_status")]
            lease = None
            if n % 5 == 4 and background:
                lease = jobs_mod.claim_job(db, owner="worker", job_types=background)
            if lease is None:
                lease = jobs_mod.claim_job(db, owner="worker", job_types=list(handlers))
            if lease is None:
                break
            handler = handlers.get(lease.job.job_type)
            try:
                if handler is None:
                    raise RuntimeError(f"no handler for {lease.job.job_type}")
                # RT-03 S02：成功 claim 后、handler 前提交首次运行更新——
                # handler 执行期间另一 DB Session 能看到 RUNNING。claim_job
                # 自己 commit，此更新是领取后的独立事务（claim commit 与本
                # commit 之间存在崩溃窗口：Job 已 LEASED 而 run 未 RUNNING，
                # 由租约过期抢占+mark_run_started 幂等恢复）。
                self._mark_schedule_run_started(db, lease.job)
                if lease.job.job_type == 'drug_trace.collect':
                    from workflow_core.lease_guard import LeaseGuard
                    with LeaseGuard(self.sf, lease):
                        handler(db, lease.job)
                else:
                    handler(db, lease.job)
                db.commit()
                # RT-03 S02：finish_job True=本 lease epoch 更新成功；只有刷新
                # 后持久化的 SUCCEEDED/FAILED 终态才回写 Operation 与
                # ScheduleRun（RETRY_WAIT 不是终态，不提前终结业务侧）。
                if jobs_mod.finish_job(db, lease, success=True):
                    db.refresh(lease.job)
                    if lease.job.state == 'SUCCEEDED':
                        self._finalize_operation(db, lease.job, "SUCCEEDED")
            except jobs_mod.JobYield:
                db.rollback()
                jobs_mod.yield_job(db, lease)
            except jobs_mod.JobLeaseLost:
                db.rollback()  # The new owner alone may update completion/operation.
            except RequestDeferred as exc:
                db.rollback()
                if jobs_mod.defer_job(db, lease, exc.retry_after):
                    db.refresh(lease.job)
                    if lease.job.state == 'FAILED':
                        self._finalize_operation(db, lease.job, 'FAILED', lease.job.last_error)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                ok = jobs_mod.finish_job(db, lease, success=False,
                                         error={"message": str(exc)[:500]},
                                         retry_after_seconds=getattr(exc, "retry_after", None))
                if ok:
                    # RT-03 S02：True 含 RETRY_WAIT（重试等待也更新成功）——
                    # 刷新后仅 FAILED（重试耗尽/不可重试）才落终态回写。
                    db.refresh(lease.job)
                    if lease.job.state == 'FAILED':
                        self._finalize_operation(
                            db, lease.job, "FAILED", {"message": str(exc)[:500]})
            n += 1
        return n

    def _mark_schedule_run_started(self, db, job) -> None:
        """RT-03 S02 装配点：调度 Job 首次开跑回写（非调度 Job 原样返回）。

        独立测试须证明本钩子生效：handler 执行期间用另一 Session 可观察到
        run.state=RUNNING（见 tests/integration/test_rt03_worker_assembly.py）。
        """
        if not (job.payload or {}).get("schedule_run_id"):
            return
        try:
            from scheduling.scheduler import mark_run_started
            mark_run_started(db, job)
            db.commit()
        except Exception as exc:  # noqa: BLE001 —— 回写失败不阻断 job 执行
            db.rollback()
            log.warning("mark_run_started failed for job %s: %s",
                        job.id, exc)

    def _finalize_operation(self, db, job: Job, state: str, error: dict | None = None):
        """Operation 异步结果落终态：job 成功/失败都同步到关联 Operation（R28）。"""
        from core_db.models import Operation
        # LT-04：调度 Job 终态回写 schedule_run（迟到成功保留 late 痕迹）。
        # 无 Operation 的 job 原 _finalize_operation 不落 commit——此处显式提交。
        if (job.payload or {}).get("schedule_run_id"):
            try:
                from scheduling.scheduler import finalize_run
                finalize_run(db, job, state, error)
                db.commit()
            except Exception as exc:  # noqa: BLE001 —— 回写失败不掩盖job终态
                db.rollback()
                log.warning("schedule run finalize failed: %s", exc)
        op_id = (job.payload or {}).get("operation_id")
        if not op_id:
            return
        if (job.payload or {}).get("multi_job_operation"):
            # T5 存储迁移：单一 Operation 对应多资产 Job，终态由各 Job 共同
            # 结算（handler 内计数，最后一个 Job 置 SUCCEEDED；任一失败置 FAILED），
            # 不做单 Job 提前终结。
            return
        op = db.get(Operation, op_id)
        if op is None or op.state in ("SUCCEEDED", "FAILED"):
            return
        op.state = state
        if state == "SUCCEEDED":
            if not op.result:
                op.result = {"job_id": job.id, "job_type": job.job_type}
            op.error = {}
        else:
            op.error = error or {"message": "job failed"}
        db.commit()

    # ------------------------------------------------------------ LT-04 系统任务
    def _j_alert_evaluate(self, db, job: Job):
        """告警评估（写库+可能写文件；由默认停用的 system.alert_evaluate 驱动）。"""
        from alerting.evaluator import evaluate_all
        stats = evaluate_all(db, job.tenant_id, registry=self.registry)
        db.commit()
        job.payload = {**(job.payload or {}), "stats": stats}

    def _j_alert_digest(self, db, job: Job):
        from alerting.digest import run_digest
        out = run_digest(db, job.tenant_id, registry=self.registry)
        db.commit()
        job.payload = {**(job.payload or {}), "digest": out}

    def _j_run_guard(self, db, job: Job):
        from scheduling.run_guard import scan
        out = scan(db, job.tenant_id)
        db.commit()
        job.payload = {**(job.payload or {}), "guard": out}

    def _j_push_manual(self, db, job: Job):
        """T7 手动提交的投递执行：与工作流 send 节点共用同一套投递函数
        （core_handlers.ensure_push_submission/perform_push），由 worker 统一执行。"""
        from contracts import SubmissionState
        from core_db.models import PackageRevision, Submission
        from core_handlers import build_delivery_payload, perform_push
        sub = db.get(Submission, job.payload["submission_id"])
        if sub is None:
            return
        if sub.state in (SubmissionState.CONFIRMED, SubmissionState.ACCEPTED):
            return
        pr = db.get(PackageRevision, sub.package_revision_id)
        if pr is None:
            raise RuntimeError("package revision missing for manual push")
        # 真实目的地守卫在API入口（_guard_production）已拦截；此处与工作流
        # send 节点同一投递语义（Mock客户端；真实目的地Profile受B09约束）。
        payload = build_delivery_payload(db, pr)
        perform_push(db, sub.tenant_id, sub, payload)
        # LG-11：投递落 UNKNOWN（如超时）时自动排一次远端查证，与
        # POST /submissions/{id}/retry 同一 Job 类型与 dedup（提交ID），
        # 重复执行零新副作用；查证后仍未知则保留 UNKNOWN 待人工/再查。
        if sub.state == SubmissionState.UNKNOWN:
            jobs_mod.enqueue_job(db, sub.tenant_id, "destination.query_unknown",
                                 sub.id, {"submission_id": sub.id})

    def _j_storage_migrate_asset(self, db, job: Job):
        """T5 单资产迁移执行：拷贝→hash校验→replica位置→CAS释放预留。
        Operation 终态由本 handler 结算（multi_job_operation）；
        可重试异常（retry_after）保持 Operation 运行态等待重试。"""
        from core_db.models import Operation
        from apps.storage_service import service as storage_service
        op = db.get(Operation, (job.payload or {}).get("operation_id")) \
            if (job.payload or {}).get("operation_id") else None
        try:
            result = storage_service.migrate_asset(db, job)
            if op is not None and op.state not in ("SUCCEEDED", "FAILED"):
                acc = dict(op.result or {})
                acc["migrated"] = int(acc.get("migrated", 0)) + 1
                if acc["migrated"] >= int(acc.get("total", acc["migrated"])):
                    op.state = "SUCCEEDED"
                    acc["finished_at"] = utcnow().isoformat()
                op.result = acc
                db.commit()
        except Exception as exc:  # noqa: BLE001
            if getattr(exc, "retry_after", None) is not None:
                db.rollback()
                raise  # 可重试：Job转RETRY_WAIT，Operation保持运行态待恢复
            db.rollback()
            if op is not None and op.state not in ("SUCCEEDED", "FAILED"):
                op.state = "FAILED"
                op.error = {"code": "STORAGE_FAILURE",
                            "message": f"asset migration failed for job {job.id}"}
                db.commit()
            raise

    def _j_workflow_launch(self, db, job: Job):
        if "acquisition_id" in job.payload:
            self._launch(db, job.tenant_id, "pathology.mock_push", job.payload)
        elif "case_id" in job.payload:
            self._launch(db, job.tenant_id, "pathology.mock_publish", job.payload)
        elif "package_id" in job.payload:
            self._launch(db, job.tenant_id, "metrics.mock_push", job.payload)

    def _launch(self, db, tenant_id, workflow_key, context) -> WorkflowRun | None:
        wf = (db.query(WorkflowRevision)
              .filter_by(tenant_id=tenant_id, workflow_key=workflow_key,
                         status="PUBLISHED")
              .order_by(WorkflowRevision.revision.desc()).first())
        if wf is None:
            log.warning("workflow %s not published; skip launch", workflow_key)
            return None
        # run 去重键 = workflow + 业务实体 + 包修订 + 模拟标志（R09）：
        # 同包同目标幂等；新包修订/重扫/补报告必须创建新run。
        entity_parts = [workflow_key]
        for k in ("acquisition_id", "case_id", "package_revision_id", "package_id"):
            if context.get(k):
                entity_parts.append(f"{k}={context[k]}")
        dedup_marker = "#".join(entity_parts)
        for r in db.query(WorkflowRun).filter_by(
                tenant_id=tenant_id, workflow_revision_id=wf.id).all():
            if (r.context or {}).get("_dedup") == dedup_marker and r.state in (
                    "RUNNING", "WAITING", "SUCCEEDED", "NEEDS_REVIEW"):
                return r
        ctx = dict(context)
        ctx["_dedup"] = dedup_marker
        # W10：run创建即冻结依赖闭包（workflow revision + Pack revision + 字典引用）。
        # 存在未回退的 ConfigRelease 时按 release 固定的 pack revision（发布链证据），
        # 否则取当前发布Pack；回放不读latest。
        pack_rev = None
        release = (db.query(ConfigRelease)
                   .filter_by(tenant_id=tenant_id)
                   .order_by(ConfigRelease.released_at.desc()).first())
        if release is not None and release.rolled_back_at is None:
            pack_entry = next((e for e in (release.revision_ids or [])
                               if e.get("type") == "hospital_pack"), None)
            if pack_entry:
                pinned = db.get(ConfigRevision, pack_entry.get("revision_id"))
                if pinned is not None:
                    pack_rev = pinned
        if pack_rev is None:
            pack_rev = (db.query(ConfigRevision)
                        .filter_by(tenant_id=tenant_id, config_type="hospital_pack",
                                   state="PUBLISHED")
                        .order_by(ConfigRevision.revision.desc()).first())
        ctx["_config_closure"] = {
            "workflow_revision_id": wf.id, "workflow_digest": wf.digest,
            "pack_revision_id": pack_rev.id if pack_rev else None,
            "pack_digest": pack_rev.digest if pack_rev else None,
            "config_release_id": release.id if release else None,
            "dictionaries": (pack_rev.content_json or {}).get(
                "approved_dictionaries", {}) if pack_rev else {},
        }
        run = dag_mod.create_run(db, tenant_id, wf.id, trigger="job", context=ctx)
        dag_mod.materialize_steps(db, run, wf.dsl)
        db.commit()
        return run

    def _j_pathology_resolve(self, db, job: Job):
        acq = db.get(Acquisition, job.payload["acquisition_id"])
        if acq is None:
            return
        rt = self.registry.build_runtime(db, job.tenant_id)
        result = self.registry.resolve(rt, acq)
        db.commit()
        # Operation 结果绑定实际解析输出（R28）
        from core_db.models import Operation
        op = db.get(Operation, job.payload.get("operation_id")) \
            if job.payload.get("operation_id") else None
        if op is not None and op.state not in ("SUCCEEDED", "FAILED"):
            op.result = {"job_id": job.id, "resolve": {
                k: result.get(k) for k in ("state", "wait_reason", "case_id",
                                           "slide_id")}}
        if result["state"] == "ASSOCIATED":
            self.registry.enqueue(db, job.tenant_id, "pathology.freeze_attempt",
                                  f"case:{result['case_id']}",
                                  {"case_id": result["case_id"]})

    def _j_pathology_freeze(self, db, job: Job):
        from domains.pathology.models import PathologyCase
        case = db.get(PathologyCase, job.payload["case_id"])
        if case is None:
            return
        rt = self.registry.build_runtime(db, job.tenant_id)
        pr = self.registry.freeze_case(db, job.tenant_id, case, rt)
        db.commit()
        if pr is not None:
            # 冻结包 → 双目的地：PUSH Mock + 视图发布（T10：状态互不覆盖）
            self._launch(db, job.tenant_id, "pathology.mock_publish",
                         {"case_id": case.id, "package_revision_id": pr.id})

    def _j_pathology_resolve_waiting(self, db, job: Job):
        barcode = job.payload["barcode"]
        rt = self.registry.build_runtime(db, job.tenant_id)
        for acq in db.query(Acquisition).filter_by(tenant_id=job.tenant_id).all():
            if (acq.manifest.get("identity_hints") or {}).get("barcode_raw") == barcode:
                result = self.registry.resolve(rt, acq)
                db.commit()
                if result["state"] == "ASSOCIATED":
                    self.registry.enqueue(db, job.tenant_id, "pathology.freeze_attempt",
                                          f"case:{result['case_id']}",
                                          {"case_id": result["case_id"]})

    def _j_poll_receipts(self, db, job: Job):
        sub = db.get(Submission, job.payload["submission_id"])
        if sub is None:
            return
        from contracts import SubmissionState
        receipts = self._poll_mock_receipts()
        matched = [r for r in receipts
                   if r.get("submission_key") == sub.idempotency_key]
        for r in matched:
            self.registry.store_receipt(db, sub.tenant_id, sub.destination_id, r, sub.id)
        db.commit()
        if not matched and sub.state != SubmissionState.CONFIRMED:
            # 无回执：保持待确认，但指数退避（1s→2s→…封顶60s），避免打爆真实平台。
            # 重排必须开新作业行（dedup加代次后缀）：复用原键会被 enqueue 去重
            # 提前返回（payload 不更新、poll_count 不递增），且本作业稍后会被
            # finish_job 置 SUCCEEDED，复用同一行会让轮询链就此中断。
            poll_count = int((job.payload or {}).get("poll_count", 0)) + 1
            delay = min(60, 2 ** min(poll_count - 1, 6))
            self.registry.enqueue(
                db, sub.tenant_id, "destination.poll_receipts",
                f"{sub.id}#poll{poll_count}",
                {"submission_id": sub.id, "poll_count": poll_count},
                delay_seconds=delay)

    def _j_query_unknown(self, db, job: Job):
        sub = db.get(Submission, job.payload["submission_id"])
        if sub is None:
            return
        result = self._query_mock(sub.idempotency_key)
        if result.get("found") and result.get("accepted"):
            sub.state = "ACCEPTED"
            db.commit()
        elif result.get("found") is False:
            # 远端无记录：同一幂等键重发（非新建业务提交）
            self.registry.enqueue(db, sub.tenant_id, "workflow.launch",
                                  f"push2:{sub.package_revision_id}",
                                  {"package_revision_id": sub.package_revision_id,
                                   "case_id": job.payload.get("case_id")})

    def _j_metrics_run(self, db, job: Job):
        pkg_rows = job.payload.get("rows")
        # 优先双目标工作流（G23）；旧库只有单目标时回退
        dual = (db.query(WorkflowRevision)
                .filter_by(tenant_id=job.tenant_id, workflow_key="metrics.dual_mock",
                           status="PUBLISHED").first())
        key = "metrics.dual_mock" if dual is not None else "metrics.mock_push"
        self._launch(db, job.tenant_id, key,
                     {"package_id": job.payload["package_id"], "rows": pkg_rows})

    def _j_pis_poll(self, db, job: Job):
        rt = self.registry.build_runtime(db, job.tenant_id)
        cp = (db.query(SourceCheckpoint)
              .filter_by(tenant_id=job.tenant_id,
                         connector_id=job.payload.get("connector_id", ""),
                         stream="pis.created", partition="0").first())
        cursor = dict(cp.cursor_json or {}) if cp is not None else {
            "since": "0000-01-01T00:00:00"}
        cursor["until"] = "9999-12-31T23:59:59"
        # poll_changes 原地推进 (CreateTime, PathologyID) 复合游标（R11）
        rows = rt.pis.poll_changes(rt.scope(), "cases", cursor, 50)
        for r in rows:
            jobs_mod.emit(db, job.tenant_id, f"pis:case:{r['source_key']}",
                          "pis.source_changed", {"pathology_no": r["source_key"]})
        new_cursor = {"since": cursor.get("since", "0000-01-01T00:00:00"),
                      "since_key": cursor.get("since_key", "")}
        if cp is None:
            db.add(SourceCheckpoint(
                id=new_uuid(), tenant_id=job.tenant_id,
                connector_id=job.payload.get("connector_id", ""),
                stream="pis.created", partition="0", cursor_json=new_cursor))
        else:
            cp.cursor_json = new_cursor
            cp.row_version += 1
        # 游标推进与事件在同一事务提交（R11：checkpoint与本页Inbox/Outbox原子）
        db.commit()

    def _j_pis_partition(self, db, job: Job):
        from core_db.models import (SourceRecord, SourceRecordRevision,
                                    SourceReconciliationPartition,
                                    SourceReconciliationRun)
        rt = self.registry.build_runtime(db, job.tenant_id)
        key_from = job.payload.get("key_from", "0")
        key_to = job.payload.get("key_to", "99999999")
        run = SourceReconciliationRun(
            id=new_uuid(), tenant_id=job.tenant_id,
            connector_id=job.payload.get("connector_id", ""), stream="pis.cases",
            scope={"key_from": key_from, "key_to": key_to}, state="RUNNING")
        db.add(run)
        db.flush()
        part = SourceReconciliationPartition(
            id=new_uuid(), tenant_id=job.tenant_id, run_id=run.id,
            key_range=run.scope, cursor_start=key_from, cursor_end=key_to,
            state="RUNNING", started_at=utcnow())
        db.add(part)
        db.flush()
        # R10：keyset 翻页读完整个分区；只有短页证明末页且无失败才可声明完整覆盖
        page_limit = 200
        after_key = ""
        pages_read = 0
        rows_read = 0
        last_page_proven = False
        changes: list[dict] = []
        failures: list[dict] = []
        while True:
            try:
                keys, last_page = rt.pis.reconcile_partition(
                    rt.scope(), key_from, key_to, page_limit, after_key)
            except Exception as exc:  # noqa: BLE001
                failures.append({"page_after": after_key, "reason": str(exc)[:200]})
                break
            pages_read += 1
            rows_read += len(keys)
            last_page_proven = last_page
            for k in keys:
                try:
                    case_rec = rt.pis.get_case(rt.scope(), k)
                    if case_rec is None:
                        failures.append({"key": k, "reason": "case row disappeared"})
                        continue
                    payload = case_rec.raw
                    digest = normalized_digest(payload)
                    rec = (db.query(SourceRecord)
                           .filter_by(tenant_id=job.tenant_id, namespace=rt.namespace,
                                      entity_type="pathology_case", external_id=k).first())
                    if rec is None:
                        rec = SourceRecord(id=new_uuid(), tenant_id=job.tenant_id,
                                           namespace=rt.namespace,
                                           entity_type="pathology_case", external_id=k)
                        db.add(rec)
                        db.flush()
                        db.add(SourceRecordRevision(
                            id=new_uuid(), tenant_id=job.tenant_id, record_id=rec.id,
                            source_version=f"scan:{run.id[:8]}",
                            raw_payload=payload, digest=digest,
                            observed_by="partition_scan",
                            source_change_time=utcnow()))
                        changes.append({"key": k, "kind": "new"})
                    else:
                        current = (db.query(SourceRecordRevision)
                                   .filter_by(record_id=rec.id)
                                   .order_by(SourceRecordRevision.observed_at.desc())
                                   .first())
                        if current is None or current.digest != digest:
                            db.add(SourceRecordRevision(
                                id=new_uuid(), tenant_id=job.tenant_id, record_id=rec.id,
                                source_version=f"scan:{run.id[:8]}",
                                raw_payload=payload, digest=digest,
                                observed_by="partition_scan", source_change_time=utcnow(),
                                supersedes=current.id if current else None))
                            changes.append({"key": k, "kind": "content_changed"})
                except Exception as exc:  # noqa: BLE001
                    failures.append({"key": k, "reason": str(exc)[:200]})
            if not keys or last_page:
                break
            after_key = keys[-1]
        part.pages_read = pages_read
        part.rows_read = rows_read
        part.content_changes = changes
        part.failed_pages = failures
        part.state = "FAILED" if failures else "COMPLETE"
        part.finished_at = utcnow()
        # 完整覆盖 = 无失败 且 翻页到短页证明末页（单页上限截断不得声明完整）
        run.complete_coverage = (not failures) and last_page_proven
        run.summary = {"rows": rows_read, "pages": pages_read,
                       "last_page_proven": last_page_proven,
                       "changes": len(changes), "failures": len(failures)}
        run.state = "COMPLETE" if not failures else "PARTIAL"
        run.finished_at = utcnow()
        for c in changes:
            jobs_mod.emit(db, job.tenant_id, f"pathology_case:{c['key']}",
                          "pathology.case.refresh",
                          {"pathology_no": c["key"], "kind": c["kind"]})
        db.commit()

    def _j_storage_verify_session(self, db, job: Job):
        from core_db.models import UploadSession
        from apps.storage_service import service as storage_service
        session = db.get(UploadSession, job.payload["session_id"])
        result = storage_service.verify_session(db, session)
        from core_db.models import Operation
        op = db.get(Operation, job.payload.get("operation_id"))
        if op is not None:
            op.state = "SUCCEEDED"
            op.result = result
        db.commit()

    def _j_storage_verify_asset(self, db, job: Job):
        from apps.storage_service.service import verify_asset_job
        verify_asset_job(db, job)

    # ------------------------------------------------------------ 工作流推进
    def _rearm_waiting(self, db, retry_wait_seconds: float | None):
        """WAITING_EXTERNAL 步骤到达重试时限后重新入队（不占线程睡眠）。

        DTR-BATCH-FIX Fix4（2026-11-08）：仅当确有步骤翻转才 commit——原先
        每 tick（5Hz）无条件提交，空转 commit 税在生产上与采集争抢 I/O。"""
        import time
        threshold = retry_wait_seconds
        now = time.time()
        steps = db.query(StepRun).filter_by(state="WAITING_EXTERNAL").all()
        flipped = 0
        for s in steps:
            age = now - s.updated_at.timestamp()
            if threshold is None:
                threshold = 1.0
            if age >= threshold:
                s.state = "READY"
                s.updated_at = utcnow()
                flipped += 1
        if flipped:
            db.commit()
        return flipped

    def _advance_workflows(self, db, max_steps: int) -> int:
        n = 0
        runs = (db.query(WorkflowRun)
                .filter(WorkflowRun.state.in_(["RUNNING", "WAITING"]))
                .order_by(WorkflowRun.created_at).all())
        for run in runs:
            wf = db.get(WorkflowRevision, run.workflow_revision_id)
            if wf is None:
                run.state = "FAILED"
                db.commit()
                continue
            guard = 0
            while n < max_steps and guard < 50:
                guard += 1
                dag_mod.runnable_steps(db, run, wf.dsl)
                claimed, epoch = jobs_mod.claim_step(db, owner="worker")
                if claimed is None:
                    break
                if claimed.run_id != run.id:
                    # 其他run的就绪步骤：切换到该run的上下文执行（单线程安全）
                    run = db.get(WorkflowRun, claimed.run_id)
                    wf = db.get(WorkflowRevision, run.workflow_revision_id)
                self._propagate_inputs(db, run, wf, claimed)
                try:
                    self._execute_step(db, run, wf, claimed, epoch)
                except Exception as exc:  # noqa: BLE001
                    db.rollback()
                    log.exception("step %s failed", claimed.node_id)
                    jobs_mod.finish_step(db, claimed.id, epoch, state="FAILED",
                                         error={"message": str(exc)[:500]})
                n += 1
            dag_mod.run_finished(db, run, wf.dsl)
            db.commit()
        return n

    def _propagate_inputs(self, db, run, wf, step):
        inputs = dict(run.context or {})
        inputs.pop("_dedup", None)
        preds = [e["from"] for e in wf.dsl.get("edges", [])
                 if e.get("to") == step.node_id and e.get("on", "success") == "success"]
        all_steps = {s.node_id: s for s in db.query(StepRun).filter_by(
            run_id=run.id).all()}
        for p in preds:
            ps = all_steps.get(p)
            if ps and ps.state == "SUCCEEDED" and ps.output_refs:
                inputs.update({k: v for k, v in ps.output_refs.items()
                               if k in TRANSFERABLE_KEYS})
        step.input_refs = inputs
        db.flush()

    def _execute_step(self, db, run, wf, step, epoch):
        dsl_nodes = {n["id"]: n for n in wf.dsl.get("nodes", [])}
        node = dsl_nodes.get(step.node_id, {})
        handler = self.registry.handler_for(node.get("type", ""),
                                            (node.get("plugin_ref") or {}).get("key", ""))
        if handler is None:
            jobs_mod.finish_step(db, step.id, epoch, state="FAILED", error={
                "code": "CAPABILITY_UNSUPPORTED",
                "message": f"no handler for node {step.node_id}"})
            return
        ctx = self.registry.make_ctx(db, run, step,
                                     node.get("parameters") or {},
                                     dry_run=run.dry_run)
        outcome = handler(ctx)
        db.commit()
        jobs_mod.finish_step(db, step.id, epoch, state=outcome.state,
                             outputs=outcome.outputs, wait_reason=outcome.wait_reason,
                             error=outcome.error)

    # ------------------------------------------------------------ 连接器轮询
    def _connector_poll(self, db) -> int:
        if os.environ.get("BLYB_PIS_POLL_ENABLED", "0") != "1":
            return 0
        n = 0
        for cp in db.query(SourceCheckpoint).filter_by(stream="pis.created").all():
            job = self.registry.enqueue(db, cp.tenant_id, "pis.poll_created",
                                        f"conn:{cp.connector_id}",
                                        {"connector_id": cp.connector_id},
                                        delay_seconds=30)
            job.attempts = 0
            n += 1
        return n

    # ------------------------------------------------------------ mock helpers
    def _poll_mock_receipts(self):
        from destinations.mock_http.client import MockHttpClient
        return MockHttpClient(base_url=os.environ.get(
            "BLYB_MOCK_PLATFORM_URL", "http://127.0.0.1:8090")).poll_receipts()

    def _query_mock(self, key: str):
        from destinations.mock_http.client import MockHttpClient
        return MockHttpClient(base_url=os.environ.get(
            "BLYB_MOCK_PLATFORM_URL", "http://127.0.0.1:8090")).query(key)


_DIGEST_EXCLUDED_KEYS = {"observed_at", "fetched_at", "__collected_at"}


def normalized_digest(payload: dict) -> str:
    """带版本的规范化序列化 hash（10 §6.4）：排序键、区分null/空串、排除采集时间。"""
    import hashlib
    import json

    def norm(v):
        if isinstance(v, dict):
            return {k: norm(val) for k, val in sorted(v.items())
                    if k not in _DIGEST_EXCLUDED_KEYS}
        if isinstance(v, list):
            return [norm(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(norm(payload), ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()


class RegistryAdapter:
    """把 plugin_registry 的函数适配为 engine 调用面。

    RT-02 起默认装配不再使用本类（_default_registry 统一返回
    PluginRegistryAdapter 持真实 PluginRegistry 实例）；保留仅为兼容
    可能的直接引用，不删不改行为。"""

    def __init__(self, mod):
        self.mod = mod

    def build_runtime(self, db, tenant_id):
        return self.mod.build_runtime(db, tenant_id)

    def resolve(self, rt, acq):
        from domains.pathology import resolver
        return resolver.resolve_acquisition(rt, acq)

    def freeze_case(self, db, tenant_id, case, rt):
        from domains.pathology import pipeline
        return pipeline.freeze_case(db, tenant_id, case, rt)

    def enqueue(self, db, tenant_id, job_type, dedup_key, payload, *,
                delay_seconds: int = 0):
        return self.mod.enqueue(db, tenant_id, job_type, dedup_key, payload,
                                delay_seconds=delay_seconds)

    def store_receipt(self, db, tenant_id, destination_id, raw, submission_id):
        return self.mod._store_receipt(db, tenant_id, destination_id, raw, submission_id)

    def apply_business_confirmation(self, db, tenant_id, submission_id):
        from core_handlers import apply_business_confirmation
        return apply_business_confirmation(db, tenant_id, submission_id)

    def handler_for(self, node_type, plugin_key):
        return self.mod.handler_for(node_type, plugin_key)

    def domain_job_handlers(self) -> dict:
        return {}  # 旧装配面（pathology+metrics）无领域 job 注册

    def event_routers(self) -> list:
        return []

    def make_ctx(self, db, run, step, params, *, dry_run=False):
        return self.mod.HandlerCtx(db=db, tenant_id=run.tenant_id, run=run,
                                   step=step, params=params, dry_run=dry_run)


class PluginRegistryAdapter:
    """app_runtime.PluginRegistry 的 engine 适配面（W11 metrics-only 可用）。"""

    def __init__(self, reg):
        self.reg = reg

    def build_runtime(self, db, tenant_id):
        return self.reg.build_runtime(db, tenant_id)  # 病理未启用→CAPABILITY_UNSUPPORTED

    def resolve(self, rt, acq):
        from domains.pathology import resolver  # 仅病理任务路径触发
        return resolver.resolve_acquisition(rt, acq)

    def freeze_case(self, db, tenant_id, case, rt):
        from domains.pathology import pipeline
        return pipeline.freeze_case(db, tenant_id, case, rt)

    def enqueue(self, db, tenant_id, job_type, dedup_key, payload, *,
                delay_seconds: int = 0):
        from core_handlers import enqueue
        return enqueue(db, tenant_id, job_type, dedup_key, payload,
                       delay_seconds=delay_seconds)

    def store_receipt(self, db, tenant_id, destination_id, raw, submission_id):
        from core_handlers import _store_receipt
        return _store_receipt(db, tenant_id, destination_id, raw, submission_id)

    def apply_business_confirmation(self, db, tenant_id, submission_id):
        from core_handlers import apply_business_confirmation
        return apply_business_confirmation(db, tenant_id, submission_id)

    def handler_for(self, node_type, plugin_key):
        return self.reg.handler_for(node_type, plugin_key)

    def target_summary(self, db, tenant_id, target_code, days, *, window=None):
        # RT-03 S04：晨报经 worker 执行时原先缺此透传——digest 每个业务都落
        # "摘要不可用"。委托 app_runtime.PluginRegistry（含 window 显式区间
        # 分发）；旧调用（无 window）行为与 app 侧完全一致。
        return self.reg.target_summary(db, tenant_id, target_code, days,
                                       window=window)

    def domain_job_handlers(self) -> dict:
        return self.reg.capability_merged("worker.job_handlers")

    def event_routers(self) -> list:
        return self.reg.capability_list("worker.event_routers")

    def make_ctx(self, db, run, step, params, *, dry_run=False):
        from core_handlers import HandlerCtx
        return HandlerCtx(db=db, tenant_id=run.tenant_id, run=run,
                          step=step, params=params, dry_run=dry_run)


def _default_registry():
    # RT-02 单轨装配：任何路径都返回持有真实 PluginRegistry 的适配器——
    # 旧双轨在未设 BLYB_PLUGINS 时返回 RegistryAdapter(plugin_registry 模块)，
    # 与 app.py 的 _loaded_registry=None 同源断层。未设 BLYB_PLUGINS 时复用
    # plugin_registry 兼容层的同一实例（pathology+metrics，行为与旧装配面
    # 完全一致：该集合无 worker.job_handlers/event_routers 附件）。
    import os
    if os.environ.get("BLYB_PLUGINS"):
        # 显式插件选择（如 metrics-only）：不 import plugin_registry（会加载病理）
        import app_runtime
        return PluginRegistryAdapter(app_runtime.load_registry())
    import plugin_registry
    return PluginRegistryAdapter(plugin_registry.shared_registry())


def main(interval: float = 0.2):
    import json as _json
    import os
    import threading
    import time
    import _bootstrap  # noqa: F401
    logging.basicConfig(level=logging.INFO)
    engine = make_engine()
    sf = make_session_factory(engine)
    we = WorkerEngine(sf)
    log.info("worker started; db=%s", engine.url)
    # 运维心跳（DEP-A11）：BLYB_WORKER_HEARTBEAT 指定文件则每≈5s原子刷新；
    # 容器 healthcheck 按 mtime 判活。写失败仅告警，不影响任务执行。
    # DTR-BATCH-FIX Fix6（2026-11-08）：心跳移入独立 daemon 线程——原先写在
    # 主循环里，任何长 handler（prepare/采集页）都会阻塞主循环使心跳停更，
    # healthcheck 误报 unhealthy。线程写法与原文件格式逐字节一致。
    hb_path = os.environ.get("BLYB_WORKER_HEARTBEAT", "").strip()
    hb_stop = threading.Event()

    def _heartbeat_loop():
        # 首写立即（与原主循环内联写法的首次即写一致），其后每 5s。
        while True:
            try:
                tmp = hb_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(_json.dumps({"ts": time.time(), "pid": os.getpid()}))
                os.replace(tmp, hb_path)
            except OSError:
                log.warning("heartbeat write failed: %s", hb_path)
            if hb_stop.wait(5.0):
                break

    if hb_path:
        threading.Thread(target=_heartbeat_loop, name="worker-heartbeat",
                         daemon=True).start()
    try:
        while True:
            try:
                we.tick()
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            time.sleep(interval)
    finally:
        hb_stop.set()


if __name__ == "__main__":
    main()
