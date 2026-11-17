# 药品追溯对接开发交付入口

日期：2026-10-16；任务：REPORTING-DRUGTRACE-PLAN-20261016；状态：**计划已形成，业务开发尚未执行**。

结论：继续使用已部署的 Python / Vue / PostgreSQL 平台，在现有插件、任务、配置发布、提交与对账框架中补齐药品追溯业务。当前基础服务部署成立，尚不能采集 HIS 或向码上放心正式上报。改语言不是本轮前提。

下一位 AI 按以下顺序读取：

1. [现场复核与剩余问题](DEPLOYMENT_REVIEW.md)：哪些已完成，哪些只是历史测试，哪些仍受阻。
2. [详细开发计划](DEVELOPMENT_PLAN.md)：范围、架构、文件位置、数据契约、阶段任务、上线与回退。
3. [验收矩阵](ACCEPTANCE.md)：每项必须留下证据，不能以构建成功替代业务验收。
4. [外部资料清单](EXTERNAL_INPUTS.md)：HIS、药学、网络、平台、运维分别需要提供什么。
5. [下一 AI 执行提示词](NEXT_AI_PROMPT.md)：可直接复制给执行工具。
6. [用户历史SQL复核](SQL_REVIEW.md)：新增SQL存在丢码/单据粒度等问题，已纳入工作包和验收。

本包承接此前部署计划的 P4 余项与 P5，不覆盖旧部署报告和原始接口文件。旧 [execution-plan](../../deployment/reporting-20261015/execution-plan.md) 与 [view-contract revision 4](../../deployment/reporting-20261015/view-contract.md) 是历史依据；本包明确的新增要求用于下一阶段，冲突时登记差异后实施。

本次仅做本地文档、源码审查和已授权只读现场复核：没有改服务器、没有连接 HIS 活库、没有发送业务数据。凭据继续在本机受限私有目录，不复制进本包。

> **状态附记（2026-10-16 晚，DTR-IMPLEMENT-20261016）**：本包所列 W01—W09 已离线实施完成
> （全量回归 277/6/0；浏览器闭环与证据见 evidence/browser/；交接见 [docs/ai/sessions/DTR-IMPLEMENT-20261016.md](../../../docs/ai/sessions/DTR-IMPLEMENT-20261016.md)）。
> W10/W11 现场项与 EXT-01—12 仍待材料/窗口/授权；本文其余描述为规划时点原文，保留不改。
