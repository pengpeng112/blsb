# 定时上报可视化计划交接

任务 DTR-TIMED-UI-PLAN-20261024；session `20261024T032739Z-837c251e`；工具 ZCode，模型 GLM-5.3；2026-10-24。
用户要求：前端界面显示定时上报状态、做详细分析与长任务开发计划、交其他 AI 执行。**本轮纯分析与计划，零代码改动、零生产操作。**

## 交付

- [EXECUTION_TASK.md](../../development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md)：
  现状/差距 G1—G6、设计决策 7 条、TUI-00—09 工作分解、API 契约 §5（字段级）、UI 布局、
  健康规则、验收 A01—A08、边界禁止、风险回滚。
- [NEXT_AI_PROMPT.md](../../development/drug-trace-timed-ui-plan-20261024/NEXT_AI_PROMPT.md)：可直接复制给执行 AI。
- [README.md](../../development/drug-trace-timed-ui-plan-20261024/README.md)：摘要。

## 分析依据（实测读取，未改文件）

- `DrugTrace.vue`（389 行）五个卡片结构、api 封装、角色机制；页顶"MOCK/合成"横幅为硬编码（已过时）。
- `routes.py` 764 行：现有 20 个端点；readiness 不含 scope/配额；无日报概念。
- `apps/core_api/app.py:1639` 通用 submissions 列表：仅 keyset 分页无过滤（决定：drug_trace 域新建 outcomes 端点，不动通用接口）。
- `models.py`/`plugin.py:192-197`：drug_trace 迁移 009/010/011 注册方式 → 新迁移定为 012。
- 驱动与 systemd 单元现状（timed-report-20261024 目录）→ 驱动 v2 后向兼容规则。

## 关键设计决定（写死在任务书，执行者不另选）

日报入库不读宿主文件；运行报告与平台终态两表分开不混算；健康纯数据推导（不查 systemd）；
驱动 v2 上报失败完全退化为 v1 行为；零门禁改动（A07）；生产部署避开 06:00–08:30 且须用户在场，
未获窗口确认时 TUI-08 标 BLOCKED 本地交付。

## 下一步

把 NEXT_AI_PROMPT.md 交给执行 AI；执行完成后按其 TUI-09 更新共享状态。
本计划不改发送/质量/证据/配额语义，与 2026-10-25 首跑观察互不阻塞。
