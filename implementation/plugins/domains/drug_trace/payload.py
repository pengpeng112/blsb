"""冻结包构建与平台单号映射（W04/W05 前置；DTR-B02、DTR-A13）。

- bill_code：tenant+profile+来源复合键 → 稳定编号（默认规则 v1：DTRP/DTRT 前缀 +
  SHA1(复合键) 截断；碰撞与长度上限建码时校验；配置变更不隐式重编）。
- 字典门禁：bill_type/企业ID 映射只读「已批准」DictionaryRevision（approved_by 非空
  且 source=target 域）；未映射 → QD-DICT-UNMAPPED 等待，不猜 321/323 等示例值。
- 冻结包落 DataPackage/PackageRevision（domain=drug_trace），payload 含
  profile/method/bill_code/字段原值与 provenance；digest 冻结。
"""
from __future__ import annotations

import hashlib

from contracts import ApiError, new_uuid
from contracts.api import digest_of
from core_db.models import (DataPackage, DictionaryRevision, PackageRevision)
from workflow_core.jobs import emit as emit_outbox

from . import contracts as C
from .models import DrugTraceBill, DrugTraceBillCode, DrugTraceBillRevision

BILL_CODE_PREFIX = {C.PROFILE_INOUT: "DTRP", C.PROFILE_RETAIL: "DTRT"}
BILL_CODE_MAXLEN = 64  # 工程保护值；平台实际长度限制以批准字典为准（EXT-08）


def ensure_bill_code(db, tenant_id: str, profile: str, source_bill_key: str) -> str:
    """幂等取/建平台单号（稳定、不重编、碰撞检测）。"""
    if profile not in C.PROFILES:
        raise ApiError("CAPABILITY_UNSUPPORTED", f"unknown profile {profile}")
    existing = (db.query(DrugTraceBillCode)
                .filter_by(tenant_id=tenant_id, profile=profile,
                           source_bill_key=source_bill_key).first())
    if existing is not None:
        return existing.bill_code
    prefix = BILL_CODE_PREFIX[profile]
    code = f"{prefix}-{hashlib.sha1(source_bill_key.encode('utf-8')).hexdigest()[:20].upper()}"
    if len(code) > BILL_CODE_MAXLEN:
        raise ApiError("VALIDATION_FAILED", "bill_code exceeds length guard")
    collide = (db.query(DrugTraceBillCode)
               .filter_by(tenant_id=tenant_id, profile=profile, bill_code=code)
               .first())
    if collide is not None and collide.source_bill_key != source_bill_key:
        # 确定性规则碰撞：不随机重试（会破坏可重放性）；升级 rule_revision 需人工介入
        raise ApiError("IDENTITY_CONFLICT",
                       f"bill_code collision on {code}; manual rule revision required")
    row = DrugTraceBillCode(id=new_uuid(), tenant_id=tenant_id, profile=profile,
                            source_bill_key=source_bill_key, bill_code=code,
                            rule_revision="v1")
    db.add(row)
    db.flush()
    return code


def lookup_mapping_with_rev(db, tenant_id: str, namespace: str,
                            source_value: str) -> tuple[str, str, str]:
    """批准字典映射：返回 (目标值, 命中revision_id, 命中version)。

    FIX3-04/G3-13 修订：值与 revision 来自**同一条实际命中记录**——先按
    approved_by 过滤再匹配，绝不取"最新一条"当审计指针（旧实现值取自旧批准版、
    revision 却指向新草稿，审计闭包失真）。字典必须经人工 approve；未批准版本
    的 items 不参与映射（防止把文档示例值当生产字典，EXT-08）。
    """
    if source_value is None or source_value == "":
        raise ApiError("DICTIONARY_UNMAPPED",
                       f"empty source value for {namespace}")
    revs = (db.query(DictionaryRevision)
            .filter_by(tenant_id=tenant_id, namespace=namespace)
            .order_by(DictionaryRevision.created_at.desc()).all())
    for rev in revs:
        if not rev.approved_by:
            continue
        for item in (rev.items or []):
            if str(item.get("code", "")) == str(source_value):
                return str(item.get("target", "")), str(rev.id), \
                    str(rev.version or "")
    raise ApiError("DICTIONARY_UNMAPPED",
                   f"no approved mapping for {namespace}:{source_value!r}")


def lookup_mapping(db, tenant_id: str, namespace: str, source_value: str) -> str:
    """批准字典映射（只取值；见 lookup_mapping_with_rev 的闭包语义）。"""
    return lookup_mapping_with_rev(db, tenant_id, namespace, source_value)[0]


def lookup_mapping_rev(db, tenant_id: str, namespace: str, source_value: str,
                       revisions: dict) -> str:
    """同 lookup_mapping，并记录「值/命中revision/version」三元组（G3-13）。"""
    value, rev_id, version = lookup_mapping_with_rev(
        db, tenant_id, namespace, source_value)
    revisions[namespace] = {"revision_id": rev_id, "value": value,
                            "version": version}
    return value


def _submissions_for_bill(db, tenant_id: str, bill) -> list:
    """该来源单全部提交（新→旧）：经 freeze_package 的 DataPackage.business_key
    精确关联（profile|connector|药房|业务|单号），再取其全部包版本的提交。"""
    from core_db.models import DataPackage, Submission
    key = (f"{C.PROFILE_RETAIL}|{bill.connector_id}|"
           f"{bill.pharmacy_code}|{bill.business_kind}|{bill.source_bill_no}")
    pkg = (db.query(DataPackage)
           .filter_by(tenant_id=tenant_id, domain="drug_trace",
                      business_key=key).first())
    if pkg is None:
        return []
    prs = (db.query(PackageRevision)
           .filter_by(tenant_id=tenant_id, package_id=pkg.id)
           .order_by(PackageRevision.revision.desc()).all())
    out = []
    for pr in prs:
        out.extend(db.query(Submission)
                   .filter_by(tenant_id=tenant_id, package_revision_id=pr.id)
                   .order_by(Submission.created_at.desc()).all())
    return out


def resolve_return_original(db, tenant_id: str, header: dict,
                            *, before_time=None) -> dict | None:
    """K2/SD-03：定位退药单对应的原发药单，取其**已 CONFIRMED** 冻结 payload 码列表。

    只读复用（绝不改原冻结件）：
    - 关联键：raw 的 PRESC_NO（r11 退药视图必带）+ 同药房 + business_kind=
      PATIENT_DISPENSE；raw 带 SEND_EXEC（原 send 行执行时间）时优先精确匹配
      bill_time，否则取 bill_time ≤ 退药 bill_time 中最近一张（处方号存在周期性
      复用，必须带时间约束）。
    - 原单提交必须 CONFIRMED（已平台确认的单才可冲正；ACCEPTED/UNKNOWN 不算）。
    - 返回 {"bill_id","submission_id","bill_code","codes":[...]}；找不到 → None
      （质量层落 QD-RETURN-ORIGINAL-PENDING 等待）。
    """
    from .business_rules import parse_raw_status
    raw = parse_raw_status(header.get("source_bill_kind_raw"))
    presc_no = str(raw.get("PRESC_NO", "") or "")
    if not presc_no:
        return None
    q = (db.query(DrugTraceBill)
         .filter(DrugTraceBill.tenant_id == tenant_id,
                 DrugTraceBill.business_kind == "PATIENT_DISPENSE",
                 DrugTraceBill.pharmacy_code ==
                 str(header.get("pharmacy_code", "") or ""),
                 DrugTraceBill.source_bill_no == presc_no))
    if before_time is not None:
        q = q.filter(DrugTraceBill.bill_time <= before_time)
    send_exec = str(raw.get("SEND_EXEC", "") or "")
    candidates = q.order_by(DrugTraceBill.bill_time.desc()).limit(20).all()
    if send_exec:
        exact = [b for b in candidates
                 if b.bill_time is not None and
                 b.bill_time.strftime("%Y-%m-%d %H:%M:%S") == send_exec]
        if exact:
            candidates = exact
    for bill in candidates:
        for sub in _submissions_for_bill(db, tenant_id, bill):
            if sub.state != "CONFIRMED":
                continue
            pr = db.get(PackageRevision, sub.package_revision_id)
            codes = [c for c in (pr.payload or {}).get("codes", [])
                     if isinstance(c, dict) and c.get("trace_code")]
            if not codes:
                continue
            return {"bill_id": bill.id, "submission_id": sub.id,
                    "bill_code": (pr.payload or {}).get("bill_code", ""),
                    "codes": codes}
    return None


def build_payload(db, tenant_id: str, revision: DrugTraceBillRevision,
                  bill: DrugTraceBill, *, profile: str) -> dict:
    """构建冻结 payload（不落库；调用方负责冻结与状态机）。

    FIX3-01 修订（R3-01/G3-01）：企业身份双字典——
    - ref_ent_id 字典 → payload.ref_ent_id（查询表单用）**并直接作为上传
      ref_user_id**（官方上传定义：ref_user_id 对应医院 ref_ent_id）；
    - ent_id 字典 → 库存单 from/to 的医院一侧（收发企业 ent_id 口径）；
    - supplier_ent_id 字典 → from/to 的供应商一侧（同为 ent_id 口径）。
      三个字典各自独立批准，值允许完全不同（防止同值掩盖串用）。
    - query_bill_type 字典 → query.billstatus 独立选择器（A/AI/AO），冻结进
      payload 保证旧提交查询身份稳定（G3-02），不再复用上传 bill_type 数字枚举。
    FIX2-01 保留：physic_type/client_type 按方向；内部 supplier_ref/
    warehouse_code 不透传；所有映射只读已批准字典（未批准即等待，不猜示例值）。
    SD-02/SD-03（DTR-SCOPE-DEV-20261027）：bill_type/query_bill_type 字典键经
    C.bill_type_source_key——r11 新分支（INPATIENT_DISPENSE/PATIENT_RETURN）用
    稳定 business_kind 作键（raw 含唯一单号不作键）；r10 分支沿用 raw 原串。
    PATIENT_RETURN 0 自有码时：冲正码取原发药 CONFIRMED 冻结 payload（只读）。
    """
    if profile not in C.PROFILES:
        raise ApiError("CAPABILITY_UNSUPPORTED", f"unknown profile {profile}")
    spec = C.PROFILES[profile]
    if bill.business_kind not in spec["business_kinds"]:
        raise ApiError("PROFILE_UNAPPROVED",
                       f"business kind {bill.business_kind} not eligible for {profile}")
    if revision.quality_state != C.BillQualityState.VALID:
        raise ApiError("PRECONDITION_REQUIRED",
                       f"revision quality_state={revision.quality_state}; VALID required")
    header = dict(revision.header or {})
    lines = list(revision.lines or [])
    # is_deleted 经 as_bool 归一（'0' 字符串不再被当真删除标记，R2-07）
    from . import quality as _Q
    active = [ln for ln in lines if not _Q.as_bool(ln.get("is_deleted"),
                                                   "is_deleted")]
    dict_revs: dict[str, dict] = {}
    # 字典映射（未批准即等待；绝不用文档示例值兜底）
    kind_key = C.bill_type_source_key(bill.business_kind, header)
    bill_type = lookup_mapping_rev(db, tenant_id, "drugtrace.bill_type",
                                   kind_key, dict_revs)
    pharmacy = str(header.get("pharmacy_code", ""))
    ref_ent_id = lookup_mapping_rev(db, tenant_id, C.DICT_NS_REF_ENT_ID,
                                    pharmacy, dict_revs)
    query_bill_type = lookup_mapping_rev(db, tenant_id,
                                         C.DICT_NS_QUERY_BILL_TYPE,
                                         kind_key, dict_revs)
    if query_bill_type not in C.QUERY_BILL_TYPE_SELECTORS:
        raise ApiError(
            "DICTIONARY_UNMAPPED",
            f"query_bill_type mapping {query_bill_type!r} not in documented "
            f"selectors {list(C.QUERY_BILL_TYPE_SELECTORS)}; EXT-09 evidence "
            "required before any other selector value")
    # K2/SD-03：退药冲正 0 自有码 → 原发药 CONFIRMED 冻结码列表（只读复用）
    payload_codes = [{"trace_code": str(ln.get("trace_code", "")),
                      "quantity": "" if ln.get("quantity") in (None, "")
                      else str(ln.get("quantity")),
                      "unit": str(ln.get("unit", "") or ""),
                      "source_code_record_id": str(
                          ln.get("source_code_record_id", ""))}
                     for ln in active]
    reversal_of = None
    if bill.business_kind == "PATIENT_RETURN" and not payload_codes:
        original = resolve_return_original(db, tenant_id, header,
                                           before_time=bill.bill_time)
        if original is None:
            raise ApiError(
                "PRECONDITION_REQUIRED",
                "patient return has no IN codes and no CONFIRMED original "
                "dispense payload available; wait for the original upload")
        payload_codes = [dict(c) for c in original["codes"]]
        reversal_of = {"bill_id": original["bill_id"],
                       "submission_id": original["submission_id"],
                       "bill_code": original["bill_code"]}
    payload = {
        "profile": profile,
        "method": spec["method"],
        "bill_code": ensure_bill_code(db, tenant_id, profile,
                                      f"{header.get('source_system','')}|"
                                      f"{header.get('pharmacy_code','')}|"
                                      f"{header.get('business_kind','')}|"
                                      f"{header.get('source_bill_id','')}"),
        "business_kind": bill.business_kind,
        "pharmacy_code": pharmacy,        # EXT-12 药房白名单/配额逐单复核用
        "bill_type": bill_type,
        "query_bill_type": query_bill_type,
        "ref_ent_id": ref_ent_id,          # 查询表单 ref_ent_id（query.billstatus）
        "ref_user_id": ref_ent_id,         # 上传医院标识=医院 ref_ent_id（R3-01）
        "bill_time": header.get("bill_time"),
        "code_count": len(payload_codes),
        "codes": payload_codes,
        "dict_revisions": dict_revs,
    }
    if reversal_of is not None:
        payload["reversal_of"] = reversal_of
    if profile == C.PROFILE_INOUT:
        payload["physic_type"] = lookup_mapping_rev(
            db, tenant_id, "drugtrace.physic_type", kind_key, dict_revs)
        supplier_code = str(header.get("supplier_code", "") or "")
        supplier_ent_id = lookup_mapping_rev(
            db, tenant_id, C.DICT_NS_SUPPLIER_ENT_ID, supplier_code,
            dict_revs)
        hospital_ent_id = lookup_mapping_rev(
            db, tenant_id, C.DICT_NS_ENT_ID, pharmacy, dict_revs)
        payload["client_type"] = lookup_mapping_rev(
            db, tenant_id, "drugtrace.client_type", bill.business_kind,
            dict_revs)
        if bill.business_kind == "SUPPLIER_IN":
            # 采购入库：from=供应商（发货）→ to=医院（收货）；两侧均为 ent_id
            payload["from_user_id"] = supplier_ent_id
            payload["to_user_id"] = hospital_ent_id
        else:
            # 退供应商：from=医院 → to=供应商
            payload["from_user_id"] = hospital_ent_id
            payload["to_user_id"] = supplier_ent_id
    if spec["patient_fields"] and header.get("patient_ref"):
        payload["patient_ref"] = header.get("patient_ref")
    return payload


def freeze_package(db, tenant_id: str, revision: DrugTraceBillRevision,
                   bill: DrugTraceBill, *, profile: str,
                   config_release_id: str | None = None) -> PackageRevision:
    """幂等冻结：同 digest 已 FROZEN 复用；否则 revision+1 新冻结包并 emit 事件。"""
    payload = build_payload(db, tenant_id, revision, bill, profile=profile)
    digest = digest_of(payload)
    pkg = (db.query(DataPackage)
           .filter_by(tenant_id=tenant_id, domain="drug_trace",
                      business_key=f"{profile}|{bill.connector_id}|"
                                   f"{bill.pharmacy_code}|{bill.business_kind}|"
                                   f"{bill.source_bill_no}").first())
    if pkg is None:
        pkg = DataPackage(id=new_uuid(), tenant_id=tenant_id, domain="drug_trace",
                          business_key=f"{profile}|{bill.connector_id}|"
                                       f"{bill.pharmacy_code}|{bill.business_kind}|"
                                       f"{bill.source_bill_no}")
        db.add(pkg)
        db.flush()
    last = (db.query(PackageRevision)
            .filter_by(package_id=pkg.id)
            .order_by(PackageRevision.revision.desc()).first())
    # FIX3-04：复用需 digest 且配置代次一致——同业务内容换配置代次必须新冻结包
    # （provenance.config_release_id 不同即新 revision，不返回旧代包）。
    last_cfg = str((last.provenance or {}).get("config_release_id") or "") \
        if last is not None else None
    if last is not None and last.digest == digest and last.state == "FROZEN" \
            and last_cfg == str(config_release_id or ""):
        return last
    rev_no = (last.revision + 1) if last else 1
    pr = PackageRevision(
        id=new_uuid(), tenant_id=tenant_id, package_id=pkg.id, revision=rev_no,
        schema_ref={"key": "drug_trace.bill", "version": "1.0",
                    "profile": profile},
        payload=payload, digest=digest,
        provenance={"bill_id": bill.id, "bill_revision_id": revision.id,
                    "source_revision": revision.source_revision,
                    "source_digest": revision.content_digest,
                    "config_release_id": config_release_id},
        state="FROZEN")
    db.add(pr)
    pkg.current_revision = rev_no
    db.flush()
    emit_outbox(db, tenant_id, f"drug_trace_package:{pr.id}",
                C.DomainEvent.PACKAGE_FROZEN,
                {"package_revision_id": pr.id, "package_id": pkg.id,
                 "profile": profile, "bill_id": bill.id})
    return pr
