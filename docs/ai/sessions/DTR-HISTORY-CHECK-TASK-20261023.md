# 历史核查完整执行任务书交接

2026-10-23；工具Codex，模型unknown。任务DTR-HISTORY-CHECK-TASK-20261023，session `20261023T061054Z-b04a2d78`。
用户要求将上一轮结果写成详细说明和核查计划，供其他AI一次接力执行。

## 本次交付

- [完整任务书](../../development/drug-trace-history-live-compare-20261023/EXECUTION_TASK.md)：原明细号/主单/上传单号区别，已核实统计与交叉计数、材料/私有凭据导航、Q00—Q06、原表权限边界、预算/取消/断点、分类守恒、候选修订、A01—A05、CHK-01—10和交付物。
- [可复制提示词](../../development/drug-trace-history-live-compare-20261023/NEXT_AI_PROMPT.md)。
- [新增SQL03](../../development/drug-trace-history-live-compare-20261023/sql/03-dba-code-context.sql)：旧明细号精确键查不到时按原码核验原表上下文，避免把旧键变更与源码当前未见混为一谈。仅绑定3码、ROWNUM101，不含患者列，未执行。
- 原报告/概要计划和共享状态、两处业务README已补导航。修改前副本见本轮before-task-plan目录。

本次沿用sjzc和数据资产只读规则编写核查任务；没有连接HIS或平台，没有读取凭据/私有原码，没有生产操作。目标是执行任务书可落地，不声称下一轮根因核查已完成。

## 校验与结果

实际命令：开工ai_session.py status/start；读取本轮聚合证据及旧核查方案；project_docs.py inventory；三条DBA SQL静态验证；任务涉及文档链接与此前基线比较。
精确结果和退出码见[本次计划校验](../../development/drug-trace-history-live-compare-20261023/task-plan-validation.json)。SQL静态通过只说明满足本地只读/单条/旧Oracle语法禁用项检查，不代表已在活库执行或查询计划已经验证。
无产品代码变更，不运行产品全量测试。旧verify_delivery.py会更新旧validation.json，任务书明确在新任务目录执行等价离线核验，避免接手误改r1证据。旧实时/Excel工具固定r1输出，明确禁止删除证据后原样重跑。

## 下一步

将提示词交给接手AI，按任务书完成独立可执行步骤；原表受限时向DBA交付现成三条SQL和受限样本包，其他分项继续。需要新视图/产品更改时先形成证据和候选包；本核查任务不自动包含DDL、生产部署或真实补报。
既有一单成功保留，5077条不能直接补报，正常门诊r10/退费/重复门禁保留。
