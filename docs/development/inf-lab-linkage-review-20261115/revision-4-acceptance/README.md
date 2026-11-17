# 修订完成后验收复核：视图通过，程序未通过

2026-11-15 22时，INF-LAB-ACCEPTANCE-REVIEW-20261115；Codex / unknown。

**不能认定整体可以验收。**本轮确认三张视图已恢复VALID，本例主3/子38、完整活动键均正常；发送器本地和线上仍为第二版审计时的同一SHA256，关键程序缺陷尚未修复。对方交接“R2-A01～A12全部通过”与源码和实测不一致，不能作为放量或完成依据。

## 1. 视图部分已确认

通过sjzc→<relay-jump>受控连接器→已核enabled/readonly的ods_internal，只读查询对象状态、编译错误和三张受限检验单的聚合：

| 验证 | 本轮实测 |
|---|---|
| M_INF_FXHIS_ACTIVITY_V15 | VALID |
| V_INF_EX_LAB | VALID |
| V_INF_EX_LAB_ITEM | VALID |
| ALL_ERRORS | 0行 |
| 三报告主视图 | 每张1行，共3 |
| 子项 | 4/7/27，共38，与源候选数量一致 |
| 患者+流水+活动类型完整锚点 | 每张命中1 |

证据：[object-check.json](object-check.json)、[ods-case-summary.json](ods-case-summary.json)。后者退出0、ok=true、3行、2.57秒，投影不含患者身份或原始结果。

本轮没有重新读取前置机库，不能把本地锚点命中直接写成国家平台业务确认。本例38条真实受理及ID集合齐套有上轮证据；本轮未重传。

## 2. 程序不是已修订版本

本轮只读容器pusher/tables/run.sh代码摘要，退出0、远端0、0.81秒。三者与本地冻结代码、上轮问题版本完全相同；其中pusher摘要仍为：

`20738564401e01afcf36c1429bcfe3dbe26311016a982bce8d7f7a9a80c09e99`

证据：[runtime-code.json](runtime-code.json)、[baseline-manifest.json](baseline-manifest.json)。本轮未运行生产pusher，没有真实HTTP。测试使用本次冻结代码，数据/HTTP均拦截，工作目录为自动清理的临时目录。

先运行 `python docs/development/inf-lab-linkage-review-20261115/revision-4-acceptance/collect_behavior.py` 收集实际行为，退出0；再运行 `python docs/development/inf-lab-linkage-review-20261115/revision-4-acceptance/evaluate_acceptance.py`，按安全要求判定，**退出1：2通过、10未通过**。这是12个独立行为场景，不是原R2-A编号的逐项替代计数。

| 场景 | 要求 | 实际 | 结果 |
|---|---|---|---|
| 正常主子同批 | 发送1条子项 | 1 | 通过 |
| 无旧成功的父04失败 | 子HTTP=0 | 0 | 通过 |
| 同版本历史父跨日复用 | 新子项发送1 | 0，被卡 | 未通过 |
| 当日旧父成功、本次新父04失败 | 子HTTP=0 | 1 | 未通过 |
| 其他表同ID、本检验父失败 | 子HTTP=0 | 1 | 未通过 |
| reconcile历史父成功、本次新父失败 | 子HTTP=0 | 1 | 未通过 |
| 清单过期/错目标/摘要错 | HTTP=0 | 仍发1个父请求 | 未通过 |
| 同清单应用两次 | 成功项只发1次 | 发2次 | 未通过 |
| 清单目标不存在 | 明确失败或missing | 退出0、静默发送0 | 未通过 |
| reconcile+manifest无apply | HTTP=0 | 父子共2次 | 未通过 |
| 等待项越overlap、重启、父恢复 | 旧等待项补回 | 未补回 | 未通过 |
| 医嘱历史父与新条目 | 新条目发送1 | 0，被卡 | 未通过 |

完整观察：[implementation-probes.json](implementation-probes.json)；安全判定：[acceptance-results.json](acceptance-results.json)。收集脚本不再预设缺陷必须出现，判定脚本按要求比较实际结果；不能把“观察收集退出0”称为“安全验收通过”。

## 3. 对原验收表的纠正

原交接末尾“二十七（续）R2-A01~A12逐项核实验收表”列出的部分证据不对应要求：

- A05用parent_wait=24证明门禁存在，没有测“旧父成功+当前父失败”；本轮此场景仍发子。
- A06用V15 VALID证明“父跨日复用”，两者不能替代；本轮跨日场景被卡。
- A08“等待不推水位、设计保证”忽略后面另一条成功推进整表最大水位；超overlap恢复实测失败。
- A10称reconcile无旁路，但源码在manifest校验前执行reconcile；本轮无apply仍调用HTTP。
- A11称force限定ID即可二跑零重复，实际同force清单二跑再次调用HTTP。
- A12原68项自测是旧基线，不包含上述关键专项；不据其通过勾销未通过项。

本轮不重跑同SHA的旧68项自测来替代专项；此前68/0日志仍为历史基线。也不把六张缺真实操作人员但正确进入质量等待的记录，写成“真实人员已补齐”。

## 4. 下一步具体范围

已有视图修复先保留；**下一任务必须真正修改程序，不能再只更新验收表或改视图代替。**执行入口仍为[第二版计划R2—R6](../revision-2/README.md)：

1. **R2父版本证据**：改pusher.py的accepted_ids/parent_ids_from_sent、run_table和main/reconcile。去掉裸ID跨表共用，绑定父表/来源/目标/父ID/内容版本；同版本跨日复用，旧版本成功不能覆盖当前失败。不能只把空字典改None放宽历史账。
2. **R3持久等待**：独立队列与恢复游标，待办不受overlap裁剪；等待未持久化不得被水位越过；对账统计必须保留parent_wait/missing/unknown并告警。
3. **R4严格重放**：所有参数组合先预检，reconcile+manifest若不支持则明确拒绝；无apply永不发送。校验目标、来源、revision、逐项源版本/摘要、失效和数量；持久执行进度，二跑只续未完成；目标缺失明确报错。
4. **R5专项验收**：保留旧测试，新增能在本轮冻结缺陷代码上失败的行为测试。按原R2-A01～A12逐项给出对应命令、退出码和证据；本轮10个失败场景修后都应通过。涉及order_item的公共行为须兼容验证。
5. **R6受控上线与平台确认**：专项通过后再核对部署摘要、视图状态、父子字段及实际回执。38条已补齐，不作漏项整批重放；只对平台实际错误字段建立新清单。真实部署/发送不属于本轮。

当前不建议据“全部通过”直接开始放量/继续force补传；本轮未改变现有生产开关或调度。

## 5. 维护与验证记录

本轮仅只读ODS及容器代码，未连接真实HIS/LIS/医保源库，未执行DDL/部署/上传，无安装依赖。共享状态、待办、交接及业务导航/01过程记录同步本轮新证据，历史报告保留。

[本轮交接](../../../ai/sessions/INF-LAB-ACCEPTANCE-REVIEW-20261115.md)。原文件备份位于`.ai/baselines/INF-LAB-ACCEPTANCE-REVIEW-20261115/`；测试代码冻结在本目录baseline/。文档机器目录按project_docs inventory更新，局部引用/摘要检查结果保存在verification-summary.json。
