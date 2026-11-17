"""病理业务路由（04 §5.3 + 10 §7）。由 core_api 装配，核心不 import 本模块。"""
from __future__ import annotations

from fastapi import Depends, Header
from fastapi.responses import JSONResponse

from contracts import ApiError, new_uuid, ok
from contracts.api import (ROLE_AUDITOR, ROLE_BUSINESS_REVIEWER, ROLE_OPERATOR,
                           ROLE_PLATFORM_ADMIN, ROLE_SERVICE, audit)
from core_db.models import (Acquisition, AssociationCandidate, Job, Operation,
                            QualityIssue)
from core_db.engine import utcnow


def register(app, sf, auth, plugin_registry):

    @app.get("/api/v1/pathology/cases")
    def cases(ctx=Depends(auth), q: str = "", state: str = "", limit: int = 50):
        p = ctx.require_principal()
        with sf() as db:
            from .models import PathologyCase, Slide, ScanAcquisition
            query = db.query(PathologyCase).filter_by(tenant_id=p.tenant_id)
            if q:
                query = query.filter(PathologyCase.pathology_no_raw.contains(q))
            rows = query.order_by(PathologyCase.first_seen_at.desc()).limit(
                min(200, limit)).all()
            out = []
            for c in rows:
                slides = db.query(Slide).filter_by(case_id=c.id).count()
                scans = (db.query(ScanAcquisition)
                         .join(Slide, Slide.id == ScanAcquisition.slide_id)
                         .filter(Slide.case_id == c.id).count())
                open_q = (db.query(QualityIssue)
                          .filter_by(tenant_id=p.tenant_id,
                                     entity_ref=f"pathology_case:{c.id}",
                                     state="OPEN").count())
                out.append({
                    "id": c.id, "pathology_no": c.pathology_no_raw,
                    "namespace": c.registration_namespace,
                    "state": c.state,
                    "files_complete": c.files_complete,
                    "slides_linked": c.slides_linked,
                    "clinically_ready": c.clinically_ready,
                    "target_confirmed": c.target_confirmed,
                    "slides": slides, "scans": scans,
                    "open_quality_issues": open_q,
                    "first_seen_at": c.first_seen_at.isoformat()})
            return ok(out, ctx.request_id)

    @app.get("/api/v1/pathology/cases/{case_id}")
    def case_detail(case_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            from .models import (PathologyApplication, PathologyCaseApplication,
                                 PathologyCase, PathologyIndexRecord,
                                 PathologyReportVersion, PatientReference,
                                 EncounterReference, ExpectedSlideSet,
                                 ScanAcquisition, Slide, SlideEffectiveVersion)
            from core_db.models import (Asset, DataPackage, PackageRevision,
                                        Publication, Submission)
            c = db.get(PathologyCase, case_id)
            if c is None or c.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "case not found")
            slides = db.query(Slide).filter_by(case_id=c.id).all()
            slide_out = []
            for s in slides:
                scans = (db.query(ScanAcquisition)
                         .filter_by(slide_id=s.id)
                         .order_by(ScanAcquisition.created_at).all())
                eff = (db.query(SlideEffectiveVersion)
                       .filter_by(slide_id=s.id).first())
                scan_out = []
                for sc in scans:
                    acq = db.get(Acquisition, sc.acquisition_id)
                    assets = (db.query(Asset)
                              .filter_by(acquisition_id=sc.acquisition_id).all())
                    scan_out.append({
                        "scan_id": sc.id, "source_scan_id": sc.source_scan_id,
                        "revision": sc.scan_revision,
                        "supersedes": sc.supersedes,
                        "acquisition_state": acq.state if acq else None,
                        "effective": bool(eff and eff.scan_acquisition_id == sc.id),
                        "assets": [{"id": a.id, "role": a.role, "state": a.state,
                                    "size": a.expected_size} for a in assets]})
                slide_out.append({
                    "id": s.id, "source_slide_id": s.source_slide_id,
                    "barcode_raw": s.barcode_raw, "block": s.block_id,
                    "expected_state": s.expected_state, "status_raw": s.status_raw,
                    "scans": scan_out,
                    "has_effective": eff is not None})
            # 预期清单中尚未关联的玻片（无Slide行/无扫描）也要出现在版本树，
            # 否则"该病例还缺哪片"在UI上不可见（C-26）。
            ess = (db.query(ExpectedSlideSet)
                   .filter_by(case_id=c.id)
                   .order_by(ExpectedSlideSet.revision.desc()).first())
            if ess is not None:
                linked_ids = {s.source_slide_id for s in slides}
                for it in (ess.items or []):
                    if it.get("source_slide_id") in linked_ids:
                        continue
                    slide_out.append({
                        "id": f"expected:{it.get('source_slide_id')}",
                        "source_slide_id": it.get("source_slide_id"),
                        "barcode_raw": it.get("barcode_raw", ""),
                        "block": None, "expected_state": None,
                        "status_raw": {}, "scans": [], "has_effective": False,
                        "expected_unlinked": True})
            app_links = db.query(PathologyCaseApplication).filter_by(case_id=c.id).all()
            apps = []
            for l in app_links:
                a = db.get(PathologyApplication, l.application_id)
                if a:
                    apps.append({"id": a.id, "external_no": a.external_no,
                                 "requested_at": a.requested_at.isoformat()
                                 if a.requested_at else None,
                                 "requested_at_source": a.requested_at_source,
                                 "category_raw": a.category_raw,
                                 "encounter_kind_raw": a.encounter_kind_raw,
                                 "request_org_raw": a.request_org_raw,
                                 "status_raw": a.status_raw, "voided": a.voided})
            reports = (db.query(PathologyReportVersion)
                       .filter_by(case_id=c.id)
                       .order_by(PathologyReportVersion.created_at).all())
            pat = (db.get(PatientReference, c.patient_ref_id)
                   if c.patient_ref_id else None)
            encs = (db.query(EncounterReference)
                    .filter_by(patient_ref_id=c.patient_ref_id).all()
                    if c.patient_ref_id else [])
            idx = (db.query(PathologyIndexRecord)
                   .filter_by(case_id=c.id)
                   .order_by(PathologyIndexRecord.created_at.desc()).all())
            ess = (db.query(ExpectedSlideSet)
                   .filter_by(case_id=c.id)
                   .order_by(ExpectedSlideSet.revision.desc()).first())
            pkg = (db.query(DataPackage)
                   .filter_by(tenant_id=p.tenant_id, domain="pathology",
                              business_key=f"{c.registration_namespace}:{c.source_case_id}")
                   .first())
            revisions = []
            if pkg:
                for pr in (db.query(PackageRevision)
                           .filter_by(package_id=pkg.id)
                           .order_by(PackageRevision.revision).all()):
                    subs = (db.query(Submission)
                            .filter_by(package_revision_id=pr.id).all())
                    pubs = (db.query(Publication)
                            .filter_by(package_revision_id=pr.id).all())
                    revisions.append({
                        "revision": pr.revision, "state": pr.state,
                        "digest": pr.digest[:12],
                        "frozen_at": pr.created_at.isoformat(),
                        "submissions": [{"id": s.id, "state": s.state} for s in subs],
                        "publications": [{"id": pu.id, "state": pu.state}
                                         for pu in pubs]})
            issues = (db.query(QualityIssue)
                      .filter_by(tenant_id=p.tenant_id,
                                 entity_ref=f"pathology_case:{c.id}")
                      .order_by(QualityIssue.created_at).all())
            return ok({
                "case": {"id": c.id, "pathology_no": c.pathology_no_raw,
                         "namespace": c.registration_namespace,
                         "source_case_id": c.source_case_id, "state": c.state,
                         "files_complete": c.files_complete,
                         "slides_linked": c.slides_linked,
                         "clinically_ready": c.clinically_ready,
                         "target_confirmed": c.target_confirmed},
                "patient": ({"masked": pat.display_name_masked,
                             "state": pat.state,
                             "identity": pat.identity_snapshot.get("outcome", {})}
                            if pat else None),
                "encounters": [{"visit_id": e.visit_id, "kind": e.kind,
                                "state": e.state} for e in encs],
                "applications": apps,
                "slides": slide_out,
                "expected_slide_set": ({"revision": ess.revision,
                                        "items": ess.items,
                                        "closed_signal": ess.closed_signal}
                                       if ess else None),
                "reports": [{"id": r.id, "source_report_id": r.source_report_id,
                             "revision": r.revision, "voided": r.voided,
                             "issued_at": r.issued_at.isoformat() if r.issued_at else None,
                             "diagnosis_digest": r.content_digest[:12]}
                            for r in reports],
                "index_records": [{"id": i.id, "status": i.status,
                                   "profile": i.index_profile,
                                   "display_value": i.display_value,
                                   "supersedes": i.supersedes} for i in idx],
                "package_revisions": revisions,
                "quality_issues": [{"id": q.id, "rule_code": q.rule_code,
                                    "blocking_scope": q.blocking_scope,
                                    "severity": q.severity, "state": q.state,
                                    "detail": (q.context_json or {}).get("detail", "")}
                                   for q in issues],
                "note": "分列状态：文件落盘/玻片关联/临床就绪/目标确认",
            }, ctx.request_id)

    @app.post("/api/v1/pathology/barcode-resolutions")
    def barcode_resolution(body: dict, ctx=Depends(auth),
                           idempotency_key: str = Header(default="")):
        """202 + operation；不接收任意SQL或任意生产连接地址（10 §7）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_SERVICE, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN,
                           ROLE_BUSINESS_REVIEWER)
        with sf() as db:
            acq = db.get(Acquisition, body.get("acquisition_id", ""))
            if acq is None or acq.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "acquisition not found")
            op = Operation(id=new_uuid(), tenant_id=p.tenant_id,
                           op_type="pathology.barcode_resolution",
                           state="PENDING", subject_ref=acq.id)
            db.add(op)
            db.flush()
            db.add(Job(id=new_uuid(), tenant_id=p.tenant_id,
                       job_type="pathology.resolve",
                       dedup_key=f"resolve:{acq.id}",
                       payload={"acquisition_id": acq.id,
                                "operation_id": op.id}))
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"operation_id": op.id}, ctx.request_id))

    @app.get("/api/v1/pathology/acquisitions/{acq_id}/association-candidates")
    def association_candidates(acq_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            from core_db.models import Acquisition, ManualDecision
            acq = (db.query(Acquisition)
                   .filter_by(id=acq_id, tenant_id=p.tenant_id).first())
            if acq is None:
                raise ApiError("NOT_FOUND", "acquisition not found")
            rows = (db.query(AssociationCandidate)
                    .filter_by(tenant_id=p.tenant_id, acquisition_id=acq_id)
                    .order_by(AssociationCandidate.created_at).all())
            decision_version = (db.query(ManualDecision)
                                .filter_by(tenant_id=p.tenant_id,
                                           entity_ref=f"acquisition:{acq_id}").count())
            return ok({"candidates": [{"id": r.id, "source_ref": r.source_ref,
                                       "candidate_ref": r.candidate_ref,
                                       "match_basis": r.match_basis,
                                       "candidate_state": r.candidate_state,
                                       "rule_revision": r.rule_revision}
                                      for r in rows],
                       "decision_version": decision_version}, ctx.request_id)

    @app.post("/api/v1/pathology/cases/{case_id}/associations")
    def manual_association(case_id: str, body: dict, ctx=Depends(auth),
                           if_match: str | None = Header(default=None, alias="If-Match")):
        """人工明确关联（候选来源、证据和理由；CAS 更新）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_PLATFORM_ADMIN)
        if not if_match:
            raise ApiError("PRECONDITION_REQUIRED", "If-Match required")
        if not body.get("reason"):
            raise ApiError("VALIDATION_FAILED", "reason required")
        with sf() as db:
            from .models import PathologyCase
            c = db.get(PathologyCase, case_id)
            if c is None or c.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "case not found")
            from contracts.api import digest_of
            current_etag = digest_of({"case_id": c.id,
                                      "clinically_ready": c.clinically_ready,
                                      "slides_linked": c.slides_linked})[:16]
            if if_match != current_etag and if_match != "*":
                raise ApiError("VERSION_CONFLICT",
                               "If-Match stale; refresh case state and retry")
            plugin_registry.enqueue(db, p.tenant_id, "pathology.resolve_waiting",
                                    f"barcode:{body.get('barcode_raw', '')}",
                                    {"barcode": body.get("barcode_raw", "")})
            audit(db, p, "pathology.association.manual",
                  f"pathology_case:{case_id}",
                  new_ref={"barcode_raw": body.get("barcode_raw")},
                  reason=body.get("reason", ""))
            db.commit()
            return ok({"queued": True}, ctx.request_id)

    @app.post("/api/v1/pathology/slides/{slide_id}/effective-scan")
    def set_effective_scan(slide_id: str, body: dict, ctx=Depends(auth),
                           if_match: str | None = Header(default=None, alias="If-Match")):
        """选择扫描版本：重评估未来包，不改历史包（04 §5.3）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_OPERATOR,
                           ROLE_PLATFORM_ADMIN)
        if not if_match:
            raise ApiError("PRECONDITION_REQUIRED", "If-Match required")
        with sf() as db:
            from .models import ScanAcquisition, Slide, SlideEffectiveVersion
            s = db.get(Slide, slide_id)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "slide not found")
            scan = db.get(ScanAcquisition, body.get("scan_acquisition_id", ""))
            if scan is None or scan.slide_id != s.id:
                raise ApiError("VALIDATION_FAILED", "scan not among this slide")
            from contracts.api import digest_of
            current = db.query(SlideEffectiveVersion).filter_by(slide_id=s.id).first()
            etag = (digest_of({"row_version": current.row_version})[:16]
                    if current else "0")
            if if_match not in (etag, "*"):
                raise ApiError("VERSION_CONFLICT",
                               f"If-Match {if_match} != current {etag}")
            if current is None:
                db.add(SlideEffectiveVersion(
                    id=new_uuid(), tenant_id=p.tenant_id, slide_id=s.id,
                    scan_acquisition_id=scan.id,
                    selection_policy="manual",
                    decision_ref=f"user:{p.subject_code}"))
            else:
                current.scan_acquisition_id = scan.id
                current.selection_policy = "manual"
                current.decision_ref = f"user:{p.subject_code}"
                current.row_version += 1
            db.flush()
            plugin_registry.enqueue(db, p.tenant_id, "pathology.freeze_attempt",
                                    f"case:{s.case_id}", {"case_id": s.case_id})
            audit(db, p, "pathology.slide.effective_scan",
                  f"slide:{slide_id}",
                  new_ref={"scan": scan.id}, reason=body.get("reason", ""))
            db.commit()
            return ok({"slide_id": s.id,
                       "effective_scan_id": scan.id,
                       "note": "未来包重新评估；已冻结历史提交不受影响"}, ctx.request_id)
