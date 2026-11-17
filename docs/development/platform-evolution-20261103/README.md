# 平台演进：现状分析与长任务路线图（2026-11-03）

> 编制：Antigravity / claude-opus-5.5，任务 `PLATFORM-EVOLUTION-PLAN-20261103`。
> 性质：**分析 + 执行计划**，本目录只写文档，没有修改任何代码、部署或生产环境。
> 读者：①用户（决定执行顺序）；②每天执行一个长任务的外部 AI（下称"执行 AI"）。

---

## 2026-11-10 执行基线修订（先读）

本目录原始分析时间为11-03；以下旧现状与旧日期仅为历史背景。当前以[复核与执行补充](REVIEW-20261110.md)、[下一AI提示词](NEXT_AI_PROMPT.md)及共享STATE/HANDOFF为准。

- LT-01/02/03和LT-08已有交付，不重做；下一项为**LT-03-R1复用边界补强**，再按LT-04→05→06A/B→07→09推进。
- 保留药品与传染病生产链路；所有新调度含系统任务默认停用，镜像只能观测；file_default通道可启用，其他通知不外发。
- 核心迁移014与插件迁移020已存在，015—019仍是保留号，须验证后补迁移兼容。
- 本次仅修订计划，不代表新开发、全量回归或11-10生产核查已完成。完整补强约束见REVIEW的R01—R10，优先于任务书旧示例。

## 一、一句话结论

系统已经**真正跑起来了两个上报**（码上放心药品追溯、传染病前置机），但它们是**两种完全不同的形态**：
码上放心走平台插件+宿主驱动脚本，传染病是平台外的独立脚本+crontab+文件台账；医保电子病历一行代码都没有；
病理只在 Mock 里闭环。v0.4 设计的"通用内核 + 插件 + Profile + Pack"骨架**已经有了，但没被贯彻**：
核心里还写死着病理逻辑，没有"上报业务"这个一级概念，没有平台级调度、告警、统一台账，
前端 19 个页面里只有药品追溯一页能给业务人员用。

**改进主线**：先补"上报业务目录 + 统一推送/回执 + 调度告警"三块通用底座，再把传染病、医保电子病历按同一标准
挂进来，同时重做前端外壳和运营页面。全程**不动正在生产运行的链路**（新能力先 Mock/影子/默认停用），
生产切换另行由用户在场决定。

---

## 二、现状分析（证据来自代码与 STATE/BACKLOG，2026-11-03）

### 2.1 四个上报业务的真实形态

| 业务 | 目标平台 / 通道 | 代码位置 | 运行形态 | 状态 |
|---|---|---|---|---|
| 1 码上放心（药品追溯） | 阿里健康 TOP API，<prod-host>→DMZ <relay-net-host> mTLS 转发 | `implementation/plugins/domains/drug_trace/`、`destinations/alihealth_drugtrace/` | 平台插件 + **宿主 systemd 驱动脚本**（`docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`，不在 implementation 里） | 生产，仅门诊；住院/退药 fail-closed |
| 2 医保电子病历（山东） | 山东省医保平台 V2.0，网关/签名未核实 | **无** | — | 未开始（说明.txt：只需"调试通发送成功"） |
| 3 病理（医保病理云） | 协议 B09 未发布 | `domains/pathology/` 等 | 平台插件，仅 Mock / 视图发布 | 本地合成闭环，B01—B09 全 OPEN |
| 4 传染病（国家前置软件） | <frontend-host> HTTP JSON | `implementation/deploy/inf_pusher/`、`deploy/inf_watchdog/` | **平台外独立脚本**，cron + `state/*.jsonl` 文件台账，watchdog 只落文件 | 生产，住院 5 类文书 |
| （潜在）医保影像云 PACS | 济南医保影像云 V2.0 | 仅病理 chk_info/asset_info 投影 | — | 16 数据集/288 字段已提取，未实现 |

### 2.2 后端架构问题（按影响排序）

1. **没有"上报业务（ReportTarget）"一级实体**。现在只有 Destination（发往哪）和插件（怎么处理），
   回答不了"本院一共要报几个、各自负责人、唯一推送方、生命周期、今天报得怎么样"。
2. **插件注册写死**：`plugins/app_runtime.py` 的 `PLUGIN_MANIFESTS` 是硬编码字典；
   `PluginRegistry.build_runtime` 写死 `pathology.build_runtime`；`load_registry` 基线写死
   `profile.pathology_internal_v1`。`core_api/app.py`（2263 行、65 路由）约 62 处病理引用
   （overview、connector test、manual-decisions、publications、field-lineage）；
   `worker/engine.py` 硬编码 `pathology.*` 事件与 drug_trace 优先级。
3. **推送/回执不通用**：通用层只有 `destination.mock_http`、`destination.view_publication`；
   `core_handlers.register_destination_adapter` 已留了口子但只有药品追溯私用；回执查询/对账写在
   `drug_trace/service.py`；传染病回执是 jsonl 文件。
4. **没有平台调度器**：药品追溯靠宿主 timer+文档目录里的驱动，传染病靠宿主 crontab，worker 只有 0.2s 轮询。
   历史事故全部与此相关：systemd `%` 转义、cron 缺 PATH、容器 UTC 日界、collect_timeout、
   **取数挂起抱锁 71 小时无人知晓**。
5. **没有平台告警**：`alert_event` 表与 `/alerts` 接口存在，但无规则、无外发；inf_watchdog 自成一套且 file-only。
6. **字段映射/Profile 未落地**：`implementation/profiles/`、`hospital_packs/example`、`migrations/core` 都是空目录；
   取数 SQL 与报文拼装写死在各插件代码里。
7. **RBAC 粗**：6 个固定角色，没有"按上报业务授权"；前端菜单不按角色过滤；审计只能看最近 200 条。
8. 技术债：`plugin_registry.py` 旧兼容层与 `BLYB_PLUGINS` 双路径并存（未设变量时不加载 drug_trace）；
   `deploy/release/dist` 留有整份旧代码快照易误读；`connector_sdk` 数据类全是病理语义。

### 2.3 前端问题

- Vue 3.5 + vue-router + Vite，**无 UI 库、无状态管理、无图表、无组件目录（0 个复用组件）**；
  全局样式写在 `App.vue`；普遍 `any`，无统一加载/空态/错误处理，大量 `JSON.stringify` 直出。
- 19 个菜单平铺不分组、不按角色；无路由守卫。
- **只有 `DrugTrace.vue`（635 行，由脚本生成、已上线打磨）可给业务人员用**；传染病、医保电子病历零界面；
  病理页面面向 Mock。
- 缺：跨业务驾驶舱、上报业务中心、调度任务、告警中心、提交/回执检索、用户角色管理、审计检索导出、联调工具台。

### 2.4 反复出现、应产品化解决的运维痛点

| 痛点 | 历史证据 | 由哪个长任务解决 |
|---|---|---|
| 定时靠宿主 cron/timer，配置坑多 | %%s 转义、PATH、UTC 日界 | LT-04 调度 |
| 挂起/超时无人知晓 | 71h 抱锁、collect_timeout、心跳 11355s | LT-04 运行治理 + 告警 |
| UNKNOWN/ACCEPTED 积压需人工核查 | 205/206 UNKNOWN、12 轮耗尽 | LT-03 台账 + 处置 |
| 列表先 limit 后筛选漏异常 | submission-outcomes 200 条 | LT-03 统一台账（先筛后限） |
| 双上报风险 | 原厂家程序、crbsb | LT-01 唯一推送方登记 |
| 状态分散、看不见 | 传染病只有 jsonl | LT-05 外部台账导入 + LT-06 页面 |
| 授权/证书到期 | 发送授权 11-22 到期 | LT-04 到期类告警 + LT-06 待办 |
| 采集每单 ~11s 非查询开销 | DTR-FBI-VERIFY | LT-08 |

---

## 三、目标形态

```mermaid
flowchart TD
  subgraph Console["管理控制台（重做外壳+运营页）"]
    D1["驾驶舱"] --- D2["上报业务中心"] --- D3["调度/告警/台账"] --- D4["联调台/配置/系统"]
  end
  subgraph Core["通用内核"]
    RT["ReportTarget 上报业务目录"]
    SCH["Scheduler 调度+运行治理"]
    ALR["Alert 规则+通道+晨报"]
    LED["统一提交台账/回执分类"]
    DST["DestinationAdapter 协议"]
  end
  subgraph Plugins["业务插件（manifest 声明）"]
    P1["drug_trace 码上放心"]
    P2["infectious 传染病（先影子）"]
    P3["medins_emr 医保电子病历（联调）"]
    P4["pathology 病理"]
  end
  Console --> Core
  Core --> Plugins
  Plugins --> DST
  DST --> T1["alihealth"] & T2["generic_http_json"] & T3["mock_http"]
```

---

## 四、长任务清单（每个约一个执行日）

| 顺序 | 任务 | 依据 | 依赖/边界 |
| --- | --- | --- | --- |
| 已交付，保留 | LT-01/02/03、LT-08 | 共享交接与实际模块，见[复核表](REVIEW-20261110.md#1-结论与当前起点) | 不重复建骨架；历史实测不代表本次重跑 |
| 下一项 | LT-03-R1 通用复用补强 | REVIEW R02/R08、NEXT_AI_PROMPT | 仅新通用/Mock与只读统计，不改生产发送 |
| 后续1 | [LT-04 调度治理与告警](LT-04-调度运行治理与告警.md) | 任务书+R01/R03/R04/R05 | 新调度默认停用，镜像强制只观测 |
| 后续2 | [LT-05 传染病台账与影子](LT-05-传染病纳管.md) | 任务书+R06/R07 | 副本/合成、仅本机Mock |
| 后续3 | [LT-06A/B 安全后端与运营页](LT-06-运营页面.md) | 先A后端，再B页面，R08 | 未齐的关键范围校验阻断发布 |
| 后续4 | [LT-07 医保契约与联调台](LT-07-医保电子病历联调台.md) | 任务书+R09 | 不猜签名/编号，不调用真实医保 |
| 后续5 | [LT-09 脚手架与发布准备](LT-09-标准化收尾与发布准备.md) | 任务书+R10 | 仅离线材料齐备才构建，不部署 |

> [!IMPORTANT]
> 每个长任务**必须独立可交付**：当天做不完时按"停止条件"收尾为 `handed_off`，写清已完成/未完成，
> 第二天同一任务续做，**不要跳到下一个任务**。顺序可由用户调整，但依赖列必须满足。

执行AI从 [NEXT_AI_PROMPT.md](NEXT_AI_PROMPT.md) 的当前续做入口开始，不从旧“第1天”重跑。

---

## 五、所有长任务通用的执行通则（执行 AI 必读，每个 LT 文件都引用本节）

### 5.1 开工四步（每天都做）

```powershell
cd F:\python\blyb
# 1) 读约定与状态（不用全读历史，只读这几份 + 当天 LT 文件）
#    AGENTS.md, docs/ai/STATE.md 前 60 行, docs/ai/BACKLOG.md 前 40 行,
#    docs/development/platform-evolution-20261103/README.md（本文件）, 当天 LT-xx 文件
# 2) 看有没有别人占用你要改的文件
python scripts/ai_session.py status
# 3) 登记（TOOL/MODEL 写你真实的工具和模型，不知道就写 unknown）
python scripts/ai_session.py start --task LT-0X-YYYYMMDD --tool TOOL --model MODEL --scope <LT 文件"修改范围"里列的每个路径>
# 4) 跑一次基线测试并把结果写进交接文件（之后对比用）
cd implementation
python -m pytest tests -q -ra -p no:cacheprovider --junitxml=var/<任务ID>-baseline.xml
# 立即记录 $LASTEXITCODE，不用末尾输出管道掩盖pytest退出码。
```

基线已有失败时记录节点ID、原因和退出码，区分环境问题与相关缺陷；不为本任务顺手改无关逻辑。增量必须无新增失败；相关关键失败仍阻断可部署验收，不能用“原来就失败”放行。

### 5.2 全局红线（违反任何一条 = 任务失败）

1. **不连接任何真实系统**：不 SSH 到 <prod-host>/<relay-jump>/DMZ，不连 HIS/EMR/ODS/PIS Oracle 或 SQL Server，
   不向 <frontend-host>、阿里健康、山东医保发任何请求。需要外部数据时用合成数据或用户手工拷贝来的脱敏文件。
2. **不联网安装依赖**：不 `pip install`、不 `npm install <新包>`。只用 `requirements.txt` 已有包、
   Python 标准库、`package.json` 已有依赖。缺失依赖只阻断受影响验证，继续独立工作；不得自造签名、安全协议或字典来冒充缺失能力。
3. **禁改清单**（只读，可以参考、复制思路，不能改）：
   - `implementation/deploy/inf_pusher/**`、`implementation/deploy/inf_watchdog/**`（传染病生产代码）
   - `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py` 及同目录脚本（药品生产驱动）
   - `implementation/plugins/destinations/alihealth_drugtrace/**` 的发送/限速语义
   - `DrugTraceProductionEvidence` 发送门禁、药品追溯 `bill_type`/字典批准逻辑
   - 已登记迁移001—014及020的内容（含插件迁移）（只能新增编号迁移）
   - `设计交付v0.4/`、`归档/`、`数据上报/**` 下的原始规范/样例文件（可读，新文件另存）
   - `implementation/deploy/release/dist/**`（旧快照，不要读它当现行代码，也不要删）
4. **不删/弱化测试断言、不加 xfail、不改测试去适配错误行为**。
5. **新能力默认安全**：新调度含系统任务默认停用；镜像服务端OBSERVE_ONLY，不允许enable/run-now或改类型绕过。本批新增影子/联调只可访问本机Mock，不能仅靠is_mock或可编辑Target字段授权。ReportTarget新闸门只服务新通用路径，严禁挂入药品/传染病既有生产链路；真实切换另行授权。file_default通道启用不等于系统调度自动启用。
6. **不写密码/密钥/患者信息**到代码、文档、测试夹具。凭据只用"引用名"（如环境变量名 `INF_SMTP_PASSWORD`）。
   合成数据的人名用"测试患者001"、证件号用明显假的 `000000199001010000` 格式。
7. **病理不变量不能放松**：0/1/N 候选、前导零、患者/就诊分离、质量等待、配置 revision 固定、dry-run 零副作用、租户隔离。

### 5.3 迁移编号规则

截至11-10本地核实：核心登记014，药品插件另有020性能索引，插件与核心共用编号序列（病理2、药品9—12及20）。015—019保留给本系列；迁移执行器按已执行step集合逐项判断，不能把最大号020当作中间迁移均已执行。使用前核对全部插件登记及目标库历史：

| 编号 | 用途 | 任务 |
|---|---|---|
| 014 | report_target 上报业务目录（已实现） | LT-01 |
| 015 | schedule / schedule_run | LT-04 |
| 016 | alert_rule / notification_channel / notification_log + alert_event 扩列 | LT-04 |
| 017 | external_submission_record 外部台账 | LT-05 |
| 018 | console 账号角色范围（按业务授权） | LT-06 |
| 019 | debug_exchange 联调记录 | LT-07 |
| 020 | drug_trace_perf_indexes（已存在，不可占用） | 独立药品根修 |

```powershell
cd implementation
rg -n "@register\(|migration.*\.step\s*=|migration.*\.name\s*=" packages/core_db/migrate.py plugins/domains -g "*.py"
```

若保留号已被其他任务占用，不改旧编号；确认全注册表后再分配未占用新号，并同步所有任务书、测试和迁移对照表。每个迁移都要写 downgrade 或在注释说明为何只前进
（参照 012/013 的写法），并在 SQLite 与 PostgreSQL 方言下都可执行（避免 PG 专属语法；JSON 列用现有模型中的同类写法）。

### 5.4 测试与验收命令

```powershell
# 后端局部（每完成一步就跑相关的）
cd F:\python\blyb\implementation
python -m pytest tests/<你新增或相关的文件> -q -ra -p no:cacheprovider
# 后端全量（收尾必须跑，结果写进交接）
python -m pytest tests -q -ra -p no:cacheprovider --junitxml=var/<任务ID>-regression.xml
# 复核探针（收尾必须跑）
python -m pytest docs/audit/2026-10-01/test_review_probes.py -q -ra -p no:cacheprovider --junitxml=var/<任务ID>-probes.xml
# 前端（动了前端就必须跑）
cd apps/admin_web
npm run build
$testFiles = @(Get-ChildItem -LiteralPath tests -Recurse -File -Filter '*.test.mjs' | Sort-Object FullName | Select-Object -ExpandProperty FullName)
if ($testFiles.Count -eq 0) { throw '未找到前端测试' }
node --test $testFiles
if ($LASTEXITCODE -ne 0) { throw '前端测试失败' }
```

**完成报告必须分三类写**：实测通过（附命令+退出码+通过数）、实现未测（为什么没测）、外部阻断（等谁）。
没跑过的测试不能写"通过"；collect-only只证明收集。探针实际位于implementation/docs/audit，直接python执行pytest文件不代表运行了测试。Node22目录参数不可用，必须枚举测试文件。

### 5.5 收尾四步（每天都做）

1. 写交接文件 `docs/ai/sessions/<任务ID>.md`：改了哪些文件、新增迁移号、真实命令及结果、未完成项、下一步。
2. 更新 `docs/ai/STATE.md` 顶部加一节（≤10 行，带日期+任务ID+交接链接），`docs/ai/BACKLOG.md` 顶部加剩余项。
   **不要改写旧节**。`docs/ai/HANDOFF.md` 顶部更新"上一任务/下一步"。
3. 业务/API/状态变化同步四类实施文档：`implementation/docs/实施状态矩阵.md`、`04-API目录对照矩阵.md`、
   `接口与设计差异.md`、`测试执行记录.md`。涉及某上报业务的，同步 `数据上报/<业务>/README.md` 与 `数据上报/README.md` 索引。
   新增文档后运行 `python scripts/project_docs.py inventory`，再运行 `python scripts/project_docs.py check`，既有问题与本次新增问题分开。禁改文件用前后SHA256清单核查，mtime或git diff为空均不足以单独证明零修改。
4. `python scripts/ai_session.py end --session <ID> --result completed|handed_off --summary "..." --handoff docs/ai/sessions/<任务ID>.md`
   （没全部完成只能 `handed_off`）。

### 5.6 卡住时怎么办

- 同一错误反复出现时更换验证方式并记录依据，不绕过限制。仅阻断受影响步骤，继续独立工作；依赖无法满足时把该项和所需证据交接，不因固定重试次数而假装全部完成或放弃其他可做事项。
- 发现计划与代码实际不符（如函数名变了）：以代码为准，在交接文件"计划偏差"一节记录，继续。
- 需要用户决策的（如真实通道参数、是否切换生产）：记录到 BACKLOG"待用户"，用 Mock/默认停用继续完成其余部分。
