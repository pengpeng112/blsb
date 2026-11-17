"""药品追溯契约（W02）：profile、业务分类、视图字段、状态机常量。

口径来源：docs/development/drug-trace-20261016/{DEVELOPMENT_PLAN,SQL_REVIEW}.md
与官方 API 页（2026-10-16 查证）。所有数值/枚举在院方批准字典确认前一律视为
「文档记录值」，不得直接作为生产字典上线（见 EXT-08）。
"""
from __future__ import annotations

# ---------------------------------------------------------------- profile
PROFILE_INOUT = "alihealth_yljg_inout_v1"
PROFILE_RETAIL = "alihealth_yljg_retail_v1"

# 官方 TOP 方法名（developer.alibaba.com 2026-10-16 查证；EXT-07 关闭前签名一致性未验）
METHOD_UPLOAD_INOUT = "alibaba.alihealth.drugtrace.top.yljg.uploadinoutbill"
METHOD_UPLOAD_RETAIL = "alibaba.alihealth.drugtrace.top.yljg.uploadretail"
METHOD_QUERY_BILL_STATUS = "alibaba.alihealth.drugtrace.top.yljg.query.billstatus"

# 目的地身份 = destination.profile_revision 的 profile key（不加 schema 列：
# 已部署 001—008 库在未启用本插件时保持旧代码可运行）。两个 profile key 都指向
# 同一 adapter（DTR-BASE-01：显式目标派发，未知目标报不支持）。
DESTINATION_PROFILE_KEYS = (PROFILE_INOUT, PROFILE_RETAIL)

PROFILES: dict[str, dict] = {
    PROFILE_INOUT: {
        "key": PROFILE_INOUT,
        "label": "供应商采购入库/退供应商（uploadinoutbill）",
        "method": METHOD_UPLOAD_INOUT,
        "business_kinds": {"SUPPLIER_IN", "SUPPLIER_RETURN"},
        # 文档记录码数上限（当前文档 10000，边界描述有歧义）：作为可配置保护值，
        # 接近即阻断而不是截断；正式值以院方批准字典为准（EXT-08）。
        "code_limit_guard": 10000,
        "patient_fields": False,
    },
    PROFILE_RETAIL: {
        "key": PROFILE_RETAIL,
        "label": "患者发药/患者退药（uploadretail）",
        "method": METHOD_UPLOAD_RETAIL,
        # K4：住院发药按零售口径候选（bill_type 平台确认前字典 DRAFT、发送 fail-closed）
        "business_kinds": {"PATIENT_DISPENSE", "PATIENT_RETURN",
                           "INPATIENT_DISPENSE"},
        # 文档记录 3500；同上为保护初值。
        "code_limit_guard": 3500,
        "patient_fields": True,
    },
}


def profile_for_kind(business_kind: str) -> str | None:
    """业务分类 → profile。分类不明（UNKNOWN/未登记）返回 None → 进入质量等待，
    绝不能仅凭 IN_OUT_TYPE='OUT' 选择上传接口（DTR-A11/S05）。"""
    for key, spec in PROFILES.items():
        if business_kind in spec["business_kinds"]:
            return key
    return None


# ---------------------------------------------------------------- 业务分类
# 来源方向值（视图 IN_OUT_TYPE）→ 候选业务分类。仅为「候选」：最终分类必须由
# 视图 business_kind 字段（院方视图推导）+ 批准字典确认；映射不到 → UNKNOWN 等待。
BUSINESS_KINDS = {
    "SUPPLIER_IN": "供应商采购入库",
    "SUPPLIER_RETURN": "退供应商（供应商流转出库）",
    "PATIENT_DISPENSE": "患者发药（零售出库）",
    "PATIENT_RETURN": "患者退药",
    # K4（SD-02）：住院发药；r11 申请表体系来源，bill_type 待平台确认
    "INPATIENT_DISPENSE": "住院发药（患者）",
    "UNKNOWN": "业务分类不明（等待，不发送）",
}
# 用户历史 SQL 的 OUT+PRESC_DETAILID 条件只是零售候选，不得套用到库存分支（S05）。
RETAIL_CANDIDATE_HINTS = ("OUT", "PRESC_DETAILID_NOT_NULL")

# ---------------------------------------------------------------- 视图契约 r5
# 视图名沿用旧 view-contract（r4）命名；r5 为新增草案（不改 r4 原文）。
VIEW_BILL = "V_DRUG_TRACE_BILL"
VIEW_CODE = "V_DRUG_TRACE_CODE"

# 主单必需字段（缺失即采集失败，不猜默认值）
BILL_VIEW_REQUIRED = (
    "source_system",        # 来源系统代码
    "source_bill_id",       # 来源主单业务键（原值字符串，含前导零）
    "source_bill_no",       # 原始单号（展示用；业务键仍用 source_bill_id）
    "source_revision",      # 来源版本（单调可比；无法提供时用 updated_at+digest）
    "business_kind",        # 业务分类（院方视图推导；UNKNOWN 允许并等待）
    "pharmacy_code",        # 药房/机构代码（跨药房同号隔离的组成部分）
    "bill_time",            # 业务完成时间（发药/出入库完成，非入表时间）
    "updated_at",           # 变更发现游标（半开区间上界）
    "finalized",            # 是否完成（未完成等待）
    "is_void",              # 是否作废（作废进入变更流，不静默消失）
)
BILL_VIEW_OPTIONAL = (
    "source_event_seq",     # 可靠单调事件序列（优先于 updated_at 游标）
    "warehouse_code",       # 库存分支：仓库
    "supplier_code",        # 库存分支：供应商内部标识
    "counterparty_ref",     # 收发对端内部标识/方向说明
    "expected_code_count",  # 仅在来源有独立依据时提供；缺失不做码数强校验
    "patient_ref",          # 零售分支：患者标识（最小化，按需读取）
    "encounter_ref",        # 零售分支：就诊标识
    "source_bill_kind_raw", # 原始业务类型字段（KIND_TYPE 等原值保留供审计）
)

# 明细必需字段：与主单一致的完整来源复合键 + 明细/码级标识
CODE_VIEW_REQUIRED = (
    "source_system", "source_bill_id", "pharmacy_code", "business_kind",
    "source_revision",
    "source_line_id",       # 明细行键（处方明细/出入库明细行）
    "source_code_record_id",# 扫码记录稳定主键（每条真实扫码一行，绝不聚合挑码）
    "trace_code",           # 应报层级追溯码（F/S/T 由院方视图/字典定义；原样字符串）
    "updated_at",
    "is_deleted",           # 删除标记：删除也是变更（显式可见，不静默过滤）
)
CODE_VIEW_OPTIONAL = (
    "quantity",             # 数量（字符串定点数；拆零须有业务依据）
    "unit",
    "code_level",           # F/S/T 层级标注（未确认时由质量规则标记边界）
    "parent_code",          # 父包装码（来源可提供时）
    "f_trace_code", "s_trace_code", "t_trace_code",  # 原值保留（不盲 COALESCE/全发）
    "bill_time",            # 明细行业务时间（与主单可能不同；整单组包以主单为准并审计差异）
)

# 采集约束（DTR-A14/A08）：白名单视图 + 绑定参数 + keyset 分页；时间一律半开区间
CURSOR_KEYS = ("updated_at", "source_bill_id", "pharmacy_code", "business_kind")
HALF_OPEN = ">= begin AND < end"   # 相邻窗口端点不重复（S03）

# ---------------------------------------------------------------- 状态机
class BillQualityState:
    WAITING = "WAITING"       # 质量等待（0码/未完成/字典未映射/分类不明/未齐套）
    VALID = "VALID"           # 可组包
    INVALID = "INVALID"       # 不可修复（需人工处置）
    # K1（SD-05）：门诊单超 7 天回看窗仍 0 码 → 终态"不上报"；仅 PATIENT_DISPENSE
    NOT_REPORTABLE = "NOT_REPORTABLE"

class DomainJobType:
    COLLECT = "drug_trace.collect"          # 整单抽取（窗口）
    PREPARE = "drug_trace.prepare"          # 冻结包+提交预览
    SEND = "drug_trace.send"                # 发送（需证据+批准）
    QUERY = "drug_trace.query_status"       # 主动查询
    RECONCILE = "drug_trace.reconcile"      # 对账
    # K3（SD-04）：定向复核（按单据键点查重扫，不做窗口扫描；驱动 v3 回看阶段用）
    RESCAN = "drug_trace.rescan"

class DomainEvent:
    BILL_INGESTED = "drug_trace.bill.ingested"        # 新版本落库
    PACKAGE_FROZEN = "drug_trace.package.frozen"      # 冻结包可发送
    SUBMISSION_UNKNOWN = "drug_trace.submission.unknown"
    # 查询轮询达到上限仍未终态：显式告警转人工（FIX3-03，不静默结束）
    SUBMISSION_ALERT = "drug_trace.submission.alert"

# 质量规则码（沿用全局 QUALITY_RULES 风格；领域专属前缀 QD-）
QUALITY_RULE_CODES = {
    "QD-NO-CODES",            # 0 码：等待，不得发空码列表
    "QD-NO-CODES-FINAL",      # K1/SD-05：门诊超 7 天回看窗仍 0 码 → NOT_REPORTABLE 终态
    "QD-RETURN-REVERSES-ORIGINAL",  # K2/SD-03：冲正码取自原发药冻结 payload（只读复用）
    "QD-RETURN-ORIGINAL-PENDING",   # K2/SD-03：原发药单尚未 CONFIRMED，冲正等待
    "QD-DUP-SOURCE-ROW",      # 来源重复行（同 source_code_record_id 多行）
    "QD-DUP-CODE",            # 同单同码
    "QD-LEVEL-UNCONFIRMED",   # F/S/T 层级未确认（能力边界显式化）
    "QD-KIND-UNKNOWN",        # 业务分类不明
    "QD-NOT-FINALIZED",       # 来源未完成
    "QD-VOIDED",              # 来源作废（变更流事件，不发送）
    "QD-DICT-UNMAPPED",       # 字典未映射（bill_type/企业ID）
    "QD-COUNT-MISMATCH",      # expected_code_count 不符（仅在来源提供该依据时）
    "QD-CODE-LIMIT-GUARD",    # 接近/超出 profile 码数保护值（阻断不截断）
    "QD-QUANTITY-INVALID",    # 数量非法（非定点数/负数/拆零无依据）
    "QD-REVISION-CONFLICT",   # 同版本内容冲突
    "QD-PARENT-CHILD-UNPROVEN",  # 父子包装关系未证实
    "QD-PARENT-CHILD-SAME-BILL",  # 父子包装码同单（重复计数风险，阻断；FIX2-07）
    "QD-CROSS-PHARMACY-KEY",  # 复合键不完整/跨药房串单风险
    "QD-BRANCH-UNAPPROVED",   # 业务分支未获批准规则（FIN-04：等待，不静默放行）
}

# ---------------------------------------------------------------- 平台侧枚举
# 查询状态映射：FIX2-01/R2-03 初版 + FB-02 官方当期口径补全（2026-10-21 取证
# 《医疗机构接口对接指南-2026.01》——docs/development/drug-trace-firstbill-exec-
# 20261021/reference/official-guide-202601.md「医疗机构接口权限包能力介绍」节）：
#   result_type：0=处理中 1=上传成功 3=处理成功 4=处理失败
#   sub_process_flag：51=全部成功 52=部分成功（部分成功=rt3+spf52）
# 0→PROCESSING、4→REJECTED 按该官方枚举登记；仍未登记组合一律 MANUAL_REVIEW
# （不猜）。修订须附官方当期文档/批准口径证据（EXT-08/09）。
QUERY_STATUS_MAP: dict[tuple[str, str], str] = {
    ("0", "*"): "PROCESSING",       # 官方：处理中（FB-02 补全）
    ("1", "*"): "PROCESSING",       # 官方：上传成功（受理未终态，继续查询）
    ("3", "51"): "CONFIRMED",       # 处理完成+全部成功
    ("3", "52"): "PARTIAL",         # 处理完成+部分成功（明细保存，绝不 CONFIRMED）
    ("4", "*"): "REJECTED",         # 官方：处理失败（终态；FB-02 补全）
}


def lookup_query_status(result_type: str, sub_process_flag: str) -> str:
    """(result_type, sub_process_flag) → 业务状态；未知组合 → MANUAL_REVIEW。"""
    key = (str(result_type or ""), str(sub_process_flag or ""))
    if key in QUERY_STATUS_MAP:
        return QUERY_STATUS_MAP[key]
    return QUERY_STATUS_MAP.get((key[0], "*"), "MANUAL_REVIEW")


# 零售 bill_type 示例 321 与说明 323 有差异：未批准前不预置默认值（EXT-08）。
BILL_TYPE_UNAPPROVED_SENTINEL = None

# SD-02/SD-03（r11 新分支）的 bill_type/query_bill_type 字典查找键：r10 分支沿用
# source_bill_kind_raw 原串（既有批准字典项不动）；r11 分支的 raw 含单号等唯一值
# （REQ_NO=/DIS_REC=/PRESC_NO=…），不能作字典键——改用稳定的 business_kind 作键。
# 字典项（DRAFT，待平台确认后人工批准）：code=INPATIENT_DISPENSE/PATIENT_RETURN。
BILL_TYPE_KEY_OVERRIDES = {
    "INPATIENT_DISPENSE": "INPATIENT_DISPENSE",
    "PATIENT_RETURN": "PATIENT_RETURN",
}


def bill_type_source_key(business_kind: str, header: dict) -> str:
    """bill_type/query_bill_type 字典查找键（SD-02/SD-03 契约速查）。"""
    override = BILL_TYPE_KEY_OVERRIDES.get(str(business_kind or ""))
    if override:
        return override
    raw = str((header or {}).get("source_bill_kind_raw") or "")
    return raw or str(business_kind or "")

# ---------------------------------------------------------------- FIX3-01 契约
# 企业身份双字典（官方 2026-10-17 页复核，R3-01）：
# - ref_ent_id：上传 ref_user_id 与查询 ref_ent_id 共用的医院企业引用标识；
# - ent_id：库存单收发企业（from_user_id/to_user_id）所需的企业标识——医院一侧
#   与供应商一侧各用 ent_id 口径，绝不与 ref_ent_id 混用（两字典值必须可不同）。
DICT_NS_REF_ENT_ID = "drugtrace.ref_ent_id"
DICT_NS_ENT_ID = "drugtrace.ent_id"
DICT_NS_SUPPLIER_ENT_ID = "drugtrace.supplier_ent_id"
DICT_NS_QUERY_BILL_TYPE = "drugtrace.query_bill_type"

# query.billstatus 的 bill_type 是独立查询选择器（官方文档值 A/AI/AO），与上传
# bill_type（321/323 等数字枚举）不是同一字典（R3-01/G3-02）。映射值必须落在
# 已文档化选择器内；平台若确认支持其他值，须附版本证据后修订本常量（EXT-09）。
QUERY_BILL_TYPE_SELECTORS = ("A", "AI", "AO")

# 未发送提交的配置换代终态（FIX3-04）：旧 PREPARED 提交被新配置代次取代后不再
# 可发送（perform_send 仅 PREPARED 可发），payload 原文永不原地修改。
SUBMISSION_STATE_SUPERSEDED = "SUPERSEDED"
