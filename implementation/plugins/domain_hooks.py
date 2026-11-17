"""领域钩子（LT-01）：核心只通过钩子拿业务数据，不 import 任何具体领域模块。

ADR-01 落地动作之一：core_api 的 overview / 上报业务摘要不再硬编码病理/药品
查询，改经 PluginRegistry.capability_list 聚合各插件登记的钩子。钩子约定：

- 全部**只读**：钩子内不得写库、入队、发外部请求（测试与评审红线）。
- HOOK_OVERVIEW：fn(db, tenant_id) -> dict，多插件结果按键合并（后注册覆盖
  同名键——各插件须用自己的前缀键，如 cases/ / drug_trace_*）。
- HOOK_TARGET_SUMMARY：fn(db, tenant_id, target_code, days) -> TargetSummary|None，
  第一个返回非 None 的插件生效（插件自己判断 target_code 是否归属本插件）。
- HOOK_BUILD_RUNTIME：保留病理 runtime 语义的间接层（build_runtime 仍以
  pathology.build_runtime 附件为兜底，双附件不删，旧路径不动）。
"""
from dataclasses import dataclass, field

HOOK_OVERVIEW = "hook.overview_contrib"      # fn(db, tenant_id) -> dict（合并进 /admin/overview）
HOOK_TARGET_SUMMARY = "hook.target_summary"  # fn(db, tenant_id, target_code, days) -> TargetSummary | None
HOOK_BUILD_RUNTIME = "hook.build_runtime"    # 保留病理 runtime 语义


@dataclass
class TargetSummary:
    """单上报业务在时间窗内的统一状态桶摘要（上报中心驾驶舱数据形状）。

    LT-03-R1（R02）口径分离——三组数字不得混读，也不能相加成"全院实时成功率"：
    - ``buckets``/``daily``：**窗口内**计数（window_kind 标注窗口类型；
      rolling_days=滚动 N 天；hospital_day=院内自然日）；
    - ``backlog``（窗口内未解决）与 ``backlog_total``（**全量**未解决积压，
      不受窗口裁剪——窗外 UNKNOWN 不能被窗口隐藏）分开；
    - ``last_success_at``：最近一次**业务确认**（CONFIRMED）时间，全量口径；
    - ``quality_waiting``：尚无 Submission 的质量等待实体数（领域单据态）。
    ``evidence_kind``：real|mock|mixed|""（证据形态，Mock 数据不得当真实成功）。
    """
    target_code: str
    window_days: int
    buckets: dict[str, int]                 # 键必须 ⊆ REPORT_BUCKETS
    daily: list[dict] = field(default_factory=list)  # [{"date":"2026-11-01","CONFIRMED":10,...}]
    last_success_at: str | None = None      # ISO8601 UTC
    backlog: int = 0                        # 窗口内待发送+发送中+未知
    unmapped_states: dict[str, int] = field(default_factory=dict)
    data_source: str = "platform"           # platform | external_ledger | none
    note: str = ""
    window_kind: str = "rolling_days"       # rolling_days | hospital_day
    backlog_total: int | None = None        # 全量未解决积压（None=未统计）
    quality_waiting: int = 0                # 无 Submission 的质量等待实体数
    evidence_kind: str = ""                 # real | mock | mixed | ""


# ---------------------------------------------------------------- 统一台账（LT-03）
HOOK_LEDGER_SEARCH = "hook.ledger_search"
# fn(db, tenant_id, target_code, filters: LedgerFilter, cursor: str|None, limit: int)
#   -> LedgerPage | None；第一个认领该 target_code 的插件生效。
# 硬规则（2026-10-26 "先 limit 200 再筛选漏旧 UNKNOWN" 历史 bug 的根治）：
# 所有过滤条件必须进 SQL WHERE 再 ORDER BY updated_at DESC, id DESC LIMIT n+1；
# 游标 = base64("updated_at|id")；下一页条件
# (updated_at < :ts) OR (updated_at = :ts AND id < :id)。


@dataclass
class LedgerFilter:
    bucket: str | None = None         # REPORT_BUCKETS 之一
    date_from: str | None = None      # 'YYYY-MM-DD'（院内日期，UTC+8）
    date_to: str | None = None
    q: str | None = None              # 业务单号/提交ID 精确或前缀


@dataclass
class LedgerRow:
    id: str
    target_code: str
    business_key: str
    bucket: str
    raw_state: str
    created_at: str
    updated_at: str
    attempts: int
    last_error_code: str | None
    last_error_message: str
    detail_route: str | None = None


@dataclass
class LedgerPage:
    items: list = field(default_factory=list)      # list[LedgerRow]
    next_cursor: str | None = None
    total_estimate: int | None = None


# 台账详情（LT-03-R1/R02）：fn(db, tenant_id, target_code, submission_id)
#   -> dict | None；第一个认领该 target_code 的插件生效。
# 硬规则：详情取数必须按 tenant+target+submission 绑定关系校验归属
# （target 绑定的 destination 或领域 domain），路由层校验 target 存在
# 不能证明 submission 属于该 target——跨 target 读取一律按不存在处理。
HOOK_LEDGER_DETAIL = "hook.ledger_detail"

# 分类统计（RT-04 A3）：fn(db, tenant_id, target_code, days, source_instance)
#   -> dict | None；第一个认领该 target_code 的插件生效。
# 语义：文书类型 × REPORT_BUCKETS 矩阵（含零值行）+ 窗口/来源实例/观测截至
# /导入时间 + 外部与影子 Mock 分栏；只读。
HOOK_TARGET_CLASSIFICATION = "hook.target_classification"
