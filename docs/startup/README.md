# 启动、测试和部署入口说明

> 更新日期：`2026-11-21`。统一推荐从根目录执行 [project.ps1](../../project.ps1)（自动固定工作目录、透传退出码、不自动联网安装依赖、不终止占用端口的外部进程）。

## 1. 按业务定位材料与连库

- **上报业务导航**：从 [数据上报总索引](../../数据上报/README.md) 进入对应业务的 `README.md`（定位接口原件、代码/视图、凭据受限路径与最新证据）。
- **只读查库**：统一经 [DATABASE_ACCESS.md](DATABASE_ACCESS.md) 与全局 `sjzc` 技能（受控元数据/只读通道，不读取明文凭据）。

## 2. 本地开发、演示与健康检查

| Action | 委托入口 | 说明 |
|---|---|---|
| `Dev` | `implementation/run_dev.py` | 迁移 + 合成种子，前台启动 core(8000)/storage(8001)/exchange(8002)/mock(8090) + worker |
| `Demo` | `implementation/run_dev.py --demo` | 同上 + 3 片+1 重扫与 Agent 单轮演示 |
| `Showcase` | `scripts/seed_local_showcase.py --confirm-local-demo` | 灌入 5 业务高仿真演示数据（仅限本机 SQLite，生产环境自动熔断） |
| `Health` | 本机 HTTP 只读探活 | 检查 `8000/8001/8002/8090` `/health` 状态 |
| `Metrics` | `implementation/apps/run_metrics_only.py` | 独立 metrics-only 模式（默认 8010） |
| `Agent` | `python -m apps.edge_agent.run --once` | 本地采集端单轮运行 |

- 默认开发核心库：`implementation/packages/var/blyb.sqlite3`；交换库：`implementation/var/exchange.sqlite3`。
- `Dev`/`Demo`/`Showcase` 若检测到非 SQLite `BLYB_DATABASE_URL` 会主动拒绝执行；前台 `Ctrl+C` 停止。

## 3. 验证与维护命令

```powershell
.\project.ps1 -Action TestFast                                      # 快速冒烟（<15s：传染病双 selftest + 协作单测）
.\project.ps1 -Action Test -TestPath tests/unit/test_inf_activity_gate.py  # 定向单测
.\project.ps1 -Action Test                                          # 后端全量 pytest（约 3–4 分钟）
.\project.ps1 -Action TestWeb                                       # 前端 node --test（使用现有 node_modules）
.\project.ps1 -Action Build                                         # 前端离线构建（vue-tsc + vite）
.\project.ps1 -Action Probes                                        # 43 项架构复核探针
.\project.ps1 -Action Docs                                          # 活跃文档断链 + 来源/归档 SHA256 核验
.\project.ps1 -Action Status                                        # 查看活跃 AI 会话锁与最近交接
.\project.ps1 -Action Context                                       # 生成精简 .ai/context-packet.md
```

- 新增或归档文档后运行 `python scripts/project_docs.py inventory` 更新 `docs/catalog.json`。
- `implementation/tests/functional/run_live.sh` 会终止固定端口进程并清空测试目录，仅限确认完全隔离时手工执行。

## 4. 数据库迁移与生产部署形态

迁移需显式指定目标库 URL：

```powershell
$env:BLYB_DATABASE_URL = 'sqlite:///F:/python/blyb/implementation/var/example/core.sqlite3'
.\project.ps1 -Action Migrate
```

| 形态 | 入口与手册 | 当前边界 |
|---|---|---|
| **医院内网 SQLite 单机** | [implementation/deploy/intranet/README.md](../../implementation/deploy/intranet/README.md) | 加固启动脚本拒绝空令牌与占口，用于单机落地 |
| **传染病宿主推送与守护** | `implementation/deploy/inf_pusher/`、`inf_watchdog/` | 已在生产推送宿主机运行（详见 [INF-BUGFIX-20261119](../ai/sessions/INF-BUGFIX-20261119.md)） |
| **四包受控生产升级** | [DEPLOY_UPGRADE_RUNBOOK.md](../development/platform-polish-and-stability-20261116/DEPLOY_UPGRADE_RUNBOOK.md) | 独立灰度与秒级回滚（待 HT-04 勘误收官后按窗口执行） |
| **DMZ / Linux 离线包** | `implementation/deploy/dmz/`、`linux/docker-offline/`、`oracle/`、`offline/` | 保留单份离线依赖（含 EL7 Python 运行时 `deploy/dmz/el7-runtime/`） |
| **Windows Agent EXE / 服务** | `implementation/deploy/windows/` | Win10/11 EXE 已实测；Win7 / SCM / 50GiB 压测未做现场验收 |
| **备份与新根恢复** | [备份与恢复.md](../../implementation/docs/runbooks/备份与恢复.md) | 自动化恢复测试已有；清洁机演练待维护窗口 |
