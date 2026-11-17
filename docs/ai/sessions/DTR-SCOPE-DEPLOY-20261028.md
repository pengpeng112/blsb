# DTR-SCOPE-DEPLOY-20261028 交接（scope-20261028 凌晨部署）

- 执行：ZCode / GLM-5.3，2026-10-29 02:00–03:10 CST，session `20261028T053321Z-ca40f62e`（定时 automation-07c84c94 触发）。
- 依据：DEPLOY-RUNBOOK-20261029.md 五 Phase；用户授权原话见 runbook 头。

## 结果总表

| Phase | 结果 |
|---|---|
| 1 传输 | ✅ 456.8MB tar 经跳板（49.7MB/s），目标侧 SHA256 全过、指纹匹配 |
| 2 升级 | ✅ upgrade.sh exit0；六容器 healthy；驱动 v3 SHA 2f33c026…cc662 吗齐（旧件已备份）；timer active→06:10；迁移无新增（13 号系 10-27 既有 013_console_password_sessions） |
| 3 配置 | ✅ his-inpatient-r11a 创建+PUBLISHED+**ENABLED**（collect 证据有效）；his-return-r11a 创建 DRAFT（profile 闸在 publish 即拦=设计内更彻底）；字典 bill_type/query_bill_type r11-20261028（INPATIENT_DISPENSE→321/A）**已批准**；PATIENT_RETURN 仅 DRAFT；r10 字典零影响 |
| 4 住院冒烟 | ⏸ **按手册中止**（详见 evidence/deploy/PHASE4-DEFERRED.md）：真采集 3.9s/查询（视图分支二 TO_CHAR(DIS_RECORDNO) 索引失效），全程~5h 超界；已 CANCEL（12 张入库住院单无害） |
| 5 留证 | ✅ evidence/deploy/*（transfer/upgrade/config/smoke/DEFERRED）+ 终态快照 |

## 过程修正（如实）

- Phase2 首跑 exit126（tar 丢执行位）→ chmod 755 后过（stability 轮同款步骤）。
- Phase4 v1/v2 误按窗口复用了今晨 r10 门诊日批运行（列表接口无连接器字段）→ v3 改总是新建；
  v1/v2 证据 json 里的 summary 是门诊数据，以 PHASE4-DEFERRED.md 为准。

## 明晨复核（06:10 v3 首跑）

1. 回执含 `lookback` 字段（回看近 7 天无码门诊单 rescan 计数）；
2. 门诊发送行为与 stability-r3 基线一致（10-28 窗单量~2000）；
3. NOT_REPORTABLE 迁移开始出现（10-21 前无码单触碰即迁移）；
4. 无新增 UNKNOWN/失败峰值。

## 白天待办（住院冒烟重试）

1. DBA 对 V_DRUG_TRACE_CODE_INP 分支二 EXPLAIN 并调优（改半连接/先取键列表再 IN）；
2. 小窗重采（10-27 08:00–16:00，050121 码密集段）→ DRY_RUN 验 bill_type=321 → 单张真实发送；
3. 产品改进候选：collection-runs 列表返回 connector 字段。
