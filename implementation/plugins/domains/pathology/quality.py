"""语义质量规则引擎（10 §5）。规则在 IndexCodec 之前执行。

每次校验固定 evaluated_at、原值、规则修订、时区和容差，保证回放可解释。
病例存储不被质量错误阻止——问题只阻断对应 blocking_scope。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

RULESET_REVISION = "hospital-demo-quality-v1"


@dataclass
class QualityInput:
    case: dict                    # pathology_case 关键字段
    application: dict | None      # {requested_at, requested_at_source, category_raw, ...}
    reports: list                 # [{issued_at, audit_time, source_report_id, ...}]
    identity: dict | None         # IdentityOutcome 序列化
    completeness: dict | None     # {expected_known, expected_count, scanned_count, missing}
    encounter: dict | None        # {state, candidates_count}
    org_mapping: dict | None      # 已批准 报告机构→医保码 映射；None=未批准
    category_dictionary: dict | None  # 已批准类别字典；None=未批准


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def evaluate(inp: QualityInput, *, evaluated_at: datetime,
             clock_tolerance_seconds: int = 300,
             ruleset_revision: str = RULESET_REVISION) -> list[dict]:
    issues: list[dict] = []

    def add(rule, scope, path, detail, severity="BLOCKING"):
        issues.append({"rule_code": rule, "blocking_scope": scope, "field_path": path,
                       "severity": severity, "detail": detail,
                       "rule_revision": ruleset_revision,
                       "evaluated_at": evaluated_at.isoformat(),
                       "context": {"case_id": inp.case.get("id")}})

    app = inp.application or {}
    requested_at = _parse(app.get("requested_at"))
    requested_source = app.get("requested_at_source") or ""

    # --- 时间语义：申请时间权威字段未确认 / 只有创建时间
    if requested_at is None or requested_source in ("", "CreateTime", "unknown"):
        add("QF-TIME-SEMANTICS", "index", "application.requested_at",
            f"application time semantics unconfirmed (source={requested_source!r}); "
            f"CreateTime/local creation is not the authoritative application time")

    if requested_at is not None:
        if requested_at > evaluated_at + timedelta(seconds=clock_tolerance_seconds):
            add("QF-DATE-FUTURE", "index", "application.requested_at",
                f"application time {requested_at.isoformat()} is after evaluated_at"
                f"+tolerance({clock_tolerance_seconds}s); not corrected from apply-no or now")
        birth = _parse(inp.case.get("birth_date"))
        if birth and requested_at < birth:
            add("QF-DATE-BEFORE-BIRTH", "index", "application.requested_at",
                f"application time before patient birth {birth.isoformat()}; birth unknown not fabricated")
        for r in inp.reports:
            issued = _parse(r.get("issued_at"))
            if issued and issued < requested_at:
                # 外院会诊原报告（早于本次申请）在输入中标注 consultation_original=True 时不告警
                if not r.get("consultation_original"):
                    add("QF-DATE-ORDER", "index", f"report:{r.get('source_report_id')}",
                        f"report issued {issued.isoformat()} before application "
                        f"{requested_at.isoformat()}")

    # --- 证件/身份（来自 identity 决策结果）
    if inp.identity and inp.identity.get("issues"):
        for i in inp.identity["issues"]:
            add(i["rule_code"], i["blocking_scope"], i["field_path"], i["detail"])

    # --- 类别
    category = app.get("category_raw")
    if inp.category_dictionary is None:
        add("QF-CATEGORY", "index", "application.category",
            f"category dictionary not approved; raw={category!r} kept, no default '常规'")
    elif category in (None, "", "--") or category not in inp.category_dictionary:
        add("QF-CATEGORY", "index", "application.category",
            f"category raw={category!r} not in approved dictionary; waiting, no inference")

    # --- 机构码
    org_raw = app.get("request_org_raw") or inp.case.get("report_org_raw")
    if inp.org_mapping is None:
        add("QF-ORG", "index", "application.request_org",
            f"report organization → insurance code mapping not approved (raw={org_raw!r}); "
            f"scanner org is not substituted")
    elif org_raw not in inp.org_mapping:
        add("QF-ORG", "index", "application.request_org",
            f"organization raw={org_raw!r} not in approved mapping; waiting")

    # --- 状态值域未批准：不据 0/512/打印状态 推断扫描/审核/齐套
    status_raw = app.get("status_raw")
    if status_raw not in (None, "") and not inp.case.get("status_dictionary_approved"):
        add("QF-STATUS", "readiness", "application.status",
            f"status raw={status_raw!r} has no approved dictionary; not treated as "
            f"scanned/audited/complete", severity="WARNING")

    # --- 就诊
    enc = inp.encounter or {}
    if enc.get("state") != "CONFIRMED":
        n = enc.get("candidates_count", 0)
        if n > 1:
            add("QF-ENCOUNTER", "readiness", "encounter",
                f"multiple visits matched ({n}) without confirmed VISIT bridge; "
                f"latest visit is NOT auto-selected")
        elif n == 0:
            add("QF-ENCOUNTER", "readiness", "encounter",
                "no encounter resolved yet")

    # --- 齐套
    comp = inp.completeness or {}
    if not comp.get("expected_known"):
        add("QF-COMPLETENESS", "readiness", "expected_slide_set",
            "expected slide set unknown (closed signal unapproved); completeness not asserted")
    else:
        missing = comp.get("missing") or []
        if missing:
            add("QF-COMPLETENESS", "readiness", "expected_slide_set",
                f"{len(missing)} expected slide(s) without approved scan: "
                f"{[m.get('barcode_raw') for m in missing][:10]}")
        unapproved = comp.get("unapproved_scans") or []
        if unapproved:
            add("QF-COMPLETENESS", "readiness", "slide_effective_version",
                f"{len(unapproved)} slide(s) have scans but no approved effective version")

    return issues


def blocks(issues: list[dict], scope: str) -> bool:
    return any(i["blocking_scope"] == scope and i["severity"] == "BLOCKING" for i in issues)
