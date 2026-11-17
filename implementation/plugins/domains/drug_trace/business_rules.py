"""HIS 处方业务分支适配层（DTR-FINALIZE-20261020 FIN-04）。

r7 视图契约把 HIS 原始状态列原样暴露在 ``source_bill_kind_raw``
（形如 ``"PRESC_SOURCE=0;FLAG=1;SPLIT_FLAG=1"``，分号分隔 key=value，
全部原值字符串，不改写不猜默认）。本层把原始状态映射为业务分类——
**只按显式登记的批准规则映射**：

- ``ApprovedStatusRules``：院方批准的（presc_source, flag）→ 分支表。
  初始为空（**没有任何已批准分支**）；批准以新的登记条目 + 证据引用进入，
  不改历史条目。
- 未命中批准规则 → ``("UNKNOWN", reason)``：进入质量等待
  （QD-BRANCH-UNAPPROVED），在 UI 可见，绝不静默跳过或降级成正常发药。

活库证据边界（docs/development/his-live-20261019）：8 条成功关联样本为
SOURCE=0/FLAG=1（门诊正常发药）；另有 42 张 SOURCE=1/FLAG=0（住院）与
FLAG=2/3（全退/部分退，注释口径）**未获院方批准**——不得凭样本或注释
自行批准（FIN-A16）。

分支定义（互斥、显式）：
- OUTPATIENT_NORMAL   门诊正常发药（候选：SOURCE=0 & FLAG=1）
- OUTPATIENT_REFUND_FULL / OUTPATIENT_REFUND_PARTIAL 退药（候选：FLAG=2/3）
- INPATIENT_DISPENSE  住院发药（候选：SOURCE=1；FLAG 口径未确认）
- 拆零是**码级**属性（SPLIT_FLAG），不构成单据分支；数量分摊规则单独批准。

持续增量模式的启用条件同样显式（FIN-04 两模式分离）：
- 批次模式（默认）：显式时间窗 + 完整业务键整单读取（read_bill_whole
  复核 revision + 持久重取账本已实现），可用于首张联调与历史补报。
- 持续增量模式：只有当 HIS/DBA 确认了**可靠、可索引的变更来源**
  （变更日志表/可索引修改时间/事件流，覆盖旧单补码、删码、无码转有码、
  退药修改）后才允许启用；``require_continuous_mode`` 校验连接器配置的
  ``change_source`` 证据，缺失即拒绝（AUTO_PRODUCTION 显式阻断）。
  MED_TRACE_RECORD 约 839 万行且无时间索引——短回看/周期抽样不能
  保证捕获任意旧单变化，不得当作可靠增量来源。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from contracts import ApiError

# ---------------------------------------------------------------- 原始状态解析
def parse_raw_status(raw) -> dict[str, str]:
    """source_bill_kind_raw → {key: value}（原值字符串；畸形条目跳过并记 __bad）。

    不解析语义：只切分号与第一个 '='。值不再二次解释（前导零/空格保留）。
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    bad = 0
    for part in str(raw).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            bad += 1
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    if bad:
        out["__bad_entries"] = str(bad)
    return out


# ---------------------------------------------------------------- 分支定义
BRANCH_OUTPATIENT_NORMAL = "OUTPATIENT_NORMAL"
BRANCH_OUTPATIENT_REFUND_FULL = "OUTPATIENT_REFUND_FULL"
BRANCH_OUTPATIENT_REFUND_PARTIAL = "OUTPATIENT_REFUND_PARTIAL"
BRANCH_INPATIENT_DISPENSE = "INPATIENT_DISPENSE"
BRANCH_UNKNOWN = "UNKNOWN"

ALL_BRANCHES = (BRANCH_OUTPATIENT_NORMAL, BRANCH_OUTPATIENT_REFUND_FULL,
                BRANCH_OUTPATIENT_REFUND_PARTIAL, BRANCH_INPATIENT_DISPENSE)

# 分支 → 本期 profile 的 business_kind 候选（批准后生效；未批准不进 PROFILES）
_BRANCH_TO_KIND = {
    BRANCH_OUTPATIENT_NORMAL: "PATIENT_DISPENSE",
    BRANCH_OUTPATIENT_REFUND_FULL: "PATIENT_RETURN",
    BRANCH_OUTPATIENT_REFUND_PARTIAL: "PATIENT_RETURN",
    # 住院发药的接口/方向未确认（uploadretail 或独立口径），映射留空待批准
    BRANCH_INPATIENT_DISPENSE: "",
}


@dataclass(frozen=True)
class ApprovedRule:
    """一条已批准的原始状态 → 分支规则。

    evidence_ref 必填：批准依据（需求确认单/院方签认/当期官方文档）；
    没有证据引用的规则不允许登记（防止代码内私自放行）。
    """
    presc_source: str
    flag: str
    branch: str
    approved_by: str
    evidence_ref: str


@dataclass
class ApprovedStatusRules:
    """批准规则登记（可审查修订；历史条目不改，新增追加）。"""
    entries: tuple[ApprovedRule, ...] = field(default=())

    def resolve(self, presc_source: str, flag: str) -> ApprovedRule | None:
        for r in self.entries:
            if r.presc_source == presc_source and r.flag == flag:
                return r
        return None


# 当前登记：**空**。8 条样本与源注释都不构成批准（FIN-A16）。
APPROVED_STATUS_RULES = ApprovedStatusRules(entries=())

# 候选观察（未批准）：供院方批准时对照，不参与运行时判定
CANDIDATE_RULES = (
    ("0", "1", BRANCH_OUTPATIENT_NORMAL,
     "his-live 8/8 样本为 SOURCE=0/FLAG=1（仅观察，未批准）"),
    ("0", "2", BRANCH_OUTPATIENT_REFUND_FULL,
     "源注释 FLAG=2 全退（注释非批准）"),
    ("0", "3", BRANCH_OUTPATIENT_REFUND_PARTIAL,
     "源注释 FLAG=3 部分退（注释非批准）"),
    ("1", "0", BRANCH_INPATIENT_DISPENSE,
     "his-live 42 张 SOURCE=1/FLAG=0（口径未确认，未批准）"),
)


def classify_branch(raw_status: dict[str, str],
                    rules: ApprovedStatusRules | None = None) -> tuple[str, str]:
    """原始状态 → (分支, 原因)。未批准 → (UNKNOWN, 需要的理由)。

    返回的分支只用于可见的解释与质量项；business_kind 仍由
    resolve_business_kind 输出（未批准 → UNKNOWN 进入质量等待）。
    """
    rules = rules if rules is not None else APPROVED_STATUS_RULES
    src = raw_status.get("PRESC_SOURCE", "")
    flag = raw_status.get("FLAG", "")
    rule = rules.resolve(src, flag)
    if rule is not None:
        return rule.branch, f"approved rule by {rule.approved_by}"
    if src == "" and flag == "":
        return BRANCH_UNKNOWN, "raw status absent (PRESC_SOURCE/FLAG missing)"
    return BRANCH_UNKNOWN, (
        f"no approved rule for PRESC_SOURCE={src!r} FLAG={flag!r}; "
        "sample evidence or column comments are not approval (EXT-02/08)")


def resolve_business_kind(header: dict,
                          rules: ApprovedStatusRules | None = None) -> str:
    """主单 → business_kind：视图已给非 UNKNOWN 值时尊重视图；否则按批准
    规则从 source_bill_kind_raw 解析；未批准 → UNKNOWN（质量等待）。"""
    kind = str(header.get("business_kind", "") or "")
    if kind and kind != "UNKNOWN":
        return kind
    raw = parse_raw_status(header.get("source_bill_kind_raw"))
    if not raw:
        return "UNKNOWN"
    branch, _ = classify_branch(raw, rules)
    return _BRANCH_TO_KIND.get(branch) or "UNKNOWN"


def branch_wait_issue(header: dict) -> dict | None:
    """未批准分支的质量项（WAITING；可见不静默）。

    仅在 business_kind 因分支未批准而为 UNKNOWN 时生成；普通
    QD-KIND-UNKNOWN（视图无分类）仍由 quality.evaluate_revision 登记。
    """
    kind = str(header.get("business_kind", "") or "")
    if kind and kind != "UNKNOWN":
        return None
    raw = parse_raw_status(header.get("source_bill_kind_raw"))
    if not raw:
        return None
    branch, reason = classify_branch(raw)
    if branch != BRANCH_UNKNOWN:
        return None
    return {"rule": "QD-BRANCH-UNAPPROVED", "severity": "BLOCKING",
            "detail": {"raw": dict(sorted(raw.items())), "reason": reason,
                       "policy": "wait for hospital-approved branch rules; "
                                 "never silently billed as normal dispense"}}


# ---------------------------------------------------------------- 增量模式门禁
def require_continuous_mode(connector_config: dict | None) -> None:
    """持续增量模式授权检查（FIN-04/FIN-A18）。

    连接器配置 ``collection_mode="continuous"`` 时必须同时提供
    ``change_source={"kind": ..., "evidence_ref": ...}``（HIS/DBA 确认的
    可靠、可索引变更来源）。缺失 → SOURCE_PROFILE_UNVERIFIED 拒绝；
    批次模式（默认 "batch"）不受影响。
    """
    cfg = connector_config or {}
    mode = str(cfg.get("collection_mode") or "batch")
    if mode != "continuous":
        if mode not in ("batch",):
            raise ApiError(
                "VALIDATION_FAILED",
                f"unknown collection_mode {mode!r}; allowed: batch|continuous")
        return
    cs = cfg.get("change_source")
    if not isinstance(cs, dict) or not cs.get("kind") \
            or not cs.get("evidence_ref"):
        raise ApiError(
            "SOURCE_PROFILE_UNVERIFIED",
            "continuous collection mode requires a confirmed, indexable "
            "change source {kind, evidence_ref} covering late code adds/"
            "deletes and refunds; short lookback over an 8.39M-row "
            "time-index-less table is NOT a reliable change source "
            "(FIN-04; AUTO_PRODUCTION stays blocked until DBA confirms)")
