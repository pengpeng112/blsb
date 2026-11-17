# 第三阶段完善（PT-01 ～ PT-06）每日直接复制粘贴 AI 提示词清单

> **使用说明**：每天新建一个 AI 对话，将对应阶段的提示词代码块**整段复制粘贴**发送给执行 AI。每个阶段均为包含架构深改、专项自动化测试与全量回归的**深度长任务（预计耗时 1.5～3 小时）**。若某天因上下文或额度未全部跑完（会话标为 `handed_off`），次日复制最底部的【中断续做提示词】接着跑完同一阶段，再进入下一阶段。

---

## 第 1 阶段（Day 1）：PT-01 传染病推送内核加固与稳定性闭环

```text
你在 Windows 工作区 F:\python\blyb 中工作，这是一个医院数据归集与上报平台。
今天的任务是长任务 【PT-01：传染病推送内核加固与稳定性闭环（落实 revision-2 R1～R5）】。

请严格按以下顺序自主深入执行，不要每一步停下来问我，一直做到全部验收清单通过并规范收尾：

1. 必读文档（开工前完整阅读）：
   - 阅读 AGENTS.md（工作区共同约定与红线）。
   - 阅读第三阶段总规划 docs/development/platform-polish-and-stability-20261116/README.md 全文。
   - 阅读今日详细任务书 docs/development/platform-polish-and-stability-20261116/PT-01-传染病推送内核加固与稳定性闭环.md 全文。
   - 阅读独立复核依据 docs/development/inf-lab-linkage-review-20261115/revision-2/README.md 与 docs/development/inf-lab-linkage-review-20261115/revision-4-acceptance/README.md（及 collect_behavior.py、evaluate_acceptance.py）。

2. 会话登记与冲突检查：
   - 运行 python scripts/ai_session.py status 查看是否有其他活跃会话认领目录（注意不要覆盖其他活跃会话正在编辑的文件）。
   - 按 PT-01 的“1. 修改范围”运行 python scripts/ai_session.py start --task PT-01-20261117 --tool <你的真实工具名> --model <你的真实模型名或unknown> --scope ... 登记会话。
   - 记录禁改文件（evaluate_acceptance.py、test_review_probes.py、DrugTrace 三件套等）的初始 SHA256。

3. 按 PT-01 任务书步骤 1～5 逐项完整实现（不得跳过或简化任何一项）：
   - 步骤 1（ODS 视图 v1.5 组合修订包）：在 docs/development/inf-report-20261027/sql/revisions/20261117-lab-linkage-v1.5/ 下新建不可覆盖的组合修订 SQL 与说明，一次性合并修复 F01（急诊 activityTypeCode=2 不再退回 1）、F08（以报告完整键 PATIENT_ID+TEST_NO 计算候选就诊数，0/1/N 明确等待，严禁 3 行变 9 行膨胀）、F02（彻底移除把科室码 PERFORMED_BY 当员工号 operatorId 的错误兜底）、F03（一般定量 H/L 不再误映射为药敏耐药码 12/10），并在单测中用内存 SQLite 完整仿真验证这 4 类语义。
   - 步骤 2（统一父版本状态解析器 R2）：在 implementation/deploy/inf_pusher/tables.py 为 exam_item 补上 "parent_fk": "exClinicalId"；在 pusher.py 实现统一的 ParentAcceptanceTracker 供 main、单表、reconcile、manifest 共用（消灭死参数 parent_accept）：支持跨日有效已受理父复用，但当本班次或当日父记录更新推送失败（client-validate / 02 / 03 / 04 / 网络失败）时必须标为 FAILED_CURRENT 严格阻断子项发送，绝不允许被 sent.jsonl 旧成功账或 Mock 回执绕过！
   - 步骤 3（持久化等待队列 R3、原子写与流式取数）：把 watermark.json、pending-wait.json、last-run-*.json、reconcile-*.json 全部改为同目录临时文件 + fsync + os.replace 原子写；实现 PendingWaitStore（pending-wait.json，仅存表名/ID/父ID/源时间，零患者隐私），在每次扫描增量前先按 ID 定向重查待办项，确保等待项即使越过 watermark - overlap_days 及重启后在父恢复时仍能 100% 自动补回发送；在 reconcile-<D>.json 补齐 parent_wait 与 skipped_invalid 统计并写告警行；将 fetch_rows 的 fetchall() 升级为 fetchmany(2000) 分批读取防百万行内存暴涨。
   - 步骤 4（严格重放 manifest 预检与持久进度 R4）：所有执行分支前统一校验 manifest schema（manifest_id、target、source_instance、table、expires_at 过期拦截、max_count、逐行 expected_source_time 与 expected_digest 漂移拦截，不依赖 CLI 是否传 --since）；未加 --apply 时 100% 零副作用；按 ID 定向核对并对缺失目标明确报错非零退出；用 manifest-state-<id>.json 持久化逐项完成进度，确保同一 force 清单二跑对已成功项零重复 HTTP 请求！
   - 步骤 5（部署脚本保 +x 权限与影子插件同步）：修复 deploy_8156_infpusher.py 对 *.sh（run.sh）使用 mode="755"；同步更新 implementation/plugins/domains/infectious/ 下的 tables.py 与 send.py 子表父门禁。

4. 硬性红线：
   - 严禁运行任何脚本 SSH 连接线上 <prod-host> 或真实 ODS/前置机；严禁修改 evaluate_acceptance.py 或历史冻结 SQL；严禁 pip/npm install。

5. 验收与收尾：
   - 运行 implementation/deploy/inf_pusher/selftest.py（退出码 0）。
   - 验证 revision-4-acceptance 的全部 12 个独立行为场景在当前修复后代码上达成 **12/12 全部 PASS**！
   - 运行新增专项单测 pytest tests/unit/test_pt01_inf_pusher_reliability.py（≥25 项全绿）、复核探针 test_review_probes.py（43/43 passed）与后端全量回归（0 代码级 failed, 0 error）。
   - 写交接 docs/ai/sessions/PT-01-20261117.md，更新 STATE.md、BACKLOG.md、HANDOFF.md，跑 python scripts/project_docs.py inventory 与 check（新增断链 0），最后执行 ai_session.py end。
```

---

## 第 2 阶段（Day 2）：PT-02 企业微信提醒全链路升级与统一告警通道

```text
你在 Windows 工作区 F:\python\blyb 中工作，这是一个医院数据归集与上报平台。
今天的任务是长任务 【PT-02：企业微信提醒全链路升级（守护进程告警修复 + 平台级 wecom_relay 统一通道）】。

请严格按以下顺序自主深入执行，不要每一步停下来问我，一直做到全部验收清单通过并规范收尾：

1. 必读文档：
   - 阅读 AGENTS.md。
   - 阅读 docs/development/platform-polish-and-stability-20261116/README.md 全文（特别注意铁律第 4 条：严禁将工程黑话写入前端 UI！）。
   - 阅读今日详细任务书 docs/development/platform-polish-and-stability-20261116/PT-02-企业微信提醒升级与统一告警通道.md 全文。
   - 只读参考 数据上报/4、传染病病历数据上报/interfaces/21-企微告警通道.md 与 docs/ai/sessions/INF-ROOTCAUSE-20261116.md 了解院内 wechat-relay 协议与隧道机制。

2. 会话登记：
   - 运行 python scripts/ai_session.py status 检查活跃范围。
   - 按 PT-02 的“1. 修改范围”运行 python scripts/ai_session.py start --task PT-02-20261118 --tool <你的真实工具名> --model <你的真实模型名或unknown> --scope ... 登记会话。

3. 按 PT-02 任务书步骤 1～3 逐项完整实现：
   - 步骤 1（深修 deploy/inf_watchdog/watchdog.py 企微告警）：
     * 修复 iter_receipts 中 min_ymd 计算后未传给 _receipt_files() 导致全盘扫历史大文件的 Bug；重写 daily_report 回执统计，流式快速区分 sent / real_failed / parent_wait(按 table,id 去重，绝不再计入 failed 造成 96 倍虚高！) / client_validate，杜绝 08:00 晨报扫数百万行超时！
     * 修复 dispatch() 在外部 relay 驱动抛异常（如隧道瞬断重连）时仍写入 dedupe["sent"][key] 静默吞掉 1 小时告警的致命 Bug（仅外部驱动真实送达才记 dedupe["sent"]）！
     * 将 dedupe["levels"] 细化到独立告警子键（区分 c5_push_failures 与 c5_wait 独立恢复通知），恢复正文全中文化并附带故障持续时长；支持 WARN→CRITICAL 升级穿透冷却立即通知；将 _relay_payload 的 source_event_id 改为基于 (source_id, dedupe_key, level, hour_bucket) 哈希，支持 429/503 Retry-After 短退避重试。
     * 将企微告警正文升级为清晰的【异常现象 / 涉及文书 / 持续时长 / 处置建议】四段式排版（严守 D5 白名单零患者信息），晨报升级为【晨报】标题并补齐 13 表完整度与水位概览；支持可选夜间免打扰 quiet_hours（夜间静默 WARN，CRITICAL 仍 24h 即时推送）；新增 c11_relay_tunnel 401 应用层真达自检写入 status.json；同步仓库 .service（TimeoutStartSec=12min、EnvironmentFile）与 config.template.json。
   - 步骤 2（平台 blyb_platform 一等公民企业微信通道与告警引擎升级）：
     * 在 implementation/plugins/alerting/channels.py 新增 wecom_relay（院内统一消息平台 /api/v2/events，字节级一致 HMAC-SHA256 签名，仅允许 url_env、secret_env、source_id 等环境变量引用，零明文入库，无 receivers）与 wecom_robot（群机器人 Markdown/Text，校验响应 JSON errcode==0）。
     * 支持通道测试 dry_run=true（零网络请求生成完整中文企微卡片预览与签名摘要）与 probe=true（无签名空包校验 HTTP 401 真达而不打扰收件人）；真实外发由环境变量 BLYB_ALLOW_EXTERNAL_NOTIFY=1 门控。
     * 升级 evaluator.py / rules.py / redact.py：淘汰裸键值串，输出自然中文告警卡片；修复 WARN→CRITICAL 不更新级别且被冷却压制的 Bug；新增告警风暴聚合（单轮 >5 条合并为摘要卡）与夜间免打扰；修复 NO_SUCCESS_WITHIN 兼容 ACCEPTED 业务（消除传染病永久假告警），新增 WATERMARK_STALE、PARENT_WAIT_OVER、WATCHDOG_STALE（交叉监控 inf_watchdog status.json 停摆）规则！
   - 步骤 3（修复并升级前端 Alerts.vue）：
     * 修复 Alerts.vue 对数组 RULE_FORMS 调 Object.keys() 导致规则类型下拉显示 0..8 且参数输入框消失的 P0 Bug！
     * 通道表格用清爽中文摘要替代黑底 JsonViewer；新建/编辑抽屉完整支持 wecom_relay 与 wecom_robot 表单，新增「预览卡片」弹窗与「连通性探测」交互；清理本页全部裸英文枚举与工程黑话。

4. 硬性红线：
   - 所有测试均连接本机临时启动的 Mock HTTP Server，严禁连接真实生产隧道或企微服务器；严禁将工程黑话写入 UI 文案；严禁 pip/npm install。

5. 验收与收尾：
   - 运行 implementation/deploy/inf_watchdog/selftest.py（退出码 0）。
   - 运行后端新增测试 pytest tests/unit/test_pt02_wecom_and_alerting.py（≥25 项全绿）、复核探针（43/43）、后端全量回归（0 代码级 failed, 0 error）。
   - 运行前端 npm run build（exit 0）与全部 *.test.mjs（含 pt02_alerts_wecom.test.mjs 全绿）。
   - 更新四类实施文档、STATE/BACKLOG/HANDOFF，写交接 docs/ai/sessions/PT-02-20261118.md，跑 project_docs.py inventory 与 check（新增断链 0），最后执行 ai_session.py end。
```

---

## 第 3 阶段（Day 3）：PT-03 传染病上报全维监控专页与生产只读观测接入

```text
你在 Windows 工作区 F:\python\blyb 中工作，这是一个医院数据归集与上报平台。
今天的任务是长任务 【PT-03：传染病上报全维监控专页与生产只读观测接入】。

请严格按以下顺序自主深入执行，不要每一步停下来问我，一直做到全部验收清单通过并规范收尾：

1. 必读文档：
   - 阅读 AGENTS.md。
   - 阅读 docs/development/platform-polish-and-stability-20261116/README.md 全文（牢记铁律第 4 条：UI 必须全是自然专业的医院中文表达，零工程黑话！）。
   - 阅读今日详细任务书 docs/development/platform-polish-and-stability-20261116/PT-03-传染病全维监控专页与只读观测接入.md 全文。
   - 只读阅读 implementation/apps/admin_web/src/views/DrugTrace.vue 体会优秀的业务专属监控座舱布局（注意严禁修改 DrugTrace 三件套！）。

2. 会话登记：
   - 运行 python scripts/ai_session.py status 确认无冲突。
   - 按 PT-03 的“1. 修改范围”运行 python scripts/ai_session.py start --task PT-03-20261119 --tool <你的真实工具名> --model <你的真实模型名或unknown> --scope ... 登记会话。

3. 按 PT-03 任务书步骤 1～3 逐项完整实现：
   - 步骤 1（修复外部状态导入器 3 大硬伤并补齐 13 表完整映射）：
     * 修复 plugins/external_ledger/importers/inf_pusher.py 单一 source_tz 导致 receipts（容器 UTC）与 sent.jsonl/watermark.json（源端 CST）必差 8 小时的 Bug，支持标准混合口径（receipts=UTC, sent/watermark=CST）并设为前端默认推荐项！
     * 修复 classify() 把 stage=="parent-wait" 误归入 REJECTED 被拒桶的严重失真，单列 PARENT_WAIT（等待父表受理）！
     * 补齐全部 13 张传染病上报接口的准确中文名称、端点与主子依赖映射（住院病案首页、入院记录、出院记录、死亡记录、首次病程、日常病程、检查报告、检查项目、检验报告、检验明细、医嘱主表、医嘱明细、生命体征），让 hooks.classification() 完整返回 13 张表，彻底消灭把 95% 数据塞进“其他/未分类”的黑洞！
   - 步骤 2（实现多快照解析、GB 级目录流式只读增量同步器与 5 个监控端点）：
     * 新增幂等编号迁移（SQLite + PG 双兼容）建立 infectious_table_telemetry、infectious_watchdog_snapshot 与 external_file_cursor 表。
     * 支持完整解析 watermark.json、last-run-real.json、reconcile-YYYYMMDD.json、watchdog/status.json（C1–C11）、daily-YYYYMMDD.txt、alerts-YYYYMMDD.jsonl（全部脱敏白名单，零患者字段）。
     * 实现 monitor_sync.py：对配置的只读挂载目录（BLYB_INF_PUSHER_STATE_DIR / BLYB_INF_WATCHDOG_STATE_DIR）按文件字节偏移流式增量尾读（100% O_RDONLY 零写源目录，内存恒定 <30MB，兼容轮转）。
     * 在 plugins/domains/infectious/routes.py 挂载带 target_scopes 鉴权的 5 个端点：/api/v1/infectious/monitor/{overview,tables,watchdog,reconcile,sync}。
   - 步骤 3（打造 /infectious 传染病专属五页签监控大屏 & 修复驾驶舱/台账展示）：
     * 修复 Dashboard.vue、TargetDetail.vue 与 utils/dashboard.mjs 对 ACCEPTED 受理型业务显示“0 已确认”、趋势图贴地为 0 以及顶部误报“尚无业务确认记录”的问题；修复 LedgerPane.vue 点开 external_record 抽屉一片空白的 Bug（完整展示文书名、源时间、错误码中文诊断）。
     * 新建 src/views/InfectiousMonitor.vue（路由 /infectious，侧栏“传染病上报”直达！），精心实现五大页签：①「运行总览」（5 张可钻取指标卡 + 14 天趋势 + 错误码中文分布 + 右侧关注待办与班次摘要）；②「13 接口全景矩阵」（按 6 大临床域分组展示 13 表水位、滞后健康条、本班扫/发/等父/尾差/失败与一键查明细）；③「守护体检与企微晨报」（C1–C11 状态灯卡片 + 告警流水 + 08:00 晨报阅读器）；④「提交回执明细」（按 13 类文书与错误阶段筛选）；⑤「对账与数据接入」（日终对账 + 影子 7 类差异对比 + 改进版副本导入）。

4. 硬性红线：
   - 严禁修改 DrugTrace 三件套；对外部状态目录必须 100% 只读零副作用；UI 零工程黑话；严禁 pip/npm install。

5. 验收与收尾：
   - 运行后端新增测试 pytest tests/integration/test_pt03_infectious_monitor.py（≥25 项全绿）、复核探针（43/43）、后端全量回归（0 代码级 failed, 0 error）。
   - 运行前端 npm run build（exit 0）、全部 *.test.mjs（含 pt03_infectious_monitor.test.mjs 5 页签真实 SSR 断言全绿）及路由 AST 审计（missing=0）。
   - 更新四类实施文档、STATE/BACKLOG/HANDOFF，写交接 docs/ai/sessions/PT-03-20261119.md，跑 project_docs.py inventory 与 check（新增断链 0），最后执行 ai_session.py end。
```

---

## 第 4 阶段（Day 4）：PT-04 全站导航信息架构重组、视觉设计系统统一与乱象清零

```text
你在 Windows 工作区 F:\python\blyb 中工作，这是一个医院数据归集与上报平台。
今天的任务是长任务 【PT-04：全站导航信息架构重组、视觉设计系统统一与乱象清零】。

请严格按以下顺序自主深入执行，不要每一步停下来问我，一直做到全部验收清单通过并规范收尾：

1. 必读文档：
   - 阅读 AGENTS.md。
   - 阅读 docs/development/platform-polish-and-stability-20261116/README.md 全文。
   - 阅读今日详细任务书 docs/development/platform-polish-and-stability-20261116/PT-04-全站导航重组与视觉交互焕新.md 全文（逐行对照其中的菜单表与文案整改对照表！）。

2. 会话登记：
   - 运行 python scripts/ai_session.py status 确认无冲突。
   - 按 PT-04 的“1. 修改范围”运行 python scripts/ai_session.py start --task PT-04-20261120 --tool <你的真实工具名> --model <你的真实模型名或unknown> --scope ... 登记会话。
   - 记录 DrugTrace.vue、drugTrace.css、drugTraceDisplay.ts、package.json 的初始 SHA256。

3. 按 PT-04 任务书步骤 1～3 逐项彻底重塑前端体验：
   - 步骤 1（重构侧栏导航信息架构 + 病理工作台收拢 + SVG 图标 + 面包屑）：
     * 将 menu.ts 原本杂乱平铺的 26 项精简重组为清晰的 4 组核心导航：「监控总览」（全院上报驾驶舱）、「医院上报业务」（上报业务总表 / 传染病上报 / 码上放心追溯 / 山东医保病案 / 济南医保影像云 / 医保病理云）、「运行与告警」（提交与回执台账 / 定时调度任务 / 企微与告警中心）、「平台配置与系统」（支持分组折叠：数据源连接器 / 字段映射与转换 / 上报流程编排 / 字典与发布包 / 账号与权限范围 / 安全审计日志）。
     * 新建 PathologyHub.vue（路由 /pathology），将原本散落在侧栏的 8 个病理子页面（Cases/Quality/Review/Submissions/Publications/Devices/Files/Storage）优雅整合进病理工作台二级页签，同时 100% 保留全部原路径路由兼容与返回导航条！删除无引用的死文件 Audit.vue。
     * 新建轻量线性 SVG 图标组件 MenuIcon.vue，修复侧栏折叠到 56px 后变成无图标空白蓝条的缺陷；修复 /targets/:code 等动态路由的面包屑显示真实业务中文名。
   - 步骤 2（修公共组件失灵、统一全站视觉规范与渐进式披露布局）：
     * 修复 ErrorState.vue 只检查 slots.retry 导致全站 20+ 处 @retry 错误页完全不渲染“重试”按钮的 P0 交互 Bug！
     * 在 tokens.css 补齐 --text / --text-muted 等令牌，为 .app-content 增加舒适居中最大宽度，规范按钮层级（主按钮、次按钮、危险按钮、高对比度分段切换器），消除裸 button 导致删除/辅助按钮全变刺眼亮蓝块的问题。
     * 为连接器/映射/编排/配置包四页加上统一的四步流程引导条 StepGuideBar.vue；将 Connectors.vue（新建移入抽屉）、Mapping.vue（三页签）、Pack.vue（三页签）、Workflows.vue（全面接入公共组件与三页签+运行诊断抽屉）从纵向堆叠大长墙升级为清爽的渐进式披露界面！
   - 步骤 3（全站“人话化”大扫除）：
     * 修复 Pack.vue 中的裸 **...** 星号；修复 Targets.vue 唯一推送方标签为“已确认唯一 / 待核实 / 双推并行中”；
     * 将全站所有筛选下拉、表格状态列、环形图图例中的裸英文枚举全部替换为标准中文展示；
     * 彻底清除全站所有按钮、标题、副标题、提示条里的工程/审计黑话（如“revision 乐观锁”、“服务端试算非前端伪造”、“NOT_ALLOWED_THIS_BATCH”、“非 split(',') 假解析”、“绕过审计的路不存在”、中英混杂 snake_case 表单标签等），改写为自然清晰的医院业务中文！

4. 硬性红线：
   - 严禁修改 DrugTrace 三件套与 package.json；严禁删除既有路由路径（旧书签必须全部可访问）；严禁 npm install。

5. 验收与收尾：
   - 运行 npm run build（exit 0）与全部 *.test.mjs（新增 pt04_navigation_and_ux.test.mjs ≥25 项断言全绿，含黑话与裸星号 0 命中扫描）。
   - 运行复核探针（43/43）与路由审计（missing=0），核对禁改文件 SHA256 一致。
   - 更新四类实施文档、STATE/BACKLOG/HANDOFF，写交接 docs/ai/sessions/PT-04-20261120.md，跑 project_docs.py inventory 与 check（新增断链 0），最后执行 ai_session.py end。
```

---

## 第 5 阶段（Day 5）：PT-05 通用多业务接入底座升级与济南医保影像云工作台激活

```text
你在 Windows 工作区 F:\python\blyb 中工作，这是一个医院数据归集与上报平台。
今天的任务是长任务 【PT-05：通用多业务接入底座升级与济南医保影像云工作台激活】。

请严格按以下顺序自主深入执行，不要每一步停下来问我，一直做到全部验收清单通过并规范收尾：

1. 必读文档：
   - 阅读 AGENTS.md。
   - 阅读 docs/development/platform-polish-and-stability-20261116/README.md 全文。
   - 阅读今日详细任务书 docs/development/platform-polish-and-stability-20261116/PT-05-多业务标准底座与影像云工作台激活.md 全文。

2. 会话登记：
   - 运行 python scripts/ai_session.py status 确认无冲突。
   - 按 PT-05 的“1. 修改范围”运行 python scripts/ai_session.py start --task PT-05-20261121 --tool <你的真实工具名> --model <你的真实模型名或unknown> --scope ... 登记会话。

3. 按 PT-05 任务书步骤 1～3 逐项完整实现：
   - 步骤 1（建立统一的三大多业务标准遥测钩子）：
     * 扩展 TargetSummary 支持 primary_bucket（CONFIRMED / ACCEPTED）与 last_accepted_at；新建 plugins/domains/medins_emr/hooks.py 补齐山东医保摘要钩子（消灭驾驶舱“暂无数据”灰卡）。
     * 新增 HOOK_TARGET_STREAMS 与 GET /api/v1/report-targets/{code}/streams（统一输出各业务下的子表/交易/数据集矩阵、字段数、水位、滞后与计数），为传染病（13 表）、山东医保（23 交易）、济南影像云（16 数据集，补齐全部 16 个数据集准确中文名！）、药品追溯（只读单据分类）、病理云全部接通！
     * 新增 HOOK_TARGET_HEALTH 与 GET /api/v1/report-targets/{code}/health（统一输出生命周期、唯一推送方、目的地配置、调度、未关告警及业务专属体检项）。
   - 步骤 2（补齐 RESEND_ALLOWED 受控重发与死信队列管理闭环）：
     * 新增 POST /api/v1/report-center/ledger/{target}/{submission_id}/requeue（校验范围、角色、必填理由与生产安全闸门，完成人工允许重发后的受控投递与审计闭环）。
     * 新增死信任务查看与受控重驱接口 GET /api/v1/ops/dead-letter-jobs 与 POST .../redrive，并在 Schedules.vue 增加「异常死信队列」管理页签。
   - 步骤 3（激活济南医保影像云前端工作台 & 升级通用座舱与一键脚手架）：
     * 在 src/api/index.ts 正式导出此前孤立失联的 src/api/imaging.ts；新建通用契约沙箱组件 ContractSandbox.vue（左侧 16 数据集中文列表搜索 + 中间合成样例载入与 JSON 编辑 + 右侧 288 字段契约树搜索、校验结果与本地 Mock 投影闭环）以及通用 StreamsPane.vue、HealthPane.vue。
     * 将 TargetDetail.vue 升级为按能力自动装配的七页签标准业务座舱（概况 / 子表与数据集 / 健康体检 / 联调与契约沙箱 / 提交台账 / 调度与告警 / 业务档案与配置）。
     * 在 AppLayout.vue 支持将 /api/v1/report-targets 中新增的已授权业务动态合并追加到侧栏「医院上报业务」菜单；同步升级 scripts/new_report_target.py 及其模板，使后续新接入的第 6、第 7 个医院上报项目只需 1 条命令即自动拥有侧栏菜单、汇总卡片、子表进度、健康体检与契约沙箱全套界面！

4. 硬性红线：
   - 严禁修改 DrugTrace 三件套与 alihealth_drugtrace；严禁放宽 assert_target_can_send_real 或 Mock 目的地校验；UI 零工程黑话；严禁 pip/npm install。

5. 验收与收尾：
   - 运行后端新增测试 pytest tests/integration/test_pt05_multi_target_platform.py（≥25 项全绿，含用脚手架创建第 6 个新业务端到端验证）、复核探针（43/43）、后端全量回归（0 代码级 failed, 0 error）。
   - 运行前端 npm run build（exit 0）、全部 *.test.mjs（含 pt05_imaging_and_sandbox.test.mjs 全绿）及路由 AST 审计（missing=0, unresolved=0）。
   - 更新相关业务 README.md、四类实施文档、STATE/BACKLOG/HANDOFF，写交接 docs/ai/sessions/PT-05-20261121.md，跑 project_docs.py inventory 与 check（新增断链 0），最后执行 ai_session.py end。
```

---

## 第 6 阶段（Day 6）：PT-06 全栈高仿真数据演练、UX 自动巡检守门与生产升级手册收官

```text
你在 Windows 工作区 F:\python\blyb 中工作，这是一个医院数据归集与上报平台。
今天的任务是第三阶段收官大考 【PT-06：全栈高仿真数据演练、UX 自动巡检守门与生产受控升级手册】。

请严格按以下顺序自主深入执行，不要每一步停下来问我，一直做到全部验收清单通过并规范收尾：

1. 必读文档：
   - 阅读 AGENTS.md。
   - 阅读 docs/development/platform-polish-and-stability-20261116/README.md 全文。
   - 阅读今日详细任务书 docs/development/platform-polish-and-stability-20261116/PT-06-全栈高仿真演练与终验发布手册.md 全文及 PT-01～PT-05 的交接记录。

2. 会话登记：
   - 运行 python scripts/ai_session.py status 确认无冲突。
   - 按 PT-06 的“1. 修改范围”运行 python scripts/ai_session.py start --task PT-06-20261122 --tool <你的真实工具名> --model <你的真实模型名或unknown> --scope ... 登记会话。

3. 按 PT-06 任务书步骤 1～3 逐项完整落实：
   - 步骤 1（编写本地一键高仿真全景演示灌数脚本 scripts/seed_local_showcase.py）：
     * 第一防线强制校验 --confirm-local-demo 且拦截任何非本机数据库连接或 production 环境（退出码 2）。
     * 一键幂等生成全套逼真合成运行态（含传染病 13 表水位/班次/parent_wait/回执/C1–C11 守护状态/08:00 晨报同步入库、山东医保 23 交易联调记录、济南影像云 16 数据集投影记录、企微中继通道配置与告警/恢复样例、调度运行历史），让用户在本地打开浏览器即可直观体验全部监控大屏效果！
   - 步骤 2（编写全站 UX 与代码洁净度终极守门套件 pt06_ux_quality_gate.test.mjs）：
     * 静态扫描全站 .vue 视图与组件：锁死 0 处裸 Markdown ** 星号、0 处禁用工程黑话词表、0 处孤立未引用 API 文件。
     * 注入高仿真 Mock 数据，对全站所有注册路由视图逐一执行真实 Vue SSR renderToString 挂载断言：0 异常、侧栏折叠必含 SVG 图标、ErrorState 必渲染重试按钮、Alerts 规则下拉必含真实枚举、InfectiousMonitor 必渲染全部 13 个中文文书名与 C1–C11 守护项、ContractSandbox 必渲染济南影像云 16 个数据集！发现任何细节瑕疵当场修复。
   - 步骤 3（编写跨栈端到端验收测试 + 分组件生产受控升级手册 + 终验总报告）：
     * 编写 tests/e2e/test_pt06_phase3_end_to_end.py，在全新临时库串联验证传染病只读同步与 5 大监控端点、企业微信 wecom_relay 预览/401 探针/HMAC 签名投递/升级/恢复全链、多业务 streams/health 与影像云投影及死信重驱闭环。
     * 编写 docs/development/platform-polish-and-stability-20261116/DEPLOY_UPGRADE_RUNBOOK.md（拆分为 ODS 视图 v1.5、<prod-host> inf_pusher、<prod-host> inf_watchdog 企微、管理平台与只读挂载 4 个独立可灰度可秒级回滚的操作包）与 PHASE3_ACCEPTANCE_REPORT.md。

4. 硬性红线：
   - 严禁 SSH 连接任何生产服务器或触碰真实源库；严禁修改 DrugTrace 三件套、alihealth_drugtrace、test_review_probes.py、evaluate_acceptance.py。

5. 终验全部硬指标与收官：
   - 实跑 implementation/deploy/inf_pusher/selftest.py（exit 0）+ revision-4-acceptance 12/12 全绿 + implementation/deploy/inf_watchdog/selftest.py（exit 0）。
   - 实跑后端全量回归 python -m pytest tests -q -ra -p no:cacheprovider --junitxml=var/PT-06-final-regression.xml（0 代码级 failed, 0 error）与复核探针 test_review_probes.py（43/43 passed）。
   - 实跑前端 npm run build（exit 0）、全部 *.test.mjs（100% passed）与路由 AST 审计（missing=0, unresolved=0）。
   - 实跑 seed_local_showcase.py 本地临时库演练（exit 0）及非本机拦截测试（exit 2）。
   - 核对全部禁改文件 SHA256 一致，更新四类实施文档、STATE/BACKLOG/HANDOFF，写交接 docs/ai/sessions/PT-06-20261122.md，跑 project_docs.py inventory 与 check（新增断链 0），最后执行 ai_session.py end --result completed 圆满收官！
```

---

## 备用：若某阶段当天未跑完（状态标为 `handed_off`），次日复制这段续做

```text
你在 Windows 工作区 F:\python\blyb 中工作。上一会话执行的第三阶段长任务 【填任务编号，如 PT-03】 尚未全部完成（状态为 handed_off）。
今天请继续完成该阶段，不要跳到下一阶段：

1. 阅读 AGENTS.md、docs/development/platform-polish-and-stability-20261116/README.md 以及该阶段详细任务书 docs/development/platform-polish-and-stability-20261116/【填任务编号】-*.md。
2. 阅读上一会话的交接文件 docs/ai/sessions/【填上次交接文件名】.md，查看已完成部分与剩余待办，从断点继续完成剩余代码与全部测试，不要推翻已通过测试的成果。
3. 用 python scripts/ai_session.py start 登记续做会话，严格遵守“零工程黑话进 UI、零触碰生产、零安装外部新包”红线，跑通该阶段全部必达验收标准（含后端回归、探针 43/43、前端 build + SSR 测试全绿）。
4. 全部通过后写今日交接文件、更新 STATE/BACKLOG/HANDOFF 与四类实施文档、跑 python scripts/project_docs.py inventory 与 check（新增断链 0），执行 python scripts/ai_session.py end --result completed 结束会话。
```
