# 会话交接：AUDIT-AND-RECTIFY-PLAN-20261111

- **会话 ID**：`20261111T120313Z-9a0e5a7c`
- **任务标识**：`AUDIT-AND-RECTIFY-PLAN-20261111`
- **执行工具 / 模型**：`Antigravity` / `gemini-4-carbon`
- **日期**：2026-11-11
- **任务性质**：对前序 AI 执行的 `LT-01`～`LT-09`（及 `LT-03-R1`、`LT-06A/06B`）全量源码开展独立深度复核，并制定第二阶段深度整改与界面全面升级长任务计划（`RT-01`～`RT-06`）。**零生产触碰、零业务代码修改。**

## 1. 实测验证命令与客观证据

1. **独立复核探针实测**（工作目录 `implementation/`）：
   - 命令：`python -m pytest docs/audit/2026-10-01/test_review_probes.py -q -ra -p no:cacheprovider`
   - 退出码：`1`
   - 结果：`42 passed, 1 failed, 3453 warnings in 8.46s`
   - 失败项：`test_R22_oracle_can_initialize_with_configured_driver_and_dsn`（`plugins/sources/db_view/oracle_client.py:62` 对测试桩 `SimpleNamespace` 调用不存在的 `init_oracle_client` 抛 `AttributeError`，并写入粘滞 `_oracle_client_init_error`）。
2. **前端零新增依赖 Vue SFC 编译与 SSR 渲染能力实测**（工作目录 `implementation/apps/admin_web/`）：
   - 命令：`node -e "import('vue/server-renderer').then(m => console.log('SSR OK:', typeof m.renderToString)); import('@vue/compiler-sfc').then(m => console.log('SFC OK:', typeof m.parse));"`
   - 退出码：`0`
   - 结果：`SFC OK: function`、`SSR OK: function`（证实可在零 `npm install` 前提下建立真实 Vue 组件挂载与 HTML 断言测试，终结“纯函数单测 51/51 全绿但页面白屏”的盲区）。
3. **前序交付测试数字核对说明**：
   - 前序 `LT-09` 交接报告记录后端全量 `911 passed, 8 skipped, 0 failed, 1 error`、前端 `51/51 passed` + `npm run build` 通过；本次为规划与源码静态+探针审计会话，未重跑全部 911 项后端单测，已在报告中严格区分“本次实测”与“前序报告记录”。

## 2. 独立源码深度审计核心结论（名义完成 vs 实际质量）

前序 AI 按计划完成了 `LT-01`～`LT-09` 的数据库迁移（`015`～`020`）、模块骨架和纯逻辑单测，但在**前端真实渲染、前后端字段契约、范围鉴权闭环、调度状态机、传染病真采集与医保嵌套契约/隐私脱敏**上遗留了 **40+ 处具体缺陷**（详见 [README.md](../../development/platform-rectification-20261111/README.md)）：

- **前端 P0 白屏/失灵（7 处致命缺陷）**：
  1. `Dashboard.vue:135-137` 将含 `target_code`（无 `code`）的 `TargetSummary` 传给 `dashboard.mjs:9-15` 的 `sortTargets()`，当两个业务同处 `PRODUCTION`（系统默认种子即满足）时触发 `undefined.localeCompare` 白屏崩溃。
  2. `Tabs.vue:3-12` 未声明 `<slot />`，导致把全部 7 个页签包在 `<Tabs>...</Tabs>` 内的 `TargetDetail.vue:11-146` 正文 **100% 空白**。
  3. `router/index.ts:40-45` 中 `/system/users` 组件映射为 `undefined`，且静态路由 `/targets/inf_cdc_frontend` 组件为 `undefined` 并劫持动态路由 `/targets/:code`，两处必现白屏。
  4. `PageHeader.vue:12` 缺 `<slot name="actions" />`，全站新页面顶部操作按钮全部隐身。
  5. `Drawer.vue:19` 与 `Modal.vue:19` 仅触发 `update:open` 未触发 `close`，弹窗抽屉点击关闭无效。
  6. `StatusTag.vue:9-10` 仅读 `bucket/state`，新页面全传 `:status`，导致状态标签全显示 `—`。
  7. `Users.vue:150` 提交 `scopes` 而非后端 `target_scopes`；`AuditSearch.vue:8` 原生链接导出 CSV 必报 `401` 且带 8 小时 UTC 偏移；`TargetDetail.vue:309` 调不存在的 `/api/v1/destinations/{id}` 报 `404`；10 个平台老配置页完全未升级。
- **后端安全、状态机与业务契约缺陷**：
  1. **4 处 `target_scopes` 越权**：`medins_emr/routes.py` 全部 7 个端点、`PUT /api/v1/report-targets/{code}`、`POST/PUT /api/v1/alert-rules`、旧 `/api/v1/alerts` 与 `/api/v1/submissions` 均漏范围校验；`ops_routes.py` 缺写操作 `AuditEvent`；`system_routes.py:162-177` 单行锁存在双管理员并发互降归零竞态。
  2. **双轨残留与 Compose 缺口**：`app.py` 与 `worker/engine.py` 在未设 `BLYB_PLUGINS` 时仍令 `_loaded_registry = None`；`drug-trace.override.yml` 的 `migrate/bootstrap` 漏配 `BLYB_PLUGINS`。
  3. **调度与告警缺陷**：`ScheduleRun` 从不进入 `RUNNING`、`duration_seconds` 恒为 `0`、首次重试失败即永久标 `FAILED`、`RUN_ONCE` 补跑历史风暴且 `deadline_at` 立即超时；`alerting/digest.py:21,63` 对 naive UTC 调 `.astimezone()` 且混传 aware/naive 导致早报 100% 抛错吞成“表达式待核”；4 个列表接口 `cursor` 形同虚设。
  4. **传染病与医保缺陷**：`infectious/send.py:74-82` 漏填 `PackageRevision.digest`（空串破坏幂等与人工处置）、无真实 SQL `collect.py`、原地篡改 `PUBLISHED` 配置；山东医保 `build_profile.py:208` 漏提取 `12601`—`12604`（仅 19/23 交易）、输出表混入 `input`、嵌套拍平；`medins_emr/routes.py:35-49` 的 `mask_sensitive` 仅脱敏顶层标量，导致嵌套层患者姓名/身份证/手机号明文落入 `debug_exchange`！
  5. **脚手架缺陷**：`scripts/new_report_target.py:135` 靠全局唯一标记判重，只能添加一次新业务。

## 3. 新增规划产出文件（8 份）

全部位于 `docs/development/platform-rectification-20261111/`：
1. [README.md](../../development/platform-rectification-20261111/README.md) — 独立源码深度审计总报告（40+ 缺陷精准文件行号定位）与 6 天长任务路线图
2. [RT-01-前端P0修复与组件挂载测试.md](../../development/platform-rectification-20261111/RT-01-前端P0修复与组件挂载测试.md) — 第 1 天详细任务书
3. [RT-02-安全鉴权单轨装配与基线清零.md](../../development/platform-rectification-20261111/RT-02-安全鉴权单轨装配与基线清零.md) — 第 2 天详细任务书
4. [RT-03-调度状态机与告警中心深修.md](../../development/platform-rectification-20261111/RT-03-调度状态机与告警中心深修.md) — 第 3 天详细任务书
5. [RT-04-传染病真影子与医保嵌套契约.md](../../development/platform-rectification-20261111/RT-04-传染病真影子与医保嵌套契约.md) — 第 4 天详细任务书
6. [RT-05-运营写闭环与遗留页全面升级.md](../../development/platform-rectification-20261111/RT-05-运营写闭环与遗留页全面升级.md) — 第 5 天详细任务书
7. [RT-06-多业务扩展与全栈端到端终验.md](../../development/platform-rectification-20261111/RT-06-多业务扩展与全栈端到端终验.md) — 第 6 天详细任务书
8. [DAILY_PROMPTS.md](../../development/platform-rectification-20261111/DAILY_PROMPTS.md) — 每日直接复制粘贴执行的提示词清单（含中断续做提示词）

## 4. 未完成项与下一步接力建议

- 本次规划与审计任务已全部完成。
- **下一棒（执行 AI）**：严格按顺序从 **第 1 天 `RT-01`** 开始执行（详见 [DAILY_PROMPTS.md](../../development/platform-rectification-20261111/DAILY_PROMPTS.md) 与 [RT-01-前端P0修复与组件挂载测试.md](../../development/platform-rectification-20261111/RT-01-前端P0修复与组件挂载测试.md)），依次推进 `RT-01 → RT-02 → RT-03 → RT-04 → RT-05 → RT-06`，全程严守零外部联网安装、零触碰线上药品追溯与传染病生产链路的铁律。
