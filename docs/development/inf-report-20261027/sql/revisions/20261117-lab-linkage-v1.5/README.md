# 检验链路视图 v1.5 组合修订包（20261117 · 一次性合并 F01/F08/F02/F03）

- **任务**：PT-01-20261117（第三阶段"传染病推送内核加固与稳定性闭环"步骤 1）
- **修订依据**：`docs/development/inf-lab-linkage-review-20261115/revision-2/README.md` §2 F01/F02/F03/F08 与 §3 R1；
  独立复核 `revision-4-acceptance/README.md`（视图部分已 VALID，本包合并全部已验证修复，杜绝"改一处回退另一处"）。
- **前置基线**：`sql/13-v_inf_ex_lab.sql`（v1.4 在库版）、`sql/14-v_inf_ex_lab_item.sql`（v1.2 在库版）、
  `revisions/20261115-lab-linkage-v1.3/`（F01 修复稿，含 F08 放大缺陷，**不可直接回滚复用**）、
  `revisions/20261115-lab-linkage-v1.5/`（前一版修订稿；其 01 号对照视图缺 TEST_NO/CLINIC_NO 列，
  与 02 号主视图 JOIN 不自洽——本包替代该组合，原目录冻结只读保留）。
- **历史原件全部只读保留**，本目录为不可覆盖的新交付；DBA 按 01→02→03 顺序执行。

## 修订内容（四项一次性合并）

| 缺陷 | 修订 | 落点 |
|---|---|---|
| **F08** 候选计数粒度错误：v1.3 按 (patient, test_no, clinic_no) 分组后计数恒 1，且输出丢弃报告键导致同一就诊 3 报告膨胀 9 行 | 以**报告完整键 (PATIENT_ID, TEST_NO)** 为归属粒度统计候选就诊数 `CAND_CNT=COUNT(DISTINCT act.SERIAL_NUMBER)`；`CAND_CNT=1` 唯一绑定（急诊保持 `'2'`），`0/>1` 将 `SELECTED_SERIAL_NUMBER/ACTIVITY_TYPE_CODE` 置空进入等待（0/1/N 铁律，不任取 MIN/MAX） | `01-m_inf_fxhis_activity_lab_v15.sql` |
| **F01** 急诊类型回退：在库 13 号门诊段写死 `'1'`，急诊患者（锚点 type=2）被错标门诊 | 门诊段类型/流水**只来自对照视图唯一候选**（`actv.ACTIVITY_TYPE_CODE`，急诊='2'），绝不再写死 | `02-v_inf_ex_lab_v15.sql` |
| **F02** 科室码冒充员工号：v1.4 把执行科室 `PERFORMED_BY` 兜底进 `OPERATOR_ID`，掩盖"缺真实操作人" | `OPERATOR_ID` **只允许真实人员工号**（`TRANSCRIPTIONIST` 为工号、或姓名命中 `STAFF_DICT.EMP_NO`）；彻底删除 `NVL(TO_CHAR(a.PERFORMED_BY),...)` 兜底；缺失保持 `NULL` → 推送器 `client-validate` 拦截进入质量等待 | `02-v_inf_ex_lab_v15.sql`（住院+门诊两段） |
| **F03** 一般定量 H/L 误映射耐药码：在库 14 号把 `H/L` 转国标药敏 `'12'/'10'`（高/低度耐药） | 一般定量偏高/偏低只保留源异常标志（`SOURCE_EXAMINATION_RESULT_CODE/NAME` 与参考区间标志 `RI`），`EXAMINATION_RESULT_CODE` 置 `NULL`；仅真实定性（阳/阴/弱阳性）与药敏（敏感/耐药）语境才填标准码 | `03-v_inf_ex_lab_item_v15.sql`（住院+门诊两段） |

其余口径不变：住院段窗口 2026-01-01、门诊段窗口 **2026-11-14** 起（主子一致）；
明细 `EX_LAB_ID` 与主视图同一报告候选集对齐（主视图因 0/N 等待不出的报告，明细同样不出，主子齐套）。

## 离线等价语义验证

`implementation/tests/unit/test_pt01_inf_pusher_reliability.py`（PT-01 专项套件）在内存 SQLite 中
构造四类合成场景验证本包语义（与 DBA 执行后的线上验证 SQL 同口径）：

1. 同一急诊就诊 3 张检验单 → 对照视图恰好 **3 行**、`CAND_CNT=1`、`ACTIVITY_TYPE_CODE='2'`（不膨胀 9 行）；
2. 同患者同日 2 次门诊就诊 → 每张报告 `CAND_CNT=2` → 置空等待，主视图 0 行（不瞎猜 MAX）；
3. `TRANSCRIPTIONIST` 为空且 `PERFORMED_BY` 为科室码 → `operatorId=NULL` → 被 `validate_required` 拦截；
4. 定量 `H/L` 明细 → `EXAMINATION_RESULT_CODE` 不产出 `'10'/'12'`，参考区间标志 `2/1` 保留。

## DBA 执行与验收顺序

1. 执行 `01-m_inf_fxhis_activity_lab_v15.sql`（新建对照视图，无表依赖）→ 跑文件内验证 SQL；
2. 执行 `02-v_inf_ex_lab_v15.sql`（重建 V_INF_EX_LAB，门诊段改走对照视图）→ 跑文件内验证 SQL；
3. 执行 `03-v_inf_ex_lab_item_v15.sql`（重建 V_INF_EX_LAB_ITEM，结果语义修复）→ 跑文件内验证 SQL；
4. 汇总验收（三视图 VALID、本例三父 type=2 唯一命中、主 3/子 38 对齐、6 单 operatorId 不再是科室码）。

## 回滚

```sql
-- DROP VIEW ODS.V_INF_EX_LAB_ITEM;      -- 重建 v1.2（sql/14-v_inf_ex_lab_item.sql 原件）
-- DROP VIEW ODS.V_INF_EX_LAB;           -- 重建 v1.4（sql/13-v_inf_ex_lab.sql 原件）
-- DROP VIEW ODS.M_INF_FXHIS_ACTIVITY_LAB_V15;
```

外部已受理事实不因视图回滚回退；更正走受限新清单（revision-2 R6），不整批重放。
