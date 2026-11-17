"""核心通用节点处理器与装配辅助（W11 拆分）。

本模块不 import 任何领域插件：目标/投递/回执/发布处理器只依赖
核心模型（Task/Package/Asset/Submission/Publication）与目的地库，
供 pathology / metrics 等领域共享。领域特定处理器见各领域 plugin.py。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from contracts import (ApiError, SubmissionState, new_uuid)
from contracts.api import digest_of
from core_db.engine import utcnow
from core_db.models import (DataPackage, Destination, PackageRevision, Publication,
                            ReceiptEvent, Submission, SubmissionItem, DeliveryAttempt)
from workflow_core.dag import StepOutcome


# ================================================================ 处理器上下文
@dataclass
class HandlerCtx:
    db: object
    tenant_id: str
    run: object
    step: object
    params: dict
    dry_run: bool = False


def production_allowed(db, tenant_id: str) -> bool:
    """生产守卫（W10）：存在 OPEN 外部阻断时禁止真实发送/发布。
    文件归集与本地处理不受此守卫影响。"""
    from core_db.models import ExternalBlocker
    return db.query(ExternalBlocker).filter_by(
        tenant_id=tenant_id, status="OPEN").count() == 0


def enqueue(db, tenant_id, job_type, dedup_key, payload, *, delay_seconds: int = 0):
    from datetime import timedelta
    from workflow_core.jobs import enqueue_job
    job = enqueue_job(db, tenant_id, job_type, dedup_key, payload)
    if delay_seconds:
        job.available_at = utcnow() + timedelta(seconds=delay_seconds)
    db.flush()
    return job


def _case_from_run(ctx: HandlerCtx) -> str | None:
    return ctx.step.input_refs.get("case_id") or ctx.run.context.get("case_id")


def _package_revision(ctx: HandlerCtx):
    pr_id = ctx.step.input_refs.get("package_revision_id") or \
        _prev_output(ctx, "package_revision_id") or \
        ctx.run.context.get("package_revision_id")
    if pr_id:
        return ctx.db.get(PackageRevision, pr_id)
    case_id = _case_from_run(ctx)
    if not case_id:
        return None
    from domains.pathology.models import PathologyCase  # 领域实体按需加载
    case = ctx.db.get(PathologyCase, case_id)
    if case is None:
        return None
    from domains.pathology import pipeline as path_pipeline
    pkg = path_pipeline.get_or_create_package(ctx.db, ctx.tenant_id, case)
    if not pkg.current_revision:
        return None
    return (ctx.db.query(PackageRevision)
            .filter_by(package_id=pkg.id, revision=pkg.current_revision).first())


def _prev_output(ctx: HandlerCtx, key: str):
    """上游步骤输出沿 run 传递：查本run已完成步骤的 outputs。"""
    from core_db.models import StepRun
    steps = (ctx.db.query(StepRun)
             .filter_by(run_id=ctx.run.id, state="SUCCEEDED")
             .order_by(StepRun.updated_at).all())
    for s in reversed(steps):
        if s.output_refs and key in s.output_refs:
            return s.output_refs[key]
    return ctx.run.context.get(key)


# ================================================================ 目标映射/校验
def h_target_map(ctx: HandlerCtx) -> StepOutcome:
    """目标映射：构建提交载荷（索引+玻片+报告）；逐字段血缘记录在 payload.provenance。"""
    pr = _package_revision(ctx)
    if pr is None:
        return StepOutcome("FAILED", error={"message": "no frozen package revision"})
    payload = dict(pr.payload)
    submission_payload = {
        "submission_key": f"{pr.package_id}:{pr.revision}",
        "domain": payload.get("domain") or _domain_of(ctx, pr),
        "index": payload.get("index"),
        "case": payload.get("case"),
        "slides": payload.get("slides"),
        "reports": payload.get("reports"),
        "provenance": {"package_revision": pr.id, "digest": pr.digest},
    }
    return StepOutcome("SUCCEEDED", outputs={"payload": submission_payload,
                                             "package_revision_id": pr.id})


def _domain_of(ctx: HandlerCtx, pr) -> str:
    pkg = ctx.db.get(DataPackage, pr.package_id)
    return pkg.domain if pkg is not None else "unknown"


def h_target_validate(ctx: HandlerCtx) -> StepOutcome:
    payload = (ctx.step.input_refs.get("payload")
               or _prev_output(ctx, "payload") or {})
    if payload.get("domain") == "metrics":
        rows = payload.get("rows") or []
        if not rows:
            return StepOutcome("FAILED", error={
                "code": "REQUIRED_FIELD_MISSING", "message": "metrics rows empty"})
        import re as _re
        for i, r in enumerate(rows):
            if not _re.fullmatch(r"-?\d+\.\d{2}", str(r.get("amount", ""))):
                return StepOutcome("FAILED", error={
                    "code": "VALIDATION_FAILED",
                    "message": f"metrics row {i} amount not decimal(2): {r.get('amount')!r}"})
        return StepOutcome("SUCCEEDED", outputs={"validated": True})
    missing = [k for k in ("index", "case", "slides") if not payload.get(k)]
    if missing:
        return StepOutcome("FAILED", error={
            "code": "REQUIRED_FIELD_MISSING", "message": f"missing {missing}"})
    if not payload.get("index", {}).get("serialized"):
        return StepOutcome("FAILED", error={
            "code": "REQUIRED_FIELD_MISSING",
            "message": "index not serialized; quality gates did not pass"})
    return StepOutcome("SUCCEEDED", outputs={"validated": True})


# ================================================================ 投递/回执/发布
# T2 推式 ingress：worker/engine.py::_route_event 认识的事件类型才置 ROUTED
# 并发 OutboxEvent（未知类型保持 RECEIVED，不猜测路由）。
INGRESS_ROUTABLE_EVENT_TYPES = frozenset({
    "asset.available",
    "pathology.case.updated",
    "package.frozen",
    "submission.confirmed",
    "pis.source_changed",
    "pathology.case.refresh",
})


def resolve_push_destination(db, tenant_id: str, destination_id: str | None = None):
    """选取PUSH目的地并做生产守卫（W10/R38；DTR-BASE-01：显式目标优先）。

    destination_id 显式给出时按其精确选取（校验 PUSH/ENABLED/租户）；否则沿用
    既有「首个 PUSH」兼容语义（病理/metrics 既有链路）。无目的地/真实目的地遇
    OPEN 阻断抛 ApiError（调用方决定映射为步骤FAILED还是HTTP错误）。"""
    if destination_id:
        dest = db.get(Destination, destination_id)
        if dest is None or dest.tenant_id != tenant_id:
            raise ApiError("NOT_FOUND", "destination not found")
        if dest.mode != "PUSH" or dest.status != "ENABLED":
            raise ApiError("CAPABILITY_UNSUPPORTED",
                           f"destination {destination_id} not PUSH/ENABLED")
    else:
        dest = (db.query(Destination)
                .filter_by(tenant_id=tenant_id, mode="PUSH", status="ENABLED")
                .first())
    if dest is None:
        raise ApiError("CAPABILITY_UNSUPPORTED", "no PUSH destination enabled")
    if not dest.is_mock and not production_allowed(db, tenant_id):
        # W10：真实目的地发送前统一能力守卫（Mock不受影响）
        raise ApiError(
            "PROFILE_UNAPPROVED",
            "real destination blocked by open external blockers (B01—B09)")
    return dest


# DTR-BASE-01：非 Mock 目标适配器注册（插件加载时登记；未知 plugin_id 拒绝，
# 不得让真实目标静默跑成 MockHttpClient）
DESTINATION_ADAPTERS: dict[str, object] = {}


def register_destination_adapter(plugin_id: str, fn) -> None:
    DESTINATION_ADAPTERS[plugin_id] = fn


def build_delivery_payload(db, pr) -> dict:
    """冻结包 → 提交载荷（与工作流 target.map 节点同一语义：病理索引+玻片+报告
    / metrics 行）。工作流 send 节点与手动提交（T7）共用，不复制两份。"""
    payload = dict(pr.payload)
    pkg = db.get(DataPackage, pr.package_id)
    domain = payload.get("domain") or (pkg.domain if pkg is not None else "unknown")
    if domain == "metrics":
        return {
            "submission_key": f"{pr.package_id}:{pr.revision}",
            "domain": "metrics", "rows": payload.get("rows", []),
            "provenance": {"package_revision": pr.id, "digest": pr.digest}}
    return {
        "submission_key": f"{pr.package_id}:{pr.revision}",
        "domain": domain,
        "index": payload.get("index"),
        "case": payload.get("case"),
        "slides": payload.get("slides"),
        "reports": payload.get("reports"),
        "provenance": {"package_revision": pr.id, "digest": pr.digest},
    }


def ensure_push_submission(db, tenant_id: str, dest, pr, payload: dict):
    """按 (destination, package_revision) 幂等键查找/创建 Submission + 提交项。
    返回 (submission, created)。D01：重复推送复用既有提交，不新建业务对象。"""
    idem = f"{dest.id}:{pr.id}"
    submission = (db.query(Submission)
                  .filter_by(destination_id=dest.id, idempotency_key=idem).first())
    if submission is not None:
        return submission, False
    submission = Submission(
        id=new_uuid(), tenant_id=tenant_id, package_revision_id=pr.id,
        destination_id=dest.id, submission_revision=1,
        snapshot_digest=pr.digest, state=SubmissionState.PREPARED,
        idempotency_key=idem)
    db.add(submission)
    db.flush()
    db.add(SubmissionItem(
        id=new_uuid(), tenant_id=tenant_id, submission_id=submission.id,
        item_key="index", data_ref={"kind": "index",
                                    "value": payload.get("index")}))
    for s in payload.get("slides", []) or []:
        for scan in s.get("scans", []):
            if scan.get("effective"):
                for a in scan.get("assets", []):
                    db.add(SubmissionItem(
                        id=new_uuid(), tenant_id=tenant_id,
                        submission_id=submission.id,
                        item_key=f"asset:{a['asset_id']}",
                        asset_id=a["asset_id"],
                        data_ref={"sha256": a["sha256"], "role": "asset"}))
    db.flush()
    return submission, True


def _destination_config(db, dest) -> dict:
    """读目的地 PUBLISHED ConfigRevision（config_type="destination",
    owner_id=dest.id）→ content_json；无则 {}（LT-03）。"""
    from core_db.models import ConfigRevision
    if not getattr(dest, "config_revision", None):
        return {}
    cr = db.get(ConfigRevision, dest.config_revision)
    if cr is None or cr.state != "PUBLISHED":
        return {}
    return dict(cr.content_json or {})


def perform_push(db, tenant_id: str, submission: Submission, payload: dict) -> dict:
    """执行一次HTTP推送并落 DeliveryAttempt 与状态转换（outcome 语义与
    h_deliver_push 原实现一致：ACCEPTED/PARTIAL/REJECTED/UNSUPPORTED/UNKNOWN）。

    DTR-BASE-01：真实（非 Mock）目标按 plugin_id 派发到已注册适配器；
    未注册适配器的真实目标显式 CAPABILITY_UNSUPPORTED，绝不回落 Mock。

    LT-03-R1（R02）：Mock 目的地若其 profile 键为 generic
    （destination.generic_http_json），也显式派发到 generic 适配器——本地
    Mock/影子必须走与真实同一 generic 代码路径与行为档（旧行为档兼容：
    其他 Mock 目的地仍走 MockHttpClient；药品路径不受影响）。generic 服务层
    对 is_mock=True 强制本机回环地址，改 is_mock/Target 字段不能把非本机
    地址带入 Mock 分支，也不能绕过真实分支闸门。
    """
    dest = db.get(Destination, submission.destination_id)
    profile_key = (dest.profile_revision or "").split("@")[0] \
        if dest is not None else ""
    adapter = DESTINATION_ADAPTERS.get(profile_key) if dest is not None else None
    if dest is not None and not dest.is_mock:
        # LT-03 修订：Destination 无 plugin_id 列（旧代码 dest.plugin_id 会
        # AttributeError）——适配器按 profile 键解析：profile_revision 取
        # "插件前缀@版本" 的前缀（药品=alihealth_yljg_*；generic 目的地用
        # destination.generic_http_json 作为 profile 前缀）。同时在派发前接入
        # LT-01 生产闸门：真实 generic 目的地必须登记 report_target_code 且过
        # assert_target_can_send_real；**药品 profile 键不在闸门范围**（药品
        # 沿用 DrugTraceProductionEvidence 门禁，LT-03 不改生产链路，且药品
        # 真实发送实际不经 perform_push——tests/integration/
        # test_perform_push_gate.py 有证明测试）。
        if adapter is None:
            raise ApiError(
                "CAPABILITY_UNSUPPORTED",
                f"no adapter registered for real destination profile "
                f"{profile_key!r}; refusing to fall back to mock")
        if profile_key.startswith("destination."):
            cfg_cfg = _destination_config(db, dest)
            target_code = str((cfg_cfg or {}).get("report_target_code", "") or "")
            if not target_code:
                raise ApiError(
                    "PROFILE_UNAPPROVED",
                    "real destination has no report_target_code; register a "
                    "ReportTarget and reference it before real sending (LT-01)")
            from report_targets import assert_target_can_send_real
            assert_target_can_send_real(db, tenant_id, target_code)
            # LT-03-R1（R02）：真实发送须有独立启用证据引用（工单/批复编号等），
            # 与 target 字段分开登记——只改 Target 字段不构成启用
            if not str((cfg_cfg or {}).get("real_send_evidence_ref", "") or "").strip():
                raise ApiError(
                    "PROFILE_UNAPPROVED",
                    "real destination config has no real_send_evidence_ref; "
                    "real sending requires independent enablement evidence "
                    "(ticket/approval reference)")
        return adapter(db, tenant_id, submission, payload)
    if dest is not None and dest.is_mock and adapter is not None and \
            profile_key.startswith("destination."):
        # LT-03-R1：Mock/影子 generic 目的地显式走 generic 装配（配置与行为档
        # 与真实一致；回环强制在 perform_generic_push）。未注册适配器的
        # destination.* profile 不猜测回落 MockHttpClient（fail-closed）。
        return adapter(db, tenant_id, submission, payload)
    from destinations.mock_http.client import MockHttpClient
    # Mock行为由环境变量注入（测试注入故障场景）；正式目的地行为应来自其发布配置
    client = MockHttpClient(base_url=os.environ.get(
        "BLYB_MOCK_PLATFORM_URL", "http://127.0.0.1:8090"),
        behavior=os.environ.get("BLYB_MOCK_BEHAVIOR", "sync_success"))
    idem = submission.idempotency_key
    result = client.submit(payload, idem)
    db.add(DeliveryAttempt(
        id=new_uuid(), tenant_id=tenant_id, submission_id=submission.id,
        attempt_no=1, request_digest=digest_of(payload)[:64],
        started_at=utcnow(), ended_at=utcnow(), http_status=result.get("http_status"),
        outcome=result["outcome"], response_snapshot={"remote_ref": result.get("remote_ref")}))
    if result["outcome"] == "ACCEPTED":
        submission.state = SubmissionState.ACCEPTED
    elif result["outcome"] == "PARTIAL":
        submission.state = SubmissionState.PARTIAL
    elif result["outcome"] == "REJECTED":
        submission.state = SubmissionState.REJECTED
    elif result["outcome"] == "UNSUPPORTED":
        submission.state = SubmissionState.REJECTED
    else:  # UNKNOWN：查询恢复，不新建提交（D01）
        submission.state = SubmissionState.UNKNOWN
    db.commit()
    return result


def h_deliver_push(ctx: HandlerCtx) -> StepOutcome:
    if ctx.dry_run:
        return StepOutcome("SUCCEEDED", outputs={"dry_run": True})
    payload = _prev_output(ctx, "payload") or {}
    pr_id = _prev_output(ctx, "package_revision_id")
    pr = ctx.db.get(PackageRevision, pr_id) if pr_id else None
    if pr is None:
        return StepOutcome("FAILED", error={"message": "package revision missing"})
    try:
        dest = resolve_push_destination(ctx.db, ctx.tenant_id)
    except ApiError as exc:
        return StepOutcome("FAILED", error={"code": exc.code,
                                            "message": exc.message})
    submission, _ = ensure_push_submission(ctx.db, ctx.tenant_id, dest, pr, payload)
    if submission.state in (SubmissionState.CONFIRMED, SubmissionState.ACCEPTED):
        return StepOutcome("SUCCEEDED", outputs={"submission_id": submission.id})
    result = perform_push(ctx.db, ctx.tenant_id, submission, payload)
    if result["outcome"] in ("ACCEPTED", "PARTIAL"):
        return StepOutcome("SUCCEEDED", outputs={"submission_id": submission.id,
                                                 "outcome": result["outcome"]})
    if result["outcome"] == "UNKNOWN":
        return StepOutcome("WAITING_EXTERNAL", wait_reason="REMOTE_RESULT_UNKNOWN",
                           outputs={"submission_id": submission.id},
                           retry_after=5)
    return StepOutcome("FAILED", error={"code": "REMOTE_REJECTED",
                                        "message": result.get("error_code", "rejected")},
                        outputs={"submission_id": submission.id})


def h_receipt_wait(ctx: HandlerCtx) -> StepOutcome:
    submission_id = _prev_output(ctx, "submission_id")
    if not submission_id:
        return StepOutcome("FAILED", error={"message": "no submission in context"})
    submission = ctx.db.get(Submission, submission_id)
    if submission is None:
        return StepOutcome("FAILED", error={"message": "submission not found"})
    if submission.state == SubmissionState.CONFIRMED:
        return StepOutcome("SUCCEEDED", outputs={"submission_id": submission_id,
                                                 "confirmed": True})
    from destinations.mock_http.client import MockHttpClient
    client = MockHttpClient(base_url=os.environ.get(
        "BLYB_MOCK_PLATFORM_URL", "http://127.0.0.1:8090"))
    receipts = client.poll_receipts()
    matched = [r for r in receipts if r.get("submission_key") == submission.idempotency_key]
    for r in matched:
        _store_receipt(ctx.db, ctx.tenant_id, submission.destination_id, r, submission.id)
    ctx.db.commit()
    if matched:
        return StepOutcome("SUCCEEDED", outputs={"submission_id": submission_id,
                                                 "receipts": len(matched)})
    # 无回执：保持待确认（D06：不显示医保成功）
    enqueue(ctx.db, ctx.tenant_id, "destination.poll_receipts",
            f"{submission.id}", {"submission_id": submission.id}, delay_seconds=2)
    ctx.db.commit()
    return StepOutcome("WAITING_EXTERNAL", wait_reason="AWAITING_RECEIPT",
                       outputs={"submission_id": submission_id})


def _store_receipt(db, tenant_id, destination_id, raw: dict, submission_id: str | None,
                   publication_id: str | None = None):
    existing = (db.query(ReceiptEvent)
                .filter_by(tenant_id=tenant_id, destination_id=destination_id,
                           external_receipt_id=raw.get("receipt_id", "")).first())
    if existing is not None:
        return existing  # 幂等；旧回执不回退新版本
    ev = ReceiptEvent(
        id=new_uuid(), tenant_id=tenant_id, destination_id=destination_id,
        external_receipt_id=raw.get("receipt_id", f"digest:{digest_of(raw)[:16]}"),
        submission_ref=submission_id, publication_ref=publication_id,
        raw_payload=raw,
        parsed_result={"result": raw.get("result"), "seq": raw.get("seq")})
    db.add(ev)
    if submission_id:
        sub = db.get(Submission, submission_id)
        if sub is not None:
            # R01：ACCEPTED=传输受理，不推进业务确认；只有 CONFIRMED 才是目标业务成功
            if raw.get("result") == "CONFIRMED":
                apply_business_confirmation(db, tenant_id, submission_id)
            elif raw.get("result") == "ACCEPTED":
                sub.state = SubmissionState.ACCEPTED
            # UNKNOWN/PARTIAL/REJECTED 不改写状态，留待逐项对账
    db.flush()
    return ev


def apply_business_confirmation(db, tenant_id: str, submission_id: str) -> bool:
    """CONFIRMED 回执的业务落地（功能测试发现的跨入口不一致修复）。

    三个回执入口（worker轮询、core API、交换网关）必须产生一致的业务状态：
    submission→CONFIRMED 且病例 target_confirmed=True。幂等；REJECTED 后的
    迟到/矛盾 CONFIRMED 不倒退状态（回执事件本身仍保留供对账）。
    """
    sub = db.get(Submission, submission_id)
    if sub is None or sub.tenant_id != tenant_id:
        return False
    if sub.state == SubmissionState.CONFIRMED:
        return True
    if sub.state == SubmissionState.REJECTED:
        return False
    sub.state = SubmissionState.CONFIRMED
    # 目标业务确认 → 病例分列状态（区别于文件落盘/玻片关联/临床就绪）
    pr = db.get(PackageRevision, sub.package_revision_id)
    case_id = (pr.provenance or {}).get("case_id") if pr else None
    if case_id:
        try:
            from domains.pathology.models import PathologyCase
            case = db.get(PathologyCase, case_id)
            if case is not None:
                case.target_confirmed = True
        except ImportError:
            pass  # metrics-only：无病理领域，忽略病例分列
    return True


def h_deliver_publish(ctx: HandlerCtx) -> StepOutcome:
    """视图发布：冻结包 → 发布前完整成员校验 → 交换库 PREPARING → 文件可达检查
    → 单事务PUBLISHED + PublicationItem落库。

    R05/R42：任一必需成员（Asset/Blob/Location/文件存在/Blob健康）不满足即阻断，
    不再静默 continue；交换区保持不可见。
    """
    if ctx.dry_run:
        return StepOutcome("SUCCEEDED", outputs={"dry_run": True})
    pr = _package_revision(ctx)
    if pr is None:
        return StepOutcome("FAILED", error={"message": "no frozen package revision"})
    dest = (ctx.db.query(Destination)
            .filter_by(tenant_id=ctx.tenant_id, mode="PULL_PUBLICATION",
                       status="ENABLED").first())
    if dest is None:
        return StepOutcome("FAILED", error={"code": "CAPABILITY_UNSUPPORTED",
                                            "message": "no PULL_PUBLICATION destination"})
    if not dest.is_mock and not production_allowed(ctx.db, ctx.tenant_id):
        return StepOutcome("FAILED", error={
            "code": "PROFILE_UNAPPROVED",
            "message": "real destination blocked by open external blockers (B01—B09)"})
    from destinations.view_publication.exchange_db import ExchangeDb
    from core_db.models import Asset, AssetLocation, Blob, PublicationItem
    exchange_path = os.environ.get(
        "BLYB_EXCHANGE_DB", os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "var", "exchange.sqlite3"))
    ex = ExchangeDb(exchange_path)
    generation = f"gen-{pr.package_id[:8]}-r{pr.revision}-{pr.id[:8]}"
    pub = (ctx.db.query(Publication)
           .filter_by(tenant_id=ctx.tenant_id, package_revision_id=pr.id,
                      destination_id=dest.id).first())
    if pub is None:
        pub = Publication(id=new_uuid(), tenant_id=ctx.tenant_id,
                          package_revision_id=pr.id, destination_id=dest.id,
                          generation_id=generation, state="PREPARING")
        ctx.db.add(pub)
        ctx.db.flush()
    if pub.state == "PUBLISHED":
        return StepOutcome("SUCCEEDED", outputs={"publication_id": pub.id})
    batch_id = ex.begin_batch(pub.id, generation,
                              dataset_scope=dest.code, manifest_digest=pr.digest,
                              tenant_id=ctx.tenant_id)
    payload = pr.payload
    # ---- 发布前成员校验：清单里每个有效扫描的每个资产都必须可达（R05/R42）
    required: list[tuple[str, str]] = []  # (asset_id, 拒绝原因)
    asset_refs: list[dict] = []
    for s in payload.get("slides", []) or []:
        for scan in s.get("scans", []):
            if not scan.get("effective"):
                continue
            for a in scan.get("assets", []):
                asset_id = a["asset_id"]
                asset = ctx.db.get(Asset, asset_id)
                if asset is None:
                    required.append((asset_id, "asset row missing"))
                    continue
                if asset.tenant_id != ctx.tenant_id:
                    required.append((asset_id, "asset tenant mismatch"))
                    continue
                if not asset.blob_id:
                    required.append((asset_id, "asset has no blob"))
                    continue
                blob = ctx.db.get(Blob, asset.blob_id)
                if blob is None:
                    required.append((asset_id, "blob missing"))
                    continue
                if blob.state != "AVAILABLE":
                    required.append((asset_id, f"blob state {blob.state}"))
                    continue
                loc = (ctx.db.query(AssetLocation)
                       .filter_by(blob_id=asset.blob_id, state="AVAILABLE").first())
                if loc is None:
                    required.append((asset_id, "no AVAILABLE location"))
                    continue
                if blob.sha256 != a.get("sha256"):
                    required.append((asset_id, "hash mismatch vs frozen manifest"))
                    continue
                asset_refs.append({"slide": s, "asset": asset, "blob": blob,
                                   "loc": loc})
    missing = [r for r, _ in required]
    if missing:
        ctx.db.rollback()
        return StepOutcome(
            "WAITING_EXTERNAL", wait_reason="ASSETS_UNREACHABLE",
            error={"code": "REQUIRED_FIELD_MISSING",
                   "message": "publication blocked; required members not reachable",
                   "members": [{"asset_id": a, "reason": r} for a, r in required]})

    # 投影：chk_info（检查）/ asset_info（文件路由）；metrics 包走指标投影
    if payload.get("case"):
        ex.upsert_projection(batch_id, "chk_info", payload["case"]["source_case_id"], {
            "examId": payload["case"]["source_case_id"],
            "pathologyNo": payload["case"]["pathology_no_raw"],
            "index": payload.get("index"),
            "systemId": "pathology-internal-v1"})
    else:
        ex.upsert_projection(batch_id, "metrics_report",
                             (pr.provenance or {}).get("import", f"pr:{pr.id}"), {
            "package": pr.package_id, "revision": pr.revision,
            "rows": len(payload.get("rows", []) or []),
            "digest": pr.digest, "systemId": "metrics-internal-v1"})
    routes = []
    unreachable = []
    for ref in asset_refs:
        s, asset, blob, loc = ref["slide"], ref["asset"], ref["blob"], ref["loc"]
        route_id = ex.add_asset_route(batch_id, asset.id, loc.physical_key,
                                      blob.size_bytes, blob.sha256,
                                      asset.media_type)
        if os.path.exists(loc.physical_key):
            ex.mark_asset_reachable(route_id)
            routes.append(route_id)
        else:
            unreachable.append({"asset_id": asset.id,
                                "reason": f"object file missing: volume offline?"})
        ex.upsert_projection(batch_id, "asset_info", asset.id, {
            "assetId": asset.id, "routeId": route_id, "role": asset.role,
            "sha256": blob.sha256, "sizeBytes": blob.size_bytes,
            "slideBarcode": s["barcode_raw"]})
    if unreachable:
        ctx.db.rollback()
        return StepOutcome(
            "WAITING_EXTERNAL", wait_reason="ASSETS_UNREACHABLE",
            error={"code": "VOLUME_OFFLINE",
                   "message": "publication blocked; object bytes unreachable",
                   "members": unreachable})
    ex.publish_batch(batch_id)
    pub.state = "PUBLISHED"
    pub.published_at = utcnow()
    pub.manifest_ref = {"batch_id": batch_id, "generation": generation,
                        "routes": routes}
    # 发布成员落库（S15：UI行数与发布清单一致）
    ctx.db.query(PublicationItem).filter_by(publication_id=pub.id).delete()
    if payload.get("case"):
        ctx.db.add(PublicationItem(
            id=new_uuid(), tenant_id=ctx.tenant_id, publication_id=pub.id,
            entity_type="chk_info", external_key=payload["case"]["source_case_id"],
            payload_ref={"dataset": "chk_info",
                         "examId": payload["case"]["source_case_id"]}, state="PUBLISHED"))
    else:
        ctx.db.add(PublicationItem(
            id=new_uuid(), tenant_id=ctx.tenant_id, publication_id=pub.id,
            entity_type="metrics_report",
            external_key=str((pr.provenance or {}).get("import", f"pr:{pr.id}")),
            payload_ref={"dataset": "metrics_report", "digest": pr.digest,
                         "rows": len(payload.get("rows", []) or [])}, state="PUBLISHED"))
    for ref in asset_refs:
        ctx.db.add(PublicationItem(
            id=new_uuid(), tenant_id=ctx.tenant_id, publication_id=pub.id,
            entity_type="asset_info", external_key=ref["asset"].id,
            asset_id=ref["asset"].id,
            payload_ref={"dataset": "asset_info", "sha256": ref["blob"].sha256,
                         "sizeBytes": ref["blob"].size_bytes}, state="PUBLISHED"))
    ctx.db.commit()
    ex.add_evidence(pub.id, "visible", {"batch_id": batch_id,
                                        "rows": 1 + len(routes)})
    return StepOutcome("SUCCEEDED", outputs={"publication_id": pub.id,
                                             "batch_id": batch_id})
