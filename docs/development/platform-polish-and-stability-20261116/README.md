# 第三阶段：传染病高可靠加固、企业微信智能提醒、全站界面焕新与多业务标准化规划（PT-01 ～ PT-06）

- **编制日期**：2026-11-16
- **前置基线**：`RT-01`～`RT-06` 本地终验完成（[FINAL_ACCEPTANCE_REPORT.md](../platform-rectification-20261111/FINAL_ACCEPTANCE_REPORT.md)）+ 传染病 13 接口上线与守护企微联调（`INF-ROOTCAUSE-20261116`）+ 检验关联独立复核修订计划（[inf-lab-linkage-review-20261115/revision-2](../inf-lab-linkage-review-20261115/revision-2/README.md) 与 [revision-4-acceptance](../inf-lab-linkage-review-20261115/revision-4-acceptance/README.md)）。
- **核心目标**：
  1. **传染病上报稳如磐石**：把 `revision-2` 尚未落地的 6 大高危边界场景（旧父成功掩盖新父失败、等待项越过水位永久丢失、重放清单无版本/断点保护、`run.sh` 丢执行位、大表 `fetchall` 内存暴涨、视图 `v1.5` 组合修复）一次性彻底落地闭环。
  2. **企业微信提醒精准好用**：修复 `inf_watchdog` 隧道闪断吞 1 小时告警、晨报扫 855 万行超时与 `parent-wait` 96 倍虚高、恢复通知漏报等隐患；并将院内 `wechat-relay`（HMAC-SHA256 `/api/v2/events`）正式纳入平台 `blyb_platform` 一等公民通知通道（支持零打扰 `dry-run` 卡片预览、`401` 隧道真达探针、级别升级穿透、风暴聚合与夜间免打扰）。
  3. **传染病拥有专属全维监控大屏**：新建对标并超越“码上放心”的 `/infectious` 传染病专属监控座舱（13 接口全景矩阵、逐表水位与滞后、本班/窗口 `sent/parent_wait/failed/client_validate`、`C1–C11` 守护体检灯、晨报阅读器、对账看板、流式只读增量读取 GB 级状态目录）。
  4. **彻底消灭前端“乱糟糟、不直观、满屏工程黑话”**：将 26 项杂乱平铺侧栏重组为清晰的 **4 组核心导航**（把 8 个病理内部子页收拢进病理工作台二级页签，激活失联的济南医保影像云 16 数据集前端工作台），修复 `ErrorState` 全站无重试按钮、`Alerts` 规则下拉显示 `0..8`、`Pack` 裸露 `**` 星号、侧栏折叠空白等缺陷，清除所有生搬硬套任务书的工程黑话。
  5. **多业务标准化即插即用**：建立统一的 `summary`（兼容 `CONFIRMED` 与 `ACCEPTED` 双口径）+ `streams`（逐子表/数据集水位进度）+ `health`（业务健康探针）+ `ContractSandbox`（通用契约校验沙箱）+ 动态业务菜单，让后续医院对接第 6、第 7、第 N 个上报项目时**免手写定制前端即可自动获得完整监控与联调界面**。

---

## 一、独立全栈评估发现的核心问题清单（按四大痛点归类）

### 1.1 痛点一：传染病上报（13 接口在推）缺少监控界面，且现有展示多处失真

1. **无专属监控页**：`implementation/apps/admin_web/src/router/menu.ts:16` 中“传染病上报”仅跳通用页 `/targets/inf_cdc_frontend`（`TargetDetail.vue`），未导入副本时直接空白显示 `暂无数据（该业务无摘要来源）`。
2. **即便导入后，首页和详情页仍显示 `0 已确认` 与全零趋势线，并长期挂黄条假告警**：
   - 传染病前置机 `result=true` 按规范映射为 `ACCEPTED`（`CONFIRMED=0`，`last_success_at=None`）；
   - `Dashboard.vue:53, 160`、`TargetDetail.vue:380-400` 与 `utils/dashboard.mjs:46-49, 78` 只展示和绘制 `CONFIRMED`，导致几十万条已受理记录在界面上显示 **`0 已确认`**、14 天趋势图永远是一条贴地 `0` 直线，且首页顶部永久提示 `尚无业务确认（CONFIRMED）记录`！
3. **台账点击详情抽屉完全空白**：`LedgerPane.vue:29-45` 只读平台 `Submission` 字段（`state/idempotency_key/attempts`），而传染病 `hooks.py:122-148` 返回 `external_record` 字段（`record_key/doc_type_name/source_op_time/last_error_code`），点开后全是空白或 `—`。
4. **外部状态导入器 4 处硬伤（`plugins/external_ledger/importers/inf_pusher.py`）**：
   - **8 小时时区错位（行 170, 224, 230）**：单一 `source_tz` 同时套用给容器 UTC 的 `receipts-*.jsonl`（`ts`）与源库 CST 的 `sent.jsonl`（`opTime`），无论选 UTC 还是 CST 必有一侧偏 8 小时！
   - **`parent-wait` 误判为 `REJECTED`（行 85-102）**：`classify()` 未处理 `stage == "parent-wait"`，全部落入 `REJECTED`，导致等待父表的正常子项污染拒绝率！
   - **`watermark.json` 逐行当 JSONL 解析全部报错 `NOT_JSON`（行 156, 238）**，且完全忽略 `last-run-real.json`、`reconcile-*.json` 与 `inf_watchdog/state/{status.json,alerts-*.jsonl,daily-*.txt}`。
   - **20MB 上传上限（行 59）**：真实放量日回执单文件达 855 万行（>1GB），浏览器 base64 上传根本无法承载；而 `docker-compose.yml:93, 162` 早已挂载 `/data/exchange`，却无流式只读增量同步器。
5. **13 表中有 7 张核心大表被塞进“其他/未分类”**：`plugins/domains/infectious/hooks.py:156, 205-213` 仍写死旧 6 表，将占总量 95% 以上的检验、检验明细、医嘱、医嘱明细、生命体征、入院记录、出院记录全部归入“其他/未分类”，且前端从头到尾未调用 `/classification` 接口。

### 1.2 痛点二：前端界面“很乱、不直观、乱糟糟”的根源定位

1. **侧栏 26 项菜单无序堆砌（`src/router/menu.ts:7-57`）**：
   - 9 个病理内部原型页（`/overview` 平台运行指标[Mock]、`/submissions` 发布与上报[Mock]、`/publications` 视图发布、`/reconciliations` 对账、`/quality` 质量队列、`/review` 关联复核、`/devices` 设备采集、`/files` 文件资产、`/storage` 存储管理）伪装成全局菜单，与新运营中心菜单混排（例如同时存在“上报驾驶舱”和“平台运行指标”，同时存在“提交与回执”和“发布与上报”）。
   - 侧栏折叠后（`AppLayout.vue:94-95`）因无任何图标，23 个菜单项变成纯空白蓝条；动态路由 `/targets/:code` 面包屑丢失父级与真实业务名。
   - **济南医保影像云前端完全失联**：`src/api/imaging.ts` 已写好 4 个 API 函数，但既未在 `src/api/index.ts` 导出，也无任何视图调用！
2. **三套视觉风格冲突 + 公共组件失灵**：
   - `ErrorState.vue:17` 检查 `slots.retry` 而非 `@retry` 事件监听（`onRetry`），导致**全站 20+ 处错误态的“重试”按钮永远不显示**！
   - `Alerts.vue:441-443` 对数组 `RULE_FORMS` 调 `Object.keys()`，导致新建告警规则时**“类型”下拉框显示 `0, 1, 2...8`，且参数输入框永远消失**！
   - `Workflows.vue` 未接入任何新公共组件，受 `components.css:37` 裸 `button` 全局蓝色样式影响，连每一行的 `✕` 删除按钮都渲染成刺眼的实心亮蓝主按钮；`Mapping.vue` 模式切换按钮分不清选中态；14 个视图使用了 `tokens.css` 未定义的 `--text-muted`。
   - `Mapping.vue`、`Pack.vue`、`Connectors.vue`、`Workflows.vue` 把列表、新建大表单、模拟面板、黑底 `<JsonViewer>` 全部纵向堆成一堵长墙，缺乏抽屉/页签渐进式披露。
3. **把 AI 任务书里的工程黑话原样抄进了医院用户界面**：
   - 按钮写着 `保存（revision 乐观锁）`（`TargetDetail.vue:190`、`Alerts.vue:142`）；
   - 标题/提示写着 `模拟（POST /api/v1/mappings/{key}/simulate；≤20 条；服务端试算非前端伪造）`（`Mapping.vue:125`）、`解析为 RFC4180 语义…非 split(',') 假解析`（`Metrics.vue:13`）、`（PUT 不夹带 enabled，绕过审计的路不存在）`（`Schedules.vue:169`）、`（服务端 NOT_ALLOWED_THIS_BATCH）`（`Alerts.vue:198`）；
   - `Pack.vue:102, 127` 在 HTML `<p>` 里直接裸露 Markdown 星号 `**创建新发布**`、`**不会恢复任何配置**`；
   - `Targets.vue:165` 把唯一推送方状态翻译成莫名其妙的 `成功 / 警告 / 异常`；`LedgerPane`、`Alerts`、`TargetDetail`、`Cases` 多处直接裸露 `ACCEPTED / PRODUCTION / EXTERNAL / CRITICAL` 英文代码。

### 1.3 痛点三：传染病推送稳定性（对照 `revision-2` 仍有 6 大未闭环风险）

1. **新父失败被历史旧父成功掩盖（R2 / F04 未修）**：`deploy/inf_pusher/pusher.py:364-370` 只查 `sent.jsonl` 历史曾发 ID；若同一父单今日更新后推送报 `04` 失败，旧账仍放行新子项带病上传！且 `run_table` 的 `parent_accept` 形参完全未读取（死参数），`tables.py:185` 中 `exam_item` 仍缺 `"parent_fk": "exClinicalId"`。
2. **等待父受理的子项越过水位后永久漏传（R3 / F06 未修）**：`parent_wait` 只记回执不进持久队列；同表后续新记录成功推进 `watermark.json`（且该文件非原子覆盖写！）超过 `overlap_days`（2 天）后，旧等待子项即使父表修复也再也不会被扫描！`run_reconcile` 也未统计和暴露 `parent_wait` 与 `skipped_invalid`。
3. **重放清单 `manifest` 缺严格校验与断点记录（R4 / F07 未修）**：未校验 `target/expires_at/max_count`/逐项源时间与摘要；同一 `force` 清单二跑会重复发 HTTP；目标 ID 缺失时静默退出 `0`。
4. **全量 `fetchall()` 内存风险与部署丢 `+x` 权限**：`pusher.py:271` 对百万行子表 `fetchall()` 一次装入内存；`deploy_8156_infpusher.py:59, 92` 默认 `644` 上传导致 `run.sh` 丢失可执行位。
5. **ODS 检验视图 `v1.5` 组合修复待落地（R1 / F01-F03, F08）**：门诊急诊 `activityTypeCode=2` 回退、科室码 `PERFORMED_BY` 冒充操作员 `operatorId`、定量 `H/L` 误映射耐药 `12/10`、以及 `V13` 候选计数粒度放大隐患需合并为干净的 `v1.5` SQL 修订包供 DBA 审阅执行。

### 1.4 痛点四：企业微信提醒存在漏报/超时隐患，且未打通平台统一通道

1. **`inf_watchdog` 现有企微链路 4 个隐患（`deploy/inf_watchdog/watchdog.py`）**：
   - **隧道闪断吞 1 小时告警（行 821-830）**：`dispatch()` 在 `driver_relay` 抛异常时仍执行 `dedupe["sent"][key] = now_ep`，导致一旦 SSH 隧道重连抖动一次，该严重告警整整 1 小时不再重发！
   - **历史回执全量扫描与 08:00 晨报超时隐患（行 380-385, 422, 893-905）**：计算出的 `min_ymd` 从未传给 `_receipt_files()`；且 `daily_report` 未开启预筛直接 `json.loads` 扫全历史千万行文件，并把 `parent-wait` 当成 `failed` 累加（虚高 96 倍）！
   - **恢复通知合并漏报与英文检查名（行 848-857）**：`c5_push_failures` 与 `c5_wait` 共用顶层恢复状态且恢复正文打印裸英文 `c5_push_failures`；`source_event_id` 哈希含动态数字文本导致防重键漂移；仓库 `.service` 与 `config.template.json` 未同步生产配置。
2. **平台侧 `blyb_platform` 完全不支持 `wechat-relay`**：`plugins/alerting/channels.py` 仅支持 `file/smtp/webhook` 且硬拦截外发；`evaluator.py` 在 `WARN → CRITICAL` 升级时不更新级别且被冷却期压制；缺少告警风暴聚合、夜间免打扰以及对 `inf_watchdog` 自身存活的交叉监控。

---

## 二、分阶段长任务路线图（6 个大阶段，每阶段预计 1.5～3 小时）

| 阶段 | 任务编号 | 任务名称 | 详细任务书 | 核心产出 |
| :--- | :--- | :--- | :--- | :--- |
| **第 1 阶段** | **`PT-01`** | **传染病推送内核加固与稳定性闭环** | [PT-01-传染病推送内核加固与稳定性闭环.md](PT-01-传染病推送内核加固与稳定性闭环.md) | `pusher.py` 父版本门禁 R2 + 持久等待队列 R3 + 严格重放 R4 + 流式取数 + `deploy` 保 `+x` + SQL `v1.5` 修订包；`evaluate_acceptance` **12/12 全绿** |
| **第 2 阶段** | **`PT-02`** | **企业微信提醒全链路升级与统一告警通道** | [PT-02-企业微信提醒升级与统一告警通道.md](PT-02-企业微信提醒升级与统一告警通道.md) | 修 `watchdog.py` 吞告警/晨报全扫/恢复漏报/四段式卡片/静默窗；平台新增 `wecom_relay` & `wecom_robot` 通道（含 `dry-run` 预览与 `401` 真达探针）+ `Alerts.vue` 修复升级 |
| **第 3 阶段** | **`PT-03`** | **传染病全维监控专页与生产只读观测接入** | [PT-03-传染病全维监控专页与只读观测接入.md](PT-03-传染病全维监控专页与只读观测接入.md) | 修导入器时区/`parent-wait`/多文件快照；新增流式尾读增量只读同步器与 5 个监控端点；新建 `/infectious` 专属五页签监控大屏（13 接口矩阵+`C1–C11` 守护+晨报+对账） |
| **第 4 阶段** | **`PT-04`** | **全站导航重组、视觉系统统一与乱象清零** | [PT-04-全站导航重组与视觉交互焕新.md](PT-04-全站导航重组与视觉交互焕新.md) | 26 项侧栏重组为 4 组清爽导航（8 个病理子页收纳进 `/pathology` 二级页签）+ SVG 图标 + 修 `ErrorState` 重试按钮 + 改造堆叠长墙页 + **全站工程黑话/裸英文清零** |
| **第 5 阶段** | **`PT-05`** | **通用多业务接入底座升级与影像云工作台激活** | [PT-05-多业务标准底座与影像云工作台激活.md](PT-05-多业务标准底座与影像云工作台激活.md) | 统一 `streams`/`health`/双口径 `summary` 钩子 + 补齐山东医保/指标钩子 + 死信重驱 + 通用契约沙箱 `ContractSandbox.vue`（激活济南影像云 16 数据集）+ 动态业务菜单与脚手架升级 |
| **第 6 阶段** | **`PT-06`** | **高仿真全量数据演练、UX 自动巡检守门与升级手册** | [PT-06-全栈高仿真演练与终验发布手册.md](PT-06-全栈高仿真演练与终验发布手册.md) | 本地高仿真多业务运行态种子脚本 + 前端全路由 UX 守门测试（黑话/星号/断链/空组件 0 容忍）+ 全栈 E2E 回归 + `<prod-host>`/DBA/平台受控升级与回滚手册 |

- **每日直接复制粘贴提示词**：见 [DAILY_PROMPTS.md](DAILY_PROMPTS.md)。

---

## 三、所有执行 AI 必须遵守的十条铁律（违者视为验收不通过）

1. **严禁擅自触碰线上生产环境**：所有阶段均在本地工作区 `F:\python\blyb` 开发与离线/回环测试，**严禁 SSH 登录 `<prod-host>` / `<relay-jump>` / `84`，严禁直连真实 HIS/LIS/PIS/ODS/前置机/企微生产地址发送真实请求**。生产部署统一按 `PT-06` 产出的升级手册由用户在维护窗口执行。
2. **严禁干扰正在上传的生产链路**：码上放心（`DrugTrace.vue`、`drugTrace.css`、`drugTraceDisplay.ts`、`plugins/destinations/alihealth_drugtrace/**`）保持原样零破坏；平台新增闸门和调度默认 `enabled=False`，不得阻断线上药品或传染病上传。
3. **零外部联网安装依赖**：严禁执行 `pip install`、`npm install`、`pnpm`、`yarn` 下载任何新包；100% 复用仓库现有 Python 标准库/已装依赖与前端内置 Vue3 + SVG + TypeScript。
4. **【新增铁律】严禁把任务书里的技术/审计黑话抄进前端界面！**：
   - 界面面向医院信息科运维与临床/公卫业务人员，所有标题、副标题、表单标签、按钮、空状态、提示条必须使用**自然、专业、简洁的中文业务表达**。
   - 严禁在 UI 中出现：`revision 乐观锁`、`服务端试算非前端伪造`、`NOT_ALLOWED_THIS_BATCH`、`RFC4180 语义非 split(',')`、`PUT 不夹带 enabled`、`POST /api/v1/...`、裸 Markdown `**星号**`、或 `编码 code *` 这类中英混杂 snake_case 标签！
5. **非重叠范围协作协议**：开工先跑 `python scripts/ai_session.py status`；若发现其他活跃会话认领了某目录（如 `数据上报/4、传染病病历数据上报`），不要覆盖其正在编辑的文件，可在 `docs/development/` 留同步说明并在交接中注明。
6. **数据库变更必须走编号迁移**：在 `implementation/packages/core_db/migrate.py` 中从 **`023`** 起递增编写同时兼容 SQLite 与 PostgreSQL 的幂等迁移，严禁修改 `001`～`022` 历史迁移。
7. **冻结包与 SQL 规范**：SQL 修订新建独立版本目录（如 `20261117-lab-linkage-v1.5/`），严禁原地篡改历史已冻结 SQL、Profile revision 或 `test_review_probes.py`、`revision-4-acceptance/evaluate_acceptance.py` 验收判定脚本。
8. **隐私脱敏零妥协**：企微告警、日志、外部导入台账、联调历史均严禁落盘或外发患者姓名、身份证号、手机号、详细住址或病历原文。
9. **真实挂载渲染测试**：前端改动必须补充基于内置 `@vue/compiler-sfc` + `vue/server-renderer` 的真实组件 SSR 挂载测试，严禁只写纯函数测试冒充 UI 验收。
10. **诚实交接与文档闭环**：每阶段结束必须实跑局部测试 + 全量回归（记录真实命令与退出码，区分“实测通过 / 本地环境 skip / 外部阻断”），更新四类实施文档、`STATE.md`、`BACKLOG.md`、`HANDOFF.md`，跑 `python scripts/project_docs.py inventory` 与 `check` 确认新增断链为 `0`，最后执行 `ai_session.py end`。
