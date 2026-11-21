# BLYB · 项目历史升级需求演进总览

> 编制日期：`2026-11-21`。本文件归纳医院数据归集与上报平台（BLYB）自启动以来各阶段的升级需求、架构演进与归档证据，方便人类与多 AI 快速理解“系统为何长成今天这样”，而不必通读上千行历史流水。

---

## 演进时间线（6 大阶段）

### 阶段 0：顶层设计与基线确立（2026-09-30 ~ 2026-10-02）
- **升级需求**：构建医院统一数据归集与上报平台，拒绝每个上报项目重造轮子；确立「通用内核 + 业务插件 + 双模式交换」架构，以**医保病理云索引**为首个试点业务。
- **关键产出**：
  - 现行总体基线：[医院数据归集与上报平台总体方案 v0.4](../医院数据归集与上报平台总体方案_v0.4.md) 与 [设计交付 v0.4](../设计交付v0.4/00_阅读入口与开发约束.md)。
  - 真实库初审原件：[AI复核报告_v0.3设计与真实库核验_2026-09-30.md](../AI复核报告_v0.3设计与真实库核验_2026-09-30.md)（哈希锁定原位保留）。
  - 历史归档：[归档/2026-09-30](../归档/2026-09-30/README.md)（v0.1/v0.2 方案）、[归档/2026-10-02](../归档/2026-10-02/README.md)（v0.3 方案与旧提示词）、[设计交付v0.3](../设计交付v0.3/README.md)。

### 阶段 1：MVP 落地、独立复核与第 4 轮内网部署（2026-10-01 ~ 2026-10-15）
- **升级需求**：跑通从扫描采集（Windows Edge Agent）→ 分片对象存储（Storage）→ 业务 API/Worker → 双模式交换（HTTP Mock + 视图发布）→ Vue3 控制台全链路；建立 43 项独立审计探针与内网单机部署脚本。
- **关键产出**：
  - 唯一源码根：`implementation/`（`apps/packages/plugins/tests/deploy/docs`）。
  - 历史复核与第 4 轮任务书（已归档保留原件）：[归档/2026-11-21/历史阶段任务与复核](../归档/2026-11-21/README.md)。
  - 审计探针：`implementation/docs/audit/2026-10-01/test_review_probes.py`（43 项，长期作为回归守门）。

### 阶段 2：码上放心药品追溯生产攻坚与 DMZ 链路落地（2026-10-16 ~ 2026-11-08）
- **升级需求**：打通真实院内 HIS/ODS 只读通道（统一经受控数据资产平台 `sjzc` 技能），解决 DMZ 转发、时钟 NTP 校准、上游限流/重试、账号密码登录与大批量入库性能（`batchfix`），上线码上放心零售追溯上报。
- **关键产出**：
  - 业务导航与实测证据：[数据上报/1、码上放心平台/README.md](../数据上报/1、码上放心平台/README.md)。
  - 历史研发/部署/运维专项目录：`docs/development/drug-trace-*`（21 个目录）、`docs/operations/drug-trace-*`（16 个目录）、`docs/deployment/drug-trace-*`（4 个目录）、`docs/deployment/reporting-20261015/`、`docs/development/his-live-20261019/`。

### 阶段 3：国家传染病前置软件 13 接口上线与防通报治理（2026-10-27 ~ 2026-11-19）
- **升级需求**：
  1. 独立轻量化推送器 `inf_pusher` + 宿主守护 `inf_watchdog` 上线生产宿主机（`<prod-host>`），覆盖 13 类住院/检验/检查/医嘱/生命体征/死亡文书；
  2. 应对省市疾控通报，逐轮加固：死亡 T+0 驱动修复、父受理门禁（R2）、持久等待队列（R3）、定向重放（R4）、前置机 `emr_activity_info` + `emr_patient_info` **双闸门**防误发、企微中继告警、ODS v1.5/v1.6 视图合规升级；
  3. 2026-11-19 防中断重构（容器内锁与自超时、父键索引回查消除 `DPY-4024`、`datetime` 绑定消除 `ORA-01861`、6h 回执冷却与 10,000 行刷盘），单班耗时由 19.9 分钟压至 **3 分 36 秒**。
- **关键产出**：
  - 推送与守护代码：`implementation/deploy/inf_pusher/`、`implementation/deploy/inf_watchdog/`。
  - 业务档案与通报证据：[数据上报/4、传染病病历数据上报/README.md](../数据上报/4、传染病病历数据上报/README.md)。
  - 专项 SQL 与复核目录：`docs/development/inf-report-20261027/`（含 v1.5/v1.6 视图包）、`inf-lab-linkage-review-20261115/`、`inf-notice-20261118/`、`inf-continuity-review-20261119/`。

### 阶段 4：多业务统一平台演进（`LT-01~09` → `RT-01~06` → `PT-01~06`，2026-11-03 ~ 2026-11-18）
- **升级需求**：将单业务病理平台升级为可插拔的医院多业务上报中枢（统一收纳药品追溯、传染病、山东医保电子病历 23 交易、病理云索引、济南医保影像云 16 数据集），并完成生产级可观测性与 UI 焕新。
- **三轮演进目录**：
  - **演进期（`LT-01~09`）**：[platform-evolution-20261103](development/platform-evolution-20261103/README.md)（统一目录台账、外部账本导入、传染病影子闭环、脚手架生成第 5/N 业务）。
  - **整改期（`RT-01~06`）**：[platform-rectification-20261111](development/platform-rectification-20261111/FINAL_ACCEPTANCE_REPORT.md)（RBAC 权限隔离、山东医保 23 交易联调台、路由审计、国家编码无损解析）。
  - **稳定焕新期（`PT-01~06`）**：[platform-polish-and-stability-20261116](development/platform-polish-and-stability-20261116/PHASE3_ACCEPTANCE_REPORT.md)（传染病监控五页签座舱、企微中继告警双通道、全站 4 组导航与黑话清零、三钩子底座与契约沙箱、高仿真演练灌数与四包生产升级手册）。

### 阶段 5：第四阶段深度收口与 SOAP/XML 标准协议（`HT-01~04`，2026-11-18 ~ 进行中）
- **升级需求**：彻底清除跨会话脱节缺陷，并纯标准库扩展 WebService / SOAP 1.1 & 1.2 XML 标准医疗上报协议能力。
- **执行目录**：[platform-phase4-soap-and-closure-20261118](development/platform-phase4-soap-and-closure-20261118/README.md)（`HT-01`/`HT-02`/`HT-03` 已实测通过，`HT-04` 待收官）。

---

## 历史过程材料保留位置

- 全部历史状态/交接/待办原稿：`docs/ai/archive/`
- 155+ 单次 AI 任务交接全目录：[docs/ai/sessions/INDEX.md](ai/sessions/INDEX.md)
- 各阶段归档原件与 SHA256 清单：[归档/README.md](../归档/README.md)
- 各业务原始接口规范、Excel、PDF 与通报截图：[数据上报/README.md](../数据上报/README.md)
