"""药品追溯编排服务（W04—W07 → FIX2 → FIX3 第三轮修订）。

生产证据门禁（DTR-A02/A03 + FIX2-02 + FIX3-04 修订）：
- 任何真实采集/发送/查询/对账能力先查 DrugTraceProductionEvidence：
  无记录、状态非 APPROVED、必需项未齐（必需项由代码常量固定，不信任可缩减的
  required_items）、已撤销、已过期、destination/config_release 不精确匹配 →
  PROFILE_UNAPPROVED 拒绝（fail-closed）。config_release_id 传入即要求证据
  精确登记同一值——空配置不再是通配（R3-04）。
- send 必需项含 EXT-05/10/12；发送时按批准 scope（窗口/药房白名单/配额）逐单
  复核 + 事务内并发配额预留（FIX3-04，drug_trace_send_quota 条件原子 UPDATE）。

FIX3-02 修订：run_collection 安全点改独立只读会话（结束本事务后另开会话读
run/connector/证据——expire_on_commit=False 的会话缓存看不到其他事务的暂停）；
采集事务改页级原子（见 ingest.collect_window ADR）。

FIX3-03 修订：ACCEPTED 自动排第一代查询；查询错误与「确实无此单」分离
（QUERY_ERROR ≠ NOT_FOUND）；无 total 满页翻页推进 + 重复页/预算保护；查询
只读身份固定为冻结时配置（G3-07/08/09）。

FIX3-04/05 修订：prepare 幂等键含配置代次（未发送旧提交显式 SUPERSEDED 换代，
已发送提交拒绝换配置重发）；build_client 校验 ConfigRevision
tenant/owner/PUBLISHED；query/reconcile 复用同一全分页只读客户端。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from contracts import ApiError, SubmissionState, new_uuid
from contracts.api import digest_of
from connector_sdk import CapabilityError, RequestDeferred
from core_db.engine import utcnow
from core_db.models import (ConfigRevision, DeliveryAttempt, Destination,
                            PackageRevision, ReceiptEvent,
                            ReconciliationItem, ReconciliationRun, Submission,
                            SubmissionItem)
from workflow_core.jobs import emit as emit_outbox

from . import contracts as C
from . import ingest, payload as payload_mod
from .models import (DrugTraceBill, DrugTraceBillRevision,
                     DrugTraceCollectionRun, DrugTraceProductionEvidence,
                     DrugTraceSendQuota)

# 业务墙钟口径：HIS/TOP 均为 GMT+8（EXT-02/官方时间格式）；naive 值按此解释
_GMT8 = timezone(timedelta(hours=8))

# ---------------------------------------------------------------- 证据门禁
# 必需项由代码固定（FIX2-02：用户不可缩减）；EXT-05/10/12 为发送三重门禁。
REQUIRED_EVIDENCE: dict[str, list[dict]] = {
    "collect": [
        {"key": "EXT-01", "label": "HIS readonly 登记与服务名统一（受限只读账号）"},
        {"key": "EXT-02", "label": "视图DDL/字段字典/主单关系院方确认"},
    ],
    "send": [
        {"key": "EXT-03", "label": "DMZ→平台443出口与DNS放通"},
        {"key": "EXT-04", "label": "API/DMZ 正式域名与SAN证书、mTLS身份"},
        {"key": "EXT-05", "label": "院内NTP校时就绪（时钟偏差受控）"},
        {"key": "EXT-06", "label": "平台AppKey/AppSecret与企业标识、权限包"},
        {"key": "EXT-07", "label": "官方SDK/签名规范与测试向量核对一致"},
        {"key": "EXT-08", "label": "bill_type/企业字典批准"},
        {"key": "EXT-10", "label": "历史补报范围与既往已上报核对"},
        {"key": "EXT-12", "label": "首批真实发送授权（机构/药房/业务/时间/上限）"},
    ],
    # 只读回执查询独立能力：UNKNOWN 恢复不需要重新打开发送授权（FIX2-02）。
    "query": [
        {"key": "EXT-09", "label": "query.billstatus 覆盖范围/分页/终态规则确认"},
    ],
    "reconcile": [
        {"key": "EXT-09", "label": "query.billstatus 覆盖范围/分页/终态规则确认"},
    ],
}


def evidence_state(db, tenant_id: str, *, profile: str, capability: str,
                   destination_id: str = "", config_release_id: str | None = None):
    """返回证据状态（供 readiness/诊断）；不做断言。

    FIX2-02 修订：按 destination 精确过滤（不做 first 撞库）；必需项永远取代码
    常量；config_release 绑定不匹配 → MISMATCH_CONFIG。
    """
    q = (db.query(DrugTraceProductionEvidence)
         .filter(DrugTraceProductionEvidence.tenant_id == tenant_id,
                 DrugTraceProductionEvidence.profile == profile,
                 DrugTraceProductionEvidence.capability == capability))
    if destination_id:
        q = q.filter(DrugTraceProductionEvidence.destination_id == destination_id)
    ev = q.first()
    if ev is None:
        required = REQUIRED_EVIDENCE.get(capability, [])
        return {"status": "MISSING", "required": required,
                "missing": required,
                "missing_keys": [i["key"] for i in required]}
    required = REQUIRED_EVIDENCE.get(capability, [])
    provided_keys = {i.get("key") for i in (ev.provided_items or [])}
    missing = [i for i in required if i.get("key") not in provided_keys]
    expired = ev.valid_until is not None and ev.valid_until < utcnow()
    # FIX3-04/R3-04：调用方传入 config_release_id 即要求证据精确登记同一值；
    # 空 config_release_id 不再当通配（空证据 vs 新配置 → MISMATCH_CONFIG）。
    mismatch_config = (config_release_id is not None
                       and str(ev.config_release_id or "")
                       != str(config_release_id))
    status = ("REVOKED" if ev.revoked_at is not None
              else "EXPIRED" if expired
              else "INCOMPLETE" if missing or ev.status != "APPROVED"
              else "MISMATCH_CONFIG" if mismatch_config
              else "MISMATCH_DEST" if destination_id and ev.destination_id
              and ev.destination_id != destination_id
              else "APPROVED")
    return {"status": status, "evidence_id": ev.id,
            "required": required, "missing": missing,
            "destination_id": ev.destination_id,
            "config_release_id": ev.config_release_id,
            "scope": ev.scope if hasattr(ev, "scope") else None,
            "approved_by": ev.approved_by,
            "valid_until": ev.valid_until}


def require_production_evidence(db, tenant_id: str, *, profile: str,
                                capability: str, destination_id: str = "",
                                config_release_id: str | None = None) -> None:
    """fail-closed 断言：不满足即抛 PROFILE_UNAPPROVED（空表不放行）。"""
    state = evidence_state(db, tenant_id, profile=profile, capability=capability,
                           destination_id=destination_id,
                           config_release_id=config_release_id)
    if state["status"] != "APPROVED":
        def _keys(items):
            return [i.get("key") if isinstance(i, dict) else str(i)
                    for i in (items or [])]
        missing = _keys(state.get("missing")) or             _keys(state.get("missing_keys")) or _keys(state.get("required"))
        raise ApiError(
            "PROFILE_UNAPPROVED",
            f"drug_trace production capability {capability!r} for {profile} "
            f"not authorized: evidence status={state['status']} "
            f"missing={missing}; explicit approval with complete evidence "
            f"required (EXT items)")


def _validate_send_scope(scope: dict | None) -> dict:
    """EXT-12 发送授权范围完整性校验（FIX3-04/G3-10）：窗口、药房白名单、
    配额三者必须显式登记——空 scope/空药房列表/缺配额均拒绝（不作为全开放）。"""
    if not isinstance(scope, dict) or not scope:
        raise ApiError(
            "VALIDATION_FAILED",
            "send evidence requires explicit scope {window_begin, window_end,"
            " pharmacies[], max_bills}; empty scope is not open-ended (EXT-12)")
    wb, we = scope.get("window_begin"), scope.get("window_end")
    pharmacies = scope.get("pharmacies")
    max_bills = scope.get("max_bills")
    if not wb or not we:
        raise ApiError("VALIDATION_FAILED",
                       "send scope window_begin/window_end required (EXT-12)")
    for v in (wb, we):
        try:
            datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED",
                           f"send scope window not ISO format: {v!r}") from exc
    if not isinstance(pharmacies, list) or not pharmacies or \
            not all(str(p) for p in pharmacies):
        raise ApiError(
            "VALIDATION_FAILED",
            "send scope pharmacies must be a non-empty explicit whitelist "
            "(enumerate every approved pharmacy; no wildcard, EXT-12)")
    if not isinstance(max_bills, int) or isinstance(max_bills, bool) \
            or max_bills < 1:
        raise ApiError("VALIDATION_FAILED",
                       "send scope max_bills must be a positive integer (EXT-12)")
    return scope


def approve_evidence(db, tenant_id: str, *, profile: str, capability: str,
                     destination_id: str, provided_items: list[dict],
                     approved_by: str, valid_until=None,
                     config_release_id: str = "",
                     scope: dict | None = None) -> DrugTraceProductionEvidence:
    """登记/更新证据（platform_admin 角色；提供项必须覆盖代码必需项才置 APPROVED）。

    scope（发送授权范围，EXT-12）：{window_begin, window_end, pharmacies[],
    max_bills}——FIX3-04 起 send 能力必须完整登记（空 scope 拒绝），并发送时
    逐单复核 + 配额台账（drug_trace_send_quota）原子预留。
    """
    required = REQUIRED_EVIDENCE.get(capability, [])
    req_keys = {i["key"] for i in required}
    got_keys = {i.get("key") for i in provided_items}
    missing = req_keys - got_keys
    if capability == "send":
        _validate_send_scope(scope)
    ev = (db.query(DrugTraceProductionEvidence)
          .filter_by(tenant_id=tenant_id, profile=profile,
                     destination_id=destination_id, capability=capability).first())
    if ev is None:
        ev = DrugTraceProductionEvidence(
            id=new_uuid(), tenant_id=tenant_id, profile=profile,
            destination_id=destination_id, capability=capability)
        db.add(ev)
    ev.required_items = required
    ev.provided_items = provided_items
    ev.config_release_id = config_release_id or ""
    ev.approved_by = approved_by
    ev.valid_until = valid_until
    ev.revoked_at = None
    ev.status = "APPROVED" if not missing else "DRAFT"
    ev.scope = scope or {}
    if capability == "send":
        # 配额台账：批准时创建/更新（used 保留，防止重复审批重置计数绕过配额）
        quota = (db.query(DrugTraceSendQuota)
                 .filter_by(tenant_id=tenant_id, evidence_id=ev.id).first())
        if quota is None:
            db.add(DrugTraceSendQuota(
                id=new_uuid(), tenant_id=tenant_id, evidence_id=ev.id,
                used=0, max_bills=int(scope["max_bills"])))
        else:
            quota.max_bills = int(scope["max_bills"])
    if missing:
        raise ApiError("VALIDATION_FAILED",
                       f"evidence incomplete, missing: {sorted(missing)}")
    return ev


def revoke_evidence(db, tenant_id: str, evidence_id: str) -> None:
    ev = db.get(DrugTraceProductionEvidence, evidence_id)
    if ev is None or ev.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "evidence not found")
    ev.status = "REVOKED"
    ev.revoked_at = utcnow()


def _to_utc(value, *, naive_tz) -> datetime:
    """ISO/datetime → UTC（FIX3-04：一致时区比较，不 replace 丢偏移）。

    naive 值按 naive_tz（业务墙钟 GMT+8 或 UTC）解释后折算 UTC；带偏移值直接
   折算。解析失败抛 ValueError（调用方转 VALIDATION_FAILED）。
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz)
    return dt.astimezone(timezone.utc)


def _check_send_scope(ev_scope: dict | None, payload: dict) -> dict:
    """EXT-12 发送授权范围逐单复核（FIX3-04/G3-10/G3-11）。

    - scope 必须完整登记（窗口+药房白名单+配额）：空 scope 不放行（不再视为
      全开放）；本单药房必须在白名单内；时间比较统一折算 UTC——scope 窗口与
      bill_time 的 naive 值均按业务墙钟 GMT+8 解释（EXT-02），带偏移值按偏移
      折算，绝不 replace(tzinfo=None) 丢偏移。
    """
    _validate_send_scope(ev_scope)
    pharmacy = str(payload.get("pharmacy_code", ""))
    if pharmacy not in {str(p) for p in ev_scope["pharmacies"]}:
        raise ApiError(
            "PROFILE_UNAPPROVED",
            f"pharmacy {pharmacy!r} not in approved send scope pharmacies "
            f"(EXT-12 whitelist: {ev_scope['pharmacies']})")
    bill_time = payload.get("bill_time")
    try:
        bt = _to_utc(bill_time, naive_tz=_GMT8)
        w_b = _to_utc(ev_scope["window_begin"], naive_tz=_GMT8)
        w_e = _to_utc(ev_scope["window_end"], naive_tz=_GMT8)
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED",
                       f"send scope window or bill_time not ISO format: {exc}") from exc
    if not (w_b <= bt < w_e):
        raise ApiError(
            "PROFILE_UNAPPROVED",
            f"bill_time {bill_time} outside approved send scope "
            f"[{ev_scope['window_begin']}, {ev_scope['window_end']}) (EXT-12)")
    return ev_scope


def _reserve_send_quota(db, tenant_id: str, evidence_id: str,
                        max_bills: int) -> None:
    """事务内并发配额预留（G3-11）：条件原子 UPDATE——used < max_bills 才 +1。

    行由 approve_evidence 创建；缺失即拒绝（fail-closed，不在发送事务里补插
    以避免并发首插竞争）。保守计数：预留后即使本地阻断回退 PREPARED 也占额度。
    """
    res = db.execute(
        update(DrugTraceSendQuota)
        .where(DrugTraceSendQuota.tenant_id == tenant_id,
               DrugTraceSendQuota.evidence_id == evidence_id,
               DrugTraceSendQuota.used < DrugTraceSendQuota.max_bills)
        .values(used=DrugTraceSendQuota.used + 1, updated_at=utcnow()))
    if res.rowcount != 1:
        used = (db.query(DrugTraceSendQuota)
                .filter_by(tenant_id=tenant_id, evidence_id=evidence_id).first())
        if used is None:
            raise ApiError(
                "PROFILE_UNAPPROVED",
                "send quota ledger missing for evidence; re-approve evidence "
                "with complete EXT-12 scope before sending")
        raise ApiError(
            "PROFILE_UNAPPROVED",
            f"send quota exhausted: {used.used}/{used.max_bills} attempts "
            "under this EXT-12 approval; further sends require new approval")


# ---------------------------------------------------------------- 采集
def make_warehouse(connector_config: dict):
    """按批准连接器配置构建仓储：mode=synthetic|oracle（FIX3-02/G3-06）。

    oracle：dsn_env（该连接器专用受限只读账号；缺省回退全局兼容变量）、
    objects{bill_view,code_view}（对象白名单，仅接受 [SCHEMA.]OBJECT 标识符）。
    source_profile 可绑定版本化源事实；只有元数据、尚无活库契约验证的版本拒绝采集。
    synthetic：namespace 隔离。配置即连接绑定——采集不得跨 connector/tenant
    复用其他连接器的 DSN 或对象名。
    batch_fetch（LT-08 O4，默认 false）：源端按页批量取头+码（每页 2 次往返
    替代每单 2 次）；谓词保持复合键等值（FBI 形式），开启前须 DBA EXPLAIN。
    """
    from connector_sdk import CapabilityError
    cfg = connector_config or {}
    _batch = bool(cfg.get("batch_fetch", False))
    mode = str(cfg.get("mode", "synthetic"))
    if "source_profile" in cfg:
        from sources.db_view.source_profiles import get_source_profile
        if mode != "oracle":
            raise CapabilityError(
                "CAPABILITY_UNSUPPORTED", "source_profile requires oracle mode")
        get_source_profile(str(cfg["source_profile"])).require_runtime_ready(config=cfg)
    if mode == "synthetic":
        from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
        wh = SyntheticDrugTraceWarehouse(
            namespace=str(cfg.get("namespace", "HIS")))
        wh.batch_fetch = _batch
        return wh
    if mode == "oracle":
        import os
        dsn_env = str(cfg.get("dsn_env", "BLYB_DRUGTRACE_ORACLE_DSN"))
        dsn = os.environ.get(dsn_env, "")
        if not dsn:
            raise CapabilityError(
                "DRIVER_UNAVAILABLE",
                f"oracle mode requires {dsn_env} for this connector (EXT-01; "
                "per-connector restricted readonly binding)")
        from sources.db_view.drug_trace import OracleDrugTraceWarehouse
        expected_user = ""
        if cfg.get("source_profile"):
            expected_user = get_source_profile(str(cfg["source_profile"])).reader
        return OracleDrugTraceWarehouse(
            dsn=dsn, objects=cfg.get("objects") or None,
            expected_user=expected_user, batch_fetch=_batch)
    raise CapabilityError("CAPABILITY_UNSUPPORTED", f"unknown warehouse mode {mode}")


def _fresh_session_read(db, fn):
    """独立只读会话读取（FIX3-02/R3-05）：结束本事务后另开会话，保证看到其他
    事务已提交的 run/connector/证据状态——expire_on_commit=False 的本会话身份
    映射缓存不可信。bind 为 Connection（非 Engine）时退化为 expire_all 重读。"""
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    bind = db.get_bind()
    if isinstance(bind, Engine):
        db.commit()                     # 结束在途事务：不阻塞他人写入
        try:
            with Session(bind=bind) as fresh:
                return fn(fresh)
        except Exception:  # noqa: BLE001 —— 独立会话异常退回本会话重读
            pass
    db.commit()
    db.expire_all()
    return fn(db)


def _collect_gate(db, *, run_id: str, tenant_id: str, connector_id: str,
                  profile: str, mode: str):
    """页边界安全点（FIX3-02/R3-05/G3-04）：独立会话复核 run 状态、connector
    ENABLED、（oracle）采集证据。返回 None 继续；否则返回停止原因。

    LT-08 O3：三项复核合并到**同一**独立只读会话一次完成（此前 run/connector
    与证据各开一次会话）——读到的是同一一致性快照，判定顺序与结果不变，
    每次安全点少一次 commit/会话/往返。"""
    from core_db.models import ConnectorInstance

    def _read(session):
        run = session.get(DrugTraceCollectionRun, run_id)
        run_state = run.state if run is not None else "MISSING"
        conn = session.get(ConnectorInstance, connector_id)
        conn_ok = conn is not None and conn.tenant_id == tenant_id \
            and conn.status == "ENABLED"
        ev_status = ""
        if mode == "oracle":
            ev_status = evidence_state(
                session, tenant_id, profile=profile or C.PROFILE_INOUT,
                capability="collect")["status"]
        return run_state, conn_ok, ev_status

    run_state, conn_ok, ev_status = _fresh_session_read(db, _read)
    if run_state == "PAUSED":
        return "paused"
    if run_state in ("CANCELLED", "DONE", "FAILED", "MISSING"):
        return "cancelled"
    if not conn_ok:
        return "blocked:connector-disabled"
    if mode == "oracle" and ev_status != "APPROVED":
        return "blocked:collect-evidence-revoked"
    return None


def _fresh_run_state(db, run_id: str):
    def _read(session):
        run = session.get(DrugTraceCollectionRun, run_id)
        return run.state if run is not None else None
    return _fresh_session_read(db, _read)


def run_collection(db, tenant_id: str, connector_id: str, warehouse, *,
                   window_begin, window_end, created_by: str = "",
                   lookback_seconds: int = 600, profile: str = "",
                   run_id: str | None = None, page_limit: int = 100,
                   partition: str = "0", mode: str = "synthetic",
                   max_pages=None, execution_guard=None,
                   gate_override=None) -> dict:
    """创建（或复用既有）CollectionRun 并完成窗口采集（FIX3-02 修订）。

    - 安全点（每页边界 + 重取账本前）经 _collect_gate 独立只读会话复核：
      暂停/撤销/连接器禁用/证据撤销即停；停止时不覆盖外部已写入的终态
      （PAUSED 不被 DONE 覆盖、撤销不被改成暂停）。
    - checkpoint 分区 partition：历史补报与增量互不影响（G3-06）。
    - gate_override（测试接缝，默认 None）：提供时安全点判定改用该回调
      （仍先经 execution_guard.check）；不影响生产路径。
    """
    if run_id:
        run = db.get(DrugTraceCollectionRun, run_id)
        if run is None or run.tenant_id != tenant_id:
            raise ApiError("NOT_FOUND", "collection run not found")
        if run.state not in ("PENDING", "RUNNING"):
            # PAUSED/CANCELLED/DONE/FAILED：不无条件改 RUNNING（R2-09）；
            # RUNNING 允许续跑（崩溃恢复语义：worker 失败重试）
            return {"run_id": run.id, "skipped": run.state,
                    "state": run.state}
    else:
        run = DrugTraceCollectionRun(
            id=new_uuid(), tenant_id=tenant_id, connector_id=connector_id,
            stream=ingest.STREAM, profile=profile,
            window_begin=window_begin, window_end=window_end,
            lookback_seconds=lookback_seconds, state="RUNNING",
            created_by=created_by)
        db.add(run)
        db.flush()
    prof = ingest.new_profile()          # BLYB_DT_PROFILE=1 才非 None（LT-08）
    _t = time.perf_counter() if prof else 0.0
    if run.state == "PENDING":
        run.state = "RUNNING"
        if execution_guard:execution_guard.fence(db)
        db.commit()
    else:
        db.commit()
    if prof:
        prof.add("run_setup", time.perf_counter() - _t)

    def _gate():
        if execution_guard:execution_guard.check()
        if gate_override is not None:
            return gate_override()
        return _collect_gate(db, run_id=run.id, tenant_id=tenant_id,
                             connector_id=connector_id, profile=profile,
                             mode=mode)

    base = dict(run.summary or {}) if max_pages is not None else {}
    continued = int(base.get('slices_completed',0)) > 0
    initial_metrics = dict(getattr(warehouse,'metrics',{}))
    def _progress(delta, complete):
        if max_pages is None:return
        cumulative={k:int(base.get(k,0))+v for k,v in delta.items() if type(v) is int}
        cumulative.update(slices_completed=int(base.get('slices_completed',0))+1,
                          progress_at=utcnow().isoformat(),window_completed=complete)
        prior_metrics=base.get('source_metrics') or {}
        if hasattr(warehouse,'metrics'):
            cumulative['source_metrics']={k:float(prior_metrics.get(k,0))+v-initial_metrics.get(k,0) for k,v in warehouse.metrics.items()}
        run.summary=cumulative

    try:
        summary = ingest.collect_window(
            db, tenant_id, connector_id, warehouse,
            window_begin=window_begin, window_end=window_end, run=run,
            lookback=timedelta(seconds=0 if continued else lookback_seconds),
            gate=_gate, page_limit=page_limit, partition=partition,
            max_pages=max_pages, drain_retries=not continued,
            before_commit=execution_guard.fence if execution_guard else None,
            progress=_progress if max_pages is not None else None,
            prof=prof)
    except Exception as exc:
        db.rollback()
        from workflow_core.jobs import JobLeaseLost
        if isinstance(exc,JobLeaseLost):raise
        run2 = db.get(DrugTraceCollectionRun, run.id)
        if run2 is not None:
            run2.state = "FAILED"
            run2.finished_at = utcnow()
            db.commit()
        raise
    stopped = summary.get("stopped")
    _t = time.perf_counter() if prof else 0.0
    # 终态裁决：外部已写入的终态优先（不把 PAUSED 覆盖成 DONE / 取消改成暂停）
    fresh = _fresh_run_state(db, run.id)
    run = db.get(DrugTraceCollectionRun, run.id)
    db.refresh(run)
    if max_pages is None:run.summary = summary
    elif stopped:run.summary={**(run.summary or {}),'stopped':stopped}
    if fresh in ("PAUSED", "CANCELLED", "DONE", "FAILED"):
        run.state = fresh
    elif stopped == "paused":
        run.state = "PAUSED"          # 安全点暂停：保留断点，不置 DONE
    elif stopped == "cancelled":
        run.state = "CANCELLED"
    elif stopped and str(stopped).startswith("blocked:"):
        run.state = "FAILED"
    elif summary.get('yielded'):
        run.state = "RUNNING"
    else:
        run.state = "DONE"
    if run.state in ("DONE", "FAILED", "CANCELLED"):
        run.finished_at = utcnow()
    if execution_guard:execution_guard.fence(db)
    db.commit()
    if prof:
        prof.add("run_adjudicate", time.perf_counter() - _t)
    return {"run_id": run.id, **(run.summary or {}), 'state':run.state,
            **({'yielded':True} if summary.get('yielded') else {}),
            **({"profile": summary["profile"]} if "profile" in summary else {})}


# ---------------------------------------------------------------- 定向复核（K3/SD-04）
RESCAN_MAX_KEYS = 500            # 每次请求/每批上限（任务书 §二 SD-04）


def _rescan_one(db, tenant_id: str, connector_id: str, warehouse, key: dict,
                *, code_page_limit: int) -> dict:
    """单键定向复核：点查源侧 → 幂等导入/重判。

    返回 {outcome: skipped_confirmed|skipped_missing_source|unchanged|
    migrated_not_reportable|imported, **import_bill 结果}。
    """
    params = {"source_system": str(key.get("source_system", "") or ""),
              "pharmacy_code": str(key.get("pharmacy_code", "") or ""),
              "business_kind": str(key.get("business_kind", "") or ""),
              "source_bill_id": str(key.get("source_bill_id", "") or "")}
    bill = (db.query(DrugTraceBill)
            .filter_by(tenant_id=tenant_id, connector_id=connector_id,
                       **params).first())
    if bill is not None:
        # K3 边界：已 CONFIRMED/ACCEPTED 的单据键直接跳过（永不重报）
        for sub in payload_mod._submissions_for_bill(db, tenant_id, bill):
            if str(sub.state) in ("CONFIRMED", "ACCEPTED"):
                return {"outcome": "skipped_confirmed"}
    headers = warehouse.fetch_all("drugtrace.bill_header", params, 2)
    if not headers:
        return {"outcome": "skipped_missing_source"}
    header = headers[0]
    if bill is not None and bill.current_revision_id is not None \
            and str(header.get("source_revision")) == \
            str(bill.current_source_revision):
        rev = db.get(DrugTraceBillRevision, bill.current_revision_id)
        if rev is not None:
            if ingest._migrate_late_no_code(db, tenant_id, bill, rev):
                return {"outcome": "migrated_not_reportable",
                        "state": rev.quality_state}
            return {"outcome": "unchanged", "state": rev.quality_state}
    lines, final_header, reread = ingest.read_bill_whole(
        warehouse, header, code_page_limit=code_page_limit)
    from .business_rules import resolve_business_kind
    profile = C.profile_for_kind(resolve_business_kind(final_header))
    result = ingest.import_bill(db, tenant_id, connector_id, final_header,
                                lines, profile=profile)
    result["outcome"] = "imported"
    return result


def run_rescan(db, tenant_id: str, connector_id: str, warehouse, *,
               bill_keys: list[dict], created_by: str = "", profile: str = "",
               run_id: str | None = None, code_page_limit: int = 200) -> dict:
    """K3 迟到码补报·定向复核（SD-04；不做窗口扫描，逐单点查）。

    - 已 CONFIRMED/ACCEPTED 单据键跳过计数（已确认不重报）；
    - 源版本变化 → 整单重读 + 幂等导入（码变化 → 新 revision）；
    - 源版本未变 → 幂等重判（门诊老无码单超回看窗迁 NOT_REPORTABLE）；
    - 源侧单据消失 → 计数保留本地；
    单键失败记录 errors（≤50 明细+总数），不中断其余键。
    """
    if not bill_keys:
        raise ApiError("VALIDATION_FAILED", "rescan requires a non-empty bill_keys list")
    if len(bill_keys) > RESCAN_MAX_KEYS:
        raise ApiError("VALIDATION_FAILED",
                       f"rescan bill_keys exceeds {RESCAN_MAX_KEYS} per run")
    if run_id:
        run = db.get(DrugTraceCollectionRun, run_id)
        if run is None or run.tenant_id != tenant_id:
            raise ApiError("NOT_FOUND", "collection run not found")
        if run.state not in ("PENDING", "RUNNING"):
            return {"run_id": run.id, "skipped": run.state, "state": run.state}
    else:
        run = DrugTraceCollectionRun(
            id=new_uuid(), tenant_id=tenant_id, connector_id=connector_id,
            stream=ingest.STREAM, profile=profile, window_begin=None,
            window_end=None, lookback_seconds=0, state="RUNNING",
            created_by=created_by)
        db.add(run)
        db.flush()
    run.state = "RUNNING"
    db.commit()
    summary = {"keys_total": len(bill_keys), "scanned": 0,
               "skipped_confirmed": 0, "skipped_missing_source": 0,
               "unchanged": 0, "migrated_not_reportable": 0,
               "revisions_created": 0, "valid": 0, "waiting": 0,
               "invalid": 0, "not_reportable": 0,
               "errors_total": 0, "errors": []}
    for key in bill_keys:
        summary["scanned"] += 1
        try:
            result = _rescan_one(db, tenant_id, connector_id, warehouse, key,
                                 code_page_limit=code_page_limit)
        except ApiError as exc:
            db.rollback()
            summary["errors_total"] += 1
            if len(summary["errors"]) < 50:
                summary["errors"].append({
                    "key": [key.get("source_system"),
                            key.get("pharmacy_code"),
                            key.get("business_kind"),
                            key.get("source_bill_id")],
                    "code": exc.code, "message": str(exc)[:200]})
            continue
        outcome = result.get("outcome")
        if outcome in ("skipped_confirmed", "skipped_missing_source",
                       "unchanged", "migrated_not_reportable"):
            bucket = {"skipped_confirmed": "skipped_confirmed",
                      "skipped_missing_source": "skipped_missing_source",
                      "unchanged": "unchanged",
                      "migrated_not_reportable": "migrated_not_reportable"}[outcome]
            summary[bucket] += 1
        if result.get("created"):
            summary["revisions_created"] += 1
        state = result.get("state")
        if state == C.BillQualityState.VALID:
            summary["valid"] += 1
        elif state == C.BillQualityState.WAITING:
            summary["waiting"] += 1
        elif state == C.BillQualityState.NOT_REPORTABLE:
            summary["not_reportable"] += 1
        elif state == C.BillQualityState.INVALID:
            summary["invalid"] += 1
        db.commit()
    run = db.get(DrugTraceCollectionRun, run.id)
    run.summary = summary
    run.state = "DONE"
    run.finished_at = utcnow()
    db.commit()
    return {"run_id": run.id, **summary, "state": run.state}


# ---------------------------------------------------------------- 冻结与提交
def _load_config_revision(db, tenant_id: str, config_release_id: str, *,
                          owner_id: str | None = None,
                          purpose: str = "send") -> ConfigRevision:
    """ConfigRevision 闭包校验（FIX3-04/G3-12）：tenant 一致（跨租户按不存在
    拒绝，不泄露存在性）、owner 绑定、state 校验——发送/构建 client 只接受
    PUBLISHED；查询旧提交允许 PUBLISHED/RETIRED（只读原身份，G3-14：已发送
    提交按原配置查询，不因配置退役而失明）。"""
    if not config_release_id:
        raise ApiError("NOT_FOUND", "empty config release id")
    cr = db.get(ConfigRevision, config_release_id)
    if cr is None or cr.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND",
                       f"pinned config release {config_release_id} missing "
                       "for this tenant")
    allowed = ("PUBLISHED", "RETIRED") if purpose == "query" else ("PUBLISHED",)
    if cr.state not in allowed:
        raise ApiError(
            "VERSION_CONFLICT",
            f"config revision {config_release_id} state={cr.state}; "
            f"{' or '.join(allowed)} required for {purpose}")
    if owner_id is not None and str(cr.owner_id) != str(owner_id):
        raise ApiError(
            "VERSION_CONFLICT",
            f"config revision {config_release_id} owned by {cr.owner_id!r}, "
            f"not bound to {owner_id!r}")
    return cr


def _submission_idem_key(dest_id: str, profile: str, bill_id: str,
                         source_revision: str, config_release_id: str) -> str:
    """幂等键含配置代次（FIX3-04/R3-06）：不同 config_release 是不同代提交；
    尾段哈希防止超长（source_revision 可达 128 字符）。"""
    import hashlib
    tail = hashlib.sha1(
        f"{bill_id}|{source_revision}|{config_release_id}"
        .encode("utf-8")).hexdigest()[:32]
    return f"p1:{dest_id}|{profile}|{tail}"


def prepare_submission(db, tenant_id: str, revision_id: str, *, profile: str,
                       destination_id: str,
                       config_release_id: str | None = None) -> dict:
    """幂等生成冻结包与 PREPARED Submission（不发送；DTR-B02 显式目标）。

    FIX2-02/R2-15：destination 的 profile key 必须与请求 profile 完全一致；
    重复 prepare（同配置代次）返回既有提交的真实 package_revision_id。

    FIX3-04/R3-06 修订（配置换代，G3-14）：
    - 幂等键含 config_release_id——配置漂移后重新 prepare 生成**新的不可变
      提交**（新冻结包固定新配置），不再永远返回旧包（旧“提示重新 prepare 却
      返回旧提交”死路已消除）。
    - 换代前提：旧代提交必须从未发送（PREPARED）——此时旧提交置 SUPERSEDED
      （不可再发送，payload 原文不动）；旧提交已发送（ACCEPTED/UNKNOWN/终态）
      则拒绝换代：已受理/结果未知的单据绝不按新配置盲重发，走查询/对账路径。
    """
    revision = db.get(DrugTraceBillRevision, revision_id)
    if revision is None or revision.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "bill revision not found")
    bill = db.get(DrugTraceBill, revision.bill_id)
    if bill is None or bill.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "bill not found")
    dest = db.get(Destination, destination_id)
    if dest is None or dest.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "destination not found")
    if profile not in C.PROFILES:
        raise ApiError("VALIDATION_FAILED", f"unknown profile {profile}")
    profile_key = (dest.profile_revision or "").split("@")[0]
    if profile_key != profile:
        raise ApiError(
            "CAPABILITY_UNSUPPORTED",
            f"destination profile {dest.profile_revision!r} does not match "
            f"requested profile {profile!r}; exact binding required")
    if config_release_id is None:
        config_release_id = dest.config_revision or ""
    if config_release_id:
        _load_config_revision(db, tenant_id, config_release_id,
                              owner_id=dest.id)
    idem = _submission_idem_key(dest.id, profile, revision.bill_id,
                                revision.source_revision, config_release_id)
    sub = (db.query(Submission)
           .filter_by(destination_id=dest.id, idempotency_key=idem).first())
    if sub is not None and sub.state != C.SUBMISSION_STATE_SUPERSEDED:
        # 既有同代提交：返回其真实冻结版本（不新建冻结包）
        return {"submission_id": sub.id,
                "package_revision_id": sub.package_revision_id,
                "created": False, "state": sub.state}
    # 同代旧提交已 SUPERSEDED：不可复用（幂等键占用）——须走换代检查
    if sub is not None:
        newer = (db.query(Submission)
                 .filter(Submission.destination_id == dest.id,
                         Submission.supersedes_submission == sub.id).first())
        if newer is None:
            raise ApiError(
                "VERSION_CONFLICT",
                "superseded submission has no successor generation; "
                "manual intervention required")
    # 换代检查：同 bill revision 在此 destination 下的既有代次提交
    # 换代检查（DTR-BATCH-FIX Fix1，2026-11-08）：定点单查询取代
    # 「载入该目的地全部 submission 再逐行 db.get(PackageRevision)」的 O(N) 扫描
    # ——生产 22,734 个提交时每次 prepare 逐行取包修订，package_revision 累计
    # 被索引扫描 2.61 亿次（=Σ1..22734），每单冻结成本随存量线性上涨，把夜间
    # 批次拖到 collect_timeout。过滤条件与取最新一条的语义与原循环逐字一致：
    # 同租户+同目的地+非 SUPERSEDED+冻结包指向本 bill revision，created_at 最大。
    try:
        prior = (db.query(Submission)
                 .join(PackageRevision,
                       PackageRevision.id == Submission.package_revision_id)
                 .filter(Submission.tenant_id == tenant_id,
                         Submission.destination_id == dest.id,
                         Submission.state != C.SUBMISSION_STATE_SUPERSEDED,
                         PackageRevision.provenance["bill_revision_id"]
                         .as_string() == revision.id)
                 .order_by(Submission.created_at.desc())
                 .first())
    except Exception:
        # 方言兜底（JSON 表达式不可用环境）：单次往返取回 (Submission, provenance)
        # 对，Python 侧过滤——仍无逐行 get，性能目标不变。
        db.rollback()
        prior = None
        pairs = (db.query(Submission, PackageRevision.provenance)
                 .join(PackageRevision,
                       PackageRevision.id == Submission.package_revision_id)
                 .filter(Submission.tenant_id == tenant_id,
                         Submission.destination_id == dest.id,
                         Submission.state != C.SUBMISSION_STATE_SUPERSEDED)
                 .all())
        for s, prov in pairs:
            if (prov or {}).get("bill_revision_id") == revision.id:
                if prior is None or (s.created_at or utcnow()) > \
                        (prior.created_at or utcnow()):
                    prior = s
    superseded_id = None
    if prior is not None:
        if prior.state != SubmissionState.PREPARED:
            raise ApiError(
                "VERSION_CONFLICT",
                f"bill revision already prepared and advanced to "
                f"{prior.state} under config "
                f"{(db.get(PackageRevision, prior.package_revision_id)
                    .provenance or {}).get('config_release_id', '')}; sent "
                "submissions are never re-issued under a new config — "
                "resolve via query/reconciliation")
        prior.state = C.SUBMISSION_STATE_SUPERSEDED   # 旧代未发送：显式退役
        prior.updated_at = utcnow()
        superseded_id = prior.id
    pr = payload_mod.freeze_package(db, tenant_id, revision, bill,
                                    profile=profile,
                                    config_release_id=config_release_id)
    sub = Submission(
        id=new_uuid(), tenant_id=tenant_id, package_revision_id=pr.id,
        destination_id=dest.id, submission_revision=1,
        snapshot_digest=pr.digest, state=SubmissionState.PREPARED,
        idempotency_key=idem,
        supersedes_submission=superseded_id)
    db.add(sub)
    db.flush()
    for code in (pr.payload.get("codes") or []):
        db.add(SubmissionItem(
            id=new_uuid(), tenant_id=tenant_id, submission_id=sub.id,
            item_key=f"code:{code.get('source_code_record_id','')}",
            data_ref={"trace_code": code.get("trace_code"),
                      "source_code_record_id": code.get("source_code_record_id")}))
    db.commit()
    return {"submission_id": sub.id, "package_revision_id": pr.id,
            "created": True, "state": sub.state,
            "superseded_submission_id": superseded_id}


def resolve_secret_default(ref: str) -> str:
    """默认密钥解析：环境变量映射（受限；生产用 SecretRef 后端注入）。

    `mock:` 前缀仅用于离线测试引用，且仅当 destination 为 mock 时可被引用
    （build_client 已拒绝真实目标使用 mock: 引用，R2-04）。真实密钥绝不
    入库/日志。
    """
    import os
    if ref.startswith("env:"):
        return os.environ.get(ref[4:], "")
    if ref.startswith("mock:"):
        return "__mock__" + ref[5:]
    return ""


# ---------------------------------------------------------------- 发送/查询（worker 侧）
def build_client(db, tenant_id: str, dest: Destination, *,
                 config_release_id: str | None = None,
                 profile: str = "", purpose: str = "send"):
    """按 destination 配置构建 adapter client（mock 与真实隔离）。

    FIX2-02 保留：真实目标优先读冻结时固定的 config_release（旧任务不读“最新”
    配置）；真实目标禁止 mock: SecretRef；RealTransport 挂载 mTLS 与主机白名单。

    FIX3-04/R3-06 修订（G3-12）：ConfigRevision 经 _load_config_revision 校验
    ——tenant 一致（跨租户 DRAFT 配置不可被本租户目标加载）、owner 绑定到该
    destination、发送用途仅 PUBLISHED（查询用途允许 PUBLISHED/RETIRED，旧提交
    按原身份只读查询）。profile 传入时校验 destination 绑定一致。
    """
    from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
    from destinations.alihealth_drugtrace.transport import MockTransport, RealTransport
    if profile:
        profile_key = (dest.profile_revision or "").split("@")[0]
        if profile_key != profile:
            raise ApiError(
                "CAPABILITY_UNSUPPORTED",
                f"destination profile {dest.profile_revision!r} does not match "
                f"requested profile {profile!r}; exact binding required")
    cfg = {}
    if config_release_id:
        cr = _load_config_revision(db, tenant_id, config_release_id,
                                   owner_id=dest.id, purpose=purpose)
        cfg = cr.content_json or {}
    elif dest.config_revision:
        cr = _load_config_revision(db, tenant_id, dest.config_revision,
                                   owner_id=dest.id, purpose=purpose)
        cfg = cr.content_json or {}
    if dest.is_mock:
        if not cfg.get("app_key"):
            cfg = dict(cfg)
            cfg["app_key"] = "mock-app-key"          # 离线测试身份（不作平台证据）
            cfg.setdefault("app_secret_ref", "mock:offline")
        # 离线 Mock 通道：官方响应形状（FIX2-01）；查询返回全部成功终态
        # （rt=3/spf=51）。脚本化结果不作平台确认证据（DTR-B03；MOCK 在 UI 标识）。
        from urllib.parse import parse_qs

        def _scripted(body: bytes):
            from destinations.alihealth_drugtrace import protocol as _P
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            method = (form.get("method") or [""])[0]
            key = _P.method_response_key(method)
            if method.endswith("query.billstatus"):
                bill_code = (form.get("bill_code") or [""])[0]
                payload = ('{"%s": {"result": {"response_success": true,'
                           ' "model": {"total_num": 1, "result_list": '
                           '{"bill_deal_status_search_do": ['
                           '{"bill_code": "%s", "result_type": "3",'
                           ' "sub_process_flag": "51"}]}}}}}'
                           % (key, bill_code)).encode("utf-8")
                return 200, payload
            return 200, ('{"%s": {"response_success": true,'
                         ' "model": "MOCK-ACCEPTED"}}' % key).encode("utf-8")

        transport = MockTransport(body_fn=_scripted)
    else:
        base = str(cfg.get("dmz_base_url", ""))
        if not base:
            raise ApiError("CAPABILITY_UNSUPPORTED",
                           "real destination requires dmz_base_url config")
        secret_ref = str(cfg.get("app_secret_ref", ""))
        if secret_ref.startswith("mock:") or not secret_ref:
            raise ApiError("CAPABILITY_UNSUPPORTED",
                           "real destination must reference a non-mock "
                           "SecretRef (mock: prefix forbidden)")
        allow_hosts = tuple(cfg.get("dmz_allow_hosts") or ()) or ()
        transport = RealTransport(
            base, ca_file=str(cfg.get("dmz_ca_file", "")),
            cert_file=str(cfg.get("dmz_client_cert_file", "")),
            key_file=str(cfg.get("dmz_client_key_file", "")),
            connect_timeout=float(cfg.get("connect_timeout", 5.0)),
            read_timeout=float(cfg.get("read_timeout", 20.0)),
            total_deadline=float(cfg.get("total_deadline", 60.0)),
            allow_hosts=allow_hosts)
    # FIN-05/FIN-A21：可选 session 授权——SecretRef 端到端（解析即注入，
    # 不落快照/日志/浏览器）。医院自用无需 session 时允许缺省（不强制）。
    # 仅实现 env:（受限服务环境注入）；file: 明确拒绝，不假装已支持。
    session = ""
    session_ref = str(cfg.get("session_ref", "") or "")
    if session_ref:
        if session_ref.startswith(("mock:", "env:")):
            resolved_session = resolve_secret_default(session_ref)
            session = resolved_session or ""
        else:
            raise ApiError(
                "CAPABILITY_UNSUPPORTED",
                f"session_ref {session_ref.split(':', 1)[0]!r}: not "
                "implemented; use env: injected via the restricted worker "
                "environment (FIN-05; plaintext session forbidden in "
                "snapshots/logs)")
    return AlihealthDrugTraceClient(
        transport, app_key=str(cfg.get("app_key", "")),
        app_secret_ref=str(cfg.get("app_secret_ref", "")),
        resolve_secret=resolve_secret_default,
        session=session)


# ---------------------------------------------------------------- 表单参数
def _top_bill_time(value) -> str:
    """业务完成时间 → TOP yyyy-MM-dd HH:mm:ss（GMT+8 墙钟，显式时区）。

    来源视图时间为 HIS 墙钟（GMT+8 约定，EXT-02 确认）；这里不做时区平移，
    只规范格式。值为空 → VALIDATION_FAILED（bill_time 必填，R2-01）。
    """
    if value in (None, ""):
        raise ApiError("VALIDATION_FAILED",
                       "bill_time required for TOP upload (R2-01)")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value).strip().replace("T", " ")
    return text[:19]


def build_biz_params(payload: dict) -> dict:
    """冻结 payload → TOP 业务表单参数（两 profile 独立 schema；FIX2-01）。

    - 公共必填：bill_code、bill_type、bill_time、trace_codes。
    - 零售（uploadretail）：ref_user_id（医疗机构用户标识；不得用 ref_ent_id
      顶替，R2-01）。
    - 库存（uploadinoutbill）：ref_user_id + physic_type + from_user_id +
      to_user_id + client_type（官方必填，FIX2-01）。
    - 内部字段（_doc/supplier_ref/warehouse_code 等）绝不透传；患者可选字段
      仅在批准映射存在时输出。
    """
    profile = payload.get("profile", "")
    codes = ",".join(c.get("trace_code", "") for c in payload.get("codes", []))
    biz = {
        "bill_code": str(payload.get("bill_code", "")),
        "bill_type": str(payload.get("bill_type", "")),
        "bill_time": _top_bill_time(payload.get("bill_time")),
        "trace_codes": codes,
    }
    if profile == C.PROFILE_RETAIL:
        biz["ref_user_id"] = str(payload.get("ref_user_id", ""))
    elif profile == C.PROFILE_INOUT:
        biz["ref_user_id"] = str(payload.get("ref_user_id", ""))
        biz["physic_type"] = str(payload.get("physic_type", ""))
        biz["from_user_id"] = str(payload.get("from_user_id", ""))
        biz["to_user_id"] = str(payload.get("to_user_id", ""))
        biz["client_type"] = str(payload.get("client_type", ""))
    else:
        raise ApiError("CAPABILITY_UNSUPPORTED", f"unknown profile {profile!r}")
    missing = [k for k, v in biz.items() if v == "" and k != "trace_codes"]
    if missing:
        raise ApiError("VALIDATION_FAILED",
                       f"TOP required upload parameters missing: {missing} "
                       "(dictionary mapping or source field absent)")
    if profile == C.PROFILE_RETAIL and payload.get("patient_ref"):
        # 患者可选字段：仅在批准映射（冻结 payload 已解析）存在时输出
        biz["patient_ref"] = str(payload["patient_ref"])
    return biz


# ---------------------------------------------------------------- 查询代次
QUERY_MAX_POLLS = 12            # 查询代次上限：达到即告警转人工（不无限 poll）


def _enqueue_query_job(db, tenant_id: str, submission_id: str, *,
                       delay_seconds: int = 0,
                       max_polls: int = QUERY_MAX_POLLS) -> object:
    from workflow_core.jobs import enqueue_job
    from core_db.models import Job
    from contracts import JobState
    prior = (db.query(Job)
             .filter(Job.tenant_id == tenant_id,
                     Job.job_type == C.DomainJobType.QUERY,
                     Job.dedup_key.like(f"q:{submission_id}:%")).count())
    if prior >= max_polls:
        return None                      # 轮询上限：转人工（不无限 poll）
    gen = prior + 1
    job = enqueue_job(db, tenant_id, C.DomainJobType.QUERY,
                      f"q:{submission_id}:{gen}",
                      {"submission_id": submission_id, "generation": gen},
                      priority=60)
    if delay_seconds > 0:
        job.available_at = utcnow() + timedelta(seconds=delay_seconds)
    db.commit()
    return job


def _slim(d: dict, limit: int = 60) -> dict:
    """响应快照瘦身（不存完整码串/患者信息；日志脱敏口径一致）。"""
    import json
    s = json.dumps(d, ensure_ascii=False, default=str)
    return {"keys": sorted(d.keys())[:limit], "size": len(s)}


# ---------------------------------------------------------------- 发送
def _send_state_guard(submission) -> None:
    """统一发送状态守卫：仅 PREPARED 可发起发送（R2-06）。"""
    if submission.state != SubmissionState.PREPARED:
        raise ApiError(
            "VERSION_CONFLICT",
            f"submission state {submission.state} not sendable; only PREPARED "
            "may start a send (UNKNOWN/SENDING must query, CONFIRMED never "
            "resends)")


def perform_send(db, tenant_id: str, submission: Submission,
                 *, dry_run: bool = False) -> dict:
    """执行发送：状态守卫 → dry-run 零副作用分支 → 证据/范围/配置闭包校验 →
    持久化 SENDING 尝试（CAS）→ 网络 I/O → CAS 结果落库。

    - 发送前证据门禁（send capability + 显式 destination + 配置闭包一致）。
    - EXT-12 scope：单张 bill_time 必须落在批准窗口内。
    - UNKNOWN：自动排查询 job（先查询，不盲重发；DTR-B05）。
    - dry_run：在任何 client/网络/业务写入之前返回（不建 DeliveryAttempt、
      不改 Submission 状态，R2-06）。
    """
    dest = db.get(Destination, submission.destination_id)
    if dest is None:
        raise ApiError("NOT_FOUND", "destination missing")
    pr = db.get(PackageRevision, submission.package_revision_id)
    profile = (pr.schema_ref or {}).get("profile", "")
    payload = pr.payload or {}
    _send_state_guard(submission)               # PREPARED-only（含 dry-run）
    if dry_run:
        # 零副作用预览：不建 client、不建尝试、不改状态（R2-06）
        return {"outcome": "DRY_RUN", "biz_params": build_biz_params(payload),
                "submission_state": submission.state, "destination": dest.id}
    pinned_config = str((pr.provenance or {}).get("config_release_id") or "")
    # 表单参数先行构建：任何 VALIDATION_FAILED 都发生在配额预留之前
    biz = build_biz_params(payload)
    # 证据门禁（Mock 离线通道除外；安全属性先于一切预检）
    if not dest.is_mock:
        state = evidence_state(db, tenant_id, profile=profile,
                               capability="send", destination_id=dest.id,
                               config_release_id=pinned_config or None)
        require_production_evidence(db, tenant_id, profile=profile,
                                    capability="send", destination_id=dest.id,
                                    config_release_id=pinned_config or None)
        # EXT-12 scope 逐单复核（窗口/药房白名单，G3-10/G3-11）
        _check_send_scope(state.get("scope"), payload)
        # 配置闭包校验：destination 当前配置不得漂移出冻结时的 release
        if pinned_config and dest.config_revision and \
                str(dest.config_revision) != pinned_config:
            raise ApiError(
                "VERSION_CONFLICT",
                "destination config changed since prepare; re-prepare with "
                "the new config release (config closure pinning, R2-05)")
    # 来源 revision 未变校验（来源更正后旧提交不再发送）
    brid = (pr.provenance or {}).get("bill_revision_id")
    if brid:
        rev = db.get(DrugTraceBillRevision, brid)
        bill = db.get(DrugTraceBill, rev.bill_id) if rev else None
        if bill is not None and bill.current_revision_id != brid:
            raise ApiError(
                "VERSION_CONFLICT",
                "source revision changed; re-prepare required before send")
    if not dest.is_mock:
        # Known cooldowns must not consume a send quota or create an attempt.
        from destinations.alihealth_drugtrace.rate_control import preflight_config
        config = _load_config_revision(db, tenant_id, pinned_config or dest.config_revision,
                                       owner_id=dest.id, purpose='send')
        preflight_config(config.content_json or {}, C.PROFILES[profile]['method'])
    # 配额并发预留：所有可本地判定的拒绝都已完成，紧邻 SENDING CAS 提交——
    # 预留与发送尝试同事务（失败尝试也占额度：保守计数防并发窗口超发，G3-11）
    if not dest.is_mock:
        _reserve_send_quota(db, tenant_id, state["evidence_id"],
                            int(state["scope"]["max_bills"]))
    # 持久化 SENDING 尝试（网络 I/O 之前；CAS PREPARED→SENDING，R2-06）
    attempt_no = 1 + db.query(DeliveryAttempt).filter_by(
        submission_id=submission.id).count()
    attempt = DeliveryAttempt(
        id=new_uuid(), tenant_id=tenant_id, submission_id=submission.id,
        attempt_no=attempt_no, request_digest=digest_of(biz)[:64],
        started_at=utcnow())
    db.add(attempt)
    db.flush()
    res = db.execute(
        update(Submission)
        .where(Submission.id == submission.id,
               Submission.state == SubmissionState.PREPARED)
        .values(state=SubmissionState.SENDING,
                submission_revision=Submission.submission_revision + 1,
                updated_at=utcnow()))
    db.commit()
    if res.rowcount != 1:
        db.refresh(submission)
        raise ApiError("VERSION_CONFLICT",
                       f"send lost CAS race (state={submission.state}); "
                       "another worker owns this submission")
    db.refresh(submission)
    cas_rev = submission.submission_revision
    # client 构建与发送：区分「明确未发出」与「结果未知」（FIX2-04）
    # - 本地确定性错误（签名未验签/方法不支持/证书缺失）：请求必然未发出 →
    #   回退 PREPARED 并抛出（可恢复尝试已留痕）。
    # - 网络/SDK 运行时异常：结果未知 → UNKNOWN + 先查询。
    def _revert_to_prepared():
        db.execute(
            update(Submission)
            .where(Submission.id == submission.id,
                   Submission.state == SubmissionState.SENDING,
                   Submission.submission_revision == cas_rev)
            .values(state=SubmissionState.PREPARED,
                    submission_revision=Submission.submission_revision + 1,
                    updated_at=utcnow()))
        db.commit()
        db.refresh(submission)

    try:
        client = build_client(db, tenant_id, dest,
                              config_release_id=pinned_config or None,
                              profile=profile)
        method = C.PROFILES[profile]["method"] if profile in C.PROFILES \
            else payload.get("method")
        if method == C.METHOD_UPLOAD_INOUT:
            result = client.upload_inout(biz)
        elif method == C.METHOD_UPLOAD_RETAIL:
            result = client.upload_retail(biz)
        else:
            raise ApiError("CAPABILITY_UNSUPPORTED",
                           f"upload method unknown: {method}")
    except RequestDeferred:
        # A peer may install a cooldown after our preflight. No HTTP request
        # was made; preserve the attempt and conservatively reserved quota.
        attempt.ended_at = utcnow()
        attempt.outcome = 'BLOCKED_LOCAL'
        attempt.response_snapshot = {'error': {'reason': 'shared-cooldown-before-request'}}
        db.commit()
        _revert_to_prepared()
        raise
    except (ApiError, CapabilityError) as exc:
        # 明确未发出：留痕 + 回退 PREPARED（不产生 UNKNOWN/查询副作用）
        attempt.ended_at = utcnow()
        attempt.outcome = "BLOCKED_LOCAL"
        attempt.response_snapshot = {"error": {
            "reason": f"local-blocked:{type(exc).__name__}",
            "detail": str(getattr(exc, "message", exc))[:200]}}
        db.commit()
        _revert_to_prepared()
        raise
    except Exception as exc:  # noqa: BLE001 —— SDK/证书/网络运行时异常 → UNKNOWN
        result = {"outcome": "UNKNOWN",
                  "error": {"reason": f"send-failure: "
                                      f"{type(exc).__name__}"}}
    attempt.ended_at = utcnow()
    attempt.http_status = result.get("http_status")
    attempt.response_snapshot = {"outcome": result.get("outcome"),
                                 "error": result.get("error", {}),
                                 "parsed": _slim(result.get("parsed", {}))}
    if result.get('rate_limit'):
        attempt.response_snapshot['rate_limit'] = result['rate_limit']
    outcome = result.get("outcome")
    if outcome == "ACCEPTED":
        final_state = SubmissionState.ACCEPTED
        attempt.outcome = "ACCEPTED"
    elif outcome == "PARTIAL":
        final_state = SubmissionState.PARTIAL
        attempt.outcome = "PARTIAL"
    elif outcome == "REJECTED":
        final_state = SubmissionState.REJECTED
        attempt.outcome = "REJECTED"
    else:
        final_state = SubmissionState.UNKNOWN
        attempt.outcome = "UNKNOWN"
    # CAS 终态写入：仍持有 cas_rev 才允许（lease fencing，R2-06）
    res2 = db.execute(
        update(Submission)
        .where(Submission.id == submission.id,
               Submission.state == SubmissionState.SENDING,
               Submission.submission_revision == cas_rev)
        .values(state=final_state,
                submission_revision=Submission.submission_revision + 1,
                updated_at=utcnow()))
    db.commit()
    db.refresh(submission)
    if res2.rowcount != 1:
        # 失租：不覆盖新状态、不再外发；留查询路径恢复
        return {"outcome": outcome, "fenced": True,
                "state": submission.state}
    if final_state == SubmissionState.ACCEPTED:
        # FIX3-03/G3-07：受理即自动排第一代查询——不点 UI 也能推进终态
        _enqueue_query_job(db, tenant_id, submission.id, delay_seconds=0 if dest.is_mock else 120)
    elif final_state == SubmissionState.UNKNOWN:
        emit_outbox(db, tenant_id, f"drug_trace_submission:{submission.id}",
                    C.DomainEvent.SUBMISSION_UNKNOWN,
                    {"submission_id": submission.id, "profile": profile})
        db.commit()
        _enqueue_query_job(db, tenant_id, submission.id, delay_seconds=0 if dest.is_mock else 300)
    return result


# ---------------------------------------------------------------- 查询
QUERY_PAGE_SIZE = 50
QUERY_MAX_PAGES = 50            # 全分页保护上限：超出按未核验处理
QUERY_TOTAL_BUDGET_SECONDS = 120  # 单次全分页总时间预算（慢平台保护）
QUERY_BACKOFF_SECONDS = 60      # PROCESSING 下一代轮询退避
QUERY_NOT_FOUND_BACKOFF_SECONDS = 300  # 传播延迟重查退避（受限代次）
QUERY_ERROR_BACKOFF_SECONDS = 120      # 平台错误/临时失败重查退避


def _query_dates(payload: dict, fallback: datetime) -> tuple[str, str]:
    """单窗口查询日（G3-02 原行为保留）：冻结 bill_time（GMT+8）±1 天；
    bill_time 缺失回退提交创建时刻。"""
    try:
        base = _to_utc(payload.get("bill_time"), naive_tz=_GMT8) \
            if payload.get("bill_time") else _to_utc(fallback, naive_tz=_GMT8)
    except ValueError:
        base = _to_utc(fallback, naive_tz=_GMT8)
    begin = (base - timedelta(days=1)).astimezone(_GMT8).date().isoformat()
    end = (base + timedelta(days=1)).astimezone(_GMT8).date().isoformat()
    return begin, end


# FIN-05/FIN-A22：平台查询日期语义（查询按“上传创建日期”还是“业务日期”）
# 官方口径未确认（EXT-08/09）。在确认前**默认 both**：业务日期窗 + 上传日期窗
# 都查并合并结果（宁可多查不可漏查）；官方确认后经批准配置把
# query_date_semantics 固定为 bill_time / upload_time（落 ConfigRevision，
# 不改代码常量绕过）。历史补报单据 bill_time 与上传日期相差数月——单窗口
# 语义下会漏查（FIN-A22 反例）。
QUERY_DATE_SEMANTICS = ("both", "bill_time", "upload_time")


def _query_date_semantics(cfg_content: dict | None) -> str:
    v = str((cfg_content or {}).get("query_date_semantics") or "both")
    if v not in QUERY_DATE_SEMANTICS:
        raise ApiError(
            "VALIDATION_FAILED",
            f"query_date_semantics {v!r} not in {QUERY_DATE_SEMANTICS}; "
            "official confirmation required before narrowing (EXT-08/09)")
    return v


def _query_windows(payload: dict, fallback: datetime,
                   semantics: str = "both") -> list[tuple[str, str]]:
    """按语义返回查询窗口列表（去重保序）。"""
    windows: list[tuple[str, str]] = []
    if semantics in ("both", "bill_time"):
        windows.append(_query_dates(payload, fallback))
    if semantics in ("both", "upload_time"):
        upload = _query_dates({"bill_time": None}, fallback)
        if upload not in windows:
            windows.append(upload)
    # Identical or intersecting inclusive date ranges have identical coverage
    # as their union. Keep disjoint historic/current windows separate.
    if len(windows) == 2 and max(w[0] for w in windows) <= min(w[1] for w in windows):
        return [(min(w[0] for w in windows), max(w[1] for w in windows))]
    return windows


def _query_full_windows(client, base_params: dict, payload: dict,
                        fallback: datetime, semantics: str = "both") -> dict:
    """多窗口全分页查询 + 合并（FIN-A22）：任一窗 ERROR/UNKNOWN → 保守传播；
    OK 窗口按 bill_code 合并（同码取首个含明细条目），窗口数记入结果。"""
    results = []
    for begin, end in _query_windows(payload, fallback, semantics):
        full = _platform_query_full(client, {**base_params,
                                             "begin_date": begin,
                                             "end_date": end})
        full = dict(full)
        full["_window"] = [begin, end]
        results.append(full)
        if full.get("status") != "OK":
            break        # 保守：后续窗口不查，错误/未知优先传播
    bad = [r for r in results if r.get("status") != "OK"]
    if bad:
        out = dict(bad[0])
        out["windows_queried"] = len(results)
        return out
    merged: list[dict] = []
    seen: dict[str, dict] = {}
    total = None
    for r in results:
        for e in (r.get("entries") or []):
            key = str(e.get("bill_code", ""))
            if key not in seen:
                seen[key] = e
                merged.append(e)
        if r.get("total_num") is not None:
            total = max(total or 0, int(r["total_num"]))
    return {"status": "OK", "entries": merged, "total_num": total,
            "pages_read": sum(r.get("pages_read") or 0 for r in results),
            "windows_queried": len(results)}


def _platform_query_full(client, base_params: dict, *,
                         page_size: int = QUERY_PAGE_SIZE,
                         max_pages: int = QUERY_MAX_PAGES,
                         budget_seconds: float = QUERY_TOTAL_BUDGET_SECONDS) -> dict:
    """只读全分页查询（FIX3-03/05；perform_query 与 run_reconciliation 共用，
    G3-08/09/15——同一客户端行为，不能一条修好另一条仍 page1/无日期）。

    返回 {status: OK|ERROR|UNKNOWN, entries, total_num, pages_read,
    error?/reason?}：
    - OK：全部分页读满（total 读满或短页收尾）——entries 可用于判定。
    - ERROR：平台明确错误（error_response/权限/参数）——绝不等价 NOT_FOUND。
    - UNKNOWN：网络/形状/超页/预算/重复页——任何一页失败即丢弃已收集半份数据。
    无 total_num 的满页继续 page+1；重复首页（平台忽略 page 参数）判不推进。
    """
    started = time.monotonic()
    entries: list[dict] = []
    total = None
    page = 1
    pages_read = 0
    prev_first = None
    while page <= max_pages:
        if time.monotonic() - started > budget_seconds:
            return {"status": "UNKNOWN", "entries": [], "total_num": None,
                    "pages_read": pages_read,
                    "reason": "query time budget exceeded"}
        try:
            result = client.query_bill_status({
                **base_params, "page": str(page), "page_size": str(page_size)})
        except RequestDeferred:
            raise  # No request: release this same job; do not burn a query generation.
        except Exception as exc:  # noqa: BLE001 —— 传输异常=未核验（退避重查）
            return {"status": "UNKNOWN", "entries": [], "total_num": None,
                    "pages_read": pages_read,
                    "reason": f"query-failure:{type(exc).__name__}"}
        pages_read = page
        if result.get('rate_limit'):
            return {'status': 'RATE_LIMITED', 'entries': [], 'total_num': None,
                    'pages_read': pages_read, 'rate_limit': result['rate_limit'],
                    'error': result.get('error') or {}}
        if result.get("unknown"):
            return {"status": "UNKNOWN", "entries": [], "total_num": None,
                    "pages_read": pages_read,
                    "reason": str(result.get("reason", "platform-unknown"))}
        if result.get("error") and result.get("found") is not True:
            # 平台明确错误（权限/参数/业务拒绝）：保留 error 原文（R3-03 反例
            # 中错误被清空并改判 NOT_FOUND——已消除）
            return {"status": "ERROR", "entries": [], "total_num": None,
                    "pages_read": pages_read, "error": result.get("error")}
        page_entries = [e for e in
                        ((result.get("bills") or {}).get("list") or [])
                        if isinstance(e, dict)]
        first = str(page_entries[0].get("bill_code", "")) if page_entries else ""
        if page > 1 and first and first == prev_first \
                and len(page_entries) == page_size and total is None:
            return {"status": "UNKNOWN", "entries": [], "total_num": None,
                    "pages_read": pages_read,
                    "reason": "pagination not advancing (duplicate page)"}
        if first:
            prev_first = first
        entries.extend(page_entries)
        if total is None and result.get("total_num") is not None:
            total = int(result["total_num"])
        if total is not None:
            if len(entries) >= total:
                break                       # total 读满：全部分页完成
        elif len(page_entries) < page_size:
            break                           # 无 total：短页=末页
        page += 1                            # 满页必推进（含缺 total 满页）
    else:
        return {"status": "UNKNOWN", "entries": [], "total_num": None,
                "pages_read": pages_read,
                "reason": f"query exceeded {max_pages} pages; unverified"}
    # 按单号查询却观测到重复条目/总数与可观测去重数不符 → 平台分页不自洽，
    # 视为未核验（G3-15：total=2 只能观测 1 个不同单号不得判成功）
    distinct = {str(e.get("bill_code", "")) for e in entries}
    if total is not None and len(distinct) < total:
        return {"status": "UNKNOWN", "entries": [], "total_num": total,
                "pages_read": pages_read,
                "reason": f"pagination incomplete: total_num {total} > "
                          f"distinct observed bills {len(distinct)}"}
    return {"status": "OK", "entries": entries, "total_num": total,
            "pages_read": pages_read}


def _classify_query(result: dict, bill_code: str) -> dict:
    """查询结果 → 业务状态归一（FIX3-03 修订，G3-08）。

    - UNKNOWN（网络/形状/超页/预算/重复页）→ UNKNOWN（退避重查）。
    - ERROR（平台明确错误：权限/参数）→ QUERY_ERROR 并保留 error 原文——
      绝不改判 NOT_FOUND（R3-03 反例）。
    - OK 且无匹配条目 → NOT_FOUND：完整成功查询确实无此单（传播延迟由上层
      受限退避重查，不静默结束）。
    - OK 恰一条匹配 → 官方状态映射（全部成功才 CONFIRMED；部分成功 PARTIAL；
      处理中 PROCESSING；未知组合 MANUAL_REVIEW 不猜）；多条匹配 → MANUAL_REVIEW。
    兼容直接传入 parse_query_response 形状（found/bills/unknown/error）。
    """
    status = result.get("status")
    if status is None:
        if result.get("unknown"):
            status = "UNKNOWN"
        elif result.get("error") and result.get("found") is not True:
            status = "ERROR"
        elif result.get("found") is True:
            status = "OK"
        else:
            status = "ERROR"
    if status == "UNKNOWN":
        return {"state": "UNKNOWN", "reason": result.get("reason", "unknown")}
    if status == 'RATE_LIMITED':
        return {'state': 'QUERY_ERROR', 'error': result.get('error') or {},
                'rate_limit': result['rate_limit']}
    if status == "ERROR":
        return {"state": "QUERY_ERROR",
                "error": result.get("error")
                or {"reason": result.get("reason", "platform error")}}
    entries = result.get("entries")
    if entries is None:
        entries = (result.get("bills") or {}).get("list") or []
    matched = [e for e in entries if isinstance(e, dict)
               and str(e.get("bill_code", "")) == bill_code]
    if len(matched) == 0:
        return {"state": "NOT_FOUND",
                "reason": "complete platform query has no entry for bill_code"}
    if len(matched) > 1:
        return {"state": "MANUAL_REVIEW",
                "reason": f"matched {len(matched)} entries for bill_code; "
                          f"not guessing"}
    entry = matched[0]
    rt = str(entry.get("result_type", ""))
    spf = str(entry.get("sub_process_flag", ""))
    state = C.lookup_query_status(rt, spf)
    out = {"state": state, "result_type": rt, "sub_process_flag": spf}
    if state == "PARTIAL":
        out["partial_detail"] = entry      # 部分成功明细保存（FIX2-01）
    return out


def _record_query_receipt(db, tenant_id, dest_id, submission_id, gen: int,
                          raw: dict, parsed: dict, *, alert: bool = False):
    """回执按「查询代次+结果 digest」幂等保留每次状态变化；轮询上限告警单列。"""
    digest8 = digest_of(parsed)[:8]
    receipt_id = (f"alert:{submission_id}:{gen}" if alert else
                  f"query:{submission_id}:{gen}:{digest8}")
    existing = (db.query(ReceiptEvent)
                .filter_by(tenant_id=tenant_id, destination_id=dest_id,
                           external_receipt_id=receipt_id).first())
    if existing is None:
        db.add(ReceiptEvent(
            id=new_uuid(), tenant_id=tenant_id, destination_id=dest_id,
            external_receipt_id=receipt_id, submission_ref=submission_id,
            raw_payload=raw, parsed_result=parsed))


def _query_generation(db, tenant_id: str, submission_id: str) -> int:
    from core_db.models import Job
    return 1 + db.query(Job).filter(
        Job.tenant_id == tenant_id, Job.job_type == C.DomainJobType.QUERY,
        Job.dedup_key.like(f"q:{submission_id}:%")).count()


def perform_query(db, tenant_id: str, submission: Submission, *,
                  client=None) -> dict:
    """主动查询平台状态（全分页）并归一落库（UNKNOWN 恢复/受理确认）。

    FIX3-03 修订（G3-07/08/09）：
    - 查询身份固定为冻结 payload（ref_ent_id/bill_code/query_bill_type 独立
      选择器）+ 冻结时 config_release（只读原身份，purpose=query 允许 RETIRED）。
    - begin/end_date 按冻结 bill_time（GMT+8）±1 天，时区口径显式。
    - 全分页共用 _platform_query_full：权限错误→QUERY_ERROR（非 NOT_FOUND）、
      无 total 满页推进、重复页/预算保护；PROCESSING/UNKNOWN/QUERY_ERROR/
      NOT_FOUND（传播延迟）均受限退避排下一代，达到上限显式告警转人工。
    """
    from destinations.alihealth_drugtrace.protocol import parse_query_response
    dest = db.get(Destination, submission.destination_id)
    pr = db.get(PackageRevision, submission.package_revision_id)
    profile = (pr.schema_ref or {}).get("profile", "")
    payload = pr.payload or {}
    pinned_config = str((pr.provenance or {}).get("config_release_id") or "")
    if client is None:
        if not dest.is_mock:
            require_production_evidence(db, tenant_id, profile=profile,
                                        capability="query",
                                        destination_id=dest.id)
        client = build_client(db, tenant_id, dest,
                              config_release_id=pinned_config or None,
                              profile=profile, purpose="query")
    bill_code = str(payload.get("bill_code", ""))
    query_bill_type = str(payload.get("query_bill_type", ""))
    if not query_bill_type:
        raise ApiError(
            "DICTIONARY_UNMAPPED",
            "frozen payload lacks query_bill_type (FIX3-01 selector); "
            "re-prepare required before platform query")
    # FIN-A22：查询日期语义——官方确认前默认 both（业务+上传双窗口合并）；
    # 确认后经批准 ConfigRevision 固定（query_date_semantics）
    semantics = "both"
    if not dest.is_mock:
        try:
            pinned_cr = _load_config_revision(
                db, tenant_id, pinned_config or dest.config_revision or "",
                owner_id=dest.id, purpose="query")
            semantics = _query_date_semantics(pinned_cr.content_json)
        except ApiError:
            semantics = "both"       # 无可读配置 → 最保守双窗口
    full = _query_full_windows(client, {
        "ref_ent_id": str(payload.get("ref_ent_id", "")),
        "bill_code": bill_code,
        "bill_type": query_bill_type},
        payload, submission.created_at or utcnow(), semantics)
    cls = _classify_query(full, bill_code)
    gen = _query_generation(db, tenant_id, submission.id)
    _record_query_receipt(
        db, tenant_id, dest.id, submission.id, gen,
        {"query_status": full.get("status"), "unknown":
         full.get("status") == "UNKNOWN", "total_num": full.get("total_num"),
         "pages_read": full.get("pages_read"), "generation": gen},
        cls)
    # 状态推进（只前进不回退：CONFIRMED 不被乱序旧回执覆盖）
    state_now = submission.state
    target = cls.get("state")
    if target == "CONFIRMED" and state_now != SubmissionState.REJECTED:
        submission.state = SubmissionState.CONFIRMED
    elif target == "REJECTED" and state_now in (
            SubmissionState.UNKNOWN, SubmissionState.SENDING,
            SubmissionState.PREPARED, SubmissionState.ACCEPTED):
        # 官方 result_type=4 处理失败（终态；FB-02 补全）：只前进，不盲重发
        submission.state = SubmissionState.REJECTED
    elif target == "PARTIAL" and state_now in (
            SubmissionState.UNKNOWN, SubmissionState.SENDING,
            SubmissionState.PREPARED, SubmissionState.ACCEPTED):
        submission.state = SubmissionState.PARTIAL
    elif target == "ACCEPTED" and state_now in (
            SubmissionState.UNKNOWN, SubmissionState.SENDING,
            SubmissionState.PREPARED):
        submission.state = SubmissionState.ACCEPTED
    elif target in ("PROCESSING", "UNKNOWN") and state_now in (
            SubmissionState.UNKNOWN, SubmissionState.SENDING,
            SubmissionState.PREPARED):
        if state_now == SubmissionState.PREPARED:
            submission.state = SubmissionState.SENDING   # 在途可观测状态
    # QUERY_ERROR：不改判 NOT_FOUND、不降级任何状态（回执已留证据）
    db.commit()
    # 受限退避：PROCESSING/临时失败/传播延迟 NOT_FOUND 都重查，达到上限转人工
    backoff = {"PROCESSING": QUERY_BACKOFF_SECONDS,
               "UNKNOWN": QUERY_BACKOFF_SECONDS,
               "QUERY_ERROR": QUERY_ERROR_BACKOFF_SECONDS,
               "NOT_FOUND": QUERY_NOT_FOUND_BACKOFF_SECONDS}.get(target)
    if backoff is not None:
        import random
        backoff = min(1800, backoff * 2 ** min(max(0, gen - 1), 5))
        backoff = max(backoff, float((cls.get('rate_limit') or {}).get('retry_after') or 0))
        backoff += random.uniform(0, min(30, backoff * .1))
        job = _enqueue_query_job(db, tenant_id, submission.id,
                                 delay_seconds=backoff)
        if job is None:
            # 轮询上限：显式告警转人工（不静默结束；G3-08/09）
            _record_query_receipt(
                db, tenant_id, dest.id, submission.id, gen,
                {"generation": gen, "poll_limit": True},
                {"state": "MANUAL_REVIEW",
                 "reason": "query poll limit reached; manual review required"},
                alert=True)
            emit_outbox(db, tenant_id,
                        f"drug_trace_submission:{submission.id}",
                        C.DomainEvent.SUBMISSION_ALERT,
                        {"submission_id": submission.id,
                         "reason": "query poll limit reached",
                         "generation": gen})
            db.commit()
    return {"classification": cls, "generation": gen,
            "query_status": full.get("status"),
            "pages_read": full.get("pages_read"),
            "total_num": full.get("total_num")}


# ---------------------------------------------------------------- 对账
def run_reconciliation(db, tenant_id: str, *, destination_id: str, profile: str,
                       window_begin, window_end, pharmacy_code: str = "",
                       client=None) -> dict:
    """三方对账：来源快照 vs 本地提交 vs 独立平台查询快照（FIX2-05 → FIX3-05）。

    FIX3-05 修订（G3-15/16/17）：
    - 平台查询与 perform_query 复用同一 _platform_query_full（全分页 + 日期 +
      身份固定）——不能一条修好、另一条仍 page1/无日期。
    - 分页不完整（UNKNOWN）或平台明确错误（ERROR）→ 整轮 UNVERIFIED：绝不
      missing_remote/matched/可补报（total=2 只见 1 项不得判 matched）。
    - 查询身份/配置按各提交冻结 payload + 冻结 config_release（多目标/多配置
      各用各的身份；purpose=query 允许已 RETIRED 的历史配置）。
    - 无经确认的平台范围枚举能力：明确声明「仅本地已知单据逐单核对，未检查
      平台额外单据」（remote_extra_checked=False）；missing_local 只登记待人工
      处置，绝不自动补发（补报须完整独立证据 + 新批准）。
    - 码级核对依赖平台码级明细接口（EXT-09 未确认）：code_level_verified=False。
    """
    require_production_evidence(db, tenant_id, profile=profile,
                                capability="reconcile",
                                destination_id=destination_id)
    dest = db.get(Destination, destination_id)
    if dest is None or dest.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "destination not found")
    profile_key = (dest.profile_revision or "").split("@")[0]
    if profile_key != profile:
        raise ApiError("CAPABILITY_UNSUPPORTED",
                       f"destination profile {dest.profile_revision!r} does not "
                       f"match reconciliation profile {profile!r}")
    kinds = C.PROFILES[profile]["business_kinds"]
    q = (db.query(DrugTraceBill)
         .filter(DrugTraceBill.tenant_id == tenant_id,
                 DrugTraceBill.business_kind.in_(kinds))
         .order_by(DrugTraceBill.pharmacy_code, DrugTraceBill.source_bill_no))
    if pharmacy_code:
        q = q.filter(DrugTraceBill.pharmacy_code == pharmacy_code)
    run = ReconciliationRun(
        id=new_uuid(), tenant_id=tenant_id, destination_id=destination_id,
        scope={"domain": "drug_trace", "profile": profile,
               "window_begin": window_begin.isoformat() if window_begin else None,
               "window_end": window_end.isoformat() if window_end else None,
               "pharmacy_code": pharmacy_code,
               "code_level_verified": False,
               # 无范围枚举能力：显式声明未检查平台额外单据（G3-16）
               "remote_extra_checked": False,
               "remote_extra_note": "仅本地已知单据逐单核对；未经确认的范围"
                                    "枚举能力，未检查平台额外单据"},
        state="RUNNING")
    db.add(run)
    db.flush()
    counts = {"missing_local": 0, "missing_remote": 0, "state_mismatch": 0,
              "matched": 0, "count_mismatch": 0, "unverified_remote": 0}
    # 本地提交索引：bill_revision_id → Submission（同 revision 取最新提交）
    submission_by_revision: dict[str, Submission] = {}
    for sub in db.query(Submission).filter_by(
            tenant_id=tenant_id, destination_id=destination_id).all():
        pr2 = db.get(PackageRevision, sub.package_revision_id)
        if pr2 is None:
            continue
        brid = (pr2.provenance or {}).get("bill_revision_id")
        if not brid:
            continue
        cur = submission_by_revision.get(brid)
        if cur is None or sub.created_at > cur.created_at:
            submission_by_revision[brid] = sub
    # 平台查询快照：每单全分页（与 perform_query 同一客户端/身份契约）
    remote_snapshot: dict[str, dict] = {}
    platform_ok = True
    remote_error = ""
    client_cache: dict[str, object] = {}

    def _client_for(config_id: str):
        if client is not None:
            return client                    # 测试注入的只读客户端
        key = str(config_id or "")
        if key not in client_cache:
            client_cache[key] = build_client(
                db, tenant_id, dest, config_release_id=key or None,
                profile=profile, purpose="query")
        return client_cache[key]

    bills = q.all()
    if platform_ok:
        for bill in bills:
            rev = db.get(DrugTraceBillRevision, bill.current_revision_id) \
                if bill.current_revision_id else None
            if rev is None:
                continue
            if window_begin and rev.event_time and rev.event_time < window_begin:
                continue
            if window_end and rev.event_time and rev.event_time >= window_end:
                continue
            sub = submission_by_revision.get(rev.id)
            if sub is None:
                continue            # 无本地提交的对账走 missing_local，不查平台
            payload = (db.get(PackageRevision, sub.package_revision_id)
                       .payload or {})
            bill_code = str(payload.get("bill_code", ""))
            if not bill_code or not payload.get("query_bill_type"):
                continue
            pinned = str((db.get(PackageRevision, sub.package_revision_id)
                          .provenance or {}).get("config_release_id") or "")
            try:
                cl = _client_for(pinned)
            except Exception as exc:  # noqa: BLE001 —— 身份配置失效=未核验
                platform_ok = False
                remote_error = f"client-build-failure: {type(exc).__name__}"
                break
            # FIN-A22：与 perform_query 同一日期语义（默认 both；批准配置固定）
            semantics = "both"
            if not dest.is_mock:
                try:
                    pinned_cr = _load_config_revision(
                        db, tenant_id, pinned or dest.config_revision or "",
                        owner_id=dest.id, purpose="query")
                    semantics = _query_date_semantics(pinned_cr.content_json)
                except ApiError:
                    semantics = "both"
            full = _query_full_windows(cl, {
                "ref_ent_id": str(payload.get("ref_ent_id", "")),
                "bill_code": bill_code,
                "bill_type": str(payload.get("query_bill_type", ""))},
                payload, sub.created_at or utcnow(), semantics)
            if full.get("status") != "OK":
                platform_ok = False
                remote_error = str(
                    (full.get("error") or {}).get("code")
                    or (full.get("error") or {}).get("msg")
                    or full.get("reason") or "platform-incomplete")
                break
            remote_snapshot[bill_code] = _classify_query(full, bill_code)
    for bill in bills:
        rev = db.get(DrugTraceBillRevision, bill.current_revision_id) \
            if bill.current_revision_id else None
        if rev is None:
            continue
        if window_begin and rev.event_time and rev.event_time < window_begin:
            continue
        if window_end and rev.event_time and rev.event_time >= window_end:
            continue
        expected_codes = int(rev.header.get("expected_code_count") or 0) or None
        sub = submission_by_revision.get(rev.id)
        payload = (db.get(PackageRevision, sub.package_revision_id).payload or {}) \
            if sub is not None else {}
        remote = remote_snapshot.get(str(payload.get("bill_code", ""))) \
            if payload else None
        if sub is None:
            kind, actual = "MISSING_LOCAL_SUBMISSION", "none"
            counts["missing_local"] += 1     # 仅登记待人工：绝不自动补发（G3-17）
        elif not platform_ok or remote is None:
            kind = "UNVERIFIED_REMOTE"
            actual = sub.state
            counts["unverified_remote"] += 1
        elif remote.get("state") == "NOT_FOUND":
            kind, actual = "MISSING_REMOTE", sub.state
            counts["missing_remote"] += 1
        elif sub.state == SubmissionState.CONFIRMED and \
                remote.get("state") == "CONFIRMED":
            kind, actual = "MATCHED", sub.state
            counts["matched"] += 1
        else:
            kind = "STATE_MISMATCH"
            actual = f"local={sub.state},remote={remote.get('state')}"
            counts["state_mismatch"] += 1
        detail = {"bill": f"{bill.pharmacy_code}|{bill.business_kind}|"
                          f"{bill.source_bill_no}",
                  "source_revision": rev.source_revision,
                  "expected_code_count": expected_codes,
                  "remote_state": (remote or {}).get("state"),
                  "remote_detail": {k: (remote or {}).get(k)
                                    for k in ("result_type", "sub_process_flag")
                                    } if remote else None,
                  "note": "bill-level only; code-level verification pending EXT-09"}
        db.add(ReconciliationItem(
            id=new_uuid(), tenant_id=tenant_id, run_id=run.id,
            item_key=f"{bill.pharmacy_code}|{bill.business_kind}|"
                     f"{bill.source_bill_no}",
            expected_state="CONFIRMED", actual_state=str(actual),
            disposition="PENDING" if kind != "MATCHED" else "ACCEPTED",
            detail={"kind": kind, **detail}))
    run.scope = {**run.scope, "remote_snapshot": remote_snapshot,
                 "platform_ok": platform_ok, "remote_error": remote_error}
    run.summary = counts
    run.state = "COMPLETE" if platform_ok else "UNVERIFIED"
    db.commit()
    return {"run_id": run.id, "summary": counts,
            "code_level_verified": False,
            "remote_extra_checked": False,
            "platform_ok": platform_ok,
            "remote_error": remote_error}


# ---------------------------------------------------------------- 配置发布（FB-01/FB-05）
# 目的地 ConfigRevision 安全发布：API 端点与本机 CLI 共用同一校验/发布路径，
# 不允许手工 SQL 改 PUBLISHED 状态。配置世代只增不减：发布新版→旧 PUBLISHED
# 转 RETIRED（查询用途仍可读旧身份，见 _load_config_revision）。
DEST_CONFIG_FORBIDDEN_KEYS = (
    "app_secret",            # 明文 Secret 禁止入库（只允许 app_secret_ref）
    "session",               # 明文 session 禁止入库（session_ref）
    "password",
)
DEST_CONFIG_REAL_REQUIRED = (
    "app_key", "app_secret_ref", "dmz_base_url",
    "dmz_ca_file", "dmz_client_cert_file", "dmz_client_key_file",
)
QUERY_DATE_SEMANTICS_VALUES = ("both", "bill_time", "upload_time")


def validate_destination_config(content: dict, *, is_mock: bool) -> list[str]:
    """目的地配置校验（发布前置；返回问题清单，空列表=通过）。

    - 明文密钥字段直接拒绝（fail-closed，不静默丢弃）。
    - 真实目的地（is_mock=False）：六项必填；app_secret_ref 必须 env:；
      dmz_base_url 必须 https://；证书路径必须绝对路径。
    - mock 目的地：app_secret_ref 允许 mock: 前缀（离线演练专用）。
    - query_date_semantics 只接受已实现口径（默认 both 防漏查）。
    """
    problems: list[str] = []
    if not isinstance(content, dict):
        return ["config content must be a JSON object"]
    for key in DEST_CONFIG_FORBIDDEN_KEYS:
        if content.get(key):
            problems.append(
                f"plaintext field {key!r} forbidden; use "
                f"'{key}_ref' SecretRef instead")
    app_secret_ref = str(content.get("app_secret_ref", "") or "")
    if app_secret_ref and app_secret_ref.startswith("mock:") and not is_mock:
        problems.append("real destination cannot use mock: SecretRef")
    if not is_mock:
        for key in DEST_CONFIG_REAL_REQUIRED:
            if not str(content.get(key, "") or "").strip():
                problems.append(f"real destination requires {key!r}")
        if app_secret_ref and not app_secret_ref.startswith("env:"):
            problems.append(
                "real destination app_secret_ref must be env:<VAR> "
                "(restricted worker environment injection)")
        base = str(content.get("dmz_base_url", "") or "")
        if base and not base.startswith("https://"):
            problems.append("dmz_base_url must be https:// (mTLS forwarder)")
        for key in ("dmz_ca_file", "dmz_client_cert_file", "dmz_client_key_file"):
            v = str(content.get(key, "") or "")
            if v and not v.startswith("/"):
                problems.append(f"{key} must be an absolute path")
    semantics = str(content.get("query_date_semantics", "both") or "both")
    if semantics not in QUERY_DATE_SEMANTICS_VALUES:
        problems.append(
            f"query_date_semantics {semantics!r} not in "
            f"{QUERY_DATE_SEMANTICS_VALUES}")
    return problems


def publish_destination_config(db, tenant_id: str, destination_id: str,
                               content: dict, *, publish: bool,
                               set_real: bool = False,
                               actor: str = "") -> dict:
    """为目的地创建新 ConfigRevision（幂等内容去重）；publish=True 时发布并
    世代切换（旧 PUBLISHED→RETIRED；destination.config_revision 指向新版）。

    set_real=True 且目的地当前为 mock 时，将 is_mock 置 False（DRAFT 草稿阶段
    的显式切换；已发布/冻结历史不原地改）。返回操作结果（不回显 Secret 值）。
    """
    dest = db.get(Destination, destination_id)
    if dest is None or dest.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "destination not found")
    profile_key = (dest.profile_revision or "").split("@")[0]
    if profile_key not in C.PROFILES:
        raise ApiError("CAPABILITY_UNSUPPORTED",
                       f"destination profile {dest.profile_revision!r} not a "
                       "drug_trace profile")
    if set_real and dest.is_mock and dest.status != "DRAFT" \
            and dest.config_revision:
        raise ApiError(
            "VERSION_CONFLICT",
            "cannot flip mock→real on a non-draft destination with published "
            "config; create a new destination instead")
    effective_mock = False if set_real else bool(dest.is_mock)
    problems = validate_destination_config(content, is_mock=effective_mock)
    if problems:
        raise ApiError("VALIDATION_FAILED",
                       "destination config invalid: " + "; ".join(problems))
    digest = digest_of(content)
    last = (db.query(ConfigRevision)
            .filter_by(tenant_id=tenant_id, config_type="connector",
                       owner_id=dest.id)
            .order_by(ConfigRevision.revision.desc()).first())
    # 同 digest 既有世代（幂等基础）：优先复用，不重复建同内容世代
    matches = (db.query(ConfigRevision)
               .filter_by(tenant_id=tenant_id, config_type="connector",
                          owner_id=dest.id, digest=digest).all())
    active = next((m for m in matches if m.state == "PUBLISHED"
                   and dest.config_revision == m.id), None)
    draft_match = next((m for m in matches if m.state == "DRAFT"), None)
    if active is not None and publish:
        return {"config_revision_id": active.id,
                "revision": active.revision, "state": "PUBLISHED",
                "created": False,
                "note": "identical config already published and active "
                        "(idempotent no-op)"}
    if draft_match is not None and not publish:
        return {"config_revision_id": draft_match.id,
                "revision": draft_match.revision, "state": "DRAFT",
                "created": False,
                "note": "identical DRAFT config already exists "
                        "(idempotent no-op)"}
    if draft_match is not None and publish:
        # 发布既有同内容 DRAFT（不新建重复世代）
        cr = draft_match
        cr.state = "PUBLISHED"
        cr.created_by = actor or cr.created_by
        rev_no = cr.revision
        created = False
    else:
        rev_no = (last.revision + 1) if last else 1
        cr = ConfigRevision(
            id=new_uuid(), tenant_id=tenant_id, config_type="connector",
            owner_id=dest.id, revision=rev_no, content_json=content,
            digest=digest, state="PUBLISHED" if publish else "DRAFT",
            created_by=actor or "drug_trace.config")
        db.add(cr)
        created = True
    if publish:
        for r in (db.query(ConfigRevision)
                  .filter_by(tenant_id=tenant_id, config_type="connector",
                             owner_id=dest.id, state="PUBLISHED").all()):
            if r.id != cr.id:
                r.state = "RETIRED"
        dest.config_revision = cr.id
    if set_real and dest.is_mock:
        dest.is_mock = False
    db.commit()
    return {"config_revision_id": cr.id, "revision": rev_no,
            "state": cr.state, "created": created,
            "destination_config_revision": dest.config_revision,
            "destination_is_mock": bool(dest.is_mock)}
