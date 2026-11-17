# DTR-FIRSTBILL-LONGTASK-20261021

工具/模型：Codex / unknown；session：20261021T153643Z-ff4188a5。
用户要求：提供详细长任务提示词，交其他AI完成剩余工作。

交付：[NEXT_AI_PROMPT.md](../../development/drug-trace-push-review-20261021/NEXT_AI_PROMPT.md)。
包含8阶段FB-00—07、29项FB-A验收、外部输入回执和六类交付物；基于23:29现场证据编制。
要求实际开发/测试/具备授权条件的现场落地，既有凭据不重复索取；来源/签名/时钟证书/
真实配置及证据/应用查询/首张scope依次闭合，max_bills=1，不越权HIS DDL或编造审批。
外部缺项仅阻断依赖步骤，其余工作持续完成；首单必须查询终态、声明对账边界并停发。

改动：新增提示词；现有复核README增加链接；STATE/HANDOFF/BACKLOG接入任务。
修改前备份位于 `.ai/private/dtr-firstbill-plan-baseline-20261021/`，仅本机维护基线。
没有读取服务器托管凭据、没有连接服务器/HIS/平台，没有业务代码/运行配置修改，真实上传0。

验证：仅文档路径/链接及任务覆盖检查，结果见任务目录
`evidence/longtask-doc-validation.json`；未运行业务测试，不声称本任务完成真实上报。
当前部署及业务阻断继续以上一任务DTR-PUSH-REVIEW-20261021的现场证据为准。

下一AI：以新session登记实际实现范围，按提示词执行；满足外部事实后才进行真实首张发送，
缺项明确交责任方，不停在泛化建议，也不把长任务计划视作验收批准。
