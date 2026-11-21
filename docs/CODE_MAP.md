# 代码、启动、数据和生成文件地图

> 更新日期：`2026-11-21`。工作区根目录已有 `.git/`，所有非琐碎改动均可通过 `git status` / `git diff` 核对。

## 一、源码与脚本目录

| 路径 | 职责/性质 | 管理方式 |
|---|---|---|
| `implementation/apps/` | core(8000)/storage(8001)/exchange(8002) API、worker、edge_agent、Vue3 `admin_web`、seed/migrate/metrics | 唯一业务源码根，不搬迁路径 |
| `implementation/packages/` | `contracts`、`core_db`（编号迁移）、`workflow_core`、`connector_sdk`、`mapping_engine`、`storage_backend` | 通用基础库 |
| `implementation/plugins/` | 5 大业务插件（`drug_trace`/`infectious`/`medins_emr`/`pathology`/`medins_imaging`）、`alerting`、`external_ledger`、`formats/xml_payload`、`destinations/soap_xml` | 领域与协议插件，保留 `plugin_registry` 装配路径 |
| `implementation/tests/` | `unit`/`contract`/`integration`/`fault`/`e2e`/`performance`/`functional` | 自动化测试；`functional/run_live.sh` 仅限完全隔离时使用 |
| `implementation/deploy/` | `inf_pusher`、`inf_watchdog`（生产独立推送与守护）、`intranet`、`dmz`（含 `el7-runtime/`）、`linux/docker-offline`、`oracle`、`offline`、`windows`、`compose` | 仅保留有效单份离线部署材料；旧重复发行包已清理 |
| `implementation/docs/` | 四类实施文档、ADR、runbooks、`audit/2026-10-01`（43 项复核探针） | 实施级详细文档 |
| `implementation/profiles/` / `hospital_packs/` / `migrations/` | 医院配置 Profile 与迁移骨架（实际编号迁移在 `packages/core_db/migrate.py` 与插件注册） | 保持现有布局 |
| `scripts/` | `ai_session.py`、`project_docs.py`、`context_packet.py`、`new_report_target.py`、`seed_local_showcase.py` 及 `tests/test_coordination.py` | 标准库治理与脚手架工具 |
| `project.ps1` | 根目录统一启动/测试/构建/探活包装器 | 固定工作目录，委托既有入口，透传退出码 |

## 二、运行数据、归档与工具目录

| 路径 | 实际性质 | 注意事项 |
|---|---|---|
| `implementation/packages/var/blyb.sqlite3*` | 未设 `BLYB_DATABASE_URL` 时的本地默认开发库 | 不随文档搬迁 |
| `implementation/var/` | 交换库、本地卷、测试日志与演示目录 | 不入库；禁止再在仓库根目录创建平行 `var/` |
| `implementation/scanner-output/` | 本地扫描模拟器输出样例 | 默认保留 |
| `implementation/apps/admin_web/{dist,node_modules}/` | 前端构建产物与离线依赖 | 离线构建复用现有 `node_modules`，不入库 |
| `snapshots/` | 轻量恢复清单（`2026-10-02-docs-organization`、`baseline-20261016`） | 早期大 tar 包已清理，版本回退统一走 `.git` |
| `.ai/` | 本机 AI 协作会话台账（`.ai/sessions/*.json`）与接手包输出 | 不入库、无密钥 |
| `.v2c/`、`.video_agent/`、`.opencode/` | 工具私有目录 | 不扫描、不入库 |
