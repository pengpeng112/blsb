"""metrics 领域插件（W11 manifest 注册）：CSV 指标月报，无患者字段。

独立于病理插件：metrics-only 进程仅加载本插件即可完成
CSV导入→映射→校验→冻结→双目标Mock，不创建病理表。
"""
from __future__ import annotations

from contracts import PackageRevisionState, new_uuid
from contracts.api import digest_of
from core_db.models import DataPackage, PackageRevision
from workflow_core.dag import StepOutcome
from workflow_core.jobs import emit as emit_outbox

from core_handlers import HandlerCtx, _prev_output

PLUGIN_KEY = "metrics"
CAPABILITIES = ["csv_import", "freeze", "map"]


def h_metrics_import(ctx: HandlerCtx) -> StepOutcome:
    """CSV指标行 → 数据包修订（领域 metrics，无患者字段）。"""
    package_id = ctx.step.input_refs.get("package_id")
    pkg = ctx.db.get(DataPackage, package_id) if package_id else None
    if pkg is None:
        return StepOutcome("FAILED", error={"message": "package not found"})
    last = (ctx.db.query(PackageRevision)
            .filter_by(package_id=pkg.id)
            .order_by(PackageRevision.revision.desc()).first())
    if last and last.state == PackageRevisionState.FROZEN:
        return StepOutcome("SUCCEEDED", outputs={"package_revision_id": last.id})
    payload = {"rows": pkg and last.payload.get("rows") if last else []}
    return StepOutcome("SUCCEEDED", outputs={"package_revision_id": last.id if last else None})


def h_metrics_freeze(ctx: HandlerCtx) -> StepOutcome:
    if ctx.dry_run:
        return StepOutcome("SUCCEEDED", outputs={"dry_run": True})
    package_id = ctx.step.input_refs.get("package_id")
    pkg = ctx.db.get(DataPackage, package_id)
    if pkg is None:
        return StepOutcome("FAILED", error={"message": "package not found"})
    rows = ctx.step.input_refs.get("rows") or []
    digest = digest_of({"rows": rows})
    last = (ctx.db.query(PackageRevision)
            .filter_by(package_id=pkg.id)
            .order_by(PackageRevision.revision.desc()).first())
    if last and last.digest == digest and last.state == "FROZEN":
        return StepOutcome("SUCCEEDED", outputs={"package_revision_id": last.id})
    rev = (last.revision + 1) if last else 1
    pr = PackageRevision(
        id=new_uuid(), tenant_id=ctx.tenant_id, package_id=pkg.id, revision=rev,
        schema_ref={"key": "metrics.monthly", "version": "1.0"},
        payload={"rows": rows}, digest=digest,
        provenance={"import": ctx.step.input_refs.get("import_ref")},
        state="FROZEN")
    ctx.db.add(pr)
    pkg.current_revision = rev
    ctx.db.commit()
    emit_outbox(ctx.db, ctx.tenant_id, f"package_revision:{pr.id}", "package.frozen",
                {"package_revision_id": pr.id, "package_id": pkg.id, "domain": "metrics"})
    ctx.db.commit()
    return StepOutcome("SUCCEEDED", outputs={"package_revision_id": pr.id})


def h_metrics_map(ctx: HandlerCtx) -> StepOutcome:
    pr_id = _prev_output(ctx, "package_revision_id") or ctx.step.input_refs.get(
        "package_revision_id")
    pr = ctx.db.get(PackageRevision, pr_id) if pr_id else None
    if pr is None:
        return StepOutcome("FAILED", error={"message": "no package revision"})
    return StepOutcome("SUCCEEDED", outputs={
        "payload": {"submission_key": f"{pr.package_id}:{pr.revision}",
                    "domain": "metrics", "rows": pr.payload.get("rows", []),
                    "provenance": {"package_revision": pr.id, "digest": pr.digest}},
        "package_revision_id": pr.id})


def register(reg) -> None:
    reg.add_handlers({
        ("parse", "metrics.csv"): h_metrics_import,
        ("package.freeze", "metrics.package"): h_metrics_freeze,
        ("target.map", "profile.metrics_json"): h_metrics_map,
    })
