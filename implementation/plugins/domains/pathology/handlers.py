"""病理领域处理器与 Runtime 构建（W11 从 plugin_registry 拆出）。

本模块属于病理插件：仅在全量装配或病理插件启用时被加载。
metrics-only 进程不 import 本模块。
"""
from __future__ import annotations

import threading

from core_db.engine import utcnow
from core_db.models import ConfigRevision, DictionaryRevision, SourceSystem
from workflow_core.dag import StepOutcome
from workflow_core.jobs import emit as emit_outbox

from core_handlers import HandlerCtx, _case_from_run, _package_revision
from domains.pathology import pipeline as path_pipeline
from domains.pathology import resolver as path_resolver
from sources.db_view import (MedrecHisConnector, PitayaPisConnector,
                             SyntheticWarehouse)

WAREHOUSE_SINGLETON: SyntheticWarehouse | None = None
_WAREHOUSE_LOCK = threading.Lock()


def synthetic_warehouse() -> SyntheticWarehouse:
    global WAREHOUSE_SINGLETON
    with _WAREHOUSE_LOCK:
        if WAREHOUSE_SINGLETON is None:
            WAREHOUSE_SINGLETON = SyntheticWarehouse()
        return WAREHOUSE_SINGLETON


def build_runtime(db, tenant_id: str, *, dry_run: bool = False,
                  pack_revision_id: str | None = None) -> path_resolver.Runtime:
    """按 HospitalPack 与已批准字典构建病理 Runtime（W10：可固定 pack revision）。

    - pack_revision_id 给定时读取该具体 revision（run 依赖闭包回放），否则当前发布版；
    - 字典仅在 (a) Pack 批准引用 且 (b) dictionary_revision.approved_by 非空
      且在有效期内时装载（R37：Pack 引用不能替代字典自身的审批）。
    """
    wh = synthetic_warehouse()
    if pack_revision_id:
        rev = db.get(ConfigRevision, pack_revision_id)
        pack = (rev.content_json if rev else {}) or {}
    else:
        pack = _published_pack(db, tenant_id)
    pis_cfg = pack.get("sources", {}).get("pis", {})
    database = pis_cfg.get("database_candidate", "PITAYA")
    pis = PitayaPisConnector(wh, database=database,
                             schema=pis_cfg.get("schema", "dbo"))
    his = MedrecHisConnector(wh, schema=pis_cfg.get("his_schema", "MEDREC"))
    src = (db.query(SourceSystem)
           .filter_by(tenant_id=tenant_id, code="pis").first())
    # 字典装载：批准引用 + 自身 approved + 有效期（R37）
    approved_refs = (pack.get("approved_dictionaries") or {})
    now = utcnow()
    dictionaries = {}
    for d in db.query(DictionaryRevision).filter_by(tenant_id=tenant_id).all():
        if approved_refs.get(d.namespace) != d.version:
            continue
        if not d.approved_by:
            continue
        if d.effective_from is not None and d.effective_from > now:
            continue
        if d.effective_to is not None and d.effective_to < now:
            continue
        mapping = {i["code"]: i["target"] for i in (d.items or []) if "target" in i}
        dictionaries[f"{d.namespace}@{d.version}"] = mapping
    return path_resolver.Runtime(
        db=db, tenant_id=tenant_id, pis=pis, his=his,
        source_system_id=src.id if src else "",
        namespace=f"pis.pitaya.sqlserver@{database}.dbo",
        his_namespace="his.medrec.oracle@MEDREC",
        pack=pack, dictionaries=dictionaries, dry_run=dry_run)


def _published_pack(db, tenant_id: str) -> dict:
    rev = (db.query(ConfigRevision)
           .filter_by(tenant_id=tenant_id, config_type="hospital_pack",
                      state="PUBLISHED")
           .order_by(ConfigRevision.revision.desc()).first())
    return (rev.content_json if rev else {}) or {}


def _runtime_for(ctx: HandlerCtx):
    """按 run 的依赖闭包构建 Runtime（回放不读latest）；dry-run 全链传播。"""
    closure = (ctx.run.context or {}).get("_config_closure") or {}
    return build_runtime(ctx.db, ctx.tenant_id, dry_run=ctx.dry_run,
                         pack_revision_id=closure.get("pack_revision_id"))


def h_pathology_resolve(ctx: HandlerCtx) -> StepOutcome:
    acquisition_id = ctx.step.input_refs.get("acquisition_id")
    if not acquisition_id:
        return StepOutcome("FAILED", error={"message": "missing acquisition_id in context"})
    from core_db.models import Acquisition
    acq = ctx.db.get(Acquisition, acquisition_id)
    if acq is None or acq.tenant_id != ctx.tenant_id:
        return StepOutcome("FAILED", error={"message": "acquisition not found"})
    rt = _runtime_for(ctx)
    result = path_resolver.resolve_acquisition(rt, acq)
    ctx.db.commit()
    if result["state"] == "ASSOCIATED":
        emit_outbox(ctx.db, ctx.tenant_id, f"pathology_case:{result['case_id']}",
                    "dry.pathology.case.updated" if ctx.dry_run
                    else "pathology.case.updated",
                    {"case_id": result["case_id"]})
        ctx.db.commit()
        return StepOutcome("SUCCEEDED", outputs=result)
    state = {"WAITING": "WAITING_EXTERNAL"}.get(result["state"], result["state"])
    if result["wait_reason"] in ("SOURCE_AMBIGUOUS", "IDENTITY_CONFLICT"):
        state = "WAITING_HUMAN"
    return StepOutcome(state, outputs=result, wait_reason=result["wait_reason"])


def h_pathology_quality(ctx: HandlerCtx) -> StepOutcome:
    case_id = _case_from_run(ctx)
    if not case_id:
        return StepOutcome("FAILED", error={"message": "case not resolvable from run"})
    rt = _runtime_for(ctx)
    from domains.pathology.models import PathologyCase
    case = ctx.db.get(PathologyCase, case_id)
    # 复评：用最新源状态重跑 resolve（幂等）
    for acq_id in (ctx.step.input_refs.get("acquisition_id"),):
        if acq_id:
            from core_db.models import Acquisition
            acq = ctx.db.get(Acquisition, acq_id)
            if acq is not None:
                path_resolver.resolve_acquisition(rt, acq)
                ctx.db.commit()
    return StepOutcome("SUCCEEDED", outputs={"case_id": case_id})


def h_pathology_completeness(ctx: HandlerCtx) -> StepOutcome:
    case_id = _case_from_run(ctx)
    rt = _runtime_for(ctx)
    from domains.pathology.models import PathologyCase
    if not case_id:                      # RT-02：空主键先拒（与 resolve 一致）
        return StepOutcome("FAILED", error={"message": "case not found"})
    case = ctx.db.get(PathologyCase, case_id)
    if case is None:
        return StepOutcome("FAILED", error={"message": "case not found"})
    if not case.files_complete:
        return StepOutcome("WAITING_EXTERNAL", wait_reason="COMPLETENESS_UNKNOWN",
                           outputs={"case_id": case_id})
    comp = path_resolver.evaluate_completeness(rt, case, rt.pis)
    ctx.db.commit()
    if comp["missing"] or comp["unapproved_scans"] or not comp["expected_known"]:
        return StepOutcome("WAITING_EXTERNAL", wait_reason="COMPLETENESS_UNKNOWN",
                           outputs={"case_id": case_id, "completeness": comp})
    return StepOutcome("SUCCEEDED", outputs={"case_id": case_id, "completeness": comp})


def h_pathology_freeze(ctx: HandlerCtx) -> StepOutcome:
    case_id = _case_from_run(ctx)
    rt = _runtime_for(ctx)
    from domains.pathology.models import PathologyCase
    if not case_id:                      # RT-02：空主键先拒（与 resolve 一致）
        return StepOutcome("FAILED", error={"message": "case not found"})
    case = ctx.db.get(PathologyCase, case_id)
    if case is None:
        return StepOutcome("FAILED", error={"message": "case not found"})
    if ctx.dry_run:
        return StepOutcome("SUCCEEDED", outputs={"dry_run": True, "case_id": case_id})
    pr = path_pipeline.freeze_case(ctx.db, ctx.tenant_id, case, rt)
    ctx.db.commit()
    if pr is None:
        return StepOutcome("WAITING_EXTERNAL", wait_reason="COMPLETENESS_UNKNOWN",
                           outputs={"case_id": case_id})
    emit_outbox(ctx.db, ctx.tenant_id, f"package_revision:{pr.id}",
                "package.frozen", {"package_revision_id": pr.id,
                                   "package_id": pr.package_id, "domain": "pathology",
                                   "case_id": case_id})
    ctx.db.commit()
    return StepOutcome("SUCCEEDED", outputs={"package_revision_id": pr.id,
                                             "case_id": case_id})
