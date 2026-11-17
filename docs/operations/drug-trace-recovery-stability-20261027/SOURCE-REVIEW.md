# 10-26缺码及分支核查准备

本轮未执行HIS活库查询、未改视图。14:05应用最终采集快照3337张：1995 VALID，1342 WAITING。其中正常门诊260张无码，原PRESC_SOURCE=1/FLAG=0分支1079张、原0/2分支3张；后三个阻断规则落在同一1082张上，不能重复相加。全部3337张expected_code_count为空；1995张可上报并不能证明源端应有码均被采到。

## 当前受阻条件

14:04经sjzc的<relay-jump>**平台元数据**只读通道复查：`his_source_<his_host_id>`为<his-host>:1521、service_name=his、enabled=true，write_policy仍`medical_dict_push`，没有同地址另一readonly源。该登记不等于实际账号有写权，也不否定现生产BLYB_DRUGTRACE专用账号的已验收只读权限；但不足以满足本次通用sjzc活库核查门禁。

[sjzc技能](C:/Users/Administrator/.zcode/skills/sjzc/SKILL.md)要求先核对访问门槛；[HIS只读技能](F:/python/数据资产/.agents/skills/hisuser-readonly-sql/SKILL.md)明确：**“连接账号不是已登记只读源或平台write_policy不是readonly”**时停止依赖该条件的执行。源策略、时段批准未齐备，不转用聊天密码或应用高权限凭据绕过。平台本轮该追溯字段confirmed值域检索亦为空，不能据此猜新业务含义。

元数据证据见本目录`evidence/source-metadata-20261027T060435Z.json`；首轮06:01证据另存。两次均business_source_calls=0，没有读取凭据列/凭据文件内容。若文件名随实际时间有差异，以evidence目录中的同前缀文件为准。

## 只读执行包

三个SQL都是一条Oracle 11g SELECT/只读WITH，未带患者/原码/原单号输出。只由受控执行器绑定参数，参数保留在批准的受限位置，不写公开日志。

1. [主单三键与原始状态](sql/01-waiting-master.sql)：精确PRESC_DATE（到秒）+NUMBER型PRESC_NO+药房字符串；先确认唯一主单及10-26发药日。
2. [原扫码三键核查](sql/02-waiting-trace-exact.sql)：TRACE_BILLNO（字符串）+完整PRESC_DATE+STORE_CODE，保留方向/删除/拆零原值，统计T/F/S非空数及采集后创建/修改数。非空不自动等于有效追溯码。
3. [附近候选关联诊断](sql/03-waiting-trace-nearby.sql)：同TRACE_BILLNO、最多48小时时间窗、最多1001行；只用于发现日期/药房不匹配。MAY_BE_TRUNCATED=1时不得断言不存在或已完整统计。未经新来源/业务审批不改生产JOIN。

来源表/列已在14:04平台当前元数据中找到；索引依据为10-19活库既有证据：MED_TRACE_RECORD.TRACE_BILLNO和DRUG_PRESC_MASTER.PRESC_DATE/PRESC_NO/DISPENSING_DATETIME。它们是历史存在性证据，**不是本次实际执行计划/性能验收**。执行前DBA复核索引有效性与缓存计划；不使用EXPLAIN PLAN写PLAN_TABLE，不自动建索引。

首轮最多30张：正常门诊无码按药房与采集先后分层取20张、原1/0取最多7张、原0/2最多3张。仅选择10-26本批WAITING的当前revision，先标样本编号，原三键/采集时点只在受控进程中传递。每单先SQL01/02，确有必要才SQL03；单次15秒超时、累计最多5分钟、最大50结果行，超过即取消并记录未完成，不扩大扫描。账号仅有两视图授权时不申请扩大权限来凑结果，应由已有合规源或DBA执行原表核查。

## 怎样判定和完善

| 取证结果 | 处置 |
| --- | --- |
| 采集后新增/修改码，当前三键有记录 | 迟到数据候选；进一步核实际CREATE/MODIFY语义和正常分支。设计可靠变更来源/有限回看、新revision，不改原冻结payload |
| 三键无记录，附近同号有不同药房/日期 | 关联不一致候选；逐个核源业务、是否处方号复用或其他来源，不能直接LEFT JOIN放行 |
| 三键有记录但仅空T码/删除原值/其他方向 | 依据已批准字段口径核扫码/包装层级，不把F/S替代T或猜删除标志 |
| 正常门诊原始三键也无扫码候选 | 限定样本内无记录；再与药学部扫码业务核对，不能上升为全天未扫码结论 |
| 原1/0、0/2分支 | 分别确认真正业务完成事件/原单关系/方向/平台方法与类型，再独立source/dictionary revision；不能一律套321 |

旧单后补码不会自然进入以发药时间为日窗的次日日批。r10主视图updated_at=发药时间，当前没有可靠变更序列；需确认源端变更表或应用事件，才实施补码/退款增量。无独立应有码数时保持expected_code_count未知，不把现扫码行数自我填作应报数。

本包只准备核查和后续修订依据，不改变生产范围，不授权1342张无码上传。既往拆零5077/双扫31组已有10-23根因报告；不把那批历史结论直接套到本日260张新缺码。
