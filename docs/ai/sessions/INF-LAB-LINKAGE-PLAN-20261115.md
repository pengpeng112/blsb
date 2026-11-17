# INF-LAB-LINKAGE-PLAN-20261115 · Excel关联与父子传输复核/修订计划

- 工具/真实模型：Codex / unknown；会话 `20261115T101028Z-dcb8c395`，2026-11-15。
- 用户目标：按关联关系Excel复核工程师的两个问题，结合指定病例形成详细修订计划。
- 范围：`docs/development/inf-lab-linkage-review-20261115/`、本交接、共享STATE/HANDOFF/BACKLOG。业务目录仍有INF-FRONTEND活跃范围，本轮只读，不覆盖其文件。
- 基线：共享文档修改前副本在 `.ai/baselines/INF-LAB-LINKAGE-PLAN-20261115/`。生产三文件摘要与本地一致，原Excel哈希已固定在计划。

## 结果

详细交付：[检验主子表关联与先后传输修订计划v1](../../development/inf-lab-linkage-review-20261115/README.md)。

1. Excel明确检验活动类型、流水应与诊疗活动一致；本例急诊2被标门诊1不符合。exLabId=TEST_NO=主表id的外键设计正确，实际明细完整性不合格。
2. ODS本轮实测：三单源结果及可上报JOIN候选分别4/7/27=38、子项ID各自唯一；主视图各1、子视图各0、患者+流水+活动类型完整锚点命中各0。
3. <prod-host>真实回执与完整已发账复核：三主真实受理，UTC 07:01:10=北京时间15:01:10；本次扫描保留的11-14/11-15回执和完整sent账未见对应子项。不能把本例说成已经证明子抢先传；我方证据指向子项未发。
4. 正常双表主先子后，但缺记录级父受理门禁。当前部署代码与本地摘要一致；7个离线场景实跑，父04/取数异常/仅跑子表/limit父子未配套四种路径均仍调用子发送。这是风险复现，不是修复验收。
5. 视图纠正不改变源OPERATION_TIME，旧父仍会被幂等跳过；计划明确需受限manifest定向更正3父后补38子，不能删sent/改源时间绕过。

## 真实命令、证据与边界

工作目录均为 `F:/python/blyb`。

- 标准库zipfile/XML只读解析原xlsx：194个非空单元格，读取合并D8:D9，证据 `association-requirements.json`。原件未修改。
- `python docs/development/inf-lab-linkage-review-20261115/check_case_ods.py`：退出0、ok=true、3行聚合、2.94秒；执行前静态SELECT门禁通过，ods_internal登记enabled/readonly。实际SQL机械脱敏模板与摘要在 `ods-case-summary.json`。
- `python docs/development/inf-lab-linkage-review-20261115/check_production_receipts.py`：退出0、远端退出0、34.35秒；三文件完整扫描，前后字节数相同；证据 `production-receipts-summary.json`。既有凭据函数仅内存取用，先核固定指纹，不输出凭据；仅文件读取/哈希/AST，无部署/HTTP。脚本后续仅加“输入文档必须唯一”的保护，未重复扫描远端；实际读取逻辑不变。
- `python docs/development/inf-lab-linkage-review-20261115/check_parent_child_order.py`：退出0，7个合成场景；数据库/HTTP均替换，urlopen强制拒绝，临时工作目录自动回收；证据 `synthetic-order-findings.json`。
- 收尾执行 PowerShell here-string → `python -`，退出0：3个证据脚本AST语法通过；ODS/回执/候选集合断言通过；Excel原件及当前本地生产代码哈希未变；计划/交接8个本地链接存在；STATE/HANDOFF原有正文完整保留。未重复远端扫描，不运行与计划编制无关的全量回归。

无源码、SQL、真实业务库、生产配置、患者基础数据、诊疗活动推送方修改；无真实发送、无安装依赖。未查询真实HIS/LIS源库或国家平台，未证明其他推送方所有历史明细缺失。

## 待实施与下一步

计划P0—P6及A01—A12待实施/验收：冻结范围与备份→视图新revision→记录级父受理门禁→受限重放工具→离线验收→授权部署与逐家庭补传→平台关联与待确诊业务闭环。

业务README及四类档案当前未编辑；本轮独立计划由共享入口指向，后续实施在协调范围后同步业务档案。共享STATE/HANDOFF追加证据入口，BACKLOG原INF-CASE-REPAIR条目补入本计划，不另建平行业务状态。

本轮结果：复核与详细计划完成，生产修复未执行；平台待确诊触发原因仍待其业务消息与规则证据。
