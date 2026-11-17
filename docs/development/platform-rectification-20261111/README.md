# LT-01～LT-09 完成情况独立审计与深度整改长任务总览（2026-11-11）

> **2026-11-12 剩余计划已复核为 revision 2**：RT-01/02 已有交接，后续执行请从
> [新版总入口](revision-2/README.md)、[统一执行约定](revision-2/EXECUTION_CONTRACT.md)和
> [新版接力提示词](revision-2/DAILY_PROMPTS.md)开始。RT-03～06的当前实现缺口、范围、反例和验收已重新细化。
> 下文是11-11审计原始结论，保留历史，不表示RT-01/02问题仍未修复；与新版冲突的执行细节以新版及当前证据为准。

> 编制：Antigravity / Gemini 4 Carbon，任务 `AUDIT-AND-RECTIFY-PLAN-20261111`。
> 性质：**全量源码独立复核 + 第二阶段深度整改计划（RT-01～RT-06）**。本次未改动业务源码、未触碰任何生产系统。

---

## 一、核心结论：别的 AI 完成了吗？真实质量如何？

**表面进度**：外部执行 AI（ZCode/GLM-5.3 与 Codex 复核）在 11-04 至 11-11 期间已经把 `LT-08`、`LT-01`～`LT-09`（含 `LT-03-R1`、`LT-06A/06B`）**全部跑完并登记收官**。后端单元/集成测试涨到了 **911 通过 / 0 失败 / 1 环境错误**，前端 `npm run build` 退出码 0、纯函数单测 **51/51**；且在此期间药品追溯批次超时根修已在生产生效（59ms/单，提速 ~170 倍），生产链路零受损。

**深层真相（独立源码审计结论）**：**“账面收官，但一打开真浏览器、一跑真实链路就会多处白屏/报错/越权/失真”**。
之前的 AI 在没有浏览器的环境下纯盲写前端，且只用纯函数单测和后端局部接口测试验收，留下了 **6 大类、40 余处实质性缺陷（含多个 P0 级白屏崩溃与高危越权）**。如果现在直接把这套代码部署上线，新控制台基本不可用。

---

## 二、独立代码审计查出的真实问题清单（附精确文件与行号）

### 2.1 【P0 致命】前端一打开就白屏、按钮隐身、弹窗关不掉、路由断路
*原因：写 LT-06B/07 页面的 AI 没有核对 LT-02 组件的真实 Props/Slots/Events，而 `vue-tsc` 不报错未知非受控插槽与透传属性，纯函数单测也完全不挂载 Vue 组件！*

1. **新首页 `/dashboard` 默认种子直接崩溃白屏**：`Dashboard.vue:135-137` 把后端返回的 `TargetSummary`（字段是 `target_code`，没有 `code`）用 `as any[]` 强塞给 `sortTargets()`（`utils/dashboard.mjs:9-15`）。当两个业务同为 `PRODUCTION`（默认种子里“码上放心”和“传染病”都是 `PRODUCTION`）时执行 `a.code.localeCompare(b.code)`，直接抛出 `TypeError: Cannot read properties of undefined (reading 'localeCompare')` 整页崩溃！
2. **业务详情页 `TargetDetail.vue` 全部 7 个 Tab 内容 100% 空白**：`Tabs.vue:3-12` 根本没有写 `<slot />`！但 `TargetDetail.vue:11-146` 把概况、台账、调度、告警、配置、外部导入、医保联调台（`MedinsDebug`）7 个面板全塞在 `<Tabs>...</Tabs>` 标签内部，导致任何 Tab 内容在 DOM 中完全不渲染！
3. **路由注册 2 处 `component: undefined` 白屏**：`router/index.ts:40-45` 用 `it.path.replace(/^\//, '')` 去 `map` 里找组件：① `menu.ts:44` 的 `/system/users` 变成找 `map['system/users']`（实际键名是 `'users'`）→ 用户管理页白屏；② `menu.ts:12` 的 `/targets/inf_cdc_frontend` 注册了一条静态空组件路由，**劫持了动态路由 `/targets/:code`** → 点侧栏“传染病上报”或点卡片进传染病详情一律白屏！
4. **顶部操作按钮全部隐身**：`PageHeader.vue:12` 只有默认 `<slot />`，没有 `<slot name="actions" />`；但 `Dashboard.vue:6`（天数切换/刷新）、`TargetDetail.vue:6`（返回）、`Users.vue:6`（新建用户）、`AuditSearch.vue:7`（导出 CSV）全写在 `<template #actions>` 里，按钮全部消失！
5. **所有抽屉和弹窗点 ✕、遮罩、Esc 全都关不掉**：`Drawer.vue:19` 和 `Modal.vue:19` 触发的是 `update:open` 事件，但 `Schedules.vue`、`Alerts.vue`、`Users.vue`、`AuditSearch.vue`、`LedgerPane.vue`、`MedinsDebug.vue` 全部绑了 `@close="..."`，弹窗一旦打开就关不掉！
6. **所有状态标签全显示 `—`**：`StatusTag.vue:9-10` 接收 `bucket` 或 `state`，但新 8 页全部传 `:status="..."`，被当成无用 HTML 属性透传，满屏状态全是 `—`。
7. **其他前端硬伤**：
   - `Users.vue:114,150` 新建用户传 `scopes`，后端 `system_routes.py:125` 读 `target_scopes` → 新建用户勾选的业务范围被静默丢弃成空数组；
   - `AuditSearch.vue:8-10` 用原生 `<a :href="exportUrl" download>` 导出 CSV，不带 `Authorization: Bearer` 头 → 必返 **401**；且 `datetime-local` 未转 UTC，查询偏移 8 小时；
   - `TargetDetail.vue:309` 调 `GET /api/v1/destinations/${destId}` → 后端根本没有这个接口，必返 404；
   - `Dashboard.vue:57` 读 `t.backlogTotal`（驼峰）而后端是 `backlog_total`（下划线），把窗口积压误标成“（全量）”；
   - `Alerts.vue:26-49` 写成 `<table v-for="a in alerts">`（每条告警生一张无表头的独立 table），且非管理员一进页面无条件调管理员接口 `listChannels()` 弹 `403 alert`；
   - `Donut.vue:12` 双重旋转偏移导致圆环扇区飞出中心；`components.css` 裸 `aside`/`button` 全局污染侧栏折叠宽度；10 个老页面（`Cases/Quality/Review/Connectors/Mapping/Lineage/Workflows/Pack/Publications/Metrics`）完全未升级，仍是裸 textarea 手写 JSON。

### 2.2 【P0 安全/架构】鉴权绕过、审计漏记、插件双轨与 Compose 断裂
1. **`target_scopes` 多处高危越权绕过**：
   - `plugins/domains/medins_emr/routes.py:71-267` 全部 7 个医保联调接口**零调用** `p.require_target_scope("medins_emr_sd")`，无权限受限账号可随意读写；
   - `report_target_routes.py:99-115` `PUT /api/v1/report-targets/{code}` 漏调 `require_target_scope(code)`，受限 `config_editor` 可篡改任意业务配置；
   - `ops_routes.py:308-329` `PUT /api/v1/alert-rules/{code}` 未校验业务范围，可篡改他人或全局规则；
   - 旧接口 `/api/v1/alerts`（`app.py:2255`）和 `/api/v1/submissions`（`app.py:1706`）未按范围过滤，可绕过新接口读取全院数据。
2. **运维写操作漏写审计日志**：`ops_routes.py:219-391` 告警确认/关闭、规则增改、通知通道增改全部漏写 `AuditEvent`。
3. **最后管理员保护存在并发归零漏洞**：`system_routes.py:162-177` 只 `FOR UPDATE` 锁被改的那一行，两个管理员并发互相/自我降权时都会读到剩余管理员数 ≥1 并同时提交，导致全院 `platform_admin` 归零；且计数没过滤已禁用账号。
4. **插件装配双轨制与生产 Compose 断裂**：
   - `app.py:143-164` 与 `worker/engine.py:820-827` 在未设 `BLYB_PLUGINS` 时仍走弃用的 `plugin_registry`（`_loaded_registry = None`），导致 `ops_routes` 拿不到任何领域任务类型（`known_job_types(None)` 丢失药品/传染病 job）；
   - `deploy/compose/drug-trace.override.yml` 只给 `core` 和 `worker` 设了 `BLYB_PLUGINS`，**漏了 `migrate` 和 `bootstrap` 服务**！若环境变量未全局覆盖，`migrate` 会漏跑插件迁移，`bootstrap` 会因不认识 job_type 跳过镜像调度种子；且新插件 `infectious,medins_emr,medins_imaging` 未纳入生产默认安全加载清单。

### 2.3 【P1 后端内核】调度器状态机失真、早报时区崩溃、双轨告警割裂
1. **调度运行永远没有 `RUNNING` 态，耗时永远是 0 秒**：`worker/engine.py:150-167` 领到任务时不把 `ScheduleRun` 改成 `RUNNING`、不写 `started_at`；跑完时 `scheduler.py:345` 写 `started_at = started_at or now`，导致 `duration_seconds` 永远等于 `0`！
2. **Job 重试一次成功，调度记录却永久显示 `FAILED`**：`jobs.py:166` 首次报错进入 `RETRY_WAIT` 时返回 `finished=True`，`engine.py:194` 立刻把 `ScheduleRun` 标成终态 `FAILED`；等重试成功时 `scheduler.py:337` 看到已是终态直接丢弃更新。
3. **`RUN_ONCE` 变成历史全量轰炸 & 刚创建就被判超时**：`scheduler.py:101-131` 每次 tick 只把 `next_fire_at` 往前推一格而不是推过当前时间，停机一天恢复后会把过去所有班次每 10 秒补跑一遍；且 `deadline_at` 用历史计划时间算，刚入队瞬间就被 `run_guard` 判为 `TIMED_OUT`。
4. **每日早报所有 Cron 全报“表达式待核” + 8 小时时区错位**：`utcnow()` 返回无时区 naive 时间，`digest.py:21,63` 对它调 `.astimezone(CST)`（在东八区机器上偏移错乱）并传给要求 aware 时间的 `CronExpr.next_after(now)`，**100% 抛 `ValueError` 被吞掉**，早报里所有计划全显示“表达式待核”！
5. **告警中心割裂与漏洞**：`/alerts` 与 `/alerts2` 并存、旧表 `"warning"` 与新表 `"warn"` 大小写/拼写不一致导致筛漏、`cursor` 参数声明了却完全没实现分页、自监控共用同一个 `alerting.self` 指纹互相误恢复、`f.detail` 未经脱敏直接落库、webhook 直传 `"url"` 可绕过环境变量校验明文落库。

### 2.4 【P1 业务模块】传染病影子缺摘要致崩溃、医保漏提取 4 个交易且嵌套脱敏失效
1. **传染病影子插件**：
   - `infectious/send.py:74-82` 创建 `PackageRevision` 时**漏传 `digest`（默认空串 `""`）**：导致幂等键 `inf:{skey}:` 版本段为空（源数据更新后被误判已发跳过），且 `Submission.snapshot_digest` 为空导致台账无法人工处置！
   - 只有消费内存字典的假入口，没有真正实现 `collect.py`（从 SQLAlchemy/DB 视图按水位+overlap 取数），也没有实现 `HOOK_LEDGER_DETAIL`（点台账行报 404 把整张表清空），还原地修改了已发布的 `ConfigRevision`；
   - `inf_pusher.py` 外部导入器 `SHAPE_INVALID` 行漏计入 `rows_read`，仅存在于 `sent.jsonl` 的记录被静默丢弃。
2. **山东医保电子病历（LT-07）**：
   - **报告宣称 23 个交易全提取，实际只生成了 19 个**：`build_profile.py:208` 只循环了清单表 19 项，**`12601—12604` 四个目录上传交易根本没生成到 `transactions.json`、`fields/`、`samples/`**！
   - **嵌套结构被拍平导致真实报文校验全错**：规范表格平铺了子节点字段，`build_profile.py:163` 把它们全挂在 `input` 顶层（如 `input.certno`），而真实报文是嵌套的（`input.patn_bas_info.certno`），导致拿合规报文去校验时顶层全报缺必填，嵌套内层反而因 `UNKNOWN_FIELD` 跳过类型/长度校验！且输出参数表也被误混入 `input`。
   - **患者隐私明文落库漏洞**：`mask_sensitive`（`medins_emr/routes.py:35-49`）只脱敏 `input` 第一层标量，嵌套对象和数组里的姓名、身份证号、手机号（`mob` 还漏标了 `sensitive:true`）**全部明文写入 `debug_exchange` 表**！
   - 死代码与脏样例：`"日期"` 截胡 `"日期型"`、代码列 `"Y"` 被当成字典名、合成样例带非法字段 `"_synthetic": true`、前端历史记录无“回填结果”按钮、下载执行包丢了 `headers` 和 `curl`。

### 2.5 【P2 测试基线与平台进阶能力】
1. **探针 `R22` 长期失败（42/43）与 `test_drug_trace_pg` 双 Bug**：`R22` 测试桩未提供 `init_oracle_client` 与 `oracle_client.py` Thick 校验冲突；`test_drug_trace_pg.py` 硬编码端口 `55432` 冲突泄漏容器，且断言迁移列表漏掉新索引迁移，还有 3453 条 SQLAlchemy 2.0 弃用警告（`Table.tometadata`、`Query.get`）。
2. **脚手架只能用一次**：`scripts/new_report_target.py:135` 用全局唯一注释块判重，生成完第 6 个业务（影像云）后再建第 7 个业务就会跳过种子写入。
3. **管理界面配置闭环与老页面**：`TargetDetail` 不能编辑业务配置、`Schedules` 不能新建/编辑调度、`Alerts` 不能新建规则/配置通道；字段映射/配置包发布 Diff/连接器抽样预览等 10 个老页面仍停留在手写裸 JSON 阶段。

---

## 三、第二阶段深度整改与完善长任务路线图（RT-01 ～ RT-06）

为了彻底消灭上述所有隐患，让别的 AI **不用动脑猜、照着就能改对、且必须用真实无头渲染/端到端契约测试自证（不再盲写）**，我把整改拆成 **6 个重型长任务**（每个任务工作量约 1.5～3 小时，正好匹配你每天一次的长任务额度）：

| 天数 | 任务编号与名称 | 核心解决什么问题 | 详细任务书 | 动生产？ |
|---|---|---|---|---|
| **第 1 天** | **RT-01 前端 P0 白屏崩溃清零与前后端契约对齐 + 组件挂载测试** | 修掉 `Tabs`/`PageHeader`/`Drawer`/`Modal`/`StatusTag` 错配、`Dashboard` 白屏、路由 2 处断路、`Users` 丢范围、CSV 导出 401、裸 UTC 时区、SVG 圆环飞出；**建立无需浏览器的 Vue SSR/JSDOM 挂载测试守卫** | [RT-01](RT-01-前端P0修复与组件挂载测试.md) | 否 |
| **第 2 天** | **RT-02 后端安全鉴权闭环、单轨插件装配与测试基线清零** | 堵住 `medins_emr`/`report_target`/`alert_rules`/旧接口 4 处 `target_scopes` 越权；补齐运维审计；修最后管理员并发锁；统一 `app_runtime` 单轨与 Compose `migrate/bootstrap` 配置；修好 `R22`（43/43 全绿）、PG 测试双 Bug 与 3400+ 弃用警告 | [RT-02](RT-02-安全鉴权单轨装配与基线清零.md) | 否 |
| **第 3 天** | **RT-03 调度器状态机、早报时区与告警中心深度修复** | 补齐 `ScheduleRun` 的 `RUNNING` 与真实耗时；修重试假 `FAILED`；修 `RUN_ONCE` 历史轰炸与创建即超时；修早报 naive 时区崩溃（消灭“表达式待核”）；统一告警接口、真实游标分页、深度脱敏与自监控独立指纹 | [RT-03](RT-03-调度状态机与告警中心深修.md) | 否 |
| **第 4 天** | **RT-04 传染病影子真采集闭环与山东医保 23 交易嵌套契约重构** | 补齐传染病 `PackageRevision.digest`、`collect.py` 真 SQL 视图取数、`HOOK_LEDGER_DETAIL`、7 天影子对账比对工具与导入计数修复；重做医保提取器补齐 `12601-12604`（真 23 交易）、嵌套路径对齐、剔除输出表、递归全脱敏与联调台回填/下载修复 | [RT-04](RT-04-传染病真影子与医保嵌套契约.md) | 否 |
| **第 5 天** | **RT-05 运营写操作闭环与全部 10 个遗留配置页现代化升级** | 补齐业务编辑、调度新建/编辑/详情页、告警规则新建与通道表单；将未升级的 10 个老页面（字段映射可视化构建器、配置包版本 Diff 对比、连接器向导与预览、病理工作台/质量/复核/流程等）全面升级，消灭手写裸 JSON | [RT-05](RT-05-运营写闭环与遗留页全面升级.md) | 否 |
| **第 6 天** | **RT-06 多业务扩展终验：脚手架修复、影像云投影示范与全栈端到端验收** | 修脚手架只能用一次的 Bug；打通济南医保影像云核心数据集投影与契约校验示范；编写**全栈真实 HTTP 端到端冒烟测试**（真起 FastAPI + Worker + 前端 API 客户端走通全部 29 页对应接口）；更新部署 Runbook 与终验报告 | [RT-06](RT-06-多业务扩展与全栈端到端终验.md) | 否 |

---

## 四、所有整改任务通用铁律（执行 AI 必读）

1. **严禁触碰线上生产环境与生产运行链路**：
   - 不连接真实 HIS/EMR/ODS/PIS 数据库，不 SSH 登录 `<prod-host>` / `<relay-jump>` / DMZ，不请求 `<frontend-host>` 前置机或外部医保/阿里平台。
   - **禁改文件（SHA256 必须前后一致）**：`implementation/deploy/inf_pusher/**`、`implementation/deploy/inf_watchdog/**`、`docs/development/drug-trace-timed-report-20261024/**`、`implementation/plugins/destinations/alihealth_drugtrace/**`、`implementation/apps/admin_web/src/views/DrugTrace.vue`（及其 `drugTrace.css`、`drugTraceDisplay.ts`）、已有迁移 `001`—`020` 的已上线表结构语义。
   - 码上放心与传染病正在正常上传，`sole_pusher` 与新闸门绝不能挂入药品追溯既有发送路径；所有镜像调度保持 `observe_only=True`、`enabled=False`；所有新增系统调度保持默认 `enabled=False`。
2. **离线零新包约束**：不执行 `pip install`、不执行 `npm install` 安装任何新第三方包。前端组件挂载测试直接用 Vue 3 自带的 `@vue/server-renderer`（`vue` 核心包已内置 `vue/server-renderer`！）在 `node --test` 中将 `.vue` 组件编译并 `renderToString` 断言真实 HTML 输出——**零新增依赖即可 100% 抓出空白插槽、错传属性与渲染崩溃！**
3. **禁止盲写与表面造绿**：
   - 严禁删除已有测试断言、严禁加 `xfail` 或 `skip` 掩盖错误。
   - 每个任务收尾必须同时跑通以下四套命令并贴出真实退出码与通过数：
     ```powershell
     # 1) 后端全量测试（implementation/ 目录下）
     python -m pytest tests -q -ra -p no:cacheprovider --junitxml=var/<任务ID>-regression.xml
     # 2) 独立复核探针（implementation/ 目录下，RT-02 起必须 43 passed, 0 failed）
     python -m pytest docs/audit/2026-10-01/test_review_probes.py -q -ra -p no:cacheprovider
     # 3) 前端类型检查与构建（implementation/apps/admin_web/ 目录下）
     npm run build
     # 4) 前端测试（显式枚举全部 *.test.mjs）
     $testFiles = @(Get-ChildItem -LiteralPath tests -Recurse -File -Filter '*.test.mjs' | Sort-Object FullName | Select-Object -ExpandProperty FullName)
     node --test $testFiles
     ```
4. **登记与交接**：开工 `python scripts/ai_session.py start`，收尾写 `docs/ai/sessions/<任务ID>-<日期>.md`，同步更新 `STATE.md` / `BACKLOG.md` / `HANDOFF.md` 顶部与四类实施文档，运行 `python scripts/project_docs.py inventory` 与 `check`，最后 `ai_session.py end`。

每日直接复制粘贴的提示词见：[DAILY_PROMPTS.md](DAILY_PROMPTS.md)。
