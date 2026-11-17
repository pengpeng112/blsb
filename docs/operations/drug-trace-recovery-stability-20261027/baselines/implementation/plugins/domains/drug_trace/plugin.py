"""药品追溯领域插件注册（W03 → FIX2-04 第二轮修订）。

register(reg)：
- 注册 worker job handlers（collect/prepare/send/query/reconcile）——经
  worker.job_handlers 能力合并进 engine。
- 注册事件路由（worker.event_router）：BILL_INGESTED(VALID)→prepare（按业务
  profile 精确绑定唯一目的地，R2-15 修订：不再 first 撞两个 profile 之一）、
  PACKAGE_FROZEN→提交就绪（不自动发送；发送需显式批准入口）。
- 注册迁移 009/010 与领域能力附件。

FIX2-04/R2-09 修订：worker 执行期复核（不只 API 入队时查一次）——
- collect：run 不存在/PAUSED/CANCELLED/终态 → 跳过；oracle 模式复核证据；
  connector 须仍 ENABLED。
- send：仅 PREPARED 发送；UNKNOWN/SENDING 只查询；终态不重发。
"""
from __future__ import annotations

from contracts import ApiError, SubmissionState
from core_db.models import Destination, Submission

from . import contracts as C
from . import service as S

PLUGIN_KEY = "drug_trace"
CAPABILITIES = ["collect", "prepare", "send", "query", "reconcile", "evidence"]


# ---------------------------------------------------------------- job handlers
def _j_collect(db, job):
    """窗口采集（FIX3-02/G3-06：执行期按批准连接器配置重建绑定）。

    - 连接器配置优先取 ConnectorInstance.active_config_revision（经闭包校验：
      同租户、owner 绑定该连接器、PUBLISHED）——API 入队的 mode 提示仅作
      synthetic 回退，不再丢失 dsn_env/对象白名单等连接绑定。
    - 复核 run 状态/connector ENABLED/oracle 采集证据；checkpoint 分区随任务
      payload（历史补报与增量独立）。
    """
    from . import service as svc
    from .models import DrugTraceCollectionRun
    from core_db.models import ConnectorInstance, ConfigRevision
    p = job.payload or {}
    run_id = p.get("collection_run_id")
    run = db.get(DrugTraceCollectionRun, run_id) if run_id else None
    if run is None or run.tenant_id != job.tenant_id:
        return {"skipped": "collection run missing"}
    if run.state != "PENDING" and run.state != "RUNNING":
        # PAUSED/CANCELLED/DONE/FAILED：不无条件改 RUNNING（R2-09）
        return {"skipped": f"run_state={run.state}"}
    connector = db.get(ConnectorInstance, p.get("connector_id", ""))
    if connector is None or connector.tenant_id != job.tenant_id \
            or connector.status != "ENABLED":
        return {"skipped": "connector missing or disabled"}
    connector_config = dict(p.get("connector_config") or {})
    if connector.active_config_revision:
        cr = db.get(ConfigRevision, connector.active_config_revision)
        if cr is None or cr.tenant_id != job.tenant_id \
                or cr.owner_id != connector.id or cr.state != "PUBLISHED":
            return {"skipped": "connector config binding invalid "
                               "(tenant/owner/state)"}
        connector_config = dict(cr.content_json or {})
    mode = str(connector_config.get("mode",
                                    p.get("connector_config", {}).get(
                                        "mode", "synthetic")))
    # FIN-04 两模式分离：持续增量须有确认的可索引变更来源（AUTO 阻断）
    from .business_rules import require_continuous_mode
    require_continuous_mode(connector_config)
    if mode == "oracle":
        # 执行期证据复核（API 入队后撤销证据 → 已排队任务停发）
        profile = p.get("profile", "") or C.PROFILE_INOUT
        svc.require_production_evidence(db, job.tenant_id, profile=profile,
                                        capability="collect")
    warehouse = svc.make_warehouse(connector_config)
    from datetime import datetime
    wb = datetime.fromisoformat(p["window_begin"])
    we = datetime.fromisoformat(p["window_end"])
    partition = str(p.get("checkpoint_partition", "0") or "0")
    return svc.run_collection(
        db, job.tenant_id, p["connector_id"], warehouse,
        window_begin=wb, window_end=we, created_by=p.get("created_by", ""),
        lookback_seconds=int(p.get("lookback_seconds", 600)),
        profile=p.get("profile", ""), run_id=run.id,
        partition=partition, mode=mode)


def _j_prepare(db, job):
    p = job.payload or {}
    return S.prepare_submission(
        db, job.tenant_id, p["revision_id"], profile=p["profile"],
        destination_id=p["destination_id"],
        config_release_id=p.get("config_release_id"))


def _j_send(db, job):
    """发送（FIX2-04：统一状态守卫——UNKNOWN/SENDING 只查询不重发）。"""
    p = job.payload or {}
    sub = db.get(Submission, p["submission_id"])
    if sub is None or sub.tenant_id != job.tenant_id:
        return {"skipped": "submission missing"}
    if sub.state == SubmissionState.CONFIRMED:
        return {"skipped": f"state={sub.state}"}
    if sub.state in (SubmissionState.UNKNOWN, SubmissionState.SENDING):
        # 结果未知/在途：只查询（先查后判；绝不二次外发）
        S._enqueue_query_job(db, job.tenant_id, sub.id)
        return {"skipped": f"state={sub.state}", "action": "query_enqueued"}
    if sub.state != SubmissionState.PREPARED:
        return {"skipped": f"state={sub.state}"}
    result = S.perform_send(db, job.tenant_id, sub)
    return result


def _j_query(db, job):
    p = job.payload or {}
    sub = db.get(Submission, p["submission_id"])
    if sub is None or sub.tenant_id != job.tenant_id:
        return {"skipped": "submission missing"}
    if sub.state in (SubmissionState.CONFIRMED, SubmissionState.REJECTED,
                     SubmissionState.PARTIAL, 'SUPERSEDED'):
        return {'skipped': 'terminal state', 'state': sub.state}
    return S.perform_query(db, job.tenant_id, sub)


def _j_reconcile(db, job):
    from datetime import datetime
    p = job.payload or {}
    return S.run_reconciliation(
        db, job.tenant_id, destination_id=p["destination_id"],
        profile=p["profile"],
        window_begin=datetime.fromisoformat(p["window_begin"])
        if p.get("window_begin") else None,
        window_end=datetime.fromisoformat(p["window_end"])
        if p.get("window_end") else None,
        pharmacy_code=p.get("pharmacy_code", ""))


JOB_HANDLERS = {
    C.DomainJobType.COLLECT: _j_collect,
    C.DomainJobType.PREPARE: _j_prepare,
    C.DomainJobType.SEND: _j_send,
    C.DomainJobType.RECONCILE: _j_reconcile,
    C.DomainJobType.QUERY: _j_query,
}


# ---------------------------------------------------------------- 事件路由
def route_event(db, ev) -> bool:
    """outbox 事件路由（engine 合并调用）。返回 True 表示已处理。

    - BILL_INGESTED 且质量 VALID → prepare job。目的地按该业务 profile 精确
      绑定（R2-15：药房零售单不会撞到库存 profile 的目的地）；无匹配目的地→
      不排，等配置（人工重驱）。
    - PACKAGE_FROZEN（domain=drug_trace）→ 不自动发送；提交由显式 send 入口批准。
    """
    tenant = ev.tenant_id
    p = ev.payload or {}
    if ev.event_type == C.DomainEvent.BILL_INGESTED:
        if p.get("quality_state") == C.BillQualityState.VALID:
            profile = C.profile_for_kind(p.get("business_kind", ""))
            if profile is None:
                return True          # 分类不明已在质量层等待，不路由
            dest = (db.query(Destination)
                    .filter(Destination.tenant_id == tenant,
                            Destination.status == "ENABLED",
                            Destination.profile_revision.like(f"{profile}%"))
                    .first())
            if dest is None:
                return True  # 无目的地：不排 job（配置后由人工重驱）
            from workflow_core.jobs import enqueue_job
            enqueue_job(db, tenant, C.DomainJobType.PREPARE,
                        f"prep:{p.get('revision_id')}",
                        {"revision_id": p.get("revision_id"),
                         "profile": profile,
                         "destination_id": dest.id})
        return True
    if ev.event_type == C.DomainEvent.PACKAGE_FROZEN:
        return True  # 冻结即就绪；发送走显式批准（不自动）
    return False


def _destination_adapter(db, tenant_id: str, submission, payload: dict) -> dict:
    """core_handlers.perform_push 的药品目标适配器（DTR-BASE-01）。

    校验 payload 确为药品冻结包后走 service.perform_send（状态守卫、证据门禁、
    CAS、先持久化尝试、UNKNOWN 先查询都在其中）；非药品 payload 显式不支持。
    """
    domain = (payload or {}).get("domain")
    if domain not in (None, "drug_trace"):
        raise ApiError("CAPABILITY_UNSUPPORTED",
                       f"drug_trace adapter got non-drug payload domain={domain!r}")
    return S.perform_send(db, tenant_id, submission)


def register(reg) -> None:
    from core_handlers import register_destination_adapter
    from . import contracts as _C
    from .models import migration as drug_trace_migration
    from .models import migration_fix2 as drug_trace_fix2_migration
    from .models import migration_fix3 as drug_trace_fix3_migration
    from .models import migration_fix4 as drug_trace_fix4_migration
    reg.add_migration(drug_trace_migration)
    reg.add_migration(drug_trace_fix2_migration)
    reg.add_migration(drug_trace_fix3_migration)
    reg.add_migration(drug_trace_fix4_migration)
    reg.attach("worker.job_handlers", dict(JOB_HANDLERS))
    reg.attach("worker.event_routers", route_event)
    reg.attach("drug_trace.service", S)
    for _pk in _C.DESTINATION_PROFILE_KEYS:
        register_destination_adapter(_pk, _destination_adapter)
