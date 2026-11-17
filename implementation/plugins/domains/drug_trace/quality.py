"""药品追溯质量规则（W04；DTR-A05/A06/A12/A13/S01/S06/S08）。

检查只做「判定与登记」，绝不静默修复：不 DISTINCT 去重、不截断、不转数值、
不猜测层级。所有输入保持字符串原值（前导零/超长码）。

K1（SD-05，DTR-SCOPE-DEV-20261027）：门诊（PATIENT_DISPENSE）0 有效码且
bill_time 早于回看窗（默认 7 天，GMT+8 墙钟口径）→ NOT_REPORTABLE 终态
（QD-NO-CODES-FINAL）；7 天内维持 WAITING 等 K3 补码。住院/退药分支不适用。
K2（SD-03）：PATIENT_RETURN 0 自有码时，若原发药单已 CONFIRMED（return_original
注入其冻结 payload 码列表）→ VALID（QD-RETURN-REVERSES-ORIGINAL 告警）；
否则 QD-RETURN-ORIGINAL-PENDING 等待。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from contracts import ApiError
from core_db.models import QualityIssue

from . import contracts as C

# 定点数量：可选符号 + 数字 + 至多4位小数（拆零）；其他形式进入 QD-QUANTITY-INVALID
_QTY_RE = re.compile(r"^-?\d+(\.\d{1,4})?$")

# 业务墙钟（HIS/TOP 同口径，EXT-02）：bill_time 为 naive GMT+8
_GMT8 = timezone(timedelta(hours=8))
NO_CODE_LOOKBACK_DAYS = 7          # K1 回看窗（与驱动 v3 LOOKBACK_DAYS 同默认值）


def now_gmt8() -> datetime:
    """当前业务墙钟（naive GMT+8）——与采集/bill_time 同一口径（测试可注入 now）。"""
    return datetime.now(_GMT8).replace(tzinfo=None)


def as_original_str(value) -> str:
    """外部标识原值化：None→''；数字类型不得经 float（前导零/精度丢失）。

    来源驱动给的 NUMBER 也会先落 str（Oracle NUMBER 无前导零语义）；r5 契约要求
    视图对码/单号列 TO_CHAR 输出，这里仅兜底。
    """
    if value is None:
        return ""
    if isinstance(value, float):
        # 浮点已丢精度：这是视图契约违规，宁可报错也不静默
        raise ApiError("VALIDATION_FAILED",
                       "source value arrived as float (view contract requires "
                       "TO_CHAR): %r" % (value,))
    return str(value)


_TRUE_SET = {"1", "y", "yes", "true", "t"}
_FALSE_SET = {"", "0", "n", "no", "false", "f"}


def as_bool(value, field: str = "") -> bool:
    """DBAPI/视图布尔归一（R2-07）：'0' 不再经 bool('0') 误判为真。

    未知值（'2'、乱码等）显式报错——缺必需字段语义进入质量错误，不默认已完成。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, int):
        if value == 1:
            return True
        if value == 0:
            return False
    text = str(value).strip().lower()
    if text in _TRUE_SET:
        return True
    if text in _FALSE_SET:
        return False
    raise ApiError("VALIDATION_FAILED",
                   f"boolean field {field or '?'} has unknown value {value!r}; "
                   "allowed: 1/0/y/n/true/false")


def check_quantity(qty: str) -> str | None:
    """数量校验：合法返回规范化定点串；非法返回 None（调用方登记质量项）。"""
    if qty is None or qty == "":
        return None  # 允许缺失（数量可选）
    if not _QTY_RE.match(qty):
        return None
    try:
        d = Decimal(qty)
    except InvalidOperation:
        return None
    if d <= 0:
        return None
    return str(d.normalize()) if d == d.to_integral_value() else qty


def evaluate_revision(header: dict, lines: list[dict], *, profile: str | None,
                      code_limit_guard: int, now: datetime | None = None,
                      return_original: dict | None = None
                      ) -> tuple[str, list[dict]]:
    """整单质量评估：返回 (state, issues[{rule,severity,detail}])。

    lines 为视图原值行（含 is_deleted）。判定口径：
    - 0 有效码 → WAITING（QD-NO-CODES；不发空码列表）
    - K1/SD-05：PATIENT_DISPENSE 0 码且 bill_time < now-7d → NOT_REPORTABLE 终态
      （QD-NO-CODES-FINAL；7 天内维持 WAITING 等 K3 补码；仅门诊分支）
    - K2/SD-03：PATIENT_RETURN 0 自有码 + return_original（原发药 CONFIRMED 冻结
      payload 码列表，调用方注入）→ 不因 0 码等待（冲正码取自原单）；
      无 return_original → QD-RETURN-ORIGINAL-PENDING 等待
    - 同 source_code_record_id 多行 → INVALID（QD-DUP-SOURCE-ROW；视图契约违规）
    - 同单同码（有效、非删除）→ INVALID（QD-DUP-CODE）
    - 分类 UNKNOWN → WAITING；未完成 → WAITING；作废 → INVALID（QD-VOIDED，走变更流）
    - expected_code_count 提供且不符 → WAITING（QD-COUNT-MISMATCH，等来源齐套）
    - 有效码数 ≥ 保护值 → INVALID（QD-CODE-LIMIT-GUARD：阻断不截断、不拆单）
    - 层级标注缺失且存在 f/s/t 原值 → 记 QD-LEVEL-UNCONFIRMED（能力边界显式化，
      默认仅告警不阻断：视图 trace_code 即应报码，层级选择责任在院方视图/字典）
    """
    issues: list[dict] = []
    waiting = False
    invalid = False
    no_codes_final = False

    if header.get("business_kind", "UNKNOWN") == "UNKNOWN":
        issues.append({"rule": "QD-KIND-UNKNOWN", "severity": "BLOCKING",
                       "detail": "business_kind UNKNOWN; cannot select profile"})
        waiting = True
    if not header.get("finalized"):
        issues.append({"rule": "QD-NOT-FINALIZED", "severity": "BLOCKING",
                       "detail": "source bill not finalized"})
        waiting = True
    if header.get("is_void"):
        issues.append({"rule": "QD-VOIDED", "severity": "BLOCKING",
                       "detail": "source bill voided; change-stream only"})
        invalid = True

    # 复合键完整性（跨药房串单防护）
    for k in ("source_system", "source_bill_id", "pharmacy_code"):
        if not str(header.get(k, "") or "").strip():
            issues.append({"rule": "QD-CROSS-PHARMACY-KEY", "severity": "BLOCKING",
                           "detail": f"composite key part empty: {k}"})
            invalid = True

    record_ids: dict[str, int] = {}
    seen_codes: dict[str, int] = {}
    active_codes = 0
    level_unconfirmed = False
    active_lines: list[dict] = []
    for ln in lines:
        rid = str(ln.get("source_code_record_id", "") or "")
        record_ids[rid] = record_ids.get(rid, 0) + 1
        if ln.get("is_deleted"):
            continue
        active_lines.append(ln)
        code = str(ln.get("trace_code", "") or "")
        if code:
            active_codes += 1
            seen_codes[code] = seen_codes.get(code, 0) + 1
        if not ln.get("code_level") and (ln.get("f_trace_code")
                                         or ln.get("s_trace_code")
                                         or ln.get("t_trace_code")):
            level_unconfirmed = True
        if ln.get("quantity") is not None and ln.get("quantity") != "" and \
                check_quantity(str(ln.get("quantity"))) is None:
            issues.append({"rule": "QD-QUANTITY-INVALID", "severity": "BLOCKING",
                           "detail": f"line {rid}: quantity {ln.get('quantity')!r}"})
            invalid = True

    # 父子包装同单阻断（FIX2-07）：父包装码与本单另一有效行的追溯码相同
    # → 同单同时报父码与子码（重复计数风险），INVALID 不放行。
    active_code_set = {str(ln.get("trace_code", "") or "") for ln in active_lines}
    parent_collisions = sorted({
        str(ln.get("parent_code", "") or "")
        for ln in active_lines
        if ln.get("parent_code") and str(ln.get("parent_code")) in active_code_set})
    if parent_collisions:
        issues.append({"rule": "QD-PARENT-CHILD-SAME-BILL",
                       "severity": "BLOCKING",
                       "detail": {"parent_codes_also_billed": parent_collisions[:20],
                                  "policy": "parent and child codes must not be "
                                            "billed on the same bill"}})
        invalid = True

    dup_records = {k: v for k, v in record_ids.items() if v > 1}
    if dup_records:
        issues.append({"rule": "QD-DUP-SOURCE-ROW", "severity": "BLOCKING",
                       "detail": {"duplicated_record_ids": sorted(dup_records)[:20],
                                  "counts": dup_records}})
        invalid = True
    dup_codes = {k: v for k, v in seen_codes.items() if v > 1}
    if dup_codes:
        issues.append({"rule": "QD-DUP-CODE", "severity": "BLOCKING",
                       "detail": {"duplicated_codes": sorted(dup_codes)[:20],
                                  "counts": dup_codes}})
        invalid = True

    if active_codes == 0:
        kind = str(header.get("business_kind", "") or "")
        original_codes = list((return_original or {}).get("codes") or [])
        if kind == "PATIENT_RETURN" and original_codes:
            # K2/SD-03：冲正码取自原发药冻结 payload（只读复用），不因 0 自有码等待
            issues.append({"rule": "QD-RETURN-REVERSES-ORIGINAL",
                           "severity": "WARNING",
                           "detail": {"original_bill_code":
                                      (return_original or {}).get("bill_code", ""),
                                      "original_code_count": len(original_codes),
                                      "policy": "reversal codes sourced from the "
                                                "original frozen payload "
                                                "(read-only, never modified)"}})
        elif kind == "PATIENT_RETURN":
            issues.append({"rule": "QD-RETURN-ORIGINAL-PENDING",
                           "severity": "BLOCKING",
                           "detail": "patient return has no IN codes and the "
                                     "original dispense bill is not CONFIRMED "
                                     "yet; wait for the original upload"})
            waiting = True
        else:
            issues.append({"rule": "QD-NO-CODES", "severity": "BLOCKING",
                           "detail": "no active trace codes; wait for completion"})
            waiting = True
            if kind == "PATIENT_DISPENSE":
                # K1/SD-05：仅门诊分支适用 7 天回看窗终态
                bill_time = header.get("bill_time")
                ref = now if now is not None else now_gmt8()
                cutoff = ref - timedelta(days=NO_CODE_LOOKBACK_DAYS)
                bt = _as_gmt8_naive(bill_time)
                if bt is not None and bt < cutoff:
                    issues.append({
                        "rule": "QD-NO-CODES-FINAL", "severity": "BLOCKING",
                        "detail": {
                            "bill_time": str(bill_time),
                            "lookback_days": NO_CODE_LOOKBACK_DAYS,
                            "policy": "K1: no codes after the lookback window; "
                                      "bill is terminal NOT_REPORTABLE and is "
                                      "never auto-uploaded"}})
                    no_codes_final = True

    expected = header.get("expected_code_count")
    if expected is not None and str(expected) != "":
        try:
            if int(str(expected)) != active_codes:
                issues.append({"rule": "QD-COUNT-MISMATCH", "severity": "BLOCKING",
                               "detail": {"expected": str(expected),
                                          "actual_active": active_codes}})
                waiting = True
        except ValueError:
            issues.append({"rule": "QD-COUNT-MISMATCH", "severity": "WARNING",
                           "detail": f"expected_code_count not int: {expected!r}"})

    if code_limit_guard and active_codes >= code_limit_guard:
        issues.append({"rule": "QD-CODE-LIMIT-GUARD", "severity": "BLOCKING",
                       "detail": {"active_codes": active_codes,
                                  "guard": code_limit_guard,
                                  "profile": profile,
                                  "policy": "block, never truncate/split"}})
        invalid = True

    if level_unconfirmed:
        issues.append({"rule": "QD-LEVEL-UNCONFIRMED", "severity": "WARNING",
                       "detail": "f/s/t present but code_level unconfirmed; "
                                 "trace_code from view is authoritative (EXT-08)"})

    state = (C.BillQualityState.INVALID if invalid
             else C.BillQualityState.NOT_REPORTABLE if no_codes_final
             else C.BillQualityState.WAITING if waiting
             else C.BillQualityState.VALID)
    return state, issues


def _as_gmt8_naive(value) -> datetime | None:
    """bill_time 视图原值（ISO 字符串/datetime，naive=GMT+8）→ naive datetime。"""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_GMT8).replace(tzinfo=None)
    return dt


def persist_issues(db, tenant_id: str, revision, issues: list[dict],
                   *, assume_new: bool = False) -> None:
    """质量项落 QualityIssue（entity_ref=drug_trace_bill_revision:{id}）。

    assume_new=True（LT-08 性能路径）：调用方保证该 revision 为本事务新建
    （uuid 刚生成，库中不可能存在其 entity_ref 的旧行）——跳过清旧 DELETE
    （与「删 0 行」完全等价，省一次 QualityIssue (tenant,state) 索引扫描）。
    判定逻辑不变：issues 列表原样落库。
    """
    if not assume_new:
        db.query(QualityIssue).filter_by(
            tenant_id=tenant_id,
            entity_ref=f"drug_trace_bill_revision:{revision.id}",
            state="OPEN").delete()
    for it in issues:
        db.add(QualityIssue(
            tenant_id=tenant_id,
            entity_ref=f"drug_trace_bill_revision:{revision.id}",
            rule_code=it["rule"], severity=it.get("severity", "BLOCKING"),
            blocking_scope="package", field_path="",
            context_json={"detail": it.get("detail")},
            state="OPEN"))


def late_no_code_terminal_issues(header: dict, lines: list[dict],
                                 *, now: datetime | None = None) -> list[dict] | None:
    """存量 WAITING 老单的 K1 终态迁移判定（SD-05；幂等重判，不改快照内容）。

    返回新 issues 列表（应置 NOT_REPORTABLE）；不适用（7 天内/非门诊/有码/
    判 INVALID）返回 None。调用方仅在「同 digest 幂等重导」或 rescan 触碰时执行，
    不写一次性数据订正脚本。
    """
    if str(header.get("business_kind", "") or "") != "PATIENT_DISPENSE":
        return None
    if any(not ln.get("is_deleted") and str(ln.get("trace_code", "") or "")
           for ln in lines):
        return None                      # 有有效码：不是无码单
    state, issues = evaluate_revision(
        header, lines, profile=None, code_limit_guard=0, now=now)
    if state == C.BillQualityState.NOT_REPORTABLE:
        return issues
    return None
