# 候选修订方案（rootcause-r2，2026-10-23）

逐条"证据 → 修订方向 → 回归/回退"。**全部为候选：本轮未执行任何 DDL/DML、生产视图替换、
配置发布或发送**；执行前需按各条所列授权与证据落实。不需要修改的项目也给出保留依据。

## C01 正常门诊 r10 分支 —— 保留，不修改

- 证据：本轮 r2 复核身份/授权/两视图定义与 r10 草案 token 完全一致
  （`evidence/r2-analysis-20261023T104928Z.json` → q00_view_definition_check）；
  484536 行历史键匹配且次数一致（r1）；1396 条 UNKNOWN 原始状态两个时点零迁移。
- 修订：无。
- 回归/回退：不适用。**结论：已核实范围内可继续使用；不构成全历史/全业务验收。**

## C02 5077 条未匹配 —— 根因已闭环（r2b 源表证据）：拆零扫码流，r10 不改、不并入

- **r2b 实测证据**（用户授权后经 READY_HIS 只读执行，见
  [classification-final.json](evidence/classification-final.json)）：
  - 5077 条 (码, 旧明细号) 记录全部存在于 MED_TRACE_RECORD，全部 SPLIT_FLAG=1、STORE_CODE=050121、
    TRACE_BILLNO 20 位数字、OUT、未删除（U02×5077，守恒）。
  - 主单表窗口内 PRESC_NO 仅 1—5 位（E1），20 位号不存在；050121 是主单表最大药房（E2）；
    S01—S03 按（号+日期）不带药房查主单 0 行（E3）。
- **判定**：三键 JOIN 按设计不可命中该拆零流；**不是正常门诊漏报，r10 无需修改**。
- 修订方向（未执行，待院方确认）：若药学/HIS 确认该拆零流需上报，**另立来源 profile/视图 revision**
  （以该流的业务主单/单号语义取数，正常 r10 保留、编号/药房独立固定、未知仍 UNKNOWN）；
  若确认不需覆盖，在产品/交接中明示该流不在范围。**不 INNER 改 LEFT、不把无主单码按 321 发送。**
- 回归：涉及代码时 `tests/integration/test_his_source_profile.py`、`test_drug_trace_domain.py`；
  视图 revision 由 DBA 执行后按本轮 r2 相同复核脚本重验。
- 回退：一切为新增 revision，r10 可切回。

## C02a 历史脚注（r2 阶段的中间结论，已被 r2b 闭环取代）

r2 阶段曾按任务书将 5077 条记为 U05（无源表权限时点），并给出"2330 行同码同日异标识"提示。
r2b 源表核查后该人群全部归位 U02，根因见上。旧证据保留于
[evidence/classification-summary.json](evidence/classification-summary.json)（中间态）。

## C03 1396 条 UNKNOWN / 退款拦截 —— 保留，不修改

- 证据：r2 复核 46/23/1327 三态与 r1 完全一致，411 主单，finalized/is_void 均空；
  两个时点零状态迁移。
- 修订：无。若院方要求报消费者退药，另立业务分支（来源、方向、原单关联、批准枚举），
  116/321 以官方当期文档及院方批准为准；不因旧 OUT 后来退费改报。
- 回归：`tests/integration/test_drug_trace_domain.py` 退款拦截用例保持。
- 回退：不适用。

## C04 31 组重复 → 11 张整单全 INVALID —— 成因已证（r2b）：1 小时内真实双扫

- **r2b 实测证据**：每组恰好 2 条源记录，均 OUT/未删/**SPLIT_FLAG=0**/三键 JOIN 命中；
  两条创建时间间隔全部 <1 小时（31/31 落在 lt1h 桶）；整单 11 张全部 INVALID/QD-DUP-CODE。
  同 31 码在其他药房（050106/050105）各有 1 条正常记录，属独立事件，不构成第三条重复。
- 判定：**真实双扫（重复扫码），非拆零、非视图重复、非 Excel 重复导出**。
- 修订方向（未执行）：由药学/HIS 确认双扫语义（错误重扫？补扫？是否应更正源数据或提供豁免规则）；
  规则批准前 11 张整单保持 INVALID；如需放行，须批准可审计转换（保留两条源记录、数量口径、转换依据）。
- 禁止项：DISTINCT/MAX 去重、随意 quantity=1、放松重复码/父子包装保护。
- 回归：`quality-real-samples.json` 的 12 行样本反例保留；新增豁免规则时先证实现缺再落测试。
- 回退：不适用（无改动）。

## C05 同 CREATE_TIME 反向全量 —— 未完成，按 SQL05/06 补证后再定

- 证据：两视图无 CREATE_TIME 列；MED_TRACE_RECORD 无 CREATE_TIME 索引（2026-10-19 快照）；
  旧 SQL 上界含 2026-10-12 00:00:00 而 Excel 数据止于 10-11 23:52。
- 修订方向：DBA 先跑 SQL06 确认当前索引；有/无 CREATE_TIME 索引分别决定：
  - 有 → 按日+药房分段执行同扫码窗口反向核对（新 SQL，届时编制）；
  - 无 → 仅按 SQL05（PRESC_DATE 主键索引支撑）做逐日 JOIN 覆盖统计，
    量化"源有视图无"规模；CREATE_TIME 口径反向审计明示"未完成"。
- 回归：统计型 SQL，无代码回归；结果入受限目录后与本分类汇总对账。
- 回退：不适用。

## C06 A01—A05 当前代码/测试状态（本轮逐项检查，区分"当时缺失"与"现在已修复"）

| 项 | 当前状态（2026-10-23 工作区实测） | 依据 |
|---|---|---|
| A01 迟到码/齐套/断点 | **batch 侧已实现**：页级原子采集+checkpoint 续跑、可配置重叠回看（lookback_seconds 默认600）、版本变化重读+持久重取账本（FIX3-02/3）；**continuous 模式 fail-closed**：无确认 change_source 即 SOURCE_PROFILE_UNVERIFIED（business_rules.py FIN-04 门禁保留） | `ingest.py:370-470`、`business_rules.py:195-230`、models.py DrugTraceRetryBill |
| A02 独立完整性 | 管道齐全（视图列→ingest→QD-COUNT-MISMATCH WAITING），**但 r10 视图 expected_code_count 恒 NULL**（本轮再证定义未变）→ 仍无独立应报码数来源 | `quality.py:175-186`、r2 视图定义 |
| A03 整单解析策略 | **未实现**：全仓无 ignore_part_success_flag（grep 0 命中）；仍为缺省部分解析；3/52 失败码流程未落 | grep 全 plugins 0 命中 |
| A04 包装父子/扩展业务 | 父子同单阻断已实现（QD-PARENT-CHILD-SAME-BILL）；code_level/parent_code 仍恒空 → 层级未确认保持告警+限制；数量格式护栏在 | `quality.py:141-157`、视图定义 |
| A05 两层状态展示 | **部分**：对账区有"码级待 EXT-09"列与明细掩码表；提交明细（SubmissionItem PENDING）与整单 CONFIRMED 的两层区分展示未完 | `DrugTrace.vue:101-166` |

结论：A02/A03/A05 与 2026-10-22 审计结论一致仍未实现；A01 batch 机制已具备、continuous 仍正确阻断；
A04 保护在、层级来源仍缺。这些不是本轮核查新增缺陷，不因 98.9631% 匹配而关闭。

## C07 历史防重与发送边界 —— 保留

- 证据：历史 Excel 无平台成功终态；既有 1 单 4 码 3/51 CONFIRMED。
- 修订：无。旧明细号↔整单 source_bill_id↔冻结 bill_code 三层映射保留；
  新单号查无回执不构成重发依据；平台只读终态查询需另授权（本轮 0 次平台调用）。
- 回归/回退：不适用。
