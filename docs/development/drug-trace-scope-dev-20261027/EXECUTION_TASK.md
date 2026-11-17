# 药品追溯·口径扩展开发执行书（SD-00 — SD-08）

版本 r1，2026-10-27 深夜编制：ZCode/GLM-5.3（DTR-SCOPE-PLAN-20261027）。
执行者：接手 AI，一次性完成。唯一执行依据=本文件；与既有共享状态冲突时以本文件为准，
事实性疑问查证据文件，仍不决则停下问用户。

## 执行入口（复制给执行 AI 的提示词头）

> 请在 F:/python/blyb 工作区执行任务 DTR-SCOPE-DEV-20261027。
> 按顺序读：根 AGENTS.md → docs/ai/START_HERE.md、STATE.md、HANDOFF.md、BACKLOG.md →
> docs/development/drug-trace-scope-dev-20261027/EXECUTION_TASK.md（本文件，唯一执行依据）→
> 其「必读证据」清单。
> 登记：`python scripts/ai_session.py start --task DTR-SCOPE-DEV-20261027 --tool <实际> --model <实际>
> --scope implementation/plugins/domains/drug_trace --scope implementation/plugins/sources/db_view
> --scope implementation/apps/admin_web/src/views/DrugTrace.vue --scope implementation/tests
> --scope implementation/docs --scope docs/ai --scope docs/development/drug-trace-scope-dev-20261027`。
> 用户已授权本任务内的 HIS 只读探查（SD-01，经 sjzc 受控通道，规则见 §8）；本句话即授权留档。
> 顺序执行 SD-00—SD-08 一次完成，不在每步询问；局部绿后全量回归
> （当前基线 **595 passed / 7 skipped / 0 failed**，以执行时实测为准）。
> 硬边界见 §8：**住院/退药在 bill_type 平台确认前一律 fail-closed（只采集/冻结/DRY_RUN，不真实发送）**；
> 已 CONFIRMED 单永不重发；205 不盲发。结束交付见 SD-08。

## 一、背景（必读）

生产状态（2026-10-27 15:31，DTR-RECOVERY-STABILITY）：<prod-host> 发行 stability-20261027T064000Z-r3，
timer 每日 06:10 日批（v2 驱动），10-26 批 1995 张全部平台确认，累计 4613 确认 / 205 未知，
额度 4820/80000（standing scope 到 2026-11-22）。当前口径=门诊正常分支（PRESC_SOURCE=0/FLAG=1/
发药时间非空/未退费）+ 三键关联 MED_TRACE_RECORD 的 OUT 码（r10 视图）。

**用户已确认的四条口径（不可再选，照做）：**

| # | 口径 | 含义 |
|---|---|---|
| K1 | 无码不上传 | 门诊单 0 有效码不发送（=现状 QD-NO-CODES）；7 天回看后仍无码→终态 NOT_REPORTABLE（SD-05） |
| K2 | 退药要报 | 患者退药按冲正上报；退药有追溯码（IN 方向/申请表 return 行） |
| K3 | 迟到码补报 | 方案A：每日回看近 7 天无码/部分码单，定向复核，码到→走正常发送。**边界：已 CONFIRMED 单不重报不更正** |
| K4 | 住院要报 | 住院发药按原厂家住院视图口径（申请表体系）上报 |

**HIS 侧已查明的事实（上一轮 DTR-HIS-META-20261027，勿重查）：**

1. 原厂家四类追溯视图（DRUG_USER 下）全部存在，取数源是**发药申请表体系**：
   - 门诊：`PHA_CLI_REQUEST_DRUG`（CLI_FLAG='send'/'return'，CLI_INP_FLAG=1 门诊/2 住院，
     SEND_DRUG_STATUS='Y'，CLI_EXECUTE_TIME 执行时间，SEND/RETURN_QUANTITY）。
   - 住院发药：`PHA_INP_REQUEST_DRUG`（DISP_FLAG='send'、DISP_QUANTITY>0，近 30 天，
     关联 `PHA_INP_DISPMASTER` 取 DIS_RECORDNO 医嘱发药单号）。
   - 住院退药：`INP_REQUEST_DRUG_VIEW`（DISP_QUANTITY<0，按 INPATIENT_NO+PAT_ID+
     ORDER_SUB_NO+MO_ORDER 关联回发药）。
2. 门诊退药在申请表为 send+return 成对行（实测样本数量全对冲）；原厂家口径=全退从发药
   清单剔除（不报）——**我们不抄这条**，K2 要求主动冲正。
3. `MED_TRACE_RECORD.IN_OUT_TYPE` 值域（列注释官方）：**IN=入库（退药），OUT=出库（发药）**。
4. 我们 r10 从 DRUG_PRESC_MASTER 取数，住院码挂医嘱/申请单号键所以查不到；
   260 张缺码门诊单在申请表 send_st='Y'（发药确认完成）但码未写入 MED_TRACE_RECORD，
   备用码表 PHARMACY.DRUG_PRESC_TRACE_CODE 也无——根源在扫码环节未写码（另案，见 SD-01.4）。
5. `TRACE_UPLOAD_TIME_LOG`=原厂家上传日志且**仍在每小时写入**（biz=3503 每时成功 2~43 条；
   biz=3505 重试 10-22 02:00~04:00 窗口）。BUSINESS_ID 3503/3505 含义未确认，**不猜**；
   用户正在核实原厂家是否仍向平台传数（双上报风险，与本任务无关但发送门禁相关）。

**必读证据文件：**

- `docs/operations/drug-trace-sjzc-verify-20261027/README.md`（口径确认表+HIS 元数据探查节+四轮证据链）
- `docs/operations/drug-trace-sjzc-verify-20261027/evidence/his-meta{,2,3,4}-*.json`（视图 DDL/表结构/样本实测）
- `docs/operations/drug-trace-recovery-stability-20261027/SOURCE-REVIEW.md`（缺码核查背景）
- `implementation/plugins/sources/db_view/source_profiles.py`（r10 SourceProfile 写法、objects 可配）
- `implementation/plugins/sources/db_view/drug_trace.py`（reader 模板：poll_bills/bill_codes/bill_header）
- `implementation/plugins/domains/drug_trace/{contracts,quality,ingest,service,routes,models}.py`
- `docs/development/drug-trace-content-contract-20261022/sql/rollback_to_observed_views.sql`（r10 视图 DDL 原文——r11 的列契约基准）

## 二、工作分解

### SD-00 开工登记与基线

- `ai_session.py status` 确认无冲突后按上方登记；建任务目录
  `docs/development/drug-trace-scope-dev-20261027/`（过程证据、RESULT 都放这）。
- 基线：`python -m pytest tests -q -ra --junitxml=var/sd00-baseline.xml` 记录数字；
  `cd implementation/apps/admin_web && npm run build` 记录通过。写入 `evidence/SD-00-baseline.md`。

### SD-01 T0 只读探查（用户已在入口提示词授权；经 sjzc 受控通道，规则见 §8.7）

沿用 `docs/operations/drug-trace-sjzc-verify-20261027/explore_meta*.py` 的通道模式
（<relay-jump> 容器只读连接器；本机不读凭据；连接器拒 `--` 注释——SQL 先剥注释行；
GROUP BY 不得带列别名；单条 30s、每查询 ≤50/≤10000 行、无索引列不做范围扫）。
每问一 SQL、结果落本任务目录 evidence/，结论写 `evidence/SD-01-findings.md`。

1. **住院码真实键验证（SD-02 的前提）**：从应用库（<prod-host> 只读事务，deploy_fb05 jump+nested
   通道，同 sjzc-verify 任务的 pull_day_bills.py 模式）取 10-26 批住院 WAITING 样本 ≤10 张的
   申请单号：`PHA_INP_REQUEST_DRUG.REQUEST_NO`（按 w.DISP_FLAG='send' AND DISP_QUANTITY>0 AND
   PHA_CODE IN (...) AND DISP_EXECUTE_TIME 当日；该表索引先查 ALL_IND_COLUMNS，无索引改用
   PRESC_NO/PAT_ID 路径）。然后到 HIS 按
   `TRACE_BILLNO IN (REQUEST_NO 清单)` 与 `TRACE_BILLNO IN (DIS_RECORDNO 清单)` 分别查
   MED_TRACE_RECORD（无日期/药房过滤，GROUP BY 键分布）——命中者即住院码真实键。
   同时抽 1-2 张原厂家住院 send 视图行核对（PHA_INP_TRACE_DRUG_SEND_VIEW 按样本键点查）。
2. **退药 IN 码验证（SD-03 前提）**：取门诊退药样本（申请表 return 行：CLI_FLAG='return'
   AND RETURN_DRUG_STATUS='Y' 当日 050121，含 57370/57371/57372）的 PRESC_NO/PAT_ID，
   查 MED_TRACE_RECORD `TRACE_BILLNO IN (presc_no 清单)` 全日期行——看 return 对应的码行
   是否存在、方向是否 IN、键用处方号还是别的。若 IN 行不存在，结论如实记"退药码尚未写入，
   SD-03 按 return 行+发药侧码实现冲正"（发药时 OUT 码已上报，冲正可用原码+负向类型）。
3. **BILL_TYPE 证据检索（不猜）**：在 HIS 数据字典/配置表里找原厂家的 bill_type 线索：
   `SELECT * FROM (SELECT TABLE_NAME,COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER'
   AND (COLUMN_NAME LIKE '%BILL_TYPE%' OR COLUMN_NAME LIKE '%BUSINESS%')) WHERE ROWNUM<=100`，
   及 PUB_CONFIG/SYSTEM_CONFIG 中 trace 相关键（`CONFIG_KEY LIKE '%trace%'`，点查）。
   **住院与退药的最终 bill_type 必须由用户/平台确认（321=卖出已批；住院/退货待确认）——
   SD-01 只收集证据供用户确认，不自行决定。**
4. **260 缺码共同特征（可选，时间盒 30 分钟）**：对比 260 缺码 vs 1995 成功样本在
   PHA_CLI_REQUEST_DRUG 的标志位分布（先 ALL_TAB_COLUMNS 拉全列名，选 scan/pda/upload/window
   类列，GROUP BY 对比两群体分布），产出"下一步给药学部的核对线索"。

### SD-02 住院上报开发（K4）

**架构决策（已定，照做）**：不改 r10（冻结）；新建 **r11 Oracle 视图**（DBA 执行 DDL，
执行 AI 只交付脚本不连生产建）+ 新 SourceProfile；**r11 视图必须输出与 r10 完全相同的列契约**
（bill 17 列 / code 18 列，见 rollback_to_observed_views.sql），从而 reader 模板
（poll_bills/bill_codes/bill_header）与 ingest/质量全链路复用、零改动。

1. **r11 视图 DDL 草稿**（交付 `sql/r11_inpatient_views.sql`，由用户交 DBA 在 PHARMACY 下
   创建并授权 BLYB_DRUGTRACE 同 r10 权限；本机测试用 SQLite 合成仓储不依赖真实视图）：
   - `V_DRUG_TRACE_BILL_INP`：源 `DRUG_USER.PHA_INP_REQUEST_DRUG w`（DISP_FLAG='send' AND
     DISP_QUANTITY>0），关联 `PHA_INP_DISPMASTER d` 取 DIS_RECORDNO。列映射：
     `source_bill_id = 'INP|'||w.REQUEST_NO||'|'||w.PHA_CODE`（稳定复合键，与门诊键空间隔离）；
     `source_bill_no = w.REQUEST_NO`；`business_kind='INPATIENT_DISPENSE'`（新 kind）；
     `bill_time=updated_at=w.DISP_EXECUTE_TIME`；`finalized=1/is_void=0`（DISP_FLAG='send' 即完成）；
     `source_bill_kind_raw='REQ_NO='||REQUEST_NO||';DIS_REC='||DIS_RECORDNO`；
     expected_code_count 仍 NULL（不猜应报数）。REFUND/PRESC_SOURCE 原值无——raw 里写
     `';SRC=inpatient_request'`。
   - `V_DRUG_TRACE_CODE_INP`：`MED_TRACE_RECORD t JOIN V_DRUG_TRACE_BILL_INP 的键`
     —— **关联键按 SD-01.1 实测结论写**（预期 `t.TRACE_BILLNO = w.REQUEST_NO 或 TO_CHAR(d.DIS_RECORDNO)`，
     并限定 `t.IN_OUT_TYPE='OUT'`）。若 SD-01.1 发现键另有格式，以实测为准并在视图注释里写明证据文件名。
2. **代码**：`source_profiles.py` 新增 `HIS_INP_VIEWS_20261027_R11`（key=
   `blyb_drugtrace_inp_views@20261027-r11`，objects 指向两个新视图，registered_write_policy=
   'readonly'，evidence_level 按实测填）；`contracts.py` kinds 增加 `INPATIENT_DISPENSE`、
   PROFILES[retail].business_kings 含它（执行时读 contracts.py 现结构后照现有写法扩）；
   字典：bill_type 映射**先建 DRAFT**（值待 SD-01.3/用户确认，未批准→发送被门禁拦，正合适）。
3. **质量/门禁**：business_kind=INPATIENT_DISPENSE 走既有 VALID/WAITING/INVALID 规则，
   0 码→WAITING（K1 同样适用）；**发送门禁零放松**：无批准字典/无 send scope → 拒绝。
4. **UI**：DrugTrace.vue kinds 下拉加 INPATIENT_DISPENSE；面板无需新列。

### SD-03 退药冲正开发（K2）

1. **门诊退药取数**：r11 第二组视图 `V_DRUG_TRACE_RETURN`（bill）+`V_DRUG_TRACE_RETURN_CODE`（code）：
   源 `PHA_CLI_REQUEST_DRUG w WHERE CLI_FLAG='return' AND RETURN_DRUG_STATUS='Y'`；
   `source_bill_id='RET|'||TO_CHAR(w.PRESC_CREATE_TIME,'YYYYMMDDHH24MISS')||'|'||TO_CHAR(w.PRESC_NO)||'|'||w.PHA_CODE`
   （注意处方号复用——键必须含时间）；`business_kind='PATIENT_RETURN'`（枚举已存在，核对
   PROFILES 是否已含，不含则加）；`bill_time=w.CLI_EXECUTE_TIME`（退药执行时间）；
   raw 记 `PRESC_NO/PAT_ID/原 send 行时间`。code 视图按 SD-01.2 实测：IN 码能挂则挂
   （t.IN_OUT_TYPE='IN' + 实测键）；挂不上则 code 视图返回 0 行、冲正 payload 用**原发药码**
   （从已 CONFIRMED 的原单冻结 payload 取码列表——只读，不改冻结件）。
2. **冲正 payload**：`build_payload` 支持 PATIENT_RETURN：bill_type 用退货类型
   （**待 SD-01.3+用户确认，未确认前字典 DRAFT、发送被门禁拦截=DRY_RUN 可用**）；
   数量为负或按平台退货规范（执行时读 destinations/alihealth_drugtrace/protocol.py 的
   官方两 profile 定义，若协议无零售退货 method 则如实记 BLOCKED 并只交付采集侧）。
3. **关联原单展示**：bill detail 的 raw 里带原 send 信息；UI 异常提交区不变。

### SD-04 K3 迟到码补报（方案A，已批）

1. **定向复核 job**（不做全窗口重扫，成本原因）：
   - `contracts.py` DomainJobType 增加 `RESCAN`；`routes.py` 新端点
     `POST /api/v1/drug-trace/collection-runs`（复用）body 增加 `mode:'rescan'` +
     `bill_keys:[{source_system,pharmacy_code,business_kind,source_bill_id}]`（≤500/批，
     仅 operator|admin，audit `drug_trace.rescan.create`）。
   - worker：rescan 模式逐单走既有 `drugtrace.bill_header` + `drugtrace.bill_codes` 模板
     （按 bill_keys 点查，不做窗口扫描）→ ingest 幂等（码变化→新 revision；无变化→跳过）。
   - **边界实现**：rescan 目标单必须 state∈{未提交/质量 WAITING}；对已 CONFIRMED/ACCEPTED
     的单据键直接跳过并计数（已 CONFIRMED 不重报）。
2. **驱动 v3**：日批正常阶段后追加「回看阶段」：应用库选出近 7 天（可配
   `LOOKBACK_DAYS=7`，环境变量/配置）内 0 码或部分码且未上报的门诊单键 → 调 rescan API
   （分批）→ 新 VALID 单走既有 standing scope 正常发送。回看阶段失败**不影响主日批退出码**
   （仅记 receipt['lookback']={'ok':false,...}），与 v2 的 report_push 同模式。
   驱动文件=仓库 `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`
   （改后随部署替换；本轮不部署）。
3. **部分码说明**：expected_code_count 全 NULL，"部分码"不可自动判定——回看目标=
   **0 有效码的单**（无码单）；有码单已按现状整单上报，不因后到码重报（K3 边界）。

### SD-05 无码终态（K1 补充）

- `quality.py` evaluate_revision：business_kind=PATIENT_DISPENSE 且 0 有效码且
  `header.bill_time < now-7d` → quality_state=`NOT_REPORTABLE`（新终态，非 WAITING），
  issue `QD-NO-CODES-FINAL`（severity BLOCKING，说明"超 7 天回看窗仍无码，按 K1 不上报"）；
  7 天内维持 WAITING（等 K3 补码）。注意只对门诊分支生效；住院/退药分支仍 WAITING（等口径/字典）。
- ingest 对已 WAITING 老单的终态迁移：新 revision 入库时按规则重判（幂等）；
  存量单不动（下次 rescan/日批触碰时自然迁移），不写一次性数据订正脚本。
- UI：质量状态色加 NOT_REPORTABLE（muted）；日报详情无改动。
- 迁移：无表结构变化（quality_state 是 VARCHAR），不需要迁移。

### SD-06 205 比对（核查+报告，不发送）

- 用 sjzc 通道聚合 `TRACE_UPLOAD_TIME_LOG`：按 (BUSINESS_ID, START_TIME 日/时) 汇总
  RECORD/SUCCESS/FAIL 计数，时间范围覆盖 205 张的发送时间窗（从应用库取 205 张的
  created_at 分布）。产出对照报告：原厂家在各时段是否成功传过数据 vs 我们 205 张时段。
  **如实声明：该日志无单号明细，只能做宏观覆盖对照，逐单确认仍需平台查询或人工材料**；
  报告给出"建议逐单平台核查清单"（205 张键+时间）。不修改 205 张任何状态、不重发。

### SD-07 测试与全量回归

- 新增测试（沿用 tests/integration 既有 fixture 风格，合成 SQLite 仓储扩展 r11 对象）：
  r11 SourceProfile/reader 点查、住院/退药 ingest 幂等与新 revision、PATIENT_RETURN/
  INPATIENT_DISPENSE 质量与门禁（无批准字典→send 拒绝）、NOT_REPORTABLE 终态（7 天内外
  两例）、rescan job（跳过已 CONFIRMED、码到换版、无变化跳过）、驱动 v3 回看阶段
  （失败不改退出码，成功路径新 VALID 单进入发送队列——用假 HTTP）、205 比对聚合纯函数。
- 局部绿后全量：`python -m pytest tests -q -ra --junitxml=var/sd-full-regression.xml`
  → **≥基线 passed / 0 failed**。npm build 通过。
- 禁删既有断言/xfail；语义变更在对应测试中重挂断言并注明依据（本文件 §一 口径表）。

### SD-08 文档、状态与交接（部署另行，不在本任务）

- 任务目录 RESULT.md（实测/未测/阻断分开；住院/退药发送=BLOCKED 待 bill_type 确认+scope）。
- 四类实施文档：API 矩阵（rescan 端点）、实施状态矩阵（SD 行）、测试执行记录（一节）、
  接口与设计差异（r11 键体系/INPATIENT_DISPENSE/PATIENT_RETURN/NOT_REPORTABLE）。
- 共享 STATE/HANDOFF/BACKLOG；session 文件 `docs/ai/sessions/DTR-SCOPE-DEV-20261027.md`；
  `ai_session.py end`。
- **部署清单写入 RESULT（供用户另开窗口执行）**：DBA 先建 r11 视图+授权 → build_release →
  upgrade.sh（避开 06:00–08:30、用户在场）→ 替换驱动 v3 → 只读验证 → 回滚路径。
  本任务不部署、不连生产、不发真实请求。

## 三、契约速查（新增面）

| 项 | 值 |
|---|---|
| 新 business_kind | `INPATIENT_DISPENSE`（住院发药）、复用 `PATIENT_RETURN`（门诊退药冲正） |
| r11 视图 | PHARMACY.V_DRUG_TRACE_BILL_INP / V_DRUG_TRACE_CODE_INP / V_DRUG_TRACE_RETURN / V_DRUG_TRACE_RETURN_CODE；列契约与 r10 完全一致 |
| 新 API | collection-runs 复用 + mode='rescan' + bill_keys（≤500，operator\|admin，audit） |
| 新质量终态 | NOT_REPORTABLE（QD-NO-CODES-FINAL，仅门诊 0 码且超 7 天） |
| 驱动 | v3= v2 + 回看阶段（LOOKBACK_DAYS=7 可配，失败不改退出码/回执照写） |
| 字典 | 住院/退货 bill_type 一律 DRAFT（未批准→发送 fail-closed） |

## 四、验收总表（SD-A）

| 编号 | 条件 |
|---|---|
| A01 | SD-01 四问各有实测结论+证据文件；住院码真实键/退药码存在性有定论（含"未写入"这种否定结论） |
| A02 | r11 视图 DDL 交付（列契约与 r10 逐列一致，机器比对）；SourceProfile/reader 点查测试绿 |
| A03 | 住院/退药：采集→质量→冻结→DRY_RUN 全链路绿；**无批准字典时 send 被拒（fail-closed 有测试）** |
| A04 | rescan job 三分支测试绿（跳过 CONFIRMED/码到换版/无变化跳过）；驱动 v3 回看失败不改退出码有测试 |
| A05 | NOT_REPORTABLE 终态测试绿（7 天内/外两例）；UI 显示；npm build 过 |
| A06 | 205 对照报告产出（含宏观对照+逐单核查清单+如实声明），205 状态零改动 |
| A07 | 全量回归 ≥基线 passed / 0 failed（JUnit XML）；无删除断言 |
| A08 | 四类实施文档+RESULT+共享状态+session+ai_session end 齐备；实测/未测/阻断分开 |

## 五、边界与禁止事项（违者返工）

1. **fail-closed**：住院/退药在 bill_type 经用户/平台确认并批准字典前，不真实发送
   （采集/冻结/DRY_RUN 可用）；send 门禁、配额、证据路径零放松。
2. 已 CONFIRMED/ACCEPTED 单永不重报、不改冻结 payload；205 张零状态改动、不重发。
3. 不改 r10 视图与既有迁移；r11 全部新建；无表结构迁移（quality_state 为 VARCHAR）。
4. 全部查询带 tenant_id；不新增轮询/自动重试（rescan 是日批内单次阶段+手动 API）。
5. HIS 探查仅 SD-01 范围、sjzc 受控通道、只读；不猜值域（BILL_TYPE/BUSINESS_ID 必须证据或问）；
   无索引列不做全表扫；连接器拒注释/别名规则见 SD-00 前言。
6. <prod-host> 只读事务（拉样本键/205 时间分布）；不改生产配置、不部署、不发真实平台请求。
7. 双上报风险未核实前，住院/退药发送侧保持 BLOCKED（与 1 同源）。
8. 未运行的不写通过；静态检查不冒充验收；历史通过数不抵新缺陷。

## 六、风险与回滚

| 风险 | 缓解 |
|---|---|
| 住院码键 SD-01 验证不出（IN/单号都对不上） | SD-02 降级交付：视图 bill 侧+采集可用、code 视图留 0 行并标 BLOCKED，不阻塞其余任务 |
| 退货协议不支持（uploadretail 无退货语义） | SD-03 降级：采集+DRY_RUN+差异文档，发送侧 BLOCKED 待平台方案 |
| 回看 rescan 单量大（每天数千 WAITING） | ≤500/批分页+逐单点查（bill_header 走主单 PK）；实测耗时写入 RESULT，超预算改配置窗口 |
| quality 终态误伤（时钟/时区） | 用 bill_time 与采集侧同一 GMT+8 口径比较；测试覆盖边界日 |
| 回滚 | 全部为本地未部署代码+未执行 DDL；git 工作区即可回退；r11 视图不建即无生产影响 |

## 七、当前基线（对照用）

- 全量：595 passed / 7 skipped / 0 failed（DTR-RECOVERY-STABILITY 最终，var/ 下 XML）。
- 生产：stability-20261027T064000Z-r3；日批 v2 正常；本任务不触生产。
- 编制依据证据：docs/operations/drug-trace-sjzc-verify-20261027/（口径+HIS 探查全链）。
