# 医院数据归集与上报平台（BLYB）· 项目总入口

统一医院数据归集、质量校验、审计交换与多业务上报平台，现覆盖 **5 大上报业务**（码上放心药品追溯、国家传染病前置软件、山东医保电子病历 23 交易、医保病理云索引、济南医保影像云 16 数据集），并支持 HTTP JSON / 视图交换 / WebService SOAP 1.1 & 1.2 XML 协议。

> **设计基线**：`设计交付v0.4/` · **当前真实状态**：[STATE.md](docs/ai/STATE.md) · **唯一 AI 规则源**：[AGENTS.md](AGENTS.md)

## 快速导航

| 目的 | 入口 |
|---|---|
| 任何 AI 接手开发 / 切工具 / 查规范 | [AGENTS.md](AGENTS.md) → [AI 接手入口](docs/ai/START_HERE.md) · [通用提示词](AI_START.md) |
| 看当前基线、最新交接与待办 | [STATE](docs/ai/STATE.md) · [HANDOFF](docs/ai/HANDOFF.md) · [BACKLOG](docs/ai/BACKLOG.md) |
| 按上报业务找接口原件、SQL/视图、部署与证据 | [数据上报总索引](数据上报/README.md)（1 码上放心 / 2 山东医保 / 3 病理 / 4 传染病 / 5 济南影像云） |
| 了解历史各阶段升级需求与演进脉络 | [历史升级演进总览](docs/HISTORY.md) · [归档索引](归档/README.md) |
| 本地启动、测试、前端构建与内网部署 | [统一启动说明](docs/startup/README.md) · [数据库 sjzc 入口](docs/startup/DATABASE_ACCESS.md) |
| 查全部文档与源码目录地图 | [文档分类目录](docs/README.md) · [代码与文件地图](docs/CODE_MAP.md) |

## 根目录一键命令（离线复用现有环境，不自动装依赖）

```powershell
.\project.ps1 -Action Dev       # 启动本地合成全栈（8000/8001/8002/8090 + worker）
.\project.ps1 -Action Demo      # 同上 + 生成 3 片+1 重扫与 Agent 演示
.\project.ps1 -Action Showcase  # 一键灌入高仿真 5 业务演示数据（仅本地 SQLite）
.\project.ps1 -Action Health    # 只读探测本机 8000/8001/8002/8090 /health
.\project.ps1 -Action TestFast  # 快速冒烟门禁（传染病双 selftest + 协作单测，<15s）
.\project.ps1 -Action Test      # 后端 pytest 回归（可加 -TestPath tests/unit/... 定向跑）
.\project.ps1 -Action TestWeb   # 前端 node --test
.\project.ps1 -Action Build     # 前端离线构建（vue-tsc + vite build）
.\project.ps1 -Action Probes    # 43 项架构与业务复核探针
.\project.ps1 -Action Docs      # 文档断链与来源/归档 SHA256 核验
.\project.ps1 -Action Status    # 查看活跃 AI 会话锁与近期交接（防撞车）
.\project.ps1 -Action Context   # 导出精简接手包至 .ai/context-packet.md（给纯聊天模型）
```

任意命令均可加 `-DryRun` 仅预览工作目录与执行参数。

## 目录结构一览

```text
README.md / project.ps1         项目总入口与统一启动/测试包装器
AGENTS.md / CLAUDE.md           多 AI 唯一规则源（根 AGENTS.md）与工具薄入口
AI_START.md                     任意 AI/纯聊天模型接手提示词
docs/                           文档总索引、历史演进、共享 AI 状态与专题研发/运维记录
implementation/                 唯一业务源码根（apps / packages / plugins / tests / deploy / docs）
数据上报/                       5 大上报业务原始接口、Excel、通报材料与各业务 README
设计交付v0.4/                    当前设计基线、契约与来源摘要（7 个哈希锁定原件在原位只读）
归档/                           按日期归档的历史方案、提示词、复核与多方评审原件（只读）
scripts/                        协作登记、文档校验、上下文包、演练灌数与新业务脚手架
snapshots/                      轻量恢复元数据清单（历史大包已清理，仓库已由 .git 管理）
.ai/                            本机 AI 会话认领台账与接手包输出（不入库）
```
