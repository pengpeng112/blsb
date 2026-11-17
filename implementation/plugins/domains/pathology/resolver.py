"""病理临床关联解析器（10 §2.2 端到端顺序）。

- Agent 只交 barcode_raw；服务端反查 PIS。
- 0条→WAITING_EXTERNAL/BARCODE_NOT_FOUND；多条→WAITING_HUMAN/SOURCE_AMBIGUOUS；
  唯一候选仍检查病例/申请/身份。
- 患者(PatientReference)与就诊(EncounterReference)分开；就诊等待不选最新。
- 文件可先归集；未关联的扫描不生成已确认患者绑定。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from contracts import new_uuid
from contracts.api import digest_of
from core_db.engine import utcnow
from core_db.models import (Acquisition, AssociationCandidate, AssetBundle,
                            DictionaryRevision, OutboxEvent, QualityIssue, SourceSystem)
from connector_sdk import SourceScope

from . import quality as quality_mod
from .identity import decide_identity
from .models import (Block, EncounterReference, ExpectedSlideSet, PathologyApplication,
                     PathologyCase, PathologyCaseApplication, PathologyReportVersion,
                     PatientReference, ScanAcquisition, Slide, SlideEffectiveVersion)

PATHOLOGY_NO_IN_FILENAME = re.compile(r"(?<!\d)(\d{6,8})(?!\d)")


@dataclass
class Runtime:
    db: object
    tenant_id: str
    pis: object                       # PitayaPisConnector 兼容协议
    his: object                       # MedrecHisConnector 兼容协议
    source_system_id: str
    namespace: str = "pis.pitaya.sqlserver@PITAYA.dbo"
    his_namespace: str = "his.medrec.oracle@MEDREC"
    pack: dict = field(default_factory=dict)
    dictionaries: dict = field(default_factory=dict)  # {namespace@version: {code:target}}
    rule_revision: str = quality_mod.RULESET_REVISION
    dry_run: bool = False             # 模拟上下文全链传播（R34）

    def scope(self) -> SourceScope:
        return SourceScope(tenant_id=self.tenant_id,
                           source_system_code="pis", namespace=self.namespace)

    def his_scope(self) -> SourceScope:
        return SourceScope(tenant_id=self.tenant_id,
                           source_system_code="his", namespace=self.his_namespace)

    def dict_map(self, namespace: str) -> dict | None:
        for key, mapping in self.dictionaries.items():
            if key.split("@")[0] == namespace:
                return mapping
        return None


def _wait_issue(rt: Runtime, entity: str, reason: str, detail: str, rule="QF-SOURCE-KEY"):
    issue = QualityIssue(
        id=new_uuid(), tenant_id=rt.tenant_id, entity_ref=entity,
        rule_code=rule, severity="BLOCKING", blocking_scope="association",
        field_path="", evidence_ref=None, rule_revision=rt.rule_revision,
        evaluated_at=utcnow(), context_json={"reason": reason, "detail": detail})
    rt.db.add(issue)


def upsert_case(rt: Runtime, case_rec) -> PathologyCase:
    case = (rt.db.query(PathologyCase)
            .filter_by(tenant_id=rt.tenant_id, registration_namespace=rt.namespace,
                       source_case_id=case_rec.source_key).first())
    if case is None:
        case = PathologyCase(
            id=new_uuid(), tenant_id=rt.tenant_id,
            source_system_id=rt.source_system_id,
            registration_namespace=rt.namespace,
            source_case_id=case_rec.source_key,
            pathology_no_raw=case_rec.pathology_no)
        rt.db.add(case)
        rt.db.flush()
    # 申请时间权威性由 pack 决定；未确认时病例保存原值但不推进临床就绪
    return case


def upsert_slide(rt: Runtime, case: PathologyCase, cand) -> Slide:
    slide = (rt.db.query(Slide)
             .filter_by(tenant_id=rt.tenant_id, case_id=case.id,
                        source_slide_id=cand.source_key).first())
    if slide is None:
        slide = Slide(
            id=new_uuid(), tenant_id=rt.tenant_id, case_id=case.id,
            source_slide_id=cand.source_key, barcode_raw=cand.barcode_raw,
            status_raw=cand.status_raw or {}, namespace=rt.namespace)
        rt.db.add(slide)
        rt.db.flush()
    if cand.block_key and not slide.block_id:
        block = (rt.db.query(Block)
                 .filter_by(tenant_id=rt.tenant_id, case_id=case.id,
                            source_block_id=cand.block_key).first())
        if block is None:
            block = Block(id=new_uuid(), tenant_id=rt.tenant_id, case_id=case.id,
                          source_block_id=cand.block_key)  # 标本—块外键未确认，specimen_id=None
            rt.db.add(block)
            rt.db.flush()
        slide.block_id = block.id
    return slide


def link_scan_acquisition(rt: Runtime, slide: Slide | None, acquisition: Acquisition):
    """把通用采集事件登记为病理扫描采集；重扫=同一slide新scan_acquisition（新revision）。"""
    sa = (rt.db.query(ScanAcquisition)
          .filter_by(tenant_id=rt.tenant_id,
                     acquisition_id=acquisition.id).first())
    if sa is None:
        bundle = (rt.db.query(AssetBundle)
                  .filter_by(acquisition_id=acquisition.id)
                  .order_by(AssetBundle.revision.desc()).first())
        sa = ScanAcquisition(
            id=new_uuid(), tenant_id=rt.tenant_id, slide_id=slide.id if slide else None,
            acquisition_id=acquisition.id,
            source_scan_id=acquisition.source_scan_id,
            scan_revision=acquisition.source_revision,
            bundle_id=bundle.id if bundle else None,
            state="LINKED" if slide else "PENDING")
        rt.db.add(sa)
        rt.db.flush()
    elif slide is not None and sa.slide_id is None:
        sa.slide_id = slide.id
        sa.state = "LINKED"
    rt.db.flush()
    return sa


def resolve_patient_and_encounter(rt: Runtime, case: PathologyCase, case_rec,
                                  his_conn) -> dict:
    """步骤5-6：患者主索引 + 就诊定位（分开、精确）。"""
    pack = rt.pack.get("identity_policy", {})
    type_map = rt.dict_map("id_type_ga")
    his_patients = []
    if case_rec.patient_source_id:
        his_patients = his_conn.get_patient(rt.his_scope(), case_rec.patient_source_id)
        # 注入完整号码供比对（API层不外发）
        plain = {p.patient_id: _lookup_plain_id(rt, p.patient_id) for p in his_patients}
        for p in his_patients:
            p._id_no_plain = plain.get(p.patient_id)

    outcome = decide_identity(
        pis_identity_card=case_rec.identity_card,
        his_patients=his_patients,
        approved_type_map=type_map,
        policy=pack,
        evaluated_at=utcnow().isoformat())

    pat_ref = (rt.db.query(PatientReference)
               .filter_by(tenant_id=rt.tenant_id, namespace=rt.namespace,
                          source_patient_id=case_rec.patient_source_id or "").first())
    if pat_ref is None and case_rec.patient_source_id:
        pat_ref = PatientReference(
            id=new_uuid(), tenant_id=rt.tenant_id, namespace=rt.namespace,
            source_patient_id=case_rec.patient_source_id,
            his_patient_id=his_patients[0].patient_id if len(his_patients) == 1 else None,
            display_name_masked=case_rec.name_masked or "",
            identity_snapshot={"outcome": _outcome_dict(outcome),
                               "pis_id_masked": _mask_id(case_rec.identity_card),
                               "his_patients": [
                                   {"patient_id": p.patient_id, "id_type_raw": p.id_type_raw,
                                    "id_no_masked": p.id_no_masked}
                                   for p in his_patients]},
            state="CONFIRMED" if outcome.patient_state == "CONFIRMED" else "PENDING")
        rt.db.add(pat_ref)
        rt.db.flush()
    if pat_ref is not None and case.patient_ref_id is None:
        case.patient_ref_id = pat_ref.id
    rt.db.flush()

    encounter = {"state": "WAITING", "candidates_count": 0, "ref_id": None}
    if pat_ref is not None:
        # 就诊桥接（PIS InPatientID→VISIT_ID）必须已批准且精确命中；
        # 无桥接时即便患者只有一条 visit 也保持等待（R30：不依据偶然唯一推断）
        bridge = rt.pack.get("pis_visit_bridge")
        visit_id = bridge.get("visit_id_for", {}).get(case_rec.in_patient_id or "") \
            if bridge else None
        cands = []
        if case_rec.in_patient_id and visit_id is None:
            # 无已批准桥接：即便患者只有一条 visit 也不确认（R30），
            # 但仍取候选数供复核提示（不自动选择）。
            cands = his_conn.get_encounter(rt.his_scope(), pat_ref.source_patient_id,
                                           None, "inpatient")
            encounter["state"] = "WAITING"
            encounter["candidates_count"] = len(cands)
            encounter["wait_reason"] = "VISIT_BRIDGE_UNCONFIRMED"
        elif case_rec.in_patient_id:
            cands = his_conn.get_encounter(rt.his_scope(), pat_ref.source_patient_id,
                                           visit_id, "inpatient")
            if len(cands) == 1:
                enc = (rt.db.query(EncounterReference)
                       .filter_by(tenant_id=rt.tenant_id, namespace=rt.his_namespace,
                                  patient_ref_id=pat_ref.id,
                                  visit_id=cands[0].visit_id).first())
                if enc is None:
                    enc = EncounterReference(
                        id=new_uuid(), tenant_id=rt.tenant_id, namespace=rt.his_namespace,
                        patient_ref_id=pat_ref.id, visit_id=cands[0].visit_id,
                        kind="inpatient", state="CONFIRMED",
                        candidates={"seen": len(cands),
                                    "bridge": "approved:pis_visit_bridge"})
                    rt.db.add(enc)
                    rt.db.flush()
                enc.state = "CONFIRMED"
                encounter.update(state="CONFIRMED", candidates_count=1, ref_id=enc.id)
            elif len(cands) > 1:
                # 桥接键不精确（同visit多行）：等待，不选最新（F06）
                encounter.update(state="WAITING", candidates_count=len(cands),
                                 wait_reason="VISIT_UNRESOLVED")
            else:
                encounter.update(state="WAITING", candidates_count=0,
                                 wait_reason="VISIT_UNRESOLVED")
        elif case_rec.out_patient_id:
            # 门诊路径未证实（B05）：能力缺失→等待
            encounter["state"] = "WAITING"
            encounter["wait_reason"] = "CAPABILITY_UNSUPPORTED: outpatient path unconfirmed"
    return {"identity": _outcome_dict(outcome), "encounter": encounter}


def _lookup_plain_id(rt: Runtime, patient_id: str) -> str | None:
    """完整证件号只从受控来源取（合成仓储直取；生产应走密钥服务），仅做冲突比对。"""
    if hasattr(rt.his, "wh") and hasattr(rt.his.wh, "rows"):
        for r in rt.his.wh.rows.get("PAT_MASTER_INDEX", []):
            if r["PATIENT_ID"] == patient_id:
                return r.get("ID_NO")
    return None


def upsert_application(rt: Runtime, case: PathologyCase, case_rec, pis_conn):
    apps = pis_conn.get_applications(rt.scope(), case_rec.req_note_id)
    from datetime import datetime

    def _dt(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None

    authoritative = rt.pack.get("clinical_policy", {}).get(
        "application_time_authoritative_field")
    app_row = None
    for a in apps:
        app = (rt.db.query(PathologyApplication)
               .filter_by(tenant_id=rt.tenant_id, source_system_id=rt.source_system_id,
                          source_application_id=a.source_key).first())
        raw_requested = _dt(a.requested_at)
        source_field = "患者检查申请信息表.申请日期" if a.requested_at else "CreateTime"
        # CreateTime 只在已证明新增语义下作为发现通道；申请时间权威字段未批准时不采信
        requested = raw_requested if authoritative == source_field else None
        if app is None:
            app = PathologyApplication(
                id=new_uuid(), tenant_id=rt.tenant_id, source_system_id=rt.source_system_id,
                source_application_id=a.source_key, external_no=a.external_id,
                requested_at=requested, requested_at_source=source_field if requested else "",
                encounter_kind_raw=a.encounter_kind_raw or "",
                category_raw=a.category_raw or "",
                request_org_raw=a.request_org_raw or "",
                status_raw=str(a.status_raw or ""), voided=bool(a.void_info.get("作废人")),
                raw=a.raw)
            rt.db.add(app)
            rt.db.flush()
        else:
            app.encounter_kind_raw = a.encounter_kind_raw or ""
            app.category_raw = a.category_raw or ""
            app.request_org_raw = a.request_org_raw or ""
            app.status_raw = str(a.status_raw or "")
            app.voided = bool(a.void_info.get("作废人"))
            if requested is not None and app.requested_at != requested:
                app.requested_at = requested
                app.requested_at_source = source_field
                app.revision += 1
        link = (rt.db.query(PathologyCaseApplication)
                .filter_by(case_id=case.id, application_id=app.id,
                           relation_type="primary").first())
        if link is None:
            rt.db.add(PathologyCaseApplication(
                id=new_uuid(), tenant_id=rt.tenant_id, case_id=case.id,
                application_id=app.id, relation_type="primary",
                evidence={"via": "Pitaya.ReqNoteID", "req_note_id": a.external_id}))
        app_row = app
    rt.db.flush()
    return app_row


def upsert_reports(rt: Runtime, case: PathologyCase, case_rec, pis_conn) -> list:
    out = []
    reports = pis_conn.get_reports(rt.scope(), case_rec.source_key)
    texts = pis_conn.get_report_texts(rt.scope(), case_rec.source_key) or {}
    for r in reports:
        digest = digest_of({"serial": r.report_serial, "diagnosis": r.diagnosis,
                            "gross": r.gross, "microscopic": r.microscopic,
                            "issued_at": r.issued_at, "voided": r.voided})
        from datetime import datetime

        def _dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                return None

        versions = (rt.db.query(PathologyReportVersion)
                    .filter_by(tenant_id=rt.tenant_id, case_id=case.id,
                               source_report_id=r.source_key).all())
        existing = next((v for v in versions if v.content_digest == digest), None)
        if existing is not None:
            existing.voided = r.voided
            out.append(existing)
            continue
        rev = len(versions) + 1
        rv = PathologyReportVersion(
            id=new_uuid(), tenant_id=rt.tenant_id, case_id=case.id,
            source_report_id=r.source_key, revision=rev,
            status="ISSUED", issued_at=_dt(r.issued_at),
            report_org_raw=r.report_org_raw or "",
            gross=r.gross or texts.get("gross") or "",
            microscopic=r.microscopic or texts.get("microscopic") or "",
            diagnosis=r.diagnosis or texts.get("diagnosis") or "",
            source_url=r.image_url or "", voided=r.voided,
            content_digest=digest)
        rt.db.add(rv)
        out.append(rv)
    rt.db.flush()
    return out


def evaluate_completeness(rt: Runtime, case: PathologyCase, pis_conn) -> dict:
    """whole_case 策略：预期清单来自全量分页读取（R31：单页上限不得截断预期）；
    每片需有已批准有效扫描版本；源侧 closed 信号未批准不视为清单闭合。"""
    if hasattr(pis_conn, "iter_slides"):
        slides, pages_read, last_page_proven = pis_conn.iter_slides(
            rt.scope(), case.source_case_id)
    else:  # 兼容只提供单页协议的适配器：仅一页，覆盖未证明
        slides, _ = pis_conn.list_slides(rt.scope(), case.source_case_id)
        pages_read, last_page_proven = 1, False
    items = [{"source_slide_id": s.source_key, "barcode_raw": s.barcode_raw,
              "block_key": s.block_key} for s in slides]
    ess = (rt.db.query(ExpectedSlideSet)
           .filter_by(case_id=case.id).order_by(ExpectedSlideSet.revision.desc()).first())
    closed_approved = bool(rt.pack.get("clinical_policy", {}).get(
        "expected_slide_closed_signal_approved"))
    if ess is None or {i["source_slide_id"] for i in ess.items} != {
            i["source_slide_id"] for i in items}:
        ess = ExpectedSlideSet(
            id=new_uuid(), tenant_id=rt.tenant_id, case_id=case.id,
            revision=(ess.revision + 1) if ess else 1,
            source_ref=f"{rt.namespace}:Pitaya_Slice",
            items=items, closed_signal={"approved": closed_approved},
            verified_by="pis.list_slides" if closed_approved else "")
        rt.db.add(ess)
        rt.db.flush()
    if ess is not None and ess.closed_signal is not None:
        ess.closed_signal = {**ess.closed_signal,
                             "pages_read": pages_read,
                             "last_page_proven": last_page_proven}
    missing = []
    unapproved = []
    for it in items:
        slide = (rt.db.query(Slide)
                 .filter_by(tenant_id=rt.tenant_id, case_id=case.id,
                            source_slide_id=it["source_slide_id"]).first())
        if slide is None:
            missing.append(it)
            continue
        scans = (rt.db.query(ScanAcquisition)
                 .filter_by(tenant_id=rt.tenant_id, slide_id=slide.id).all())
        if not scans:
            missing.append(it)
            continue
        eff = (rt.db.query(SlideEffectiveVersion)
               .filter_by(slide_id=slide.id).first())
        if eff is None:
            # 默认策略：最新已完成扫描（files_complete）为候选有效版本；未批准信号则标待确认
            done = [s for s in scans if _scan_files_ready(rt, s)]
            if done and closed_approved:
                latest = max(done, key=lambda s: (s.scan_revision, s.created_at))
                eff = SlideEffectiveVersion(
                    id=new_uuid(), tenant_id=rt.tenant_id, slide_id=slide.id,
                    scan_acquisition_id=latest.id,
                    selection_policy="latest_completed",
                    decision_ref="policy:latest_completed")
                rt.db.add(eff)
                rt.db.flush()
            else:
                unapproved.append({"barcode_raw": it["barcode_raw"]})
    rt.db.flush()
    # expected_known：清单闭合需要 (a)源closed信号已批准 且 (b)分页读到短页证明末页
    expected_known = closed_approved and last_page_proven
    return {"expected_known": expected_known, "expected_count": len(items),
            "scanned_count": len(items) - len(missing),
            "pages_read": pages_read, "last_page_proven": last_page_proven,
            "missing": missing, "unapproved_scans": unapproved}


def _scan_files_ready(rt: Runtime, scan: ScanAcquisition) -> bool:
    acq = rt.db.get(Acquisition, scan.acquisition_id)
    return acq is not None and acq.state == "FILES_COMPLETE"


def filename_hint_conflict(manifest: dict, pathology_no: str) -> str | None:
    """文件名中的病理号提示与 PIS 不一致 → IDENTITY_CONFLICT（只阻断自动绑定）。"""
    hints = manifest.get("identity_hints") or {}
    if hints.get("pathology_no") and hints["pathology_no"] != pathology_no:
        return hints["pathology_no"]
    for f in manifest.get("files", []):
        for m in PATHOLOGY_NO_IN_FILENAME.finditer(f.get("relative_path", "")):
            # 文件名仅作辅助提示：与PIS不一致时不覆盖PIS，转人工
            if m.group(1) != pathology_no and len(m.group(1)) == len(pathology_no):
                return m.group(1)
    return None


def resolve_acquisition(rt: Runtime, acquisition: Acquisition) -> dict:
    """主入口：一次采集事件的临床关联（幂等，可由PIS事件唤醒重跑）。"""
    manifest = acquisition.manifest
    barcode = (manifest.get("identity_hints") or {}).get("barcode_raw") or ""
    result = {"acquisition_id": acquisition.id, "barcode_raw": barcode,
              "state": "WAITING", "wait_reason": None, "case_id": None,
              "slide_id": None, "candidates": []}
    db = rt.db

    # 快速通道：该采集已绑定玻片（人工决定或先前成功）→ 不再重走条码反查，
    # 避免把人工确认过的歧义绑定重新判为 SOURCE_AMBIGUOUS（H-06）。
    linked = (db.query(ScanAcquisition)
              .filter_by(acquisition_id=acquisition.id)
              .filter(ScanAcquisition.slide_id.isnot(None)).first())
    if linked is not None:
        slide = db.get(Slide, linked.slide_id)
        case = db.get(PathologyCase, slide.case_id)
        try:
            case_rec = rt.pis.get_case(rt.scope(), case.pathology_no_raw)
        except Exception as exc:  # noqa: BLE001
            result.update(state="WAITING", wait_reason="EXTERNAL_UNAVAILABLE")
            _wait_issue(rt, f"acquisition:{acquisition.id}", "EXTERNAL_UNAVAILABLE",
                        f"PIS case fetch failed: {exc}", rule="QF-SOURCE-KEY")
            db.flush()
            return result
        if case_rec is None:
            result.update(state="WAITING", wait_reason="EXTERNAL_UNAVAILABLE")
            _wait_issue(rt, f"acquisition:{acquisition.id}", "EXTERNAL_UNAVAILABLE",
                        f"bound case {case.pathology_no_raw!r} missing in PIS; "
                        f"manual binding kept, not auto-rebound", rule="QF-SOURCE-KEY")
            db.flush()
            return result
        return _finalize_association(rt, acquisition, case, case_rec, slide, result)

    # 记录/刷新关联候选（不提前写confirmed关系）
    lookup = None
    if barcode:
        try:
            lookup = rt.pis.get_slide_by_barcode(rt.scope(), barcode)
        except Exception as exc:  # noqa: BLE001
            result.update(state="WAITING", wait_reason="EXTERNAL_UNAVAILABLE")
            _wait_issue(rt, f"acquisition:{acquisition.id}", "EXTERNAL_UNAVAILABLE",
                        f"PIS lookup failed: {exc}", rule="QF-SOURCE-KEY")
            db.flush()
            return result

    if lookup is None or not lookup["candidates"]:
        result.update(state="WAITING", wait_reason="BARCODE_NOT_FOUND")
        _wait_issue(rt, f"acquisition:{acquisition.id}", "BARCODE_NOT_FOUND",
                    f"barcode {barcode!r} has 0 PIS candidates; waiting for PIS event",
                    rule="QF-SOURCE-KEY")
        db.flush()
        return result

    cands = lookup["candidates"]
    result["candidates"] = [
        {"source_key": c.source_key, "barcode_raw": c.barcode_raw,
         "pathology_no": c.pathology_no, "source_object": c.source_object}
        for c in cands]
    for c in cands:
        db.add(AssociationCandidate(
            id=new_uuid(), tenant_id=rt.tenant_id, acquisition_id=acquisition.id,
            source_ref=f"barcode:{barcode}", candidate_kind="slide",
            candidate_ref=f"{rt.namespace}#slide:{c.source_key}",
            match_basis={"method": "pis_barcode_lookup", "truncated": lookup["truncated"]},
            candidate_state="PROPOSED", rule_revision=rt.rule_revision,
            raw_digest=digest_of({"source_key": c.source_key, "barcode": c.barcode_raw})))

    if len(cands) > 1:
        result.update(state="WAITING", wait_reason="SOURCE_AMBIGUOUS")
        _wait_issue(rt, f"acquisition:{acquisition.id}", "SOURCE_AMBIGUOUS",
                    f"barcode {barcode!r} matched {len(cands)} slides (truncated="
                    f"{lookup['truncated']}); manual selection required, first/latest NOT taken",
                    rule="QF-SOURCE-KEY")
        db.flush()
        return result

    cand = cands[0]
    try:
        case_rec = rt.pis.get_case(rt.scope(), cand.pathology_no)
    except Exception as exc:  # noqa: BLE001
        result.update(state="WAITING", wait_reason="EXTERNAL_UNAVAILABLE")
        _wait_issue(rt, f"acquisition:{acquisition.id}", "EXTERNAL_UNAVAILABLE",
                    f"PIS case fetch failed: {exc}", rule="QF-SOURCE-KEY")
        db.flush()
        return result
    if case_rec is None:
        result.update(state="WAITING", wait_reason="BARCODE_NOT_FOUND")
        _wait_issue(rt, f"acquisition:{acquisition.id}", "BARCODE_NOT_FOUND",
                    f"slide exists but case {cand.pathology_no!r} missing in PIS",
                    rule="QF-SOURCE-KEY")
        db.flush()
        return result

    case = upsert_case(rt, case_rec)
    result["case_id"] = case.id

    hint = filename_hint_conflict(manifest, case_rec.pathology_no)
    if hint:
        result.update(state="WAITING", wait_reason="IDENTITY_CONFLICT")
        _wait_issue(rt, f"acquisition:{acquisition.id}", "IDENTITY_CONFLICT",
                    f"filename/hint pathology no {hint!r} != PIS {case_rec.pathology_no!r}; "
                    f"files stay archived, auto binding blocked",
                    rule="QF-ID-CONFLICT")
        # 文件仍安全归集：登记扫描但不绑定slide
        link_scan_acquisition(rt, None, acquisition)
        db.flush()
        return result

    slide = upsert_slide(rt, case, cand)
    result["slide_id"] = slide.id
    link_scan_acquisition(rt, slide, acquisition)
    return _finalize_association(rt, acquisition, case, case_rec, slide, result)


def _finalize_association(rt: Runtime, acquisition: Acquisition,
                          case: PathologyCase, case_rec, slide: Slide,
                          result: dict) -> dict:
    """身份/就诊/申请/报告/质量/就绪标记的公共收尾（自动与人工快速通道共用）。"""
    db = rt.db
    identity_enc = resolve_patient_and_encounter(rt, case, case_rec, rt.his)
    app_row = upsert_application(rt, case, case_rec, rt.pis)
    reports = upsert_reports(rt, case, case_rec, rt.pis)

    # 质量评估（固定 evaluated_at）
    comp = evaluate_completeness(rt, case, rt.pis)
    result["case_id"] = case.id
    result["slide_id"] = slide.id if slide is not None else result.get("slide_id")
    inp = quality_mod.QualityInput(
        case={"id": case.id, "birth_date": _case_birth(case_rec),
              "report_org_raw": (reports[0].report_org_raw if reports else ""),
              "status_dictionary_approved": bool(
                  rt.dict_map("pis_status"))},
        application=_app_dict(app_row),
        reports=[{"source_report_id": r.source_report_id,
                  "issued_at": r.issued_at.isoformat() if r.issued_at else None}
                 for r in reports],
        identity=identity_enc["identity"], completeness=comp,
        encounter=identity_enc["encounter"],
        org_mapping=rt.dict_map("report_org_insurance_code"),
        category_dictionary=rt.dict_map("pathology_category"))
    issues = quality_mod.evaluate(inp, evaluated_at=utcnow(),
                                  ruleset_revision=rt.rule_revision)
    _refresh_issues(rt, case.id, issues)

    case.slides_linked = True
    case.files_complete = acquisition.state == "FILES_COMPLETE"
    case.clinically_ready = not quality_mod.blocks(issues, "readiness") and \
        not quality_mod.blocks(issues, "patient") and \
        not quality_mod.blocks(issues, "association")
    case.state = "ASSOCIATED" if case.slides_linked else "DISCOVERED"
    db.flush()
    # 关联成功后关闭该采集事件上的等待类问题（BARCODE_NOT_FOUND等），留复评历史
    for q in db.query(QualityIssue).filter_by(
            tenant_id=rt.tenant_id, entity_ref=f"acquisition:{acquisition.id}",
            state="OPEN").all():
        q.state = "RESOLVED"
    db.add(OutboxEvent(id=new_uuid(), tenant_id=rt.tenant_id,
                       aggregate_ref=f"pathology_case:{case.id}",
                       event_type="dry.pathology.case.updated" if rt.dry_run
                       else "pathology.case.updated",
                       payload={"case_id": case.id, "acquisition_id": acquisition.id}))
    db.flush()
    result.update(state="ASSOCIATED", wait_reason=None)
    if identity_enc["identity"].get("patient_state") != "CONFIRMED":
        result["wait_reason"] = identity_enc["identity"].get("wait_reason")
    return result


def _refresh_issues(rt: Runtime, case_id: str, issues: list[dict]):
    """按 rule_code+scope 刷新 OPEN 问题；修复的关闭，复评留历史（新增行）。"""
    db = rt.db
    for i in issues:
        existing = (db.query(QualityIssue)
                    .filter_by(tenant_id=rt.tenant_id,
                               entity_ref=f"pathology_case:{case_id}",
                               rule_code=i["rule_code"],
                               blocking_scope=i["blocking_scope"], state="OPEN")
                    .first())
        if existing is None:
            db.add(QualityIssue(
                id=new_uuid(), tenant_id=rt.tenant_id,
                entity_ref=f"pathology_case:{case_id}",
                rule_code=i["rule_code"], severity=i["severity"],
                blocking_scope=i["blocking_scope"],
                field_path=i["field_path"], rule_revision=i["rule_revision"],
                evaluated_at=utcnow(),
                context_json={"detail": i["detail"], **(i.get("context") or {})}))
    open_codes = {(i["rule_code"], i["blocking_scope"]) for i in issues}
    for q in db.query(QualityIssue).filter_by(
            tenant_id=rt.tenant_id, entity_ref=f"pathology_case:{case_id}",
            state="OPEN").all():
        if (q.rule_code, q.blocking_scope) not in open_codes:
            q.state = "RESOLVED"


def _app_dict(app_row) -> dict | None:
    if app_row is None:
        return None
    return {"requested_at": app_row.requested_at.isoformat() if app_row.requested_at else None,
            "requested_at_source": app_row.requested_at_source,
            "category_raw": app_row.category_raw,
            "request_org_raw": app_row.request_org_raw,
            "status_raw": app_row.status_raw}


def _case_birth(case_rec) -> str | None:
    return getattr(case_rec, "raw", {}).get("BirthDate") if hasattr(case_rec, "raw") else None


def _outcome_dict(outcome) -> dict:
    return {"patient_state": outcome.patient_state,
            "wait_reason": outcome.wait_reason,
            "id_type_code": outcome.id_type_code,
            "id_type_candidate": outcome.id_type_candidate,
            "conflict": outcome.conflict,
            "issues": outcome.issues}


def _mask_id(v):
    if not v:
        return None
    v = str(v)
    return v[:4] + "*" * (len(v) - 8) + v[-4:] if len(v) > 8 else "****"
