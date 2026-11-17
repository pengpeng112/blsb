"""药品追溯 API 路由（W07；DEVELOPMENT_PLAN §9）。

统一前缀 /api/v1/drug-trace；沿用既有响应外壳/错误码/角色。要点：
- 列表不返回完整患者/码串（掩码+计数）。
- 采集/发送/对账先过证据门禁（空证据拒绝）再排持久 job；无任意 SQL/URL 参数。
- send 需 operator+platform_admin 显式批准语义（角色+证据+未变 revision）；
  query 只排查询 job（UNKNOWN 默认只查询）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select

from contracts import ApiError, SubmissionState, new_uuid, ok
from contracts.api import (ROLE_AUDITOR, ROLE_BUSINESS_REVIEWER, ROLE_CONFIG_EDITOR,
                           ROLE_OPERATOR, ROLE_PLATFORM_ADMIN, audit)
from core_db.engine import utcnow
from core_db.models import (DeliveryAttempt, Destination, Job, ManualDecision,
                            PackageRevision, QualityIssue, Submission)

from . import contracts as C
from . import service as S
from .models import (DrugTraceBill, DrugTraceBillCode, DrugTraceBillRevision,
                     DrugTraceCodeIndex, DrugTraceCollectionRun,
                     DrugTraceDailyReport, DrugTraceProductionEvidence,
                     DrugTraceSendQuota)


class RescanBillKey(BaseModel):
    """K3/SD-04 定向复核单据键：完整来源复合键四元组。"""
    source_system: str
    pharmacy_code: str
    business_kind: str
    source_bill_id: str


class CollectBody(BaseModel):
    connector_id: str
    window_begin: str = ""     # ISO（半开下界）；mode=rescan 时忽略
    window_end: str = ""       # ISO（半开上界，固定）；mode=rescan 时忽略
    lookback_seconds: int = 600
    mode: str = "synthetic"    # synthetic|oracle|rescan；真实 oracle 需证据门禁
    profile: str = ""          # 业务分支（证据匹配用）
    # checkpoint 分区：历史补报用独立分区（如 backfill），与增量互不覆盖（G3-06）
    checkpoint_partition: str = "0"
    # K3/SD-04：mode=rescan 定向复核（≤500 键/批；仅 operator|admin，audit）
    bill_keys: list[RescanBillKey] = Field(default_factory=list)


class PrepareBody(BaseModel):
    profile: str
    destination_id: str
    config_release_id: str | None = None


class SendBody(BaseModel):
    confirm: bool = False      # 显式确认语义（前端二次确认）
    reason: str = ""


class EvidenceScopeBody(BaseModel):
    """EXT-12 发送授权范围（FIX3-04/G3-10）：窗口+药房白名单+配额必须显式完整
    ——空值不是通配；send 能力必填，其他能力忽略。"""
    window_begin: str
    window_end: str
    pharmacies: list[str] = Field(default_factory=list)
    max_bills: int


class EvidenceBody(BaseModel):
    profile: str
    capability: str            # collect|send|query|reconcile
    destination_id: str = ""
    items: list[dict] = Field(default_factory=list)  # [{key,evidence_ref}]
    valid_until: str | None = None
    # 精确配置闭包（FIX3-04）：send 证据登记时绑定 config_release_id；空值对
    # send 不生效（不再当通配）。查询/采集能力可不带。
    config_release_id: str = ""
    scope: EvidenceScopeBody | None = None


class ReconcileBody(BaseModel):
    profile: str
    destination_id: str
    window_begin: str | None = None
    window_end: str | None = None
    pharmacy_code: str = ""


class DestConfigBody(BaseModel):
    """FB-01/FB-05：目的地 ConfigRevision 发布请求（模块级——FastAPI 注解
    解析需要全局作用域）。"""
    content: dict
    publish: bool = False          # 发布需 platform_admin；默认仅建 DRAFT
    set_real: bool = False         # DRAFT mock 目的地显式切真实（审计）
    dry_run: bool = False          # 只校验+预览，不写库


class SourceConfigBody(BaseModel):
    content: dict
    publish: bool = False
    enable: bool = False
    dry_run: bool = False


class ActivationBody(BaseModel):
    enabled: bool


class DailyReportBody(BaseModel):
    """TUI-02（任务书 §5.1）：驱动 v2 日报负载。

    幂等 upsert 按 (tenant, report_date, source)；JSON 字段深度≤4、
    三字段合计序列化长度≤64KB（服务端硬校验）。"""
    report_date: str                 # ISO 日期 YYYY-MM-DD（驱动=昨日）
    source: str = "timer"            # timer|manual
    window_begin: str | None = None  # ISO（naive GMT+8 系统约定）
    window_end: str | None = None
    candidate_count: int = 0
    collection: dict = Field(default_factory=dict)
    send: dict = Field(default_factory=dict)
    query_stats: dict = Field(default_factory=dict)
    outcome: str = "OK"              # OK|PARTIAL_FAILURE|FAILED
    elapsed_seconds: float = 0.0


_REPORT_JSON_MAX_TOTAL = 64 * 1024      # 日报负载上限（任务书 §7）
_REPORT_JSON_MAX_DEPTH = 4
_GMT8 = timezone(timedelta(hours=8))
_REPORT_OUTCOMES = ("OK", "PARTIAL_FAILURE", "FAILED")
_REPORT_SOURCES = ("timer", "manual")


def _report_error_count(section: dict) -> int:
    count = section.get("errors_total")
    actual = len(section.get("errors") or [])
    return max(actual, count) if type(count) is int and count >= 0 else actual


def _report_json_guard(body: DailyReportBody) -> None:
    """日报 JSON 字段防滥用：深度≤4、合计≤64KB（超限拒绝，不截断）。"""
    for name in ("collection", "send", "query_stats"):
        value = getattr(body, name) or {}

        def _depth(v, d=0):
            if d > _REPORT_JSON_MAX_DEPTH:
                raise ApiError(
                    "VALIDATION_FAILED",
                    f"{name} nesting deeper than {_REPORT_JSON_MAX_DEPTH} levels")
            if isinstance(v, dict):
                for x in v.values():
                    _depth(x, d + 1)
            elif isinstance(v, list):
                for x in v:
                    _depth(x, d + 1)

        _depth(value)
    total = len(json.dumps(
        {"collection": body.collection, "send": body.send,
         "query_stats": body.query_stats}, ensure_ascii=False, default=str))
    if total > _REPORT_JSON_MAX_TOTAL:
        raise ApiError(
            "REQUEST_TOO_LARGE",
            f"daily report JSON payload exceeds {_REPORT_JSON_MAX_TOTAL} bytes")


def _report_ts(value) -> datetime | None:
    """窗口时间解析：naive 原样存（业务墙钟 GMT+8 约定）；带偏移折算 GMT+8 后
    去 tzinfo（与采集运行窗口同一显示口径）。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED",
                       f"bad daily report timestamp {value!r}") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(_GMT8).replace(tzinfo=None)
    return dt


def _mask_code(code: str) -> str:
    if not code:
        return ""
    if len(code) <= 8:
        return code[:2] + "*"
    return f"{code[:4]}{'*' * (len(code) - 8)}{code[-4:]}"


def register(app, sf, auth, registry):
    svc = registry.capability("drug_trace.service") if registry is not None else S

    @app.post("/api/v1/drug-trace/sources/{connector_id}/config-revisions")
    def source_config(connector_id: str, body: SourceConfigBody, ctx=Depends(auth)):
        from . import source_config as sc
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        if body.publish or body.enable:
            p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            sc.connector(db, p.tenant_id, connector_id)
            if body.dry_run:
                problems = sc.validate(body.content)
                return ok({"dry_run": True, "valid": not problems,
                           "problems": problems}, ctx.request_id)
            result = sc.publish(db, p.tenant_id, connector_id, body.content,
                                publish=body.publish, enable=body.enable,
                                actor=p.subject_code)
            audit(db, p, "drug_trace.source.config", f"connector:{connector_id}",
                  new_ref=result)
            db.commit()
            return ok(result, ctx.request_id)

    @app.post("/api/v1/drug-trace/sources/{connector_id}/disable")
    def disable_source(connector_id: str, ctx=Depends(auth)):
        from . import source_config as sc
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            obj = sc.connector(db, p.tenant_id, connector_id)
            obj.status = "DISABLED"
            audit(db, p, "drug_trace.source.disable", f"connector:{connector_id}")
            db.commit()
            return ok({"status": obj.status}, ctx.request_id)

    @app.post("/api/v1/drug-trace/destinations/{dest_id}/activation")
    def destination_activation(dest_id: str, body: ActivationBody, ctx=Depends(auth)):
        from . import source_config as sc
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            result = sc.activate_destination(db, p.tenant_id, dest_id,
                                             enabled=body.enabled)
            audit(db, p, "drug_trace.destination.activation",
                  f"destination:{dest_id}", new_ref=result)
            db.commit()
            return ok(result, ctx.request_id)

    # ============================================================ 总览/单据
    @app.get("/api/v1/drug-trace/overview")
    def overview(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            from core_db.models import DataPackage
            bills = db.query(DrugTraceBill).filter_by(tenant_id=p.tenant_id)
            total = bills.count()
            waiting = db.query(DrugTraceBillRevision).join(
                DrugTraceBill, DrugTraceBill.id == DrugTraceBillRevision.bill_id
            ).filter(DrugTraceBill.tenant_id == p.tenant_id,
                     DrugTraceBillRevision.quality_state == "WAITING").count()
            valid = db.query(DrugTraceBillRevision).join(
                DrugTraceBill, DrugTraceBill.id == DrugTraceBillRevision.bill_id
            ).filter(DrugTraceBill.tenant_id == p.tenant_id,
                     DrugTraceBillRevision.quality_state == "VALID").count()
            invalid = db.query(DrugTraceBillRevision).join(
                DrugTraceBill, DrugTraceBill.id == DrugTraceBillRevision.bill_id
            ).filter(DrugTraceBill.tenant_id == p.tenant_id,
                     DrugTraceBillRevision.quality_state == "INVALID").count()
            # K1/SD-05：超回看窗无码终态单（不再进入等待队列）
            not_reportable = db.query(DrugTraceBillRevision).join(
                DrugTraceBill, DrugTraceBill.id == DrugTraceBillRevision.bill_id
            ).filter(DrugTraceBill.tenant_id == p.tenant_id,
                     DrugTraceBillRevision.quality_state ==
                     "NOT_REPORTABLE").count()
            # 仅统计药品领域提交（R2-16：overview 不混计其他领域）
            subs = (db.query(Submission)
                    .join(PackageRevision, PackageRevision.id ==
                          Submission.package_revision_id)
                    .join(DataPackage, DataPackage.id ==
                          PackageRevision.package_id)
                    .filter(Submission.tenant_id == p.tenant_id,
                            DataPackage.domain == "drug_trace"))
            total_subs = subs.count()
            unknown = subs.filter(
                Submission.state == SubmissionState.UNKNOWN).count()
            profiles = []
            for key in C.PROFILES:
                for cap in ("collect", "send", "query", "reconcile"):
                    st = svc.evidence_state(db, p.tenant_id, profile=key,
                                            capability=cap)
                    profiles.append({"profile": key, "capability": cap,
                                     "status": st["status"],
                                     "missing": [m.get("key") for m in st.get("missing", [])]})
            return ok({"bills": total, "revisions_waiting": waiting,
                       "revisions_valid": valid, "revisions_invalid": invalid,
                       "revisions_not_reportable": not_reportable,
                       "submissions": total_subs, "submissions_unknown": unknown,
                       "evidence": profiles}, ctx.request_id)

    @app.get("/api/v1/drug-trace/bills")
    def list_bills(ctx=Depends(auth), pharmacy_code: str = "", business_kind: str = "",
                   state: str = "", limit: int = 50):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(DrugTraceBill).filter_by(tenant_id=p.tenant_id)
            if pharmacy_code:
                q = q.filter(DrugTraceBill.pharmacy_code == pharmacy_code)
            if business_kind:
                q = q.filter(DrugTraceBill.business_kind == business_kind)
            if state:
                q = q.filter(DrugTraceBill.source_status == state)
            rows = q.order_by(DrugTraceBill.updated_at.desc()).limit(
                min(200, limit)).all()
            out = []
            for b in rows:
                rev = db.get(DrugTraceBillRevision, b.current_revision_id) \
                    if b.current_revision_id else None
                open_q = db.query(QualityIssue).filter_by(
                    tenant_id=p.tenant_id,
                    entity_ref=f"drug_trace_bill_revision:{b.current_revision_id}",
                    state="OPEN").count() if b.current_revision_id else 0
                out.append({
                    "id": b.id, "pharmacy_code": b.pharmacy_code,
                    "business_kind": b.business_kind, "source_bill_no": b.source_bill_no,
                    "source_bill_id": b.source_bill_id,
                    "current_revision_id": b.current_revision_id,
                    "source_revision": b.current_source_revision,
                    "quality_state": rev.quality_state if rev else None,
                    "open_quality_issues": open_q,
                    "finalized": b.finalized, "is_void": b.is_void,
                    "bill_time": b.bill_time.isoformat() if b.bill_time else None,
                    "code_count": db.query(DrugTraceCodeIndex).filter_by(
                        bill_revision_id=b.current_revision_id,
                        is_deleted=False).count() if b.current_revision_id else 0,
                    "updated_at": b.updated_at.isoformat()})
            return ok(out, ctx.request_id)

    @app.get("/api/v1/drug-trace/bills/{bill_id}")
    def bill_detail(bill_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            b = db.get(DrugTraceBill, bill_id)
            if b is None or b.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "bill not found")
            revs = (db.query(DrugTraceBillRevision)
                    .filter_by(bill_id=b.id)
                    .order_by(DrugTraceBillRevision.created_at.desc()).all())
            return ok({
                "id": b.id, "pharmacy_code": b.pharmacy_code,
                "warehouse_code": b.warehouse_code,
                "business_kind": b.business_kind,
                "source_bill_no": b.source_bill_no,
                "source_bill_id": b.source_bill_id,
                "source_system": b.source_system,
                "finalized": b.finalized, "is_void": b.is_void,
                "bill_time": b.bill_time.isoformat() if b.bill_time else None,
                "profile": C.profile_for_kind(b.business_kind),
                "revisions": [{"id": r.id, "source_revision": r.source_revision,
                               "quality_state": r.quality_state,
                               "quality_issues": r.quality_issues,
                               "code_count": db.query(DrugTraceCodeIndex)
                               .filter_by(bill_revision_id=r.id,
                                          is_deleted=False).count(),
                               "created_at": r.created_at.isoformat()}
                              for r in revs]}, ctx.request_id)

    @app.get("/api/v1/drug-trace/revisions/{rev_id}/codes")
    def revision_codes(rev_id: str, ctx=Depends(auth), limit: int = 100,
                       cursor: str = ""):
        p = ctx.require_principal()
        # 码值敏感：仅 operator/reviewer/admin/auditor 可读，且列表掩码
        p.require_any_role(ROLE_OPERATOR, ROLE_BUSINESS_REVIEWER,
                           ROLE_PLATFORM_ADMIN, ROLE_AUDITOR)
        with sf() as db:
            rev = db.get(DrugTraceBillRevision, rev_id)
            if rev is None or rev.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "revision not found")
            q = db.query(DrugTraceCodeIndex).filter_by(
                bill_revision_id=rev.id).order_by(DrugTraceCodeIndex.source_code_record_id)
            if cursor:
                q = q.filter(DrugTraceCodeIndex.source_code_record_id > cursor)
            rows = q.limit(min(500, limit)).all()
            return ok([{
                "source_code_record_id": r.source_code_record_id,
                "source_line_id": r.source_line_id,
                "trace_code_masked": _mask_code(r.trace_code),
                "code_level": r.code_level, "quantity": r.quantity,
                "unit": r.unit, "is_deleted": r.is_deleted} for r in rows],
                ctx.request_id)

    # ============================================================ 预览（dry-run，零外连）
    @app.post("/api/v1/drug-trace/previews")
    def previews(body: dict, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_BUSINESS_REVIEWER,
                           ROLE_PLATFORM_ADMIN)
        rev_id = str(body.get("revision_id", ""))
        with sf() as db:
            rev = db.get(DrugTraceBillRevision, rev_id)
            if rev is None or rev.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "revision not found")
            b = db.get(DrugTraceBill, rev.bill_id)
            profile = C.profile_for_kind(b.business_kind)
            preview = {
                "revision_id": rev.id, "quality_state": rev.quality_state,
                "quality_issues": rev.quality_issues,
                "profile": profile,
                "profile_eligible": profile is not None,
                "code_count": sum(1 for ln in (rev.lines or [])
                                  if not ln.get("is_deleted")),
                "code_limit_guard": C.PROFILES[profile]["code_limit_guard"]
                if profile else None,
                # dry-run：不连HIS、不查平台、不产生提交（DTR-A16）
                "dry_run": True,
            }
            if profile and rev.quality_state == C.BillQualityState.VALID:
                try:
                    from . import payload as payload_mod
                    pl = payload_mod.build_payload(db, p.tenant_id, rev, b,
                                                   profile=profile)
                    preview["bill_code"] = pl.get("bill_code")
                    preview["bill_type"] = pl.get("bill_type")
                    preview["ref_ent_id"] = pl.get("ref_ent_id")
                except ApiError as exc:
                    preview["blocked"] = {"code": exc.code, "message": exc.message}
            return ok(preview, ctx.request_id)

    # ============================================================ 采集（证据门禁→job）
    @app.post("/api/v1/drug-trace/collection-runs")
    def create_collection(body: CollectBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        if body.mode not in ("synthetic", "oracle", "rescan"):
            raise ApiError("VALIDATION_FAILED",
                           "mode must be synthetic|oracle|rescan")
        with sf() as db:
            from core_db.models import ConnectorInstance
            connector = db.get(ConnectorInstance, body.connector_id)
            if connector is None or connector.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "connector not found")
            if connector.direction != "source":
                raise ApiError("VALIDATION_FAILED", "connector must be source")
            profile = body.profile
            if body.mode == "rescan":
                # K3/SD-04 定向复核：单据键点查重扫（不做窗口扫描）
                if not body.bill_keys:
                    raise ApiError("VALIDATION_FAILED",
                                   "mode=rescan requires bill_keys")
                if len(body.bill_keys) > S.RESCAN_MAX_KEYS:
                    raise ApiError("VALIDATION_FAILED",
                                   f"bill_keys exceeds {S.RESCAN_MAX_KEYS} per run")
                keys = [k.model_dump() for k in body.bill_keys]
                from core_handlers import enqueue
                run = DrugTraceCollectionRun(
                    id=new_uuid(), tenant_id=p.tenant_id,
                    connector_id=body.connector_id, stream="drugtrace.bills",
                    profile=profile, window_begin=None, window_end=None,
                    lookback_seconds=0, state="PENDING",
                    created_by=p.subject_code)
                db.add(run)
                db.flush()
                job = enqueue(db, p.tenant_id, C.DomainJobType.RESCAN,
                              f"rescan:{run.id}",
                              {"collection_run_id": run.id,
                               "connector_id": body.connector_id,
                               "connector_config": {"mode": "oracle"}
                               if connector.active_config_revision
                               else {"mode": "synthetic"},
                               "bill_keys": keys,
                               "profile": profile,
                               "created_by": p.subject_code})
                audit(db, p, "drug_trace.rescan.create",
                      f"drug_trace_collection_run:{run.id}",
                      new_ref={"connector": body.connector_id,
                               "keys": len(keys), "job_id": job.id})
                db.commit()
                return ok({"collection_run_id": run.id, "job_id": job.id},
                          ctx.request_id)
            try:
                wb = datetime.fromisoformat(body.window_begin)
                we = datetime.fromisoformat(body.window_end)
            except ValueError as exc:
                raise ApiError("VALIDATION_FAILED", f"bad window: {exc}") from exc
            if wb >= we:
                raise ApiError("VALIDATION_FAILED", "window_begin must < window_end")
            # 真实 HIS 采集证据门禁（synthetic 演练模式不触发；不写 HIS）
            if body.mode == "oracle":
                svc.require_production_evidence(
                    db, p.tenant_id, profile=profile or C.PROFILE_INOUT,
                    capability="collect")
            from core_handlers import enqueue
            run = DrugTraceCollectionRun(
                id=new_uuid(), tenant_id=p.tenant_id,
                connector_id=body.connector_id, stream="drugtrace.bills",
                profile=profile, window_begin=wb, window_end=we,
                lookback_seconds=body.lookback_seconds, state="PENDING",
                created_by=p.subject_code)
            db.add(run)
            db.flush()
            job = enqueue(db, p.tenant_id, C.DomainJobType.COLLECT,
                          f"collect:{run.id}",
                          {"collection_run_id": run.id,
                           "connector_id": body.connector_id,
                           "connector_config": {"mode": body.mode},
                           "window_begin": body.window_begin,
                           "window_end": body.window_end,
                           "lookback_seconds": body.lookback_seconds,
                           "profile": profile,
                           "checkpoint_partition": body.checkpoint_partition,
                           "created_by": p.subject_code})
            audit(db, p, "drug_trace.collection.create",
                  f"drug_trace_collection_run:{run.id}",
                  new_ref={"connector": body.connector_id,
                           "window": [body.window_begin, body.window_end],
                           "mode": body.mode, "job_id": job.id})
            db.commit()
            return ok({"collection_run_id": run.id, "job_id": job.id},
                      ctx.request_id)

    @app.post("/api/v1/drug-trace/collection-runs/{run_id}/pause")
    def pause_collection(run_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            run = db.get(DrugTraceCollectionRun, run_id)
            if run is None or run.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "run not found")
            if run.state in ("DONE", "FAILED", "CANCELLED"):
                raise ApiError("VERSION_CONFLICT", f"run already {run.state}")
            run.state = "PAUSED"
            audit(db, p, "drug_trace.collection.pause",
                  f"drug_trace_collection_run:{run.id}", reason="")
            db.commit()
            return ok({"state": run.state}, ctx.request_id)

    @app.post("/api/v1/drug-trace/collection-runs/{run_id}/cancel")
    def cancel_collection(run_id: str, ctx=Depends(auth)):
        """撤销采集（FIX2-04：在途任务在安全点停止；已排队 worker 复核后跳过）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            run = db.get(DrugTraceCollectionRun, run_id)
            if run is None or run.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "run not found")
            if run.state in ("DONE", "FAILED", "CANCELLED"):
                raise ApiError("VERSION_CONFLICT", f"run already {run.state}")
            run.state = "CANCELLED"
            audit(db, p, "drug_trace.collection.cancel",
                  f"drug_trace_collection_run:{run.id}", reason="")
            db.commit()
            return ok({"state": run.state}, ctx.request_id)

    @app.get("/api/v1/drug-trace/collection-runs")
    def list_collections(ctx=Depends(auth), limit: int = 50):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(DrugTraceCollectionRun)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(DrugTraceCollectionRun.created_at.desc())
                    .limit(min(200, limit)).all())
            return ok([{"id": r.id, "state": r.state,
                        "window": [r.window_begin.isoformat() if r.window_begin else None,
                                   r.window_end.isoformat() if r.window_end else None],
                        "summary": r.summary,
                        "created_at": r.created_at.isoformat()} for r in rows],
                      ctx.request_id)

    # ============================================================ 准备/发送/查询
    @app.post("/api/v1/drug-trace/bills/{bill_id}/prepare")
    def prepare(bill_id: str, body: PrepareBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            b = db.get(DrugTraceBill, bill_id)
            if b is None or b.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "bill not found")
            if not b.current_revision_id:
                raise ApiError("PRECONDITION_REQUIRED", "no revision ingested")
            if body.profile not in C.PROFILES:
                raise ApiError("VALIDATION_FAILED", f"unknown profile {body.profile}")
            if b.business_kind not in C.PROFILES[body.profile]["business_kinds"]:
                raise ApiError("VALIDATION_FAILED",
                               f"kind {b.business_kind} not eligible for {body.profile}")
            try:
                result = svc.prepare_submission(
                    db, p.tenant_id, b.current_revision_id,
                    profile=body.profile, destination_id=body.destination_id,
                    config_release_id=body.config_release_id)
            except ApiError as exc:
                if exc.code == "IDENTITY_CONFLICT":
                    raise ApiError("CONFLICT", exc.message) from exc
                raise
            audit(db, p, "drug_trace.prepare",
                  f"submission:{result['submission_id']}",
                  new_ref={"bill": bill_id, "profile": body.profile})
            db.commit()
            return ok(result, ctx.request_id)

    @app.post("/api/v1/drug-trace/submissions/{sid}/send")
    def send(sid: str, body: SendBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        if not body.confirm:
            raise ApiError("PRECONDITION_REQUIRED",
                           "explicit confirm=true required for real send")
        with sf() as db:
            sub = db.get(Submission, sid)
            if sub is None or sub.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "submission not found")
            # 统一状态守卫（FIX2-04：UNKNOWN/SENDING 只查询；终态不重发）
            if sub.state != SubmissionState.PREPARED:
                hint = ("use query endpoint to resolve state"
                        if sub.state in (SubmissionState.UNKNOWN,
                                         SubmissionState.SENDING) else
                        f"terminal state {sub.state}")
                raise ApiError("VERSION_CONFLICT",
                               f"submission state {sub.state} not sendable; {hint}")
            pr = db.get(PackageRevision, sub.package_revision_id)
            profile = (pr.schema_ref or {}).get("profile", "")
            # 发送前证据门禁（空证据拒绝；DTR-A02）+ 配置闭包校验
            pinned_config = str((pr.provenance or {}).get("config_release_id") or "")
            svc.require_production_evidence(
                db, p.tenant_id, profile=profile, capability="send",
                destination_id=sub.destination_id,
                config_release_id=pinned_config or None)
            # 来源 revision 未变校验（来源更正后旧提交不再发送）
            brid = (pr.provenance or {}).get("bill_revision_id")
            if brid:
                rev = db.get(DrugTraceBillRevision, brid)
                bill = db.get(DrugTraceBill, rev.bill_id) if rev else None
                if bill is not None and bill.current_revision_id != brid:
                    raise ApiError(
                        "VERSION_CONFLICT",
                        "source revision changed; re-prepare required before send")
            from core_handlers import enqueue
            job = enqueue(db, p.tenant_id, C.DomainJobType.SEND,
                          f"send:{sub.id}", {"submission_id": sub.id})
            # The existing queue orders by priority before enqueue time.
            # Approved sends must not sit behind an entire prepare batch.
            job.priority = min(job.priority, 50)
            audit(db, p, "drug_trace.send.approve", f"submission:{sub.id}",
                  new_ref={"job_id": job.id}, reason=body.reason)
            db.commit()
            return ok({"job_id": job.id, "state": sub.state}, ctx.request_id)

    @app.post("/api/v1/drug-trace/submissions/{sid}/query")
    def query_submission(sid: str, ctx=Depends(auth)):
        """手工重查：创建新查询代次（FIX2-04：不吞掉已完成 query job）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            sub = db.get(Submission, sid)
            if sub is None or sub.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "submission not found")
            job = svc._enqueue_query_job(db, p.tenant_id, sub.id)
            if job is None:
                raise ApiError("VERSION_CONFLICT",
                               "query poll limit reached; manual review required")
            audit(db, p, "drug_trace.query", f"submission:{sub.id}",
                  new_ref={"job_id": job.id, "generation": job.payload.get(
                      "generation") if job.payload else None})
            db.commit()
            return ok({"job_id": job.id,
                       "generation": (job.payload or {}).get("generation")},
                      ctx.request_id)

    # ============================================================ 对账
    @app.post("/api/v1/drug-trace/reconciliation-runs")
    def reconcile(body: ReconcileBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        if body.profile not in C.PROFILES:
            raise ApiError("VALIDATION_FAILED", f"unknown profile {body.profile}")
        with sf() as db:
            dest = db.get(Destination, body.destination_id)
            if dest is None or dest.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "destination not found")
            svc.require_production_evidence(
                db, p.tenant_id, profile=body.profile, capability="reconcile",
                destination_id=body.destination_id)
            from core_handlers import enqueue
            # dedup 键含窗口/药房（FIX2-05：不同范围不得复用同一已完成任务）
            job = enqueue(db, p.tenant_id, C.DomainJobType.RECONCILE,
                          f"recon:{body.profile}:{body.destination_id}:"
                          f"{body.window_begin}:{body.window_end}:"
                          f"{body.pharmacy_code}",
                          {"profile": body.profile,
                           "destination_id": body.destination_id,
                           "window_begin": body.window_begin,
                           "window_end": body.window_end,
                           "pharmacy_code": body.pharmacy_code})
            audit(db, p, "drug_trace.reconcile.create",
                  f"destination:{body.destination_id}",
                  new_ref={"job_id": job.id, "profile": body.profile})
            db.commit()
            return ok({"job_id": job.id}, ctx.request_id)

    @app.get("/api/v1/drug-trace/reconciliation-runs/{run_id}")
    def reconciliation_detail(run_id: str, ctx=Depends(auth), limit: int = 100):
        p = ctx.require_principal()
        from core_db.models import ReconciliationItem, ReconciliationRun
        with sf() as db:
            run = db.get(ReconciliationRun, run_id)
            if run is None or run.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "run not found")
            items = (db.query(ReconciliationItem)
                     .filter_by(run_id=run.id)
                     .limit(min(500, limit)).all())
            return ok({"id": run.id, "state": run.state, "scope": run.scope,
                       "summary": run.summary,
                       "items": [{"id": i.id, "item_key": i.item_key,
                                  "expected_state": i.expected_state,
                                  "actual_state": i.actual_state,
                                  "disposition": i.disposition,
                                  "detail": i.detail} for i in items]},
                      ctx.request_id)

    @app.post("/api/v1/drug-trace/reconciliation-items/{item_id}/dispositions")
    def reconciliation_disposition(item_id: str, body: dict, ctx=Depends(auth)):
        """人工处置（DTR-D04）：必须理由；ACCEPTED/COMPENSATE/IGNORE 需角色授权。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_PLATFORM_ADMIN)
        action = str(body.get("action", ""))
        reason = str(body.get("reason", "")).strip()
        if action not in ("ACCEPTED", "COMPENSATE", "IGNORE"):
            raise ApiError("VALIDATION_FAILED",
                           "action must be ACCEPTED|COMPENSATE|IGNORE")
        if not reason:
            raise ApiError("VALIDATION_FAILED", "disposition reason required")
        with sf() as db:
            from core_db.models import ReconciliationItem
            item = db.get(ReconciliationItem, item_id)
            if item is None or item.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "item not found")
            before = item.disposition
            item.disposition = action
            item.detail = {**(item.detail or {}),
                           "disposition_reason": reason,
                           "disposition_by": p.subject_code,
                           "disposition_at": utcnow().isoformat()}
            db.add(ManualDecision(
                id=new_uuid(), tenant_id=p.tenant_id,
                entity_ref=f"reconciliation_item:{item.id}", action=action,
                before_ref={"disposition": before},
                after_ref={"disposition": action}, reason=reason,
                actor=p.subject_code))
            audit(db, p, "drug_trace.reconcile.disposition",
                  f"reconciliation_item:{item.id}",
                  old_ref={"disposition": before},
                  new_ref={"disposition": action}, reason=reason)
            db.commit()
            return ok({"disposition": action}, ctx.request_id)

    # ============================================================ 就绪/证据
    @app.get("/api/v1/drug-trace/readiness")
    def readiness(ctx=Depends(auth), profile: str = "", destination_id: str = ""):
        """FB-01：分层就绪统一由 domains.drug_trace.readiness 计算（CLI 同源）。

        - profile/destination_id 可选；同 profile 多目的地未指定时不任意 first，
          返回 ambiguous + 候选清单。
        - NETWORK_READY 恒 UNKNOWN（本端点不做网络探测；外部实测见任务 evidence）。
        - 只读：不触发采集/凭据刷新/证据批准/网络 POST。
        """
        p = ctx.require_principal()
        if profile and profile not in C.PROFILES:
            raise ApiError("VALIDATION_FAILED", f"unknown profile {profile}")
        with sf() as db:
            from . import readiness as R
            out = []
            for key, spec in C.PROFILES.items():
                if profile and key != profile:
                    continue
                # FB-01：精确解析（显式 id 优先；多目的地→ambiguous 不自动选择）
                res = R.resolve_destinations(db, p.tenant_id, key, destination_id)
                dest = res["destination"]
                caps = []
                for cap in ("collect", "send", "query", "reconcile"):
                    st = svc.evidence_state(
                        db, p.tenant_id, profile=key, capability=cap,
                        destination_id=dest.id if dest else "")
                    caps.append({"capability": cap, "status": st["status"],
                                 "evidence_id": st.get("evidence_id"),
                                 "missing": [m.get("key") if isinstance(m, dict)
                                             else str(m)
                                             for m in st.get("missing", [])]})
                from destinations.alihealth_drugtrace.protocol import \
                    SIGNATURE_VERIFIED
                out.append({
                    "profile": key, "label": spec["label"],
                    "method": spec["method"],
                    "destination": ({"id": dest.id, "code": dest.code,
                                     "is_mock": bool(dest.is_mock),
                                     "status": dest.status,
                                     "config_revision": dest.config_revision}
                                    if dest else None),
                    "destination_ambiguous": res["ambiguous"],
                    "destination_candidates": res["candidates"],
                    "signature_verified": SIGNATURE_VERIFIED,
                    "capabilities": caps,
                    "code_limit_guard": spec["code_limit_guard"]})
            report = R.layer_report(
                db, p.tenant_id, profile=profile or C.PROFILE_RETAIL,
                destination_id=destination_id,
                evidence_state_fn=svc.evidence_state)
            return ok({"profiles": out, "layers": report["layers"],
                       "report": report}, ctx.request_id)

    # ============================================================ 目的地配置（FB-01/FB-05）
    @app.get("/api/v1/drug-trace/destinations/{dest_id}/config-revisions")
    def list_dest_configs(dest_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            from core_db.models import ConfigRevision as CR
            dest = db.get(Destination, dest_id)
            if dest is None or dest.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "destination not found")
            rows = (db.query(CR)
                    .filter_by(tenant_id=p.tenant_id, config_type="connector",
                               owner_id=dest.id)
                    .order_by(CR.revision).all())
            return ok([{"id": r.id, "revision": r.revision, "state": r.state,
                        "digest": r.digest,
                        # 摘要级内容回显：不含 SecretRef 之外的敏感字段
                        "content_keys": sorted((r.content_json or {}).keys())}
                       for r in rows], ctx.request_id)

    @app.post("/api/v1/drug-trace/destinations/{dest_id}/config-revisions")
    def create_dest_config(dest_id: str, body: DestConfigBody, ctx=Depends(auth)):
        """安全配置发布（FB-05 §2/§3）：统一走 service.publish_destination_config
        的校验/世代切换路径；不提供直接改库状态为 PUBLISHED 的旁路。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        if body.publish:
            p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            dest = db.get(Destination, dest_id)
            if dest is None or dest.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "destination not found")
            if body.dry_run:
                problems = svc.validate_destination_config(
                    body.content, is_mock=False if body.set_real
                    else bool(dest.is_mock))
                return ok({"dry_run": True, "valid": not problems,
                           "problems": problems,
                           "would_publish": body.publish,
                           "would_set_real": body.set_real}, ctx.request_id)
            result = svc.publish_destination_config(
                db, p.tenant_id, dest_id, body.content,
                publish=body.publish, set_real=body.set_real,
                actor=p.subject_code)
            audit(db, p, "drug_trace.destination.config",
                  f"destination:{dest_id}",
                  new_ref={"config_revision_id": result.get(
                      "config_revision_id"),
                      "state": result.get("state"),
                      "publish": body.publish, "set_real": body.set_real,
                      "digest_keys": sorted(body.content.keys())})
            db.commit()
            return ok(result, ctx.request_id)

    @app.post("/api/v1/drug-trace/evidence")
    def approve(body: EvidenceBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        if body.capability not in ("collect", "send", "query", "reconcile"):
            raise ApiError("VALIDATION_FAILED", "capability must be "
                           "collect|send|query|reconcile")
        if body.profile not in C.PROFILES:
            raise ApiError("VALIDATION_FAILED", f"unknown profile {body.profile}")
        with sf() as db:
            if body.destination_id:
                dest = db.get(Destination, body.destination_id)
                if dest is None or dest.tenant_id != p.tenant_id:
                    raise ApiError("NOT_FOUND", "destination not found")
            ev = svc.approve_evidence(
                db, p.tenant_id, profile=body.profile,
                capability=body.capability, destination_id=body.destination_id,
                provided_items=body.items, approved_by=p.subject_code,
                valid_until=datetime.fromisoformat(body.valid_until)
                if body.valid_until else None,
                config_release_id=body.config_release_id,
                scope=body.scope.model_dump() if body.scope else None)
            audit(db, p, "drug_trace.evidence.approve",
                  f"drug_trace_evidence:{ev.id}",
                  new_ref={"profile": body.profile,
                           "capability": body.capability,
                           "items": [i.get("key") for i in body.items],
                           "config_release_id": body.config_release_id,
                           "scope": body.scope.model_dump()
                           if body.scope else None})
            db.commit()
            return ok({"evidence_id": ev.id, "status": ev.status}, ctx.request_id)

    @app.post("/api/v1/drug-trace/evidence/{evidence_id}/revoke")
    def revoke(evidence_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            svc.revoke_evidence(db, p.tenant_id, evidence_id)
            audit(db, p, "drug_trace.evidence.revoke",
                  f"drug_trace_evidence:{evidence_id}")
            db.commit()
            return ok({"revoked": True}, ctx.request_id)

    # ============================================================ 定时上报可视化（TUI-02/03）
    @app.post("/api/v1/drug-trace/daily-reports")
    def ingest_daily_report(body: DailyReportBody, ctx=Depends(auth)):
        """驱动 v2 日报入库（幂等 upsert by tenant+report_date+source）。

        只新增数据：不影响发送/门禁/配额路径；失败安全（驱动侧单次 POST 放弃）。
        """
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        try:
            rd = date.fromisoformat(body.report_date)
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED",
                           "report_date must be ISO date (YYYY-MM-DD)") from exc
        if body.source not in _REPORT_SOURCES:
            raise ApiError("VALIDATION_FAILED",
                           f"source must be one of {_REPORT_SOURCES}")
        if body.outcome not in _REPORT_OUTCOMES:
            raise ApiError("VALIDATION_FAILED",
                           f"outcome must be one of {_REPORT_OUTCOMES}")
        _report_json_guard(body)
        wb, we = _report_ts(body.window_begin), _report_ts(body.window_end)
        if wb is not None and we is not None and wb >= we:
            raise ApiError("VALIDATION_FAILED", "window_begin must < window_end")
        # 不校验 report_date 新近度：manual 补录历史日为合法场景（audit 已留痕）
        with sf() as db:
            row = (db.query(DrugTraceDailyReport)
                   .filter_by(tenant_id=p.tenant_id, report_date=rd,
                              source=body.source).first())
            created = row is None
            if row is None:
                row = DrugTraceDailyReport(
                    id=new_uuid(), tenant_id=p.tenant_id, report_date=rd,
                    source=body.source)
                db.add(row)
            row.window_begin = wb
            row.window_end = we
            row.candidate_count = int(body.candidate_count)
            row.collection = body.collection or {}
            row.send = body.send or {}
            row.query_stats = body.query_stats or {}
            row.outcome = body.outcome
            row.elapsed_seconds = float(body.elapsed_seconds)
            row.updated_at = utcnow()
            audit(db, p, "drug_trace.daily_report.ingest",
                  f"drug_trace_daily_report:{rd.isoformat()}:{body.source}",
                  new_ref={"outcome": body.outcome,
                           "candidate_count": body.candidate_count,
                           "created": created})
            db.commit()
            return ok({"report_date": rd.isoformat(), "source": body.source,
                       "outcome": row.outcome, "created": created},
                      ctx.request_id)

    @app.get("/api/v1/drug-trace/daily-reports")
    def list_daily_reports(ctx=Depends(auth), limit: int = 30):
        """日报摘要列表（不含 errors 明细；单日详情走 {report_date}）。"""
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(DrugTraceDailyReport)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(DrugTraceDailyReport.report_date.desc(),
                              DrugTraceDailyReport.updated_at.desc())
                    .limit(min(200, limit)).all())
            return ok([{
                "report_date": r.report_date.isoformat(),
                "source": r.source, "outcome": r.outcome,
                "candidate_count": r.candidate_count,
                "sent": (r.send or {}).get("sent", 0),
                "errors_count": _report_error_count(r.send or {}),
                "query_errors_count": _report_error_count(r.query_stats or {}),
                "query_skipped_exhausted": (r.query_stats or {}).get("skipped_exhausted", 0),
                "elapsed_seconds": float(r.elapsed_seconds or 0),
                "created_at": r.created_at.isoformat()} for r in rows],
                ctx.request_id)

    @app.get("/api/v1/drug-trace/daily-reports/{report_date}")
    def daily_report_detail(report_date: str, ctx=Depends(auth)):
        """单日完整日报（含 send.errors / aborted_reason；同日多源全返回）。"""
        p = ctx.require_principal()
        try:
            rd = date.fromisoformat(report_date)
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED",
                           "report_date must be ISO date (YYYY-MM-DD)") from exc
        with sf() as db:
            rows = (db.query(DrugTraceDailyReport)
                    .filter_by(tenant_id=p.tenant_id, report_date=rd)
                    .order_by(DrugTraceDailyReport.updated_at.desc()).all())
            if not rows:
                raise ApiError("NOT_FOUND", f"no daily report for {rd.isoformat()}")
            return ok([{
                "report_date": r.report_date.isoformat(), "source": r.source,
                "window": [r.window_begin.isoformat() if r.window_begin else None,
                           r.window_end.isoformat() if r.window_end else None],
                "candidate_count": r.candidate_count,
                "collection": r.collection or {}, "send": r.send or {},
                "query_stats": r.query_stats or {}, "outcome": r.outcome,
                "elapsed_seconds": float(r.elapsed_seconds or 0),
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat()} for r in rows],
                ctx.request_id)

    @app.get("/api/v1/drug-trace/upload-stats")
    def upload_stats(ctx=Depends(auth), days: int = 14):
        """按日上传统计（本租户、仅 drug_trace 域提交）。

        - by_state：按 submission.created_at 日期（UTC）× state 计数。
        - attempts：每提交取最近一次 delivery_attempt，按 outcome 分桶
          accepted / http_error(http≥400) / other。
        平台终态以次日查询补进后的 state 为准（与日报表分开展示，不混算）。

        FIX-01：两条分组聚合 SQL（状态 + 窗口函数取每提交最新尝试），
        端点内不出现任何 .in_(...)——IN 列表超方言参数上限（SQLite 32766 /
        PG 65535）会导致生产规模（~2000 单/日 × days=90）下 500。
        """
        p = ctx.require_principal()
        days = min(90, max(1, days))
        with sf() as db:
            from core_db.models import DataPackage
            since = utcnow() - timedelta(days=days)
            day_expr = func.date(Submission.created_at)
            by_day: dict = {}
            # 1) 状态分组（单条 SQL；分组行 ≤ 天数×状态数）
            for d, state, n in (
                    db.query(day_expr.label("d"), Submission.state,
                             func.count().label("n"))
                    .join(PackageRevision,
                          PackageRevision.id == Submission.package_revision_id)
                    .join(DataPackage,
                          DataPackage.id == PackageRevision.package_id)
                    .filter(Submission.tenant_id == p.tenant_id,
                            DataPackage.domain == "drug_trace",
                            Submission.created_at >= since)
                    .group_by(day_expr, Submission.state).all()):
                bucket = by_day.setdefault(
                    str(d), {"by_state": {},
                             "attempts": {"accepted": 0, "http_error": 0,
                                          "other": 0}, "total": 0})
                bucket["by_state"][state] = n
                bucket["total"] += n
            # 2) 最近尝试（ROW_NUMBER 窗口子查询取 rn=1；分桶规则与旧实现一致）
            rn = func.row_number().over(
                partition_by=DeliveryAttempt.submission_id,
                order_by=DeliveryAttempt.attempt_no.desc()).label("rn")
            latest = select(
                DeliveryAttempt.submission_id.label("submission_id"),
                DeliveryAttempt.outcome.label("outcome"),
                DeliveryAttempt.http_status.label("http_status"),
                rn).where(DeliveryAttempt.tenant_id == p.tenant_id).subquery()
            for d, outcome, http_status, n in (
                    db.query(day_expr.label("d"), latest.c.outcome,
                             latest.c.http_status, func.count().label("n"))
                    .select_from(Submission)
                    .join(PackageRevision,
                          PackageRevision.id == Submission.package_revision_id)
                    .join(DataPackage,
                          DataPackage.id == PackageRevision.package_id)
                    .join(latest, and_(latest.c.submission_id == Submission.id,
                                       latest.c.rn == 1))
                    .filter(Submission.tenant_id == p.tenant_id,
                            DataPackage.domain == "drug_trace",
                            Submission.created_at >= since)
                    .group_by(day_expr, latest.c.outcome,
                              latest.c.http_status).all()):
                att = by_day[str(d)]["attempts"]
                if outcome == "ACCEPTED":
                    att["accepted"] += n
                elif http_status is not None and http_status >= 400:
                    att["http_error"] += n
                else:
                    att["other"] += n
            out = [{"date": k, "by_state": v["by_state"],
                    "attempts": v["attempts"], "total": v["total"]}
                   for k, v in sorted(by_day.items(), reverse=True)]
            return ok({"days": out}, ctx.request_id)

    @app.get("/api/v1/drug-trace/send-scope")
    def send_scope(ctx=Depends(auth), profile: str = C.PROFILE_RETAIL):
        """现行 send 证据 + EXT-12 scope + 配额台账总览（只读）。

        多目的地/未登记时显式字段为空（不猜）；配额 used 来自台账实时值。
        """
        p = ctx.require_principal()
        if profile not in C.PROFILES:
            raise ApiError("VALIDATION_FAILED", f"unknown profile {profile}")
        with sf() as db:
            from . import readiness as R
            out = {"profile": profile,
                   "evidence": {"evidence_id": None, "status": "MISSING",
                                "valid_until": None, "config_release_id": None},
                   "scope": None, "quota": None, "destination": None,
                   "ambiguous": False}
            res = R.resolve_destinations(db, p.tenant_id, profile, "")
            out["ambiguous"] = bool(res["ambiguous"])
            dest = res["destination"]
            if res["ambiguous"] or dest is None:
                return ok(out, ctx.request_id)   # 多目的地/未绑定：显式空
            st = svc.evidence_state(db, p.tenant_id, profile=profile,
                                    capability="send", destination_id=dest.id)
            out["destination"] = {"id": dest.id, "code": dest.code,
                                  "status": dest.status,
                                  "is_mock": bool(dest.is_mock)}
            evidence_id = st.get("evidence_id")
            if evidence_id:
                valid_until = st.get("valid_until")
                out["evidence"] = {
                    "evidence_id": evidence_id, "status": st["status"],
                    "valid_until": valid_until.isoformat()
                    if valid_until else None,
                    "config_release_id": st.get("config_release_id") or None}
                out["scope"] = st.get("scope") or None
                quota = (db.query(DrugTraceSendQuota)
                         .filter_by(tenant_id=p.tenant_id,
                                    evidence_id=evidence_id).first())
                if quota is not None:
                    out["quota"] = {"used": quota.used,
                                    "max_bills": quota.max_bills}
            return ok(out, ctx.request_id)

    @app.get("/api/v1/drug-trace/submission-outcomes")
    def submission_outcomes(ctx=Depends(auth), days: int = 7, only: str = ""):
        """近 N 天提交结果（关联最近一次 delivery_attempt 与来源单号）。

        only=problem：保留 state∈{REJECTED,PARTIAL,UNKNOWN} 或最近尝试
        outcome≠ACCEPTED 的提交；无尝试记录（从未发送）不视为异常。
        上限 200，创建时间倒序。

        FIX-02：最近尝试用 ROW_NUMBER 窗口子查询 LEFT JOIN 一次取齐
        （无尝试保留 attempt_* 为 null）；单号一次批量 in_(≤200 id) 回填，
        不再逐行单查（旧行为 ≤200 行 ≈ 400 次查询）。
        """
        p = ctx.require_principal()
        days = min(90, max(1, days))
        with sf() as db:
            from core_db.models import DataPackage
            since = utcnow() - timedelta(days=days)
            rn = func.row_number().over(
                partition_by=DeliveryAttempt.submission_id,
                order_by=DeliveryAttempt.attempt_no.desc()).label("rn")
            latest = select(
                DeliveryAttempt.submission_id.label("submission_id"),
                DeliveryAttempt.outcome.label("outcome"),
                DeliveryAttempt.http_status.label("http_status"),
                DeliveryAttempt.ended_at.label("ended_at"),
                rn).where(DeliveryAttempt.tenant_id == p.tenant_id).subquery()
            rows = (db.query(Submission, PackageRevision,
                             latest.c.outcome, latest.c.http_status,
                             latest.c.ended_at)
                    .join(PackageRevision,
                          PackageRevision.id == Submission.package_revision_id)
                    .join(DataPackage,
                          DataPackage.id == PackageRevision.package_id)
                    .outerjoin(latest, and_(
                        latest.c.submission_id == Submission.id,
                        latest.c.rn == 1))
                    .filter(Submission.tenant_id == p.tenant_id,
                            DataPackage.domain == "drug_trace",
                            Submission.created_at >= since)
                    .order_by(Submission.created_at.desc())
                    .limit(200).all())
            bill_ids = set()
            for sub, _pr, _o, _h, _e in rows:
                prov = _pr.provenance or {}
                if prov.get("bill_id"):
                    bill_ids.add(prov["bill_id"])
            bill_no = {}
            if bill_ids:
                for b in (db.query(DrugTraceBill)
                          .filter(DrugTraceBill.tenant_id == p.tenant_id,
                                  DrugTraceBill.id.in_(bill_ids)).all()):
                    bill_no[b.id] = b.source_bill_no
            out = []
            for sub, pr, outcome, http_status, ended_at in rows:
                if only == "problem":
                    if sub.state not in ("REJECTED", "PARTIAL", "UNKNOWN") \
                            and (outcome is None or outcome == "ACCEPTED"):
                        continue
                prov = pr.provenance or {}
                out.append({
                    "id": sub.id, "state": sub.state,
                    "created_at": sub.created_at.isoformat(),
                    "updated_at": sub.updated_at.isoformat(),
                    "attempt_outcome": outcome,
                    "attempt_http_status": http_status,
                    "attempt_ended_at": ended_at.isoformat()
                    if ended_at else None,
                    "source_bill_no": bill_no.get(prov.get("bill_id"), ""),
                    "bill_code": (pr.payload or {}).get("bill_code", ""),
                    "destination_id": sub.destination_id})
            return ok(out, ctx.request_id)
