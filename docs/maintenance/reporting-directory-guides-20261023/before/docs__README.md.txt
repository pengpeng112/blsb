# 文档分类目录

当前入口： [项目README](../README.md) → [AI状态/交接](ai/START_HERE.md) 或 [启动](startup/README.md)。
机器目录见 [catalog.json](catalog.json)，使用 `python scripts/project_docs.py inventory` 更新。

| 分类 | 当前文件/目录 | 使用规则 |
|---|---|---|
| 使用与启动 | 根README、project.ps1、[启动说明](startup/README.md) | 用户/工具通用入口，命令保持工作目录明确 |
| 数据库查询 | [sjzc 统一入口](startup/DATABASE_ACCESS.md)、[最新HIS活库核查及上传内容](development/his-live-20261019/README.md)、[平台资料申请](development/his-live-20261019/PLATFORM_ACCESS.md) | <relay-jump>受控连接器；平台元数据/活库分开，禁止读取凭据内容 |
| 最新安装执行 | [2026-10-21两台安装与开启执行单](deployment/drug-trace-install-20261021/README.md)、[下一AI提示词](deployment/drug-trace-install-20261021/NEXT_AI_DEPLOY.md) | 材料路径/哈希已核，先补部署前置再新包安装，L1/L2/L3分别验收；本轮未部署 |
| 最新网络复测 | [2026-10-21网络调整与推送判断](development/drug-trace-network-20261021/README.md) | DMZ公网HTTPS及真实只读查询已通过；转发未部署、院内TLS失败，真实上传0 |
| 药品收尾执行 | [2026-10-20剩余开发/发行/禁发部署任务包](development/drug-trace-finalization-20261020/README.md)、[下一AI提示词](development/drug-trace-finalization-20261020/NEXT_AI_PROMPT.md) | 12工作包、38项验收；仅计划，待下一AI执行；未齐外部资料不得假定只剩网络 |
| 真实平台联调 | [2026-10-20实测与首张上传接续计划](development/platform-live-test-20261020/README.md) | 两项只读SUCCESS；DMZ链路阻断、真实上传0，勿将查询等同上报 |
| 平台接入资料 | [2026-10-20凭证登记及本机使用方式](development/platform-credentials-20261020/README.md) | 用户资料已收；Secret仅本机受限加密保存，权限和有效性待验证 |
| 跨AI协作 | [ai/](ai/START_HERE.md)、AGENTS/CLAUDE/AI_START | 规则一份；工具入口只转发；STATE与BACKLOG作为当前摘要 |
| 当前设计 | [总体v0.4](../医院数据归集与上报平台总体方案_v0.4.md)、[设计交付v0.4](../设计交付v0.4/00_阅读入口与开发约束.md) | 设计效力优先，原始契约/提取位置保留 |
| 当前任务 | [剩余任务](ai/BACKLOG.md)、[第4轮详细任务书](../一次性执行任务-本地剩余与内网部署-2026-10-01.md) | 任务书是要求，完成须有实现与验收证据 |
| 药品追溯开发 | [最新第三次复核与FIX3任务](development/drug-trace-20261016/review-3-20261017/README.md)、[上一阶段开发包](development/drug-trace-20261016/README.md) | FIX2已有实质修复，仍有7组缺陷/14反例；局部33通过和历史全量305/6不代表真实可上报 |
| 实施状态 | [implementation/docs入口](../implementation/docs/README.md) | 四类持续文档+API矩阵；详细行为和证据，不单凭状态标题 |
| 实施决策 | [ADR索引](../implementation/docs/adr/README.md) | 记录实现选择，不覆盖正式外部业务规则 |
| 运维部署 | [runbooks索引](../implementation/docs/runbooks/README.md)、deploy目录 | 区分本地演示、单服务、容器、Windows/离线发行与现场未执行 |
| 审查/测试证据 | [原复核及整改台账](../项目独立复核与详细整改计划_2026-10-01.md)、[审查证据](../implementation/docs/audit/2026-10-01/README.md)、var中的log/XML | 每份报告按日期/轮次解释；var不作为AI默认输入 |
| 正式/草案/报告原件 | [sources索引](sources/README.md) | 正式规范、草案、解读、第二方观察效力分开，原件不改 |
| 历史归档 | [归档索引](../归档/README.md)、[在位v0.3说明](../设计交付v0.3/README.md) | 只追溯；旧路径保留跳转，不能当当前任务 |
| 运行数据/生成产物 | [文件地图](CODE_MAP.md)中的var/build/dist/snapshots | 不归档到文档，不删除、不自动分享；无业务源码分类混用 |

目录整理采用“有效文档索引＋明确历史归档”：有来源哈希/大量引用的原件在原路径保留，旧提示词和旧测试计划保存原字节后归档。
归档文件保留原有相对链接语义，阅读时按归档清单中的original_path定位，不改写历史原文。
