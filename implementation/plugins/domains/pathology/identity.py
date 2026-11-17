"""身份决策规则（10 §4）。

值 = value + source_ref + source_field + observed_at + effective_at + rule_revision + evidence_ref。
校验位通过只构成候选；默认不自动赋111，不覆盖 HIS/PIS 冲突。
"""
from __future__ import annotations

from dataclasses import dataclass, field

WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK_CHARS = "10X98765432"
GA_RESIDENT_ID = "111"  # GA/T 2000.156 居民身份证


def id_card_checksum_ok(id_no: str) -> bool:
    if not isinstance(id_no, str) or len(id_no) != 18:
        return False
    try:
        total = sum(int(id_no[i]) * WEIGHTS[i] for i in range(17))
    except ValueError:
        return False
    return CHECK_CHARS[total % 11] == id_no[17].upper()


@dataclass
class IdentityOutcome:
    patient_state: str = "WAITING"          # CONFIRMED / WAITING / WAITING_HUMAN
    wait_reason: str | None = None
    id_type_code: str | None = None          # 已批准来源的3位码（或None）
    id_type_candidate: str | None = None     # 仅候选（如校验位通过的111建议）
    id_no_ref: str | None = None             # 敏感值引用，不落明文
    conflict: bool = False
    issues: list = field(default_factory=list)
    evidence: list = field(default_factory=list)


def decide_identity(*, pis_identity_card: str | None, his_patients: list,
                    approved_type_map: dict | None, policy: dict,
                    evaluated_at: str) -> IdentityOutcome:
    """approved_type_map: {'01': '111', ...} 来自已批准 dictionary_revision；
    None/空表示证件类型字典未批准（B05），任何类型值都不能自动转换。"""
    out = IdentityOutcome()
    pis_valid = pis_identity_card and id_card_checksum_ok(pis_identity_card)
    if pis_identity_card:
        out.evidence.append({"field": "pis.IdentityCard", "observed_at": evaluated_at,
                             "note": "checksum_pass" if pis_valid else "checksum_fail_or_unknown",
                             "source_ref": "pis.pitaya.sqlserver:Pitaya.IdentityCard"})

    if not his_patients:
        out.patient_state = "WAITING"
        out.wait_reason = "EXTERNAL_UNAVAILABLE" if his_patients is None else "VISIT_UNRESOLVED"
        out.issues.append({"rule_code": "QF-ID-TYPE", "blocking_scope": "index",
                           "field_path": "patient.identity",
                           "detail": "HIS patient master record not available yet"})
        if pis_valid:
            out.id_type_candidate = GA_RESIDENT_ID  # 仅候选，不自动赋值
        return out

    if len(his_patients) > 1:
        out.patient_state = "WAITING_HUMAN"
        out.wait_reason = "SOURCE_AMBIGUOUS"
        out.issues.append({"rule_code": "QF-ID-CONFLICT", "blocking_scope": "patient",
                           "field_path": "patient.identity",
                           "detail": "multiple HIS patients matched"})
        return out

    his = his_patients[0]
    his_type_raw = his.id_type_raw
    his_id_no = getattr(his, "_id_no_plain", None)  # 由调用方按权限注入的完整号码（仅比对）
    type_map = approved_type_map or {}

    conflict = False
    if his_id_no and pis_identity_card and his_id_no != pis_identity_card:
        conflict = True
    if conflict:
        out.patient_state = "WAITING_HUMAN"
        out.wait_reason = "IDENTITY_CONFLICT"
        out.conflict = True
        out.issues.append({"rule_code": "QF-ID-CONFLICT", "blocking_scope": "patient",
                           "field_path": "patient.id_no",
                           "detail": "PIS and HIS id numbers differ; neither overrides"})
        return out

    mapped = type_map.get(his_type_raw) if his_type_raw else None
    has_number = bool(his_id_no or getattr(his, "id_no_full_ref", None))
    if mapped and has_number:
        out.patient_state = "CONFIRMED"
        out.id_type_code = mapped
        out.id_no_ref = his.id_no_full_ref
        out.evidence.append({"field": "his.ID_TYPE", "value_raw": his_type_raw,
                             "mapped_to": mapped, "dictionary": "approved",
                             "observed_at": evaluated_at})
        out.evidence.append({"field": "patient.id_no", "present": True,
                             "via": "controlled_source",
                             "observed_at": evaluated_at})
        return out

    if mapped and not has_number:
        # R33：类型已映射但号码缺失 → 不能确认（类型、号码、归属缺一不可）
        out.patient_state = "WAITING"
        out.wait_reason = "ID_NUMBER_MISSING"
        out.issues.append({"rule_code": "QF-ID-TYPE", "blocking_scope": "index",
                           "field_path": "patient.id_no",
                           "detail": "id type mapped but number missing; "
                                     "confirmation requires type+number+ownership"})
        return out

    # 类型空/脏值/未映射：PIS 校验位通过只形成候选
    out.patient_state = "WAITING_HUMAN" if (pis_valid or his_type_raw) else "WAITING"
    out.wait_reason = "AWAITING_HUMAN_DECISION" if out.patient_state == "WAITING_HUMAN" else "DICTIONARY_UNMAPPED"
    if pis_valid:
        out.id_type_candidate = GA_RESIDENT_ID
    # 经医院批准的自动回退（10 §4：默认关闭；启用需源字段语义+精确绑定+规则版本+证据）
    if (pis_valid and not his_id_no and policy.get("fallback_enabled")
            and policy.get("fallback_rule") == "pis_checksum_valid_ga111"):
        out.patient_state = "CONFIRMED"
        out.id_type_code = GA_RESIDENT_ID
        out.id_no_ref = "pis.pitaya.sqlserver:Pitaya.IdentityCard"
        out.wait_reason = None
        out.issues = [i for i in out.issues if i["rule_code"] != "QF-ID-TYPE"]
        out.evidence.append({
            "field": "pis.IdentityCard", "rule": "pis_checksum_valid_ga111",
            "rule_revision": "identity-fallback-demo-v1",
            "evidence": policy.get("fallback_evidence", ""),
            "observed_at": evaluated_at})
        return out
    out.issues.append({
        "rule_code": "QF-ID-TYPE", "blocking_scope": "index",
        "field_path": "patient.id_type",
        "detail": f"HIS ID_TYPE raw={his_type_raw!r} unmapped/empty; "
                  f"PIS checksum-valid forms candidate only; auto-assign disabled by policy",
    })
    return out
