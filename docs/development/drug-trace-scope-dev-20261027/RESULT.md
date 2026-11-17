# RESULT — DTR-SCOPE-DEV-20261027（药品追溯·口径扩展开发）

执行：ZCode/GLM-5.3，session `20261027T134424Z-45575e89`，2026-10-27。
唯一执行依据：[EXECUTION_TASK.md](EXECUTION_TASK.md)（SD-00—SD-08）。
本任务**未部署、未连生产改配置、未发真实平台请求、未改 <prod-host>/HIS 任何数据**
（只读探查与只读事务见 SD-01/SD-06 留证）。

## 结论总表（实测 / 实现未测 / 阻断 分开）

| 项 | 状态 | 说明 |
|---|---|---|
| SD-00 基线 | **实测** | 595 passed/7 skipped/0 failed（exit 0，var/sd00-baseline.xml）；npm build exit 0 |
| SD-01 四问探查 | **实测** | [SD-01-findings](evidence/SD-01-findings.md)：住院码未写入源库（两候选键 10+10 全零命中）；退药 IN 码未写入（全为历史复用 OUT 行）；HIS 无 bill_type 线索；260 缺码=多行大处方+窗口号缺失线索 |
| SD-02 住院上报（采集侧） | **实测（本地）** | r11 视图 DDL 交付（列契约与 r10 机器比对 17/18 列一致）；INPATIENT_DISPENSE 采集→质量→冻结→DRY_RUN 测试绿 |
| SD-02 住院发送 | **BLOCKED（设计内）** | bill_type 待用户/平台确认；字典 DRAFT；真实采集待 DBA 建 r11 视图（profile unresolved 阻断） |
| SD-03 退药冲正（采集侧+冲正建包） | **实测（本地）** | PATIENT_RETURN 0 自有码 + 原单 CONFIRMED → VALID、冲正 payload 复用原发药冻结码（只读，reversal_of 留痕）；无原单 → WAITING |
| SD-03 退药发送 | **BLOCKED（设计内）** | 退货 bill_type/接口语义待平台确认；字典 DRAFT |
| SD-04 K3 rescan | **实测（本地）** | 三分支（跳过 CONFIRMED/码到换版/无变化迁移）+ 源侧消失 + API（角色/≤500/审计/job）测试绿；驱动 v3 回看失败不改退出码有测试 |
| SD-04 驱动 v3 生产运行 | **实现未测（部署项）** | 未部署；部署清单见 §部署 |
| SD-05 NOT_REPORTABLE | **实测（本地）** | 7 天内/边界/7 天+1s 两例、仅门诊、存量 WAITING 幂等迁移测试绿；UI 标签+置灰 |
| SD-06 205 对照报告 | **实测（只读）** | [SD-06-report](SD-06-report.md)：我方 204 张提交于 10-25 04:28-04:36，原厂家当日 rec=0；10-23 1 张（17 时）原厂家 08-16 时有 26 条不重叠；205 状态零改动 |
| SD-07 全量回归 | **实测** | **608 passed / 7 skipped / 0 failed（exit 0，654.29s）** = 基线 595 + 新增 13；JUnit `implementation/var/sd-full-regression.xml`；npm build exit 0 |
| 住院/退药真实 Oracle 采集 | **实现未测** | 视图未建（DDL 交 DBA）；本机测试用合成仓储 |

## 实测证据索引

- 基线与回归：`implementation/var/sd00-baseline.xml`（595/7/0）、`implementation/var/sd-full-regression.xml`（**608/7/0，exit 0**）
- SD-01：`evidence/sd01-appdb-20261027T135459Z.json`、`evidence/sd01-his-20261027T135850Z.json`、
  `evidence/sd01-his-r2-*.json`、`evidence/SD-01-findings.md`（可复跑脚本 `sd01_pull_appdb.py`、
  `sd01_probe_his.py`、`sd01_probe_his_r2.py`；身份 READY_HIS@his；只读 SELECT 约 40 条 HIS + 6 条应用库）
- SD-02/03：`sql/r11_inpatient_views.sql`（4 视图 + 授权语句）、
  `implementation/tests/integration/test_drug_trace_scope_dev.py`（13 项）
- SD-04：同测试文件（rescan 三分支/API/驱动 v3 回看）
- SD-06：`SD-06-report.md`、`sd06_report.py`（纯函数可测）、`sd06_pull_205.py`；
  逐单清单在受限目录 `.ai/private/drug-trace-scope-dev-20261027/sd06-unknown205-*.json`
- 全部改动文件清单见 git diff（代码 11 文件 + 测试 2 文件 + 文档 + 驱动 v3 + UI 3 文件）

## 代码改动面

| 文件 | 内容 |
|---|---|
| `plugins/domains/drug_trace/contracts.py` | INPATIENT_DISPENSE 入 kinds/retail profile；NOT_REPORTABLE 状态；RESCAN job 类型；QD-NO-CODES-FINAL/QD-RETURN-* 规则码；BILL_TYPE_KEY_OVERRIDES+bill_type_source_key（r11 分支用稳定 business_kind 作字典键，r10 原行为不变） |
| `plugins/domains/drug_trace/quality.py` | evaluate_revision(now/return_original 参数)：门诊 0 码超 7 天（GMT+8）→NOT_REPORTABLE；PATIENT_RETURN 0 码+原单码→VALID（告警）/无原单→WAITING；late_no_code_terminal_issues（存量迁移判定） |
| `plugins/domains/drug_trace/payload.py` | resolve_return_original（raw PRESC_NO+SEND_EXEC 关联原单，仅取 **CONFIRMED** 冻结码，只读）；build_payload：字典键走 bill_type_source_key；PATIENT_RETURN 0 码→冲正码=原发药码+reversal_of 留痕 |
| `plugins/domains/drug_trace/ingest.py` | import_bill PATIENT_RETURN 原单解析；同 digest 幂等重导时 WAITING→NOT_REPORTABLE 单向迁移（快照不改）；collect_window 计 not_reportable |
| `plugins/domains/drug_trace/service.py` | run_rescan/_rescan_one（≤500 键、跳过 CONFIRMED/ACCEPTED、版本变→整单重读新 revision、不变→幂等重判、源侧消失计数；单键失败≤50 明细不中断） |
| `plugins/domains/drug_trace/routes.py` | collection-runs 复用 + mode='rescan'+bill_keys（≤500、operator\|admin、audit `drug_trace.rescan.create`、job `drug_trace.rescan`）；overview 加 revisions_not_reportable |
| `plugins/domains/drug_trace/plugin.py` | _j_rescan worker handler（连接器配置闭包重建 + oracle 采集证据执行期复核） |
| `plugins/sources/db_view/source_profiles.py` | `blyb_drugtrace_inp_views@20261027-r11`、`blyb_drugtrace_return_views@20261027-r11`（LIVE_READONLY_PROBED + unresolved 阻断，视图建好核验后另立 revision 关闭） |
| `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py` | **v3**：新增回看阶段（LOOKBACK_DAYS=7 可配 env BLYB_LOOKBACK_DAYS，0=停用；选近 8 天 0 码未上报门诊单键→分批 rescan→新 VALID 单 prepare+send，LOOKBACK_MAX_SENDS=500）；safe_run_lookback 失败只写 receipt['lookback']，主日批退出码/回执语义与 v2 完全一致 |
| UI（DrugTrace.vue / drugTraceDisplay.ts / drugTrace.css） | kinds 加住院发药；NOT_REPORTABLE 中文标签「不再上报（超回看窗无码）」+置灰 |
| 测试 | 新增 `tests/integration/test_drug_trace_scope_dev.py` 13 项；`test_drug_trace_domain.py::test_a05` 按 K1 口径重挂断言（原 WAITING→NOT_REPORTABLE，注明依据；未删断言） |

**门禁零放松**：send 证据/EXT-12 scope/配额/DMZ/CAS/查询代次路径零改动；
r11 分支字典未批准 → DICTIONARY_UNMAPPED 拒绝（有测试）；已 CONFIRMED/ACCEPTED
单 rescan 直接跳过（有测试）；205 张零状态改动（SD-06 只读）。

## SD-01 四问定论（详见 findings）

1. **住院码真实键**：REQUEST_NO（20 位）与 DIS_RECORDNO（19 位）两候选键
   10+10 个在 MED_TRACE_RECORD **全部零命中**（厂家住院视图本身可读、申请行存在、
   DISPDETAIL PDA_STATE 全 0）→ 住院码未扫码落库，非键错误。r11 code 视图按两键
   UNION ALL 建，将来任一键写入即流入（当前自然 0 行=整单 WAITING，K1 同样适用）。
2. **退药 IN 码**：57370/57371/57372 全日期仅历史复用 OUT 行（2025-07~2026-09），
   近窗零行、无任何 IN 行 → 冲正按 return 行 + 原发药 CONFIRMED 冻结码实现
   （code 视图按三键+IN 建，当前 0 行）。
3. **bill_type 证据**：HIS 无 BILL_TYPE 列（12 个 %BILL_TYPE%/%BUSINESS% 列均非），
   PUB_CONFIG/SYSTEM_CONFIG 无 trace 键 → 住院/退货 bill_type 只能用户/平台确认。
4. **260 缺码特征**：缺码组人均药行 4.44 vs VALID 组 1.46；WINDOW_NO 缺失 97.4% vs
   86.6%；SEND_DRUG_STATUS 均 Y；拆零占比无差异；申请表无 scan/pda 标志列。
   给药学部线索：多行大处方+窗口号缺失组合（050102 占 68% 首查）。

## 字典 DRAFT（待用户批准，交付 API 载荷）

bill_type 值**不猜**：平台确认后由 platform_admin 经既有字典 API 创建再 approve。
DRAFT（不参与映射、发送被门禁拦）示例载荷：

```
POST /api/v1/dictionaries   (config_editor|platform_admin)
{"namespace":"drugtrace.bill_type","version":"r11-draft-1",
 "items":[{"code":"INPATIENT_DISPENSE","target":"<平台确认值>"},
          {"code":"PATIENT_RETURN","target":"<平台确认值>"}]}          # approve 不传=DRAFT
POST /api/v1/dictionaries
{"namespace":"drugtrace.query_bill_type","version":"r11-draft-1",
 "items":[{"code":"INPATIENT_DISPENSE","target":"A"},
          {"code":"PATIENT_RETURN","target":"A"}]}   # query 选择器限 A/AI/AO；A 为已批口径
```

> query_bill_type 的 A 沿用已批门诊口径；若平台对住院/退货有独立查询选择器，
> 确认后修订。r10 门诊字典（'PRESC_SOURCE=0;FLAG=1'→321/A）不受影响。

## 部署清单（用户另开窗口执行；本任务未部署）

1. **DBA 先行**：PHARMACY 下执行 `sql/r11_inpatient_views.sql`（4 视图 WITH READ ONLY
   + 对 BLYB_DRUGTRACE 的 4 条 GRANT，语句在文件头）。可选评估：r11 code 视图依赖
   MED_TRACE_RECORD.TRACE_BILLNO 既有索引（已在位）。
2. **验证视图**：BLYB_DRUGTRACE 登录，对 4 视图做限量 SELECT（列名/类型与 r10 同构）；
   之后为两个 r11 SourceProfile 另立关闭 unresolved 的新 revision（新证据文件），
   再新建 r11 连接器配置（objects 指向新视图）并发布。
3. **应用升级**：`implementation/deploy/release/build_release.py` 构建 →
   <prod-host> `upgrade.sh`（**避开每日 06:00–08:30、用户在场**；无新迁移，
   quality_state 为 VARCHAR 无表结构变化）。
4. **驱动 v3 替换**：`/opt/blyb/tools/blyb-daily-upload.py` ← 仓库
   `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`；
   如需停用回看：env `BLYB_LOOKBACK_DAYS=0`。
5. **只读验证**：collection-runs 列表 200；rescan 手动 API（≤500 键）跑一轮观察
   summary（skipped_confirmed/migrated/created 计数）；日报回执出现 `lookback` 字段。
6. **回滚**：git 工作区回退即回退代码；r11 视图 `DROP VIEW`（不建即无生产影响）；
   驱动恢复 v2 文件；无数据订正需要撤销（NOT_REPORTABLE 迁移只改质量判定字段，
   不改快照/提交/回执）。

## 待用户/平台确认清单（不自行决定）

1. **住院 bill_type、退货 bill_type/接口语义**（uploadretail 退货如何表达：类型值
   或独立 method）——确认后建字典并 approve，发送侧自然放开（门禁不变）。
2. **原厂家是否仍向平台传数（双上报风险）**：SD-06 实测 3503 程序 10-26/27 仍每天
   成功上传（37/78 条）——核实结论前住院/退药发送保持 BLOCKED。
3. **药学部/HIS 厂家**：①住院发药为何不写码（PDA_STATE=0、两候选键零行）；
   ②退药 IN 码何时写入；③260 缺码「多行大处方+窗口号缺失」线索核查（050102 优先）。
4. 站房口径细节：r11 退药 code 视图三键中 `T.PRESC_DATE = w.PRESC_CREATE_TIME`
   的语义一致性（IN 码开始写入后核对，不一致仍恒 0 行不产假数据）。

## 风险与如实声明

- 住院/退药真实 Oracle 链路未测（视图未建）：本地合成仓储全链路绿；
  §六 降级路径按预期执行（bill 侧+采集可用，码源 BLOCKED 属源库事实）。
- 驱动 v3 回看阶段仅本地单测（假 HTTP）；首个生产日批回执需观察 lookback 字段。
- rescan 大批量耗时未在生产实测（风险表 §六：≤500/批+逐单点查；
  生产规模观察后可调窗口配置）。
- K1 迁移只在「触碰」时发生（rescan/重导），不写一次性订正脚本——存量 WAITING
  老单由驱动 v3 回看窗（8 天）逐日触碰迁移。

---

# 追加：r11 DDL 修复与自查结论（DTR-R11-DDL-FIX-20261028，2026-10-28，ZCode/GLM-5.3）

用户以 hisuser 执行 v1 DDL 失败。逐行审查+两轮只读探查（evidence/r11fix-probe-*.json）后：

## DDL 失败三因（v1/v2 → v3 全部修复，用 `sql/r11_inpatient_views_v3.sql`）

1. **v1 确定性 bug**：V_DRUG_TRACE_CODE_INP 第二 UNION 分支在 d2 声明前引用 d2.DIS_RECORDNO
   （ORA-00904）——建到第二个视图必失败。
2. **隐藏 bug（探查发现）**：`PHA_INP_REQUEST_DRUG.PRESC_CREATE_TIME` 实测为 **VARCHAR2**
   （格式 'YYYY-MM-DD HH24:MI:SS.m'）非 DATE——v1/v2 的 TO_CHAR(日期格式) 会 ORA-01481；
   v3 改 `TO_DATE(SUBSTR(...,1,19),...)`。另 PHA_CLI.PRESC_CREATE_TIME 是 TIMESTAMP(6)，
   退药码视图比较改 `CAST(... AS DATE)` 防小数秒失配。
3. **身份/权限**：hisuser 无 CREATE ANY VIEW/对象权限；正确执行=第 0 部分（DRUG_USER 属主/
   DBA 授权 4 表给 PHARMACY，幂等）→ 第 1/2 部分（**PHARMACY 属主**=建 r10 的同一账号建视图）
   → 第 3 部分（授权 BLYB_DRUGTRACE）→ 第 4 部分验证。v1 的 GRANT 只在注释里，v3 已可执行。

## 自查结论（用户问"能否自行确定"四项）

| 问题 | 结论 | 依据 |
|---|---|---|
| 3503/3505 每小时在传什么 | **医保追溯码上传**（BUSINESS_NAME=YBZSMSC，URL=10.153.50.218:8001/hsu/mihs/...，医保接口非阿里平台）——**双上报风险基本解除**；3505 医保日批 3-31~10-22 共 48 万条后停滞、3501/3503 仍在传 | DOCK_CALL_LOG 按 PROD_ID 聚合（dock_3503_3505） |
| 退药 IN 码 | **机制在、键已验证**：码表(TRACE_BILLNO,PRESC_DATE,STORE_CODE,IN)=申请表(PRESC_NO,处方开立时间,PHA_CODE) 三样本**到秒一致**（26641/31237/66035）→ r11 RETURN_CODE 视图键设计正确；10-26 三张退药无 IN 行属**漏扫例外**；且 66035 的 IN 码迟到 5 天写入（K3 回看对退药同样必要） | in_key_detail/in_key_return_rows |
| 住院写码 | **从未写入**（2026-08-15 与 10-26 两期样本码表全零）——结构性未接入，唯一待药学部/HIS 厂家解决 | inp_aug_codes、SD-01 |
| bill_type | **无法自行确定**：官方 2026.01 指南本身自相矛盾（描述 323、示例 321，EXT-08 遗留）——必须平台拍板；建议问法："零售接口 uploadretail 的患者退药/住院发药 bill_type 取值" | 官方指南取证（接口与设计差异 L325、EXT-08） |

其他修正：退药码视图验证预期从"0 行"改为">0（含历史 IN 码）"——历史 return 行会带出 IN 码，
首次采集将覆盖 8/9 月样本在内的历史退药（数量级见 RETURN 视图 COUNT，属预期行为）。

## r11 授权修正（ORA-01720，2026-10-28 续）

用户执行 v3：**四视图已建成**；第 3 部分授权报 ORA-01720（视图属主向外授权需对基表持有
WITH GRANT OPTION，第 0 部分授的是普通 SELECT；报错清单恰为三张新表，MED_TRACE_RECORD
因 r10 已带授权选项未报）。修正脚本：`sql/r11_grant_option_fix.sql`——A 部分（DRUG_USER
属主/DBA）四表补 WITH GRANT OPTION（幂等）→ B 部分重跑视图授权 → C 部分验证（四个 COUNT
+列名核对）。验证数字回传后即可关闭 r11 profile unresolved。

## r11 视图验证与住院码重大更正（2026-10-28 续二）

四视图实测 **VALID**（10-28 09:05 创建，READY_HIS 可查）：BILL_INP=6,525,493（住院申请全历史，
采集靠 DISP_EXECUTE_TIME 索引窗口）、CODE_INP=**47,295**、RETURN=7,722、RETURN_CODE=**17,238**
（历史退药 IN 码在，验证键设计正确）。

**CODE_INP 非零的定性（三轮探查，evidence/r11-verify-r{,2,3}*.json、r4-pharmacy）**：

1. **住院码真实存在且在增长**——但仅 **050121 药房**（47,292 行/4,457 单；050109 仅 3 行）；
   10-26 当日住院申请 050103=459/050107=189/050121=181/050122=167/050109=71/050110=15——
   **只有 050121 在写码且约 1/4 覆盖**（45/181），其余病区药房零接入。
2. **写入机制=T+1 早上批量补写**（10-27 12:22 发药的单，码 10-28 08:36 写入；按日分布
   10-17 起连续）。样例端到端核验：申请表 send 行 ✓、码表同单号 OUT/同药房 ✓、
   SPLIT_FLAG=1（住院摆药拆零，属正常）、码表 PRESC_DATE=医嘱时间（本视图按单号关联，不受影响）。
3. **更正 SD-01 结论**："住院码从未写入"不准确——应更正为"仅 050121 部分单 T+1 批量写入；
   SD-01 当时 10 样本恰好取自未接入药房（050103/050107 等）且未考虑 T+1 时序"。
4. **对部署后行为的预判（无需改动，链路自动成立）**：日批 06:10 采昨日住院单时 T+1 码未写
   → 无码 WAITING（K1 不上传）；次日回看 rescan 时码已写 → 新 revision → VALID → 走正常发送。
   住院数据将滞后约 1 天进入上报流，属预期。
5. 药学部/HIS 厂家问题措辞更新："住院扫码为何只接入 050121 且仅约 1/4 单（其余病区药房
   050103/050107/050122/050110 及 050121 未扫单未接入）？何时全接入？"

## bill_type 已确认=321（2026-10-28，DTR-BILLTYPE-CONFIRM-20261028）

用户经渠道确认："**bill_type 用 321 即可**"（住院发药与患者退药统一 321/使用出库）。
EXT-08 消歧关闭。字典部署时创建并批准（载荷，target 已定）：

```
POST /api/v1/dictionaries   {"namespace":"drugtrace.bill_type","version":"r11-20261028",
 "items":[{"code":"INPATIENT_DISPENSE","target":"321"},
          {"code":"PATIENT_RETURN","target":"321"}], "approve":true}
POST /api/v1/dictionaries   {"namespace":"drugtrace.query_bill_type","version":"r11-20261028",
 "items":[{"code":"INPATIENT_DISPENSE","target":"A"},
          {"code":"PATIENT_RETURN","target":"A"}], "approve":true}
```

**退药首单观察保护（新增到部署清单）**：bill_type=321 下退药报文的码/数量为正数、
与原发药同类型；reversal_of 仅本地留痕非平台字段。故退药字典批准后**首次真实退药
上传先只放 1 张**（沿用既有 max_bills=1 试单模式），核对平台回执 result_type/
sub_process_flag 与二次查询库存语义确认按冲正记账后，再放开批量。住院无此问题
（321 语义直接成立），随日批正常进入。

---

# 追加：住院码视图调优（DTR-R11-VIEWTUNE-20261029，2026-10-29，ZCode/GLM-5.3）

## 实测诊断（evidence/viewtune-*.json，READY_HIS 只读计时）

| 查询 | 耗时 | 行数 | 发现 |
|---|---|---|---|
| A 视图完整（reader 形状） | 0.12s | 0 | 快但行数错 |
| B 裸分支一（TRACE_BILLNO=REQUEST_NO） | 0.006s | **25** | **重复行 bug：1 条码×25 药品行** |
| C 裸分支二（TO_CHAR 连接现状） | 0.005s | 0 | 快（单键）但视图内计划漂移 |
| D 分支二改写（先取键再 IN） | 0.08s | 0 | 备用快写法 |
| E 视图 COUNT | **12.3s** | 1 | 慢路径实测（与昨晚 3.9s/查询同源） |

**两个问题**：①分支一 T×w 未按行对齐（申请表一行=一药品行）→ 码行按药品行数放大、
quantity/line_id 取值任意；②UNION ALL 结构计划漂移（同查询 0.12s/12.3s）。

## 修复：sql/r11_code_view_v2.sql（一语句，PHARMACY 属主执行，GRANT 不变）

- 分支一改「每单去重子查询」（GROUP BY REQUEST_NO, PHA_CODE）消灭重复、走索引；
- source_line_id 改用码行自带 T.PRESC_DETAILID；quantity 回归 r10 的 NULL 口径；
- 分支二移除（当前 0 行、键从未出现；将来需要按 D 形式加回）。
- 列名/类型与 r10 契约一致，应用侧零改动。

## 晨间观察（2026-10-29 07:1x 复核）

- 06:10 v3 首跑进行中（采集 RUNNING）——lookback 字段待跑完复核。
- **10-27 窗口缺口**：1792 张 PREPARED 未发送（昨日 timer 运行 FAILED：report 记
  collection=RUNNING/candidate=0，具体 fatal 待查昨日回执文件）。**补救**：今日运行结束后
  `driver --date 2026-10-27`（幂等 resume+send）。
- 205 UNKNOWN / 4613 CONFIRMED 与部署前一致（零漂移）。

## 下一步

1. 用户/DBA 执行 r11_code_view_v2.sql（一条 CREATE OR REPLACE）。
2. AI 复测（计时+计数+单键行数=1）→ 小窗采集（10-27 08:00–16:00）→ DRY_RUN 验 321
   → 单张真实发送。
3. 今日 v3 运行结束后：driver --date 2026-10-27 补发；复核 lookback 字段。
