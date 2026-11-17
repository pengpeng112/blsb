# HIS 追溯视图性能调优分析与方案（2026-10-29，只读取证）

任务 DTR-VIEW-TUNING-20261029（ZCode/GLM-5.3）；用户授权连库只读分析并给出优化视图、
**不影响结构**。全部经 <relay-jump> 受控连接器（平台只读账号）单条 SELECT 完成——
**零 DDL/DML，未创建/修改任何数据库对象**。

## 一、问题定位（全部实测，样本键 `2026-10-26 09:31:22|50235|050121`，10-28 上午窗口）

驱动 v3 日批采集 ~1.7s/查询、全天窗口采集 ~6h，超过驱动 4h 等待上限导致连续
collect_timeout（10-27/28/29 三天，补跑事实见 timed-report 任务目录）。

### 计时矩阵（含 ~2.7s 通道基线；净耗时=实测−基线）

| # | 查询 | 形态 | 净耗时 | 结论 |
|---|---|---|---|---|
| B | poll 窗口（视图按 updated_at 10 分钟窗 COUNT） | `IDX_DISPENSING_DATETIME` 生效 | **≈0s** | 轮询不是瓶颈 |
| C | bill 单头（视图按 source_bill_id 等值） | 拼接键谓词→主表全表扫 | **~1.1s** | 瓶颈① |
| D | bill 码明细（视图按 source_bill_id 等值） | 拼接键+TO_CHAR 连接→双表全表扫哈希 | **~1.0s** | 瓶颈② |
| E2 | 同 D 改写为 WITH 先取 M 再连 T（无 hint） | 优化器合并后同一计划 | ~1.0s | **纯改写查询形态无效** |
| F | T 侧分解谓词（TRACE_BILLNO/PRESC_DATE/STORE_CODE 等值） | `med_trace_record_trace_billno` 索引 | **≈0s（毫秒）** | 正确路径存在且极快 |
| G | M 侧 PK 分解谓词（PRESC_DATE/PRESC_NO） | `PK_DRUG_PRESC_MASTER` 索引 | **≈0s（毫秒）** | 同上 |
| E/E3 | 视图内 hint 方案 | 连接器只读校验**拦截一切注释** | 无法实测 | hint 路线须 DBA 在库上 EXPLAIN 验证（见方案2声明） |

F 与 D 结果一致（N=2，同一单同码数）——**数据无差异，差异只在访问路径**。

### 根因（两类索引失效模式）

1. **拼接键谓词**：应用按 `source_bill_id = :sb` 查单，该列是
   `TO_CHAR(PRESC_DATE,'…')||'|'||TO_CHAR(PRESC_NO)||'|'||DISPENSARY` 三列拼接——
   任何普通索引都无法服务拼接等值 → 每单查询全表扫 DRUG_PRESC_MASTER（702 万行 / 21.5 万块）。
2. **函数在索引列一侧的连接**：`T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO)` 使 M 侧
   PRESC_NO 相关索引（PK 前缀 / `_INDEX_01`）失效；优化器对拼接等值的基数不确定，
   选择了 M+T 双全表扫哈希（T=MED_TRACE_RECORD 839 万行）。
   ——与 scope 会话在住院分支二发现的是同一模式（`TO_CHAR(DIS_RECORDNO)`，实测 3.9s/查询）。

### 现有可用索引（实测取自 ALL_IND_COLUMNS；调优完全不必新增即可救码表侧）

- DRUG_PRESC_MASTER：`PK_DRUG_PRESC_MASTER(PRESC_DATE,PRESC_NO)` 唯一；
  `PK_DRUG_PRESC_MASTER_DIS(PRESC_DATE,PRESC_NO,DISPENSING_DATETIME,DISPENSARY)`；
  `IDX_DISPENSING_DATETIME`；`DRUG_PRESC_MASTER_INDEX_01(PRESC_NO)`
- MED_TRACE_RECORD：`med_trace_record_trace_billno(TRACE_BILLNO)`；`med_trace_record_trace(T_TRACE_CODE)`；PK(RECORD_ID)
- PHA_INP_DISPMASTER：`PK_PHA_INP_DISPMASTER(DIS_RECORDNO)` 唯一；`IDX_PHA_INP_DM_DISP(DISP_DATE)`
- PHA_INP_REQUEST_DRUG：`IDX_REQUEST_NO`；`IDX_INP_REQ_EXEC_TIME(DISP_EXECUTE_TIME,EXEC_SQN,DISP_FLAG,DISP_DATE)` 等

## 二、方案矩阵（按推荐顺序；**本会话均未执行**，改动均需 DBA/用户批准）

### 方案 1（治本，推荐）：主表上建一个函数索引（FBI）——索引级变更，不动表/列/数据

`sql/fbi_dba_option.sql`：在 PHARMACY.DRUG_PRESC_MASTER 上按**视图 source_bill_id 的原表达式**
建 FBI（ONLINE 建索引，不锁表写）：

```sql
CREATE INDEX PHARMACY.IX_DT_BILLKEY ON PHARMACY.DRUG_PRESC_MASTER(
  TO_CHAR(PRESC_DATE,'YYYY-MM-DD HH24:MI:SS')||'|'||TO_CHAR(PRESC_NO)||'|'||DISPENSARY)
  ONLINE NOLOGGING TABLESPACE <索引表空间>;
```

- 效果（**收敛后措辞，Codex 审查 r1#1/#2**）：单头与码明细的 `source_bill_id = :sb` 变为
  非唯一 FBI 的等值 INDEX RANGE SCAN（毫秒级），**显著降低逐单查库成本、有望使采集回到
  驱动 4h 等待上限内**——注意生产回执中约一半耗时未被 query_seconds 覆盖（10-29 回执总
  14406s、Oracle 查询仅 7245s，余 ~7160s 为入库/质量处理/排队等，见 §七），整体收益以
  完整日批窗口实测为准，不承诺"分钟级"。
- **视图零改动**，应用零改动，回滚 = `DROP INDEX PHARMACY.IX_DT_BILLKEY`（附验证 SQL 与回滚）。
- 注意事项（DBA）：①索引表达式须与视图列定义**语义一致（解析树等价**——空白/大小写无关，
  但增删格式参数/CAST 即不匹配，须重新验证）；②NLS（已实证收窄，2026-10-31）：库级 NLS_CALENDAR=GREGORIAN、
  NUMERIC_CHARACTERS='.,'、AMERICAN；PRESC_NO 全表 702 万行均为非负整数→小数分隔符风险排除；
  日期掩码全数字→DATE_LANGUAGE 无影响；剩余动作=建索引会话与应用会话 NLS_SESSION_PARAMETERS 比对；
  ③ONLINE 建索引属 EE 特性（DBA 先确认 Edition），允许并发 DML 但有 IO/CPU/临时空间开销，
  低峰执行；FBI 会引入隐藏虚拟列——"零改动"指业务可见结构（列/约束/视图契约）不变；
  仍属 schema 对象新增，按授权流程执行（NOLOGGING 与否由 DBA 按备份策略定，本方案默认不写）。
- 代价评估（粗预算，非核定）：键 ~32B/行、702 万行键文本 ~215MiB，含开销数百 MB 量级，
  建议 DBMS_SPACE.CREATE_INDEX_COST 核定+预留临时空间；写入维护代价按 HIS 实际写入
  （不止上报相关表）低峰确认，不以"日增 3 千"直接断言可忽略（Codex #19/#20）。

### 方案 2（零 schema 变更的纯视图文本改写）：视图内嵌 hint——只救码明细查询

`sql/r10_views_v2_hinted.sql`：给出 V_DRUG_TRACE_CODE v2（列名/列序/语义与现版完全一致，
仅在 SELECT 后内嵌 `/*+ LEADING(M) USE_NL(T) INDEX(T med_trace_record_trace_billno) */`，
强制"主表扫一次（拼接过滤）→ 码表索引嵌套循环"）。V_DRUG_TRACE_BILL **不改**
（单表拼接谓词无索引可用，hint 无从发挥；强加 INDEX 提示反而可能逼出更差的索引全扫）。

- 预期（**未经实测，如实声明**）：码明细 ~1.0s → ~0.5–0.9s（省掉码表 839 万行全扫），
  单头不变；日批或可压回 4h 等待内，但**不治本**。
- **验证前置（修正措辞，Codex #22/#23）**：本会话两条通道（应用账号无基表权限/平台连接器
  拦注释）无法实测 hint；但 **DBA 有基表权限，可直接执行展开后的带 hint SELECT 计时+看计划，
  无需先替换生产视图**——验证通过再 CREATE OR REPLACE；
  回滚=恢复原文本（文件内附原版全文）。CREATE OR REPLACE VIEW 不改表结构，
  但属生产对象变更，走 DBA 流程。
- 方案 1 落地后本方案即无必要（FBI 路径更优）。

### 方案 3（长期备选，不动库）：应用侧查询模板按分解列过滤

把 bill_header/bill_codes 模板改为按原始三列（PRESC_DATE/PRESC_NO/DISPENSARY）绑定
（视图需暴露原始列或新建对象）+ 发版部署。改动面最大，方案 1 生效后无必要。

## 三、r11 住院视图（未建）预防性修正——创建前改掉两类失效模式

`docs/development/drug-trace-scope-dev-20261027/sql/r11_inpatient_views_v3.sql` 存在同类问题
（分支二 `T.TRACE_BILLNO = TO_CHAR(d2.DIS_RECORDNO)` 使 `PK_PHA_INP_DISPMASTER` 失效=scope 实测
3.9s/查询；`TO_CHAR(w.DISP_EXECUTE_TIME,'…') = d.DISP_DATE` 使 w 侧 DISP_EXECUTE_TIME 索引失效）：

1. 键连接：`TO_CHAR(d2.DIS_RECORDNO)` → 要么对 d2 建 FBI `TO_CHAR(DIS_RECORDNO)`（DBA），
   要么保持现式但接受 d2 侧由其他谓词收窄后小范围扫（分支二主谓词已在 d2 上时的实际代价需 EXPLAIN）。
2. 日期连接（DISP_DATE 是 **VARCHAR2**、DISP_EXECUTE_TIME 是 DATE）：
   现式 `TO_CHAR(w.DISP_EXECUTE_TIME,'YYYY-MM-DD HH24:MI:SS') = d.DISP_DATE` 函数落在 w 侧
   （有索引侧）——建议反转为 `w.DISP_EXECUTE_TIME = TO_DATE(d.DISP_DATE,'YYYY-MM-DD HH24:MI:SS')`，
   函数移到无索引/被驱动的 d 侧，恢复 `IDX_INP_REQ_EXEC_TIME` 可用性。
   （**等价性必须先证明**：原字符串比较遇脏值仅不匹配，TO_DATE 转换遇脏值会整查询报错——
   须按 scope 会话实测样本验证全量值可解析且结果一致，不能只证明"能走索引"（Codex #25）。）
正式修正属 scope 会话 r11 范围（勿双头改），以上为提给该会话/DBA 的证据与建议。

## 四、本次实测 SQL 清单（审计）

视图存在性/表行数(ALL_TABLES)/索引(ALL_INDEXES+ALL_IND_COLUMNS)/关键列类型(ALL_TAB_COLUMNS)、
样本键取数（10 分钟窗）、B/C/D/E2/F/G/E3 计时（COUNT 聚合，max-rows≤3，无患者明细导出）。
原始输出在本机受限目录与本文表格；连接零写入。

## 五、建议执行顺序

1. DBA 低峰执行方案 1（FBI）→ 跑 fbi_dba_option.sql 内验证 SQL 确认单头/码明细毫秒级；
2. 观察次日凌晨 01:00 日批（应直接 DONE，日报 outcome=OK）；
3. 若 FBI 无法批准，再走方案 2（DBA EXPLAIN 验证 hint 后替换视图）；
4. r11 创建前按 §三 修正草案（交 scope 会话合并）。


## 六、2026-10-31 补充实测：'只改视图不加索引'的可行性边界（BLYB_DRUGTRACE 生产账号）

用户追问"可以只改视图不加索引吗"。经 worker 容器以生产只读账号（thick 模式）实测
（evidence/hint-test-20261031T023339Z.txt）：

| 测试 | 结果 |
|---|---|
| 码明细·视图现状 ×2 | 1.152s / 1.102s |
| **码明细·外层 hint（LEADING+USE_NL+INDEX）×2** | **1.127s / 1.095s——无效果**（hint 未绑定到合并后的视图内部别名，被静默忽略） |
| 单头·视图现状 ×2 | 1.105s / 1.148s |
| 1 小时窗 557 行全列 ORA_HASH | 两次完全一致（数据可复现） |

结论修正与边界（如实）：

1. **拼接等值谓词（source_bill_id）无视图文本解法**：`f(a)||'|'||g(b)||'|'||c = :bind` 无法被
   优化器分解，也没有任何视图写法能凭空构造索引访问路径——单头查询 ~1.1s（全表扫 702 万行）
   是无函数索引时的下限，**视图怎么改都一样**。
2. **hint 路线的实证降级**：外层对视图加 hint 实测无效；视图内嵌 hint 与外层机制不同
   （建视图时绑定自身查询块）**仍可能生效但无法在只读通道验证**（最小权限账号只能查视图、
   平台连接器拦注释、CREATE VIEW 是 DDL）。若走此路必须 DBA 建后跑验证 SQL，无效即回滚。
3. 即使视图内 hint 生效，**上限也只救码明细一半负载**（~1.1s→~0.5-0.9s），单头不变；
   按 10-31 观测的 ~3.5s/查询恶化水平，改完凌晨批次仍可能超 4h 上限。
4. 因此：**确定生效、零改动、结果零影响的唯一方案仍是 FBI（方案1）**；视图 hint 是
   "不确定生效、生效也只解一半"的备选；应用侧模板分解列改造是第三条路（需发版）。


## 八、2026-10-31 追问评估："改成物化视图（MV）可以吗"

用户追问 MV 替代 FBI。结论先行：**机制上可行且查询路径最干净（拼接键固化为普通列+普通 B-tree 索引，
无 FBI 的表达式匹配/NLS 匹配顾虑），但按本项目"结果不能受影响"的硬约束，MV 的代价大于 FBI**。

### 设计形态（应用零改动）

```
CREATE MATERIALIZED VIEW PHARMACY.MV_DT_BILL REFRESH <策略> AS
  SELECT <与 V_DRUG_TRACE_BILL 完全相同的列与表达式> FROM PHARMACY.DRUG_PRESC_MASTER;
CREATE INDEX IX_MV_DT_BILLKEY ON MV_DT_BILL(source_bill_id);   -- 普通索引，非 FBI
CREATE OR REPLACE VIEW V_DRUG_TRACE_BILL AS SELECT <同列> FROM MV_DT_BILL;  -- 视图改指向 MV
（码视图同理：MV_DT_CODE 物化 T⋈M 连接，并可把 TO_CHAR(PRESC_NO) 存为普通列供连接索引使用）
```

### 刷新策略三选一——每档都有硬代价（HIS 库实测先例：仅 HISUSER 3 个 BI 日结 MV、全 DEMAND/COMPLETE，
**全库零 MV 日志**，即从未有过 FAST/ON COMMIT 先例）

| 维度 | FBI（方案1） | MV·定时完整刷新 | MV·FAST ON COMMIT |
|---|---|---|---|
| 结构/对象影响 | 1 个索引 | MV 段+索引自建+刷新 job（空间 ~3-5GB vs FBI ~0.5GB） | 另需**两张 HIS 热表建 MV 日志**+同步维护（无先例） |
| 数据新鲜度 | 实时 | **陈旧窗口** | 实时 |
| HIS 写入影响 | 每行多一次索引维护 | 刷新时段读压力 | **每次 DML 同步维护**（门诊开方高峰直接加延迟） |
| NLS | 查询时表达式匹配（已实证收窄） | 仅刷新时计算一次（存储后无匹配问题，**此项最干净**） | 同左 |
| 新增失败点 | 无 | **刷新失败→批次静默读旧数据** | MV 日志膨胀/刷新积压 |

### 关键判定：为什么"定时完整刷新"在这里不安全

本项目采集语义要求"读到当前真实数据"：① 迟到码是已实证现象（明细修改晚于发药 20–251 秒，且 lookback
机制专治晚到）——夜间刷新后白天到达的码改动会被**漏读→按旧码冻结发送**，这是改变业务结果的正确性风险；
② 现行运维已常态化**白天手动补跑**（读昨日窗口+当日改动）——陈旧窗口直接命中；③ 刷新 job 本身成为
01:00 批次前的新单点故障。FAST ON COMMIT 虽无陈旧问题，但等于给 HIS 两张最热的表加同步写放大，
比一个 FBI 侵入大一个数量级，且该库无此类先例。

### 结论

- 若目的只是"避开 FBI 的表达式/NLS 顾虑"——MV 确实把这层顾虑消掉了（键在刷新时刻固化），
  但换来的要么是陈旧正确性风险（COMPLETE），要么是 HIS 写路径放大（FAST）。
- **在"不影响结果"硬约束下维持推荐顺序**：FBI（INVISIBLE 先行验证）> 视图 hint > MV。
- MV 仅在一种前提下值得考虑：院方/DBA 明确接受"迟到码最多延迟一个刷新周期"（改变业务语义，
  需业务签字，不可默认），且选择 FAST 定时刷新（仍需 MV 日志）。
