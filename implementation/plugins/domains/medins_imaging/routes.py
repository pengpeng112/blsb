"""济南医保影像云路由（RT-06 F03）：契约浏览 + 本地校验 + 本地示范投影闭环。

- 全部走 domain.routes 通用挂载（核心 app 无业务特判）；
- 数据集代码=规范大写原样（APPLY_INFO/…）；来源 revision r2-csv16 只读；
- POST /project：本地示范投影闭环——合成/粘贴记录 → 契约校验（错误行进质量
  等待清单，不冻结）→ 冻结 PackageRevision（domain=medins_imaging，幂等=内容
  digest）→ Submission → generic 回环 Mock 投递 → 审计。不伪装正式医保传输：
  目的地必须 is_mock，真实发送被闸门拒绝；
- 质量等待不冻结：错误行原样返回（错误文案无原始敏感值——校验器保证）。
"""
from __future__ import annotations

import json
import os

from contracts import ok
from contracts.api import ROLE_OPERATOR, ROLE_PLATFORM_ADMIN
from core_db.engine import utcnow
from mapping_engine.contract_validator import validate_message

TARGET = "medins_imaging_jn"
DOMAIN = "medins_imaging"
REVISION = "r2-csv16"

_REV_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))), "profiles", "medins_imaging_jn",
    "revisions", REVISION)


def _load(rel: str):
    p = os.path.join(_REV_DIR, rel)
    if not os.path.isfile(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def register_routes(app, sf, auth, registry):
    from fastapi import Body, Depends, Query

    from contracts import ApiError
    from contracts.api import ROLE_CONFIG_EDITOR

    def _require_scope(ctx):
        p = ctx.require_principal()
        p.require_target_scope(TARGET)          # 先范围后存在性（R08）
        return p

    @app.get("/api/v1/medins-imaging/datasets")
    def imaging_datasets(ctx=Depends(auth)):
        """16 数据集清单：真实代码/字段数/revision/未确认计数。"""
        _require_scope(ctx)
        ds = _load("datasets.json")
        if ds is None:
            raise ApiError("NOT_FOUND", "影像契约 revision 未生成")
        items = []
        for d in ds["datasets"]:
            c = _load(f"fields/{d['code']}.json") or {"nodes": []}
            pending = sum(1 for n in c["nodes"] if any(
                str(t).startswith("SPEC_FIELD_CONSTRAINT_PENDING")
                for t in (n.get("notes") or [])))
            items.append({"code": d["code"], "name": d.get("name") or "",
                          "field_count": d["field_count"],
                          "pending_count": pending,
                          "revision": REVISION})
        return ok({"items": items, "count": len(items),
                   "revision": REVISION}, ctx.request_id)

    @app.get("/api/v1/medins-imaging/datasets/{code}")
    def imaging_dataset_detail(code: str, ctx=Depends(auth)):
        """单数据集字段树 + 全新合成样例；未知 code 明确 404（scope 先校验）。"""
        _require_scope(ctx)
        c = _load(f"fields/{code}.json")
        if c is None:
            raise ApiError("NOT_FOUND", f"未知数据集 {code}")
        sample = _load(f"samples/{code}.json")
        return ok({"code": code, "revision": REVISION,
                   "nodes": c.get("nodes", []), "sample": sample},
                  ctx.request_id)

    @app.post("/api/v1/medins-imaging/validate")
    def imaging_validate(body: dict = Body(...), ctx=Depends(auth)):
        """本地校验指定数据集（不出网）；错误文案不含原始敏感值（校验器保证）。"""
        p = _require_scope(ctx)
        p.require_any_role(ROLE_OPERATOR, ROLE_CONFIG_EDITOR,
                           ROLE_PLATFORM_ADMIN)
        code = str(body.get("dataset", "") or "")
        records = body.get("records")
        if not isinstance(records, list) or not records:
            raise ApiError("VALIDATION_FAILED", "records 必填（非空数组）")
        if len(records) > 20:
            raise ApiError("REQUEST_TOO_LARGE", "records ≤20 条")
        c = _load(f"fields/{code}.json")
        if c is None:
            raise ApiError("NOT_FOUND", f"未知数据集 {code}")
        out = []
        for i, rec in enumerate(records):
            errs = validate_message({"input": rec}, c, None, {})
            hard = [e for e in errs if e["level"] == "error"]
            out.append({"row": i, "errors": errs,
                        "ok": not hard})
        return ok({"dataset": code, "rows": out,
                   "ok_rows": sum(1 for r in out if r["ok"])},
                  ctx.request_id)

    @app.post("/api/v1/medins-imaging/project")
    def imaging_project(body: dict = Body(...), ctx=Depends(auth)):
        """本地示范投影：records → 校验 → 冻结 → Submission → generic 回环
        Mock。数据集/记录内容合成或院方粘贴（本轮无真实源）。"""
        from contracts import new_uuid
        from core_db.models import (AuditEvent, DataPackage, Destination,
                                    PackageRevision, Submission)
        from contracts.api import digest_of
        p = _require_scope(ctx)
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        code = str(body.get("dataset", "") or "")
        records = body.get("records")
        if not isinstance(records, list) or not records:
            raise ApiError("VALIDATION_FAILED", "records 必填（非空数组）")
        if len(records) > 20:
            raise ApiError("REQUEST_TOO_LARGE", "records ≤20 条")
        c = _load(f"fields/{code}.json")
        if c is None:
            raise ApiError("NOT_FOUND", f"未知数据集 {code}")
        with sf() as db:
            # 目的地：target 绑定的 is_mock 目的地（无绑定/非 mock → 拒绝）
            from plugins.report_targets import get_target as _get_target
            t = _get_target(db, p.tenant_id, TARGET)
            dest = None
            if t.destination_id:
                dest = db.get(Destination, t.destination_id)
            if dest is None or not dest.is_mock:
                raise ApiError(
                    "PROFILE_UNAPPROVED",
                    "影像示范投影只允许 target 绑定的 Mock 目的地"
                    "（真实传输能力未确认，禁止伪装正式医保发送）")
            # 校验（错误行进质量等待清单，不冻结）
            rejected = []
            clean = []
            for i, rec in enumerate(records):
                errs = validate_message({"input": rec}, c, None, {})
                hard = [e for e in errs if e["level"] == "error"]
                (rejected if hard else clean).append(
                    {"row": i, "errors": errs} if hard else rec)
            payload = {"dataset": code, "revision": REVISION, "rows": clean}
            digest = digest_of(payload)
            # 幂等：同 dataset+同内容 digest 的未终态/已发 Submission 不重建
            # （复核修正：幂等键须限定 destination——跨目的地同内容不得复用）
            existing = (db.query(Submission)
                        .filter_by(tenant_id=p.tenant_id,
                                   destination_id=dest.id,
                                   snapshot_digest=digest)
                        .first())
            if existing is not None:
                return ok({"submission_id": existing.id,
                           "idempotent": True, "frozen": len(clean),
                           "quality_waiting": len(rejected),
                           "outcome": existing.state}, ctx.request_id)
            business_key = f"imaging:{code}:{digest[:12]}"
            pkg = (db.query(DataPackage)
                   .filter_by(tenant_id=p.tenant_id, domain=DOMAIN,
                              business_key=business_key).first())
            if pkg is None:
                pkg = DataPackage(id=new_uuid(), tenant_id=p.tenant_id,
                                  domain=DOMAIN, business_key=business_key)
                db.add(pkg)
                db.flush()
            pr = (db.query(PackageRevision)
                  .filter_by(package_id=pkg.id, digest=digest)
                  .order_by(PackageRevision.revision.desc()).first())
            if pr is None:
                last = (db.query(PackageRevision)
                        .filter_by(package_id=pkg.id)
                        .order_by(PackageRevision.revision.desc()).first())
                pr = PackageRevision(
                    id=new_uuid(), tenant_id=p.tenant_id, package_id=pkg.id,
                    revision=(last.revision + 1) if last else 1,
                    payload=payload, digest=digest, state="FROZEN",
                    provenance={"domain": DOMAIN, "dataset": code,
                                "revision": REVISION})
                db.add(pr)
            db.flush()
            sub = Submission(
                id=new_uuid(), tenant_id=p.tenant_id,
                package_revision_id=pr.id, destination_id=dest.id,
                submission_revision=1, snapshot_digest=digest,
                state="PREPARED",
                idempotency_key=f"imaging:{digest[:32]}")
            db.add(sub)
            db.flush()
            from core_handlers import perform_push
            result = perform_push(db, p.tenant_id, sub,
                                  {"dataset": code, "rows": clean})
            db.add(AuditEvent(
                id=new_uuid(), tenant_id=p.tenant_id, actor=p.subject_code,
                action="medins_imaging.project", target=f"submission:{sub.id}",
                new_ref={"dataset": code, "frozen": len(clean),
                         "quality_waiting": len(rejected),
                         "outcome": result.get("outcome"),
                         "mock": bool(dest.is_mock)}))
            db.commit()
            return ok({
                "submission_id": sub.id, "outcome": result.get("outcome"),
                "frozen": len(clean), "quality_waiting": rejected,
                "idempotent": False, "mock": True,
                "note": "本地示范投影（Mock 回环）；不构成正式医保传输"},
                ctx.request_id)
