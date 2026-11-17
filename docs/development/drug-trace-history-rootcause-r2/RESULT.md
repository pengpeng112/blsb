# 码上放心历史核查 rootcause-r2 结果（2026-10-23）

> **2026-10-23 晚更新：用户授权后源表核查已执行，原表根因已闭环。见文末「源表核查（r2b）」一节；
> 下文「外部阻断」中 SQL01—06 未执行部分已由该节更新。**

任务 DTR-HISTORY-ROOTCAUSE-R2；工具 ZCode / GLM-5.3；run `20261023T104928Z`。
执行[EXECUTION_TASK](../drug-trace-history-live-compare-20261023/EXECUTION_TASK.md) Q00—Q06。
本轮实际 HIS 在线查询 **37 条 SELECT、合计 0.876 秒**（身份/授权/定义 6 条＋Q02 三批＋重复码 1 批＋
11 张整单 22 条＋Q01 视图侧 2 批＋Q01b 身份与 2 批），全部经 <relay-jump> 受控连接器以
BLYB_DRUGTRACE（仅两视图 SELECT）执行；零触顶、零 ORA 错误。**源表（原表）查询 0 条、
HIS 写入 0、生产改动 0、平台调用 0、新上传 0。**

## 一、先回答五个最终问题（Q06）

**1. 正常门诊 r10 能否继续？** 能，在已核实范围内继续使用。本轮再次实测：身份/授权不变
（仅 CREATE SESSION＋两视图 SELECT），两视图定义 token 与 r10 草案完全一致、且与 r1 抓取逐字节相同。
r1 已证 484536/489613 行同键同次数。**仍会等待/阻断的条件不变**：业务分类 UNKNOWN（住院/其他分支）、
未完成/退费全退（1396 条所在 411 主单）、整单含重复码（本轮证实 11 张主单 INVALID）、
码数为 0、expected_code_count 提供而不符（当前恒空不触发）。

**2. 5077 条属于哪种来源/原因？** 视图侧证据已固化，**源端归因未闭环**：按任务书 U01—U05 分类，
当前 5077 条全部为 **U05（尚不能确认）**——需要 DBA 用已交付的 SQL01—04 查原表。视图侧子标签
（两个时点零变化）：3919 行同码可见于其他 14 位明细行（其中 2330 行同日、1589 行异日）、
1158 行两视图完全不见该码（按月均匀分布，非尾部滞后）。2330 行"同码同日异标识"是
"同一事件两种明细号格式"的最强提示，但**不是证明**——原记录在不在、三键 JOIN 命不命中，
只有 SQL01/02/03 对 S01—S03 三个样本的返回能定。**是否新增来源 revision：证据不足，暂不新增**；
SQL04 结果出来后按 [CANDIDATE_CHANGES](CANDIDATE_CHANGES.md) C02 的分支决定。

**3. 退款与重复怎么处理？** 1396 条 UNKNOWN：两个时点三态完全一致（FLAG=2 且未退费 1327、
FLAG=2 且退费 23、FLAG=1 且退费 46），411 主单，**零状态迁移；保留正常发送拦截，不改报 321**。
31 组同单同码：实际只涉及 **11 张不同主单**；整单取全（无截断）后用现有纯质量函数判定，
**11 张全部 INVALID / QD-DUP-CODE**——且每张单的全部有效码都恰好出现 2 次（整单双扫模式），
这些单其余门槛全过（PATIENT_DISPENSE/FLAG=1/finalized=1/void=0），唯一阻断就是重复码。
成因（SPLIT_FLAG/重扫/更正）待 SQL04 源端证据；**在此之前 11 张单都不能正常发送，也不去重放行**。

**4. 同窗口完整性与平台终态证明了吗？** **没有，如实标注"未完成"。** 第一层（全部历史键一向核对）
r1 已完成、本轮复核视图侧零变化；第二层（同扫码 CREATE_TIME 窗口双向全量）未做：
两视图无 CREATE_TIME 列，MED_TRACE_RECORD 无 CREATE_TIME 索引（2026-10-19 快照），
已交付 [SQL06](sql/06-dba-index-metadata.sql)（先确认索引）和 [SQL05](sql/05-dba-join-coverage-by-day.sql)
（按 PRESC_DATE 主键索引逐日 JOIN 覆盖统计，可量化"源有视图无"规模）。
历史平台终态：0 查询（无授权即不查），历史 Excel 无成功终态，**不构成补报清单**。

**5. 能否开启持续自动推送？** **不能。** 除上述未闭环项外，A01—A05 检查（[CANDIDATE_CHANGES](CANDIDATE_CHANGES.md) C06）：
A01 batch 机制（页级原子+checkpoint+回看+重取账本）已实现、continuous 模式无确认变更源仍 fail-closed；
A02 独立完整性来源仍缺（expected_code_count 恒空）；A03 整单解析参数 ignore_part_success_flag
**全仓未实现**；A05 整单确认 vs 码级核验的两层展示未完。一笔 3/51 或 98.9631% 匹配都不构成启用依据。

## 二、各阶段结果与验收

| 阶段 | 结果 | 关键证据 |
|---|---|---|
| Q00 输入固定 | 25 份原件 SHA 全部一致；身份/授权/视图定义=r10 且与 r1 相同；受限 reader 不变 | [r2-analysis](evidence/r2-analysis-20261023T104928Z.json) |
| Q01 未匹配 5077 | 视图侧复核零变化；S01—S03 已选样（part_1 行39/40、part_10 行1175）；绑定包＋531 码/4974 旧单清单入受限目录；SQL04 已备；**源端 0 条（无审计身份）→ 全部 U05** | [classification-summary](evidence/classification-summary.json) |
| Q02 UNKNOWN 1396 | 三态 46/23/1327 与 r1 逐对比对零迁移；411 主单 | 同上 |
| Q03 重复 31 组 | 11 张主单整全取码，全部 INVALID/QD-DUP-CODE；整单双扫模式；每单其余门槛全过 | [classification-summary](evidence/classification-summary.json)；受限 q03-whole-bill-quality.json |
| Q04 时间口径 | 第一层完成；第二层未完成，SQL05/06 交付；旧 SQL 上界含 10-12 00:00 而 Excel 止于 10-11 23:52，边界差记录在案 | CANDIDATE_CHANGES C05 |
| Q05 修订方案 | 7 条候选/保留项成文；A01—A05 逐项检查表 | [CANDIDATE_CHANGES.md](CANDIDATE_CHANGES.md) |
| Q06 防重/发送 | 五问答案见上；既有 1 单 4 码不撤销不重发；平台 0 调用 | 本文 |

### CHK-01—10 自验

| 编号 | 结果 | 说明 |
|---|---|---|
| CHK-01 | 通过 | 原件/r1 成果未覆盖（verify_r2.py 校验 r1 工作簿仍在、摘要一致） |
| CHK-02 | 通过 | U01—U05 合计 5077；4974 旧单/531 码可追踪（受限清单） |
| CHK-03 | 通过 | 1396 条均有当前原值；退款拦截保持 |
| CHK-04 | 通过 | 31 组全覆盖；实际 11 主单；整单质量全 INVALID；未去重 |
| CHK-05 | 通过（如实标注） | 双向全量未做已明示；无同窗口证据不签完整性 |
| CHK-06 | 通过 | 候选方案含来源/值域/复合身份/回退；未改历史冻结包（0 改动） |
| CHK-07 | 通过 | A01—A05 逐项查代码，未实现明确写未实现 |
| CHK-08 | 通过 | 三层单号映射保留；1 单不撤销/重发；平台终态边界明确 |
| CHK-09 | 通过 | r2 工作簿 6535 行标识全文本/20 位码回读通过；ACL 仅当前用户+SYSTEM；共享证据无原码（自动扫描 0 泄漏） |
| CHK-10 | 通过 | 交接/待办/业务 README 同步；命令与退出码见 session；6 条 SQL 未执行状态逐条标注 |

## 三、交付物清单

- 本目录：[RESULT.md](RESULT.md)、[README.md](README.md)、[CANDIDATE_CHANGES.md](CANDIDATE_CHANGES.md)、
  [EXTERNAL_INPUTS.md](EXTERNAL_INPUTS.md)、[private-artifact-index.json](private-artifact-index.json)、
  [validation.json](validation.json)、[sql/](sql/)（04—06 新增，01—03 在 r1 目录）、
  [evidence/](evidence/)（run-manifest、classification-summary、两次运行回执与分析）、
  可复跑脚本 run_r2.py / run_q01b.py / analyze_r2.py / build_workbook_r2.py / verify_r2.py。
- 受限目录（本机，仅当前用户+SYSTEM）：差异明细 JSON、DBA 样本包与 531 码/4974 旧单清单、
  11 张整单质量明细、**新工作簿《历史上报根因核查_20261023_r2.xlsx》**（5077 逐条 U05+子标签+同日差、
  1396 复核、31 组整单结果，均含原文件/工作表/行号）。路径见 private-artifact-index.json。

## 四、实测 / 未测 / 外部阻断（如实区分）

- **实测通过**：受限 reader 身份与授权、两视图定义与 r10/r1 一致、Q02 三态零迁移、Q03 全部 11 整单
  质量判定、Q01 视图侧两个时点稳定性、同码同日统计、全部离线校验（AST/SQL 静态/SHA/ACL/泄漏扫描）。
- **实现未测**：无（本轮无产品代码改动，未跑产品测试——按任务书第十二条无需全量回归）。
- **外部阻断**：SQL01—06 六条源表 SQL 未执行（无已批准审计身份；平台登记宽源
  write_policy=medical_dict_push 非 readonly，按门禁停用且不绕过）→ 5077 条源端归因、
  31 组成因、同 CREATE_TIME 反向审计、（如需）平台终态查询。恢复条件与责任方见
  [EXTERNAL_INPUTS.md](EXTERNAL_INPUTS.md)（E01—E07）。

**原表根因未闭环；同窗口反向全量未做。以上结论不因此打折，也不冒充完成。**

---

# 源表核查（r2b，2026-10-23 晚，用户明确授权"授权你执行"后完成）

任务 DTR-HISTORY-SOURCE-AUDIT-20261023（ZCode/GLM-5.3）。经 <relay-jump> 受控连接器以平台托管只读账号
**READY_HIS**（服务 his）执行源表 SELECT：SQL01/02（S01—S03）、SQL04（531 未匹配码分批 + 31 重复码）、
SQL06（索引）、逐日覆盖（TRUNC 修正版）、阶段 E 三个判别聚合。合计约 **181 条 SELECT、在线约 23 秒**，
全部只读、限量、走既有索引；源写入 0、上传 0、生产改动 0。两次早期远程装配失败（未执行任何查询）与
一次逐日分组截断（已用 TRUNC 版重跑并作废原聚合）均保留回执，如实记录。

## 根因结论（原表证据闭环）

**1. 5077 条未匹配 = 全部 U02，且原因唯一：拆零扫码业务流的单号不在门诊处方主单编号空间。**

- 5077 条的 (码, 旧明细号) 记录**全部存在于 DRUG_USER.MED_TRACE_RECORD**：IN_OUT_TYPE=OUT、
  IS_DELETED=0、**SPLIT_FLAG=1（拆零标记，5077/5077）**、STORE_CODE 单一值 **050121**、
  TRACE_BILLNO 全部 20 位数字（2963 个不同值）。
- 三键 JOIN 未命中的机制证据：主单表 DRUG_PRESC_MASTER 该窗口 **PRESC_NO 长度只有 1—5 位**
  （阶段 E1），**不存在 20 位处方号**；050121 是主单表**最大药房**（窗口内 102,644 张，E2），
  排除药房失配；S01—S03 按（号+日期）不带药房查主单仍 **0 行**（E3），排除仅药房键失配。
- **判定**：这是药房 050121 的拆零（拆分摆药/发药）OUT 扫码记录，其单号体系与门诊处方号不相交，
  r10 三键 INNER JOIN **按设计不可能**包含它们。它们**不是正常门诊漏报**，也不是视图缺陷；
  是否需要上报该业务流、以哪张主单/单号语义上报，须由 HIS 厂商/药学部确认后另立来源 revision（L02/C02）。

**2. 31 组同单同码 = 真实双扫（1 小时内重复扫码），非拆零、非视图重复。**
每组恰好 2 条源记录：均 OUT、未删除、**SPLIT_FLAG=0**、三键 JOIN 命中；两条记录创建时间间隔
全部 <1 小时。11 张整单全部 INVALID/QD-DUP-CODE 维持；是否属错误重扫、如何更正/豁免，
待药学/HIS 业务规则（L04/C04）。另证：同 31 码在其他药房（050106/050105）各有 1 条正常记录，属独立事件。

**3. 逐日覆盖（主单侧，81 天）与 r1 视图交叉验证一致。**
窗口内主单 245,794 张；142,080 张有 OUT 码（共 484,567 行 OUT 关联）≈ r1 视图窗口内 484,539 行
（按 bill_time）——**视图没有丢失已 JOIN 上的记录**；103,714 张门诊主单无任何 OUT 码（含未扫码/退药等，
属 A02 完整性问题）；住院主单（PRESC_SOURCE=1，90,024 张）窗口内**零** OUT 码记录。

**4. 仍明确未做**：T 表全量 JOIN_MISS 计数——MED_TRACE_RECORD 无 PRESC_DATE/CREATE_TIME 索引
（SQL06 实证），全表扫被任务书禁止；5077 条 U02 已由精确键证据确认，不再是"未知人群"。
CREATE_TIME 同口径反向穷举仍需 DBA 索引/窗口决策。平台历史终态 0 查询（未授权即不查）。

## 修订后的五问答案

1. **r10 正常门诊可继续**；5077 条与它无关（拆零流），不构成对 r10 的否定。
2. **5077 条已解释完毕**（U02×5077，守恒）：拆零流记录；**不自动补报、不并入正常门诊**；
   是否覆盖待院方确认业务后另立 revision。
3. **退款拦截保留**（1396 条证据未变）；**重复 11 张整单保持 INVALID**，等业务规则。
4. 同窗口完整性：主单侧 81 天覆盖已完成并与视图交叉一致；T 侧 CREATE_TIME 反向仍未做（无索引）。
5. **持续自动推送仍不启用**：A02（10.4 万张无码主单的应报码数来源）、A03、A05 未解决。

## r2b 交付

- [evidence/classification-final.json](evidence/classification-final.json)：最终分类+根因+判别证据。
- [evidence/source-audit-20261023T141852Z.json](evidence/) 等运行回执（含两次失败尝试与逐日修正记录）。
- 受限目录新工作簿《历史上报根因核查_源端归因_r2b.xlsx》（5077 逐条源端归因、31 组成因、81 天覆盖），
  ACL 仅当前用户+SYSTEM；共享证据无原码（扫描通过）。
- 可复跑脚本：run_source_audit.py / run_daily_fix.py / run_stage_e.py / classify_source.py / build_workbook_r2b.py。
