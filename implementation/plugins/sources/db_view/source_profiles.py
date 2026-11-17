"""Versioned source facts, separate from credentials and business mappings.

r1 records platform metadata; r2 records an authorized, bounded live probe.
Neither revision authorizes production collection. New evidence creates revisions.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from connector_sdk import CapabilityError


@dataclass(frozen=True)
class SourceProfile:
    key: str
    source_code: str
    metadata_system: str
    host: str
    port: int
    registered_service: str
    service_mode: str
    registered_write_policy: str
    evidence_level: str
    captured_at: str
    candidate_object: str
    columns: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    evidence_ref: str
    collection_modes: tuple[str, ...] = ("batch", "continuous")
    reader: str = ""
    objects: tuple[tuple[str, str], ...] = ()
    dsn_env: str = ""
    limitations: tuple[str, ...] = ()

    def require_runtime_ready(self, *, config: dict | None = None) -> None:
        # Registration facts alone cannot authorize production collection.
        if (self.registered_write_policy != "readonly"
                or self.evidence_level != "LIVE_CONTRACT_VERIFIED"
                or self.unresolved):
            reason = ("is metadata only" if self.evidence_level == "PLATFORM_METADATA_ONLY"
                      else "has limited live evidence only")
            raise CapabilityError(
                "SOURCE_PROFILE_UNVERIFIED",
                f"source profile {self.key} {reason}; collection blocked: "
                + ", ".join(self.unresolved))
        if self.reader:
            cfg = config or {}
            if (cfg.get("collection_mode", "batch") not in self.collection_modes
                    or cfg.get("dsn_env") != self.dsn_env
                    or cfg.get("objects") != dict(self.objects)):
                raise CapabilityError(
                    "SOURCE_PROFILE_UNVERIFIED",
                    "verified view profile requires its exact reader binding, "
                    "objects and supported collection mode")


HIS_TRACE_20261019_R1 = SourceProfile(
    key="his_source_<his_host_id>@20261019-r1",
    source_code="his_source_<his_host_id>", metadata_system="HIS",
    host="<his-host>", port=1521, registered_service="his",
    service_mode="service_name", registered_write_policy="medical_dict_push",
    evidence_level="PLATFORM_METADATA_ONLY",
    captured_at="2026-10-19T09:13:05.557142+00:00",
    candidate_object="DRUG_USER.MED_TRACE_RECORD",
    columns=(
        ("ORG_CODE", "VARCHAR2"), ("RECORD_ID", "VARCHAR2"),
        ("STORE_CODE", "VARCHAR2"), ("TRACE_BILLNO", "VARCHAR2"),
        ("DRUG_PRI", "VARCHAR2"), ("BATCH_NO", "VARCHAR2"),
        ("EXPIRE_DATE", "DATE"), ("PRODUCT_DATE", "DATE"),
        ("STORE_ID", "VARCHAR2"), ("F_TRACE_CODE", "VARCHAR2"),
        ("S_TRACE_CODE", "VARCHAR2"), ("T_TRACE_CODE", "VARCHAR2"),
        ("DRUG_ID", "VARCHAR2"), ("PRICE", "NUMBER"),
        ("CREATE_USER_ID", "VARCHAR2"), ("CREATE_USER_NAME", "VARCHAR2"),
        ("CREATE_TIME", "DATE"), ("MODIFY_USER_ID", "VARCHAR2"),
        ("MODIFY_USER_NAME", "VARCHAR2"), ("MODIFY_TIME", "DATE"),
        ("IS_DELETED", "NUMBER"), ("DELETE_USER_ID", "VARCHAR2"),
        ("DELETE_USER_NAME", "VARCHAR2"), ("DELETE_TIME", "DATE"),
        ("KIND_TYPE", "VARCHAR2"), ("IN_OUT_TYPE", "VARCHAR2"),
        ("PRESC_DATE", "DATE"), ("PRESC_DETAILID", "VARCHAR2"),
        ("SPLIT_FLAG", "NUMBER"), ("TRACES_SUM", "NUMBER"),
    ),
    unresolved=(
        "readonly_source_registration", "approved_live_window",
        "oracle_service_and_object_grants", "bill_and_code_views",
        "confirmed_value_domains", "bill_identity_and_finalization",
        "trace_level_and_change_semantics",
    ),
    evidence_ref="docs/development/his-sjzc-20261019/evidence/platform-metadata.json",
)


HIS_TRACE_20261019_R2 = replace(
    HIS_TRACE_20261019_R1,
    key="his_source_<his_host_id>@20261019-r2",
    evidence_level="LIVE_READONLY_PROBED",
    captured_at="2026-10-19T09:50:26.463257+00:00",
    unresolved=(
        "restricted_production_readonly_binding", "bill_and_code_views",
        "approved_value_domains_and_finalization", "return_and_inpatient_rules",
        "split_quantity_allocation", "late_change_discovery_and_indexes",
        "platform_identity_and_first_send_approval",
    ),
    evidence_ref="docs/development/his-live-20261019/README.md",
)


# r7（2026-10-20 草案）：基于 r2 活库事实的视图契约候选——**视图尚未建/未验**，
# evidence_level=CANDIDATE_DRAFT；DBA 建视图+限量整单核验后另立新 revision 关闭。
# 不能把 r2/r7 改标 LIVE_CONTRACT_VERIFIED，也不能删 require_runtime_ready。
HIS_TRACE_20261020_R7 = replace(
    HIS_TRACE_20261019_R2,
    key="his_source_<his_host_id>@20261020-r7",
    captured_at="2026-10-20T12:00:00+00:00",
    unresolved=(
        "dba_reviewed_view_ddl_execution",         # sql-r7/10 草案待 DBA 审核
        "restricted_production_readonly_binding",  # sql-r7/20 草案待 DBA 执行
        "live_view_contract_verification",         # sql-r7/30 未执行
        "approved_value_domains_and_finalization",
        "return_and_inpatient_rules",
        "split_quantity_allocation",
        "late_change_discovery_and_indexes",       # sql-r7/40 可选索引未评估
        "platform_identity_and_first_send_approval",
    ),
    evidence_ref="docs/deployment/reporting-20261015/view-contract-r7.md",
)


@dataclass(frozen=True)
class PrescriptionTraceFacts:
    profile_key: str
    header_table: str
    detail_table: str
    trace_table: str
    header_primary_key: tuple[str, ...]
    detail_primary_key: tuple[str, ...]
    code_primary_key: tuple[str, ...]
    candidate_bill_join: tuple[tuple[str, str], ...]
    candidate_detail_expression: str
    indexed_header_time: str
    trace_has_time_index: bool
    observed_code_rows: int
    observed_split_rows: int
    unique_detail_matches: int
    evidence_refs: tuple[str, ...]


HIS_PRESCRIPTION_TRACE_FACTS_R2 = PrescriptionTraceFacts(
    profile_key=HIS_TRACE_20261019_R2.key,
    header_table="PHARMACY.DRUG_PRESC_MASTER",
    detail_table="PHARMACY.DRUG_PRESC_DETAIL",
    trace_table="DRUG_USER.MED_TRACE_RECORD",
    header_primary_key=("PRESC_DATE", "PRESC_NO"),
    detail_primary_key=("PRESC_DATE", "PRESC_NO", "ITEM_NO"),
    code_primary_key=("RECORD_ID",),
    candidate_bill_join=(("T.TRACE_BILLNO", "TO_CHAR(M.PRESC_NO)"),
                         ("T.PRESC_DATE", "M.PRESC_DATE"),
                         ("T.STORE_CODE", "M.DISPENSARY")),
    candidate_detail_expression=(
        "TO_CHAR(D.PRESC_DATE,'YYYYMMDDHH24MISS')||'_'||"
        "TO_CHAR(D.PRESC_NO)||'_'||TO_CHAR(D.ITEM_NO)"),
    indexed_header_time="M.DISPENSING_DATETIME",
    trace_has_time_index=False,
    observed_code_rows=8, observed_split_rows=6, unique_detail_matches=8,
    evidence_refs=(
        "docs/development/his-live-20261019/evidence/20-linked-out-summary.json",
        "docs/development/his-live-20261019/evidence/24-composite-detail-key-check.json",
        "docs/development/his-live-20261019/evidence/25-detail-code-cardinality.json",
    ),
)


# Separate app-owned restricted source. The broad sjzc registration remains
# medical_dict_push; r1/r2/r7 facts are deliberately unchanged.
HIS_VIEWS_20261022_R10 = SourceProfile(
    key="blyb_drugtrace_views@20261022-r10",
    source_code="blyb_drugtrace_views", metadata_system="HIS",
    host="<his-host>", port=1521, registered_service="his",
    service_mode="service_name", registered_write_policy="readonly",
    evidence_level="LIVE_CONTRACT_VERIFIED",
    captured_at="2026-10-22T09:26:00+00:00",
    candidate_object="PHARMACY.V_DRUG_TRACE_BILL",
    columns=(), unresolved=(),
    evidence_ref="docs/development/drug-trace-r10-readiness-20261022/README.md",
    collection_modes=("batch",), reader="BLYB_DRUGTRACE",
    objects=(("bill_view", "PHARMACY.V_DRUG_TRACE_BILL"),
             ("code_view", "PHARMACY.V_DRUG_TRACE_CODE")),
    dsn_env="BLYB_DRUGTRACE_ORACLE_DSN",
    limitations=("Only normal completed outpatient branch is classified",
                 "Unknown branches remain quality waiting",
                 "No continuous late-change discovery certification",
                 "No send permission or prior-upload reconciliation"),
)


# r11（DTR-SCOPE-DEV-20261027 SD-02/SD-03）：住院发药 + 门诊退药两组视图。
# 事实基线：his-meta r1-r4 + sd01 探查（申请表体系实测：REQUEST_NO/DIS_RECORDNO
# 在 MED_TRACE_RECORD 零命中=住院码未写码；退药 IN 码未写入）——视图 DDL 已交付
# DBA，**尚未创建**，故 evidence_level=LIVE_READONLY_PROBED（基表只读探查过）+
# unresolved 阻断生产采集；视图建好+限量整单核验后另立新 revision 关闭。
HIS_INP_VIEWS_20261027_R11 = SourceProfile(
    key="blyb_drugtrace_inp_views@20261027-r11",
    source_code="blyb_drugtrace_inp_views", metadata_system="HIS",
    host="<his-host>", port=1521, registered_service="his",
    service_mode="service_name", registered_write_policy="readonly",
    evidence_level="LIVE_READONLY_PROBED",
    captured_at="2026-10-27T14:10:00+00:00",
    candidate_object="PHARMACY.V_DRUG_TRACE_BILL_INP",
    columns=(),
    unresolved=("dba_r11_view_creation",          # sql/r11_inpatient_views.sql 待 DBA 执行
                "blyb_drugtrace_r11_grants",      # 同 r10 权限（两视图 SELECT）
                "live_r11_view_contract_verification",
                "inpatient_bill_type_platform_confirmation",   # 字典 DRAFT，发送 fail-closed
                "inpatient_code_writeback_pending"),           # SD-01.1：住院码未写入源库
    evidence_ref="docs/development/drug-trace-scope-dev-20261027/evidence/SD-01-findings.md",
    collection_modes=("batch",), reader="BLYB_DRUGTRACE",
    objects=(("bill_view", "PHARMACY.V_DRUG_TRACE_BILL_INP"),
             ("code_view", "PHARMACY.V_DRUG_TRACE_CODE_INP")),
    dsn_env="BLYB_DRUGTRACE_ORACLE_DSN",
    limitations=("Inpatient codes not yet written to MED_TRACE_RECORD "
                 "(codes view currently returns 0 rows; bills stay WAITING)",
                 "Bill_type for inpatient pending platform confirmation; "
                 "dictionary stays DRAFT and send stays fail-closed"),
)


HIS_RETURN_VIEWS_20261027_R11 = replace(
    HIS_INP_VIEWS_20261027_R11,
    key="blyb_drugtrace_return_views@20261027-r11",
    source_code="blyb_drugtrace_return_views",
    candidate_object="PHARMACY.V_DRUG_TRACE_RETURN",
    objects=(("bill_view", "PHARMACY.V_DRUG_TRACE_RETURN"),
             ("code_view", "PHARMACY.V_DRUG_TRACE_RETURN_CODE")),
    unresolved=("dba_r11_view_creation",
                "blyb_drugtrace_r11_grants",
                "live_r11_view_contract_verification",
                "return_bill_type_platform_confirmation",
                "return_in_codes_not_written"),   # SD-01.2：退药 IN 码未写入；冲正用原发药码
    limitations=("Return IN codes not yet written to MED_TRACE_RECORD "
                 "(codes view currently returns 0 rows); reversal payload "
                 "reuses the original CONFIRMED dispense codes read-only",
                 "Return bill_type pending platform confirmation; dictionary "
                 "stays DRAFT and send stays fail-closed"),
)


def get_source_profile(key: str) -> SourceProfile:
    if key == HIS_VIEWS_20261022_R10.key:
        return HIS_VIEWS_20261022_R10
    if key == HIS_INP_VIEWS_20261027_R11.key:
        return HIS_INP_VIEWS_20261027_R11
    if key == HIS_RETURN_VIEWS_20261027_R11.key:
        return HIS_RETURN_VIEWS_20261027_R11
    if key == HIS_INP_VIEWS_20261028_R11A.key:
        return HIS_INP_VIEWS_20261028_R11A
    if key == HIS_RETURN_VIEWS_20261028_R11A.key:
        return HIS_RETURN_VIEWS_20261028_R11A
    if key == HIS_TRACE_20261019_R1.key:
        return HIS_TRACE_20261019_R1
    if key == HIS_TRACE_20261019_R2.key:
        return HIS_TRACE_20261019_R2
    if key == HIS_TRACE_20261020_R7.key:
        return HIS_TRACE_20261020_R7
    raise CapabilityError("CAPABILITY_UNSUPPORTED", "unknown source profile revision")


# r11a（DTR-SCOPE-DEPLOY-20261028）：r11 四视图已建并验证（evidence/r11-verify-*.json：
# 全 VALID；BILL_INP=6,525,493 / CODE_INP=47,295 / RETURN=7,722 / RETURN_CODE=17,238），
# bill_type 经用户渠道确认=321（2026-10-28，EXT-08 关闭）。住院码事实更新：仅 050121
# T+1 早批量写（约 1/4 覆盖），其余病区药房未接入——无码单按 K1 WAITING 不上报。
# 退药保留一项 unresolved：321 下退药报文为正数量+原单冻结码（reversal_of 仅本地
# 留痕），首单冲正语义须先单张试传验证平台按冲正记账，通过后另立 revision 关闭。
HIS_INP_VIEWS_20261028_R11A = replace(
    HIS_INP_VIEWS_20261027_R11,
    key="blyb_drugtrace_inp_views@20261028-r11a",
    evidence_level="LIVE_CONTRACT_VERIFIED",
    captured_at="2026-10-28T01:20:00+00:00",
    unresolved=(),
    evidence_ref="docs/development/drug-trace-scope-dev-20261027/evidence/"
                 "r11-verify-20261028T010803Z.json",
    limitations=("Inpatient codes written only by pharmacy 050121 (T+1 morning "
                 "batch, ~1/4 of its bills); other ward pharmacies not connected "
                 "— bills without codes stay WAITING per K1",
                 "bill_type=321 confirmed 2026-10-28 (user channel); dictionary "
                 "approved at deployment"),
)

HIS_RETURN_VIEWS_20261028_R11A = replace(
    HIS_RETURN_VIEWS_20261027_R11,
    key="blyb_drugtrace_return_views@20261028-r11a",
    evidence_level="LIVE_CONTRACT_VERIFIED",
    captured_at="2026-10-28T01:20:00+00:00",
    unresolved=("return_first_bill_smoke_pending",),
    evidence_ref="docs/development/drug-trace-scope-dev-20261027/evidence/"
                 "r11-verify-20261028T010803Z.json",
    limitations=("Return IN codes exist historically for some pharmacies/periods "
                 "(17,238 rows via view); absent IN codes fall back to the "
                 "original CONFIRMED dispense codes (read-only)",
                 "bill_type=321 confirmed 2026-10-28; first real return must be "
                 "a single observed send (positive-quantity semantics under 321) "
                 "before bulk enable"),
)
