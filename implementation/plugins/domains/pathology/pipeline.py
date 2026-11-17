"""病理包冻结、索引构建与目标映射（05 §4）。

- 冻结引用与数据修订，FROZEN 后不可变；更正产生新 revision。
- IndexCodec 只接受已解析、有血缘、通过指定规则版本的数据。
"""
from __future__ import annotations

from contracts import new_uuid
from contracts.api import digest_of
from core_db.engine import utcnow
from core_db.models import (AssetReference, DataPackage, PackageRevision,
                            QualityIssue, SourceSystem)
from connector_sdk import SourceScope

from . import index_codec
from .models import (PathologyApplication, PathologyCase, PathologyCaseApplication,
                     PathologyIndexRecord, PathologyReportVersion, ScanAcquisition, Slide,
                     SlideEffectiveVersion)


def get_or_create_package(db, tenant_id: str, case: PathologyCase) -> DataPackage:
    business_key = f"{case.registration_namespace}:{case.source_case_id}"
    pkg = (db.query(DataPackage)
           .filter_by(tenant_id=tenant_id, domain="pathology",
                      business_key=business_key).first())
    if pkg is None:
        pkg = DataPackage(id=new_uuid(), tenant_id=tenant_id, domain="pathology",
                          business_key=business_key)
        db.add(pkg)
        db.flush()
    return pkg


def case_payload(db, tenant_id: str, case: PathologyCase, index_record=None) -> dict:
    slides = db.query(Slide).filter_by(case_id=case.id).all()
    app_links = db.query(PathologyCaseApplication).filter_by(case_id=case.id).all()
    apps = [db.get(PathologyApplication, l.application_id) for l in app_links]
    reports = (db.query(PathologyReportVersion)
               .filter_by(case_id=case.id)
               .order_by(PathologyReportVersion.created_at).all())
    slide_out = []
    from core_db.models import Acquisition, Asset
    for s in slides:
        eff = db.query(SlideEffectiveVersion).filter_by(slide_id=s.id).first()
        scans = db.query(ScanAcquisition).filter_by(slide_id=s.id).all()
        scan_out = []
        for sc in scans:
            acq_row = db.get(Acquisition, sc.acquisition_id)
            assets = db.query(Asset).filter_by(acquisition_id=sc.acquisition_id).all()
            scan_out.append({
                "scan_acquisition_id": sc.id, "source_scan_id": sc.source_scan_id,
                "scan_revision": sc.scan_revision,
                "acquisition_state": acq_row.state if acq_row else None,
                "effective": bool(eff and eff.scan_acquisition_id == sc.id),
                "assets": [{"asset_id": a.id, "role": a.role, "state": a.state,
                            "sha256": a.expected_sha256, "size_bytes": a.expected_size}
                           for a in assets]})
        slide_out.append({
            "slide_id": s.id, "source_slide_id": s.source_slide_id,
            "barcode_raw": s.barcode_raw, "namespace": s.namespace,
            "status_raw": s.status_raw, "scans": scan_out,
            "effective_scan_id": eff.scan_acquisition_id if eff else None})
    return {
        "case": {"id": case.id, "source_case_id": case.source_case_id,
                 "pathology_no_raw": case.pathology_no_raw,
                 "namespace": case.registration_namespace,
                 "files_complete": case.files_complete, "slides_linked": case.slides_linked,
                 "clinically_ready": case.clinically_ready},
        "applications": [{
            "id": a.id, "external_no": a.external_no,
            "requested_at": a.requested_at.isoformat() if a and a.requested_at else None,
            "requested_at_source": a.requested_at_source if a else "",
            "category_raw": a.category_raw if a else "",
            "encounter_kind_raw": a.encounter_kind_raw if a else "",
            "request_org_raw": a.request_org_raw if a else "",
            "voided": a.voided if a else False} for a in apps if a],
        "slides": slide_out,
        "reports": [{"id": r.id, "source_report_id": r.source_report_id,
                     "revision": r.revision, "issued_at":
                         r.issued_at.isoformat() if r.issued_at else None,
                     "diagnosis_digest": digest_of(r.diagnosis or ""),
                     "voided": r.voided, "content_digest": r.content_digest}
                    for r in reports],
        "index": {"serialized": index_record.serialized_value,
                  "profile": index_record.index_profile,
                  "status": index_record.status} if index_record else None,
    }


def build_index(db, tenant_id: str, case: PathologyCase, rt) -> PathologyIndexRecord | None:
    """从病例+批准策略构建索引候选；字段不足返回 None（等待，不填零伪造）。

    R32：索引段7取当前有效报告的报告机构（经批准映射），不以申请机构顶替；
    申请/报告均为多行时按明确策略（唯一未作废；多行歧义→等待），不静默 .first()。
    """
    app_links = db.query(PathologyCaseApplication).filter_by(case_id=case.id).all()
    apps = [db.get(PathologyApplication, l.application_id) for l in app_links]
    live_apps = [a for a in apps if a is not None and not a.voided]
    if len(live_apps) == 1:
        app = live_apps[0]
    elif len(live_apps) > 1:
        # 多个未作废申请：申请号歧义 → 等待（显式策略，不取first）
        return None
    else:
        app = None
    reports = (db.query(PathologyReportVersion)
               .filter_by(case_id=case.id, voided=False)
               .order_by(PathologyReportVersion.revision.desc()).all())
    # 当前有效报告 = 最新修订版本（明确策略；无报告 → 等待，不用申请机构顶替）
    report = reports[0] if reports else None
    if report is None:
        return None
    # 证件类型/号码：来自身份决策已确认结果（存于 PatientReference.identity_snapshot）
    from .models import PatientReference
    pat = db.get(PatientReference, case.patient_ref_id) if case.patient_ref_id else None
    snap = (pat.identity_snapshot if pat else {}) or {}
    outcome = snap.get("outcome") or {}
    id_type = outcome.get("id_type_code")
    id_no = None
    if id_type and pat is not None:
        # 冻结时从受控来源取完整证件号（不经过API明文；HIS优先，批准回退取PIS）
        id_no = _plain_id_no(rt, pat, case.pathology_no_raw)
    if not id_type or not id_no:
        return None
    # 就诊类型：经确认申请时病人类型映射
    kind_map = rt.dict_map("encounter_kind")
    kind_raw = app.encounter_kind_raw if app else ""
    kind = kind_map.get(kind_raw) if kind_map else None
    if kind is None:
        # 未批准字典：等待
        return None
    org_map = rt.dict_map("report_org_insurance_code")
    # R32：段7使用当前有效报告的报告机构；申请机构不顶替（无映射→等待）
    org_raw = report.report_org_raw
    org = org_map.get(org_raw) if org_map else None
    cat_map = rt.dict_map("pathology_category")
    category = cat_map.get(app.category_raw) if (cat_map and app) else None
    if app is None or app.requested_at is None or not id_type or not id_no or \
            not org or not category:
        return None
    inp = index_codec.IndexInput(
        id_type=id_type, id_no=id_no, encounter_kind=kind,
        application_no=app.external_no, pathology_no=case.pathology_no_raw,
        application_time=index_codec.format_application_time(app.requested_at),
        org_code=org, category=category)
    enc = index_codec.encode(inp, profile="pathology-internal-v1")
    if enc["status"] != "VALID":
        return None
    # 幂等：同值同Profile复用既有候选，不重复创建
    same = (db.query(PathologyIndexRecord)
            .filter_by(case_id=case.id, index_profile="pathology-internal-v1",
                       serialized_value=enc["serialized"])
            .order_by(PathologyIndexRecord.created_at.desc()).first())
    if same is not None:
        return same
    prev = (db.query(PathologyIndexRecord)
            .filter_by(case_id=case.id)
            .order_by(PathologyIndexRecord.created_at.desc()).first())
    rec = PathologyIndexRecord(
        id=new_uuid(), tenant_id=tenant_id, case_id=case.id,
        index_profile="pathology-internal-v1", segments=enc["segments"],
        rule_versions={"identity": "hospital-demo-quality-v1",
                       "time": "hospital-demo-quality-v1"},
        dictionary_revisions={k: "approved" for k in
                              ("id_type_ga", "encounter_kind",
                               "report_org_insurance_code", "pathology_category")},
        display_value=_mask_serialized(enc["serialized"]),
        serialized_value=enc["serialized"], status="CANDIDATE",
        supersedes=prev.id if prev else None)
    db.add(rec)
    db.flush()
    return rec


def _mask_serialized(s: str) -> str:
    parts = s.split("-")
    if len(parts) == 8 and len(parts[1]) > 8:
        parts[1] = parts[1][:4] + "*" * (len(parts[1]) - 8) + parts[1][-4:]
    return "-".join(parts)


def _plain_id_no(rt, pat_ref, pathology_no: str | None = None) -> str | None:
    """受控来源取完整证件号：先HIS主索引，回退到已批准规则的PIS证件列。
    生产实现应经密钥服务授权读取；此处合成仓储直取（记录于差异文档）。"""
    his = getattr(rt, "his", None)
    wh = getattr(his, "wh", None)
    rows = getattr(wh, "rows", None) if wh is not None else None
    if rows:
        for r in rows.get("PAT_MASTER_INDEX", []):
            if r["PATIENT_ID"] == pat_ref.source_patient_id and r.get("ID_NO"):
                return r["ID_NO"]
    if pathology_no:
        try:
            case_rec = rt.pis.get_case(rt.scope(), pathology_no)
            if case_rec is not None and case_rec.identity_card:
                return case_rec.identity_card
        except Exception:  # noqa: BLE001
            return None
    return None


def freeze_case(db, tenant_id: str, case: PathologyCase, rt,
                *, revision_note: str = "") -> PackageRevision | None:
    """齐套+质量通过 → 冻结包；否则包停留 WAITING（不虚报完成）。"""
    pkg = get_or_create_package(db, tenant_id, case)
    blocking = (db.query(QualityIssue)
                .filter_by(tenant_id=tenant_id, entity_ref=f"pathology_case:{case.id}",
                           state="OPEN", severity="BLOCKING").count())
    if blocking or not case.clinically_ready or not case.files_complete:
        rev = pkg.current_revision
        if rev:
            pr = db.query(PackageRevision).filter_by(package_id=pkg.id, revision=rev).first()
            if pr and pr.state == "FROZEN":
                return pr  # 已冻结的历史版本保持；新等待不动旧快照
        return None
    index_record = build_index(db, tenant_id, case, rt)
    if index_record is None:
        return None
    payload = case_payload(db, tenant_id, case, index_record)
    digest = digest_of(payload)
    # 幂等：同内容不再新版本
    last = (db.query(PackageRevision)
            .filter_by(package_id=pkg.id)
            .order_by(PackageRevision.revision.desc()).first())
    if last and last.digest == digest and last.state == "FROZEN":
        return last
    revision = (last.revision + 1) if last else 1
    pr = PackageRevision(
        id=new_uuid(), tenant_id=tenant_id, package_id=pkg.id, revision=revision,
        schema_ref={"key": "pathology.package", "version": "1.0"},
        payload=payload, digest=digest,
        provenance={"case_id": case.id, "note": revision_note,
                    "index_record_id": index_record.id,
                    "frozen_at": utcnow().isoformat()},
        state="FROZEN")
    db.add(pr)
    pkg.current_revision = revision
    db.flush()
    # 冻结包引用的资产登记 retention（不删除）
    from core_db.models import Asset
    for slide in payload["slides"]:
        for scan in slide["scans"]:
            if scan["effective"]:
                for a in scan["assets"]:
                    exists = (db.query(AssetReference)
                              .filter_by(asset_id=a["asset_id"],
                                         owner_type="package_revision", owner_id=pr.id)
                              .first())
                    if not exists:
                        db.add(AssetReference(
                            id=new_uuid(), tenant_id=tenant_id,
                            asset_id=a["asset_id"], owner_type="package_revision",
                            owner_id=pr.id))
    db.flush()
    return pr
