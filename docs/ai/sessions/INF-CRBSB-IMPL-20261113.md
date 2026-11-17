# INF-CRBSB-IMPL-20261113 · 检验/医嘱/生命体征 5 接口视图 SQL 交付（实测通过）

- 会话：`20261113T090243Z-596c8fd1`（编制计划）+ 本次实施续。ZCode/k3.1-pro，2026-11-13。
- 承接：[INF-CRBSB-PLAN-20261113.md](./INF-CRBSB-PLAN-20261113.md)（计划）→ 用户三点确认（crbsb 仍在推须停/仅住院/重叠可切换）→ "crbsb 你提供 sql"。
- 产出：5 个 V_INF_* 视图 SQL + tables.py 注册段，**全部 ODS 只读实测通过**。

## 交付物（docs/development/inf-report-20261027/sql/）

| 文件 | 接口 | 方案 | 实测 |
|---|---|---|---|
| 13-v_inf_ex_lab.sql | 检验报告 | 回源表 LAB_TEST_MASTER（去 DISTINCT+冗余 JOIN，activity_type 修正=6） | ROWNUM<=50 ✅ |
| 14-v_inf_ex_lab_item.sql | 检验项目 | 回源表 LAB_TEST_MASTER+LAB_TEST_ITEMS+LAB_RESULT | ROWNUM<=20 ✅ |
| 15-v_inf_order.sql | 医嘱处方 | 回源表 HIS.ORDERS（ORDER_CLASS in('A','B')+EXISTS DIAGNOSIS+锚点） | ROWNUM<=50 ✅ |
| 16-v_inf_order_item.sql | 医嘱条目 | 回源表 HIS.ORDERS+PUB_LYYP+DRUG_DICT（主子 id 同源） | ROWNUM<=20 ✅ |
| 18-v_inf_vital_signs_record.sql | 生命体征 | 包 CRBSB 一层+activity_name 归一'住院' | ROWNUM<=20 ✅ |
| tables-register-5views.py | — | tables.py 新增 5 注册段（含 depends_on 主子序） | — |

## 关键技术结论（实测驱动）

1. **serial 无需重算**：CRBSB 各视图 serial 原生=INP_NO‖VISIT_ID（医嘱复合键早已被 CRBSB 注释）——初判"无法归一"不成立。
2. **性能决定路径**：CRBSB.emr_ex_lab 连 ROWNUM<=3 都超时（DISTINCT+多 LEFT JOIN）→ 回源表；
   emr_order 包一层锚点 EXISTS 超时 → 回源表；emr_vital_signs_record 包一层正常。
3. **推送器是增量查询**（水位窗口几天），回源表后增量窗口实测正常。
4. **口径修正**：检验 activity_type_code 住院=6（CRBSB 误用 2）；生命体征 activity_type_name 归一'住院'（CRBSB 原'入院'）。
5. **重叠 3 张（09/11/12）维持不动**：CRBSB.id=INP_NO‖次数 vs 我方 id=patient_id‖次数——切换会重复，不建议。

## 待执行（交 DBA/执行 AI）

1. **双推**：crbsb 仍在推这 5 接口——上线前须信息科停 crbsb 对应接口推送。
2. DBA 执行 5 个视图 SQL（含验证 SQL：行数/可关联率 100%/主子对齐 100%）。
3. tables.py 合并 tables-register-5views.py 的 5 段 + ROLL_OUT_ORDER 追加。
4. 六步法上线：mock→real limit3→放量，failed=0、errorCode 02/03/04=0。
5. 铁律回写（01/02/interfaces 13-18/03 矩阵/STATE/BACKLOG/交接/inventory）。

## 档案回写

计划文件追加"实施修订记录"（本文件要点已并入 plans/20261113-检验医嘱生命体征上报执行计划.md 附录）。

## 追加：视图部署复核 + 上线提示词（2026-11-14）

用户已执行 5 视图 SQL（DBA 建）。复核：5 视图全部 VALID；近 30 天可关联率 100%
（检验 46892/医嘱 172712/生命体征 260469 行全部命中诊疗锚点）。子表全表 COUNT 超时=大表正常现象
（推送器增量查询不受影响，已实测 ROWNUM 正常）。主子对齐由同源同键设计保证（ex_lab_item.ex_lab_id=TEST_NO、
order_item.order_id=patient_id‖visit_id‖order_no‖order_sub_no），上线后小窗口验证即可。
产出一次性任务提示词：[plans/20261114-五接口上线执行提示词.md](../../数据上报/4、传染病病历数据上报/plans/20261114-五接口上线执行提示词.md)
（Ph0 双推停止→注册→部署→六步法→验证→铁律回写，自包含可交执行 AI 一次完成）。

## 追加：order_item PUB_LYYP 放大问题修复（2026-11-14，INF-CRBSB-REVIEW）

**问题**（执行 AI 发现）：order_item 年度 fetch 300s 超时，行集 ~2× 放大——根因=`LEFT JOIN ODS.PUB_LYYP`
（8590 行仅 6096 个 HIS药品编码，一码多行致 JOIN 放大+执行计划差）。**这是我初版 SQL 的缺陷**。

**修复过程**：
1. 先试"JOIN 改去重内联（GROUP BY HIS药品编码）"——行数恢复但**全窗口仍超时**（近 1 天也超时）：
   去重只解决行数、未解决大表 JOIN 执行成本。
2. **最终方案：去掉 JOIN PUB_LYYP，drug_code/drug_name 改标量子查询**（标准编码优先、ORDER_CODE/ORDER_TEXT
   兜底），与 order 主表同执行路径（his.orders+pat_visit+pmi+锚点）。

**实测验证**：①ROWNUM<=20 正常；②近 1 天全窗口 **TOTAL=6184、DISTINCT_ID=6184（不超时、无重复无放大）**。
对比：order 主表 mock 全过 vs order_item 超时，唯一差异就是 JOIN PUB_LYYP——去 JOIN 后性能与主表一致。

**文件**：16-v_inf_order_item.sql（头部已注修复说明）。**需 DBA 重新执行该文件替换视图**，并补 order_item mock。

**给执行 AI 的评价**：Ph0 双推未确认→停 Ph0 不推（正确）；selftest 66/66 全绿；主子对齐 100%；铁律回写规范；
已上线 6 表零影响。selftest 两处按 6 表硬编码断言改动态=必要修正；vital CREATE_DATE 不映射（非必填防格式拒收）=合理。
**mock 误用生产 config 向生产 receipts 追加 101,256 行 mode=mock 回执**——操作失误（mock 应隔离 config），
虽 append-only 不影响水位/已发，但污染生产回执（对账依据），需运维按 mode 过滤/清理，后续 mock 一律用隔离 config。

## 九、5 新表上线实录（2026-11-15，INF-5VIEW-GO，Ph0 实证解除后用户拍板）

1. **Ph0 实证**：他方日志近 30 日 22,078 条=诊疗活动 16,259+患者基本信息 5,819，**检验/医嘱/生命体征特征零命中**——双推解除。
2. 13 表版切容器（17:13）→ 立即 5 表 limit3：ex_lab/ex_lab_item/order/order_item 4 表全过；
   **vital_signs 3 条全 04"创建时间必填"**（CREATE_DATE 14 位串未映射——执行 AI 曾弃映射防格式错，平台实测必填）
   → 出班（12 表版 17:18 切回）。
3. **4 表放量**（分批 limit 8/15 万多轮）：ex_lab 100,942 ✅；ex_lab_item ~123 万 ✅；order ~29 万（**01×7 平台主键冲突**尾差）；
   order_item ~37 万 ✅。水位全推至 11-14 19:27（order_item 曾卡 11-04=批量轮窗口边界，补轮即进，非缺陷）。
4. **04 尾差定位**：ex_lab_item 04×21="检验定量结果参考区间-下限长度不超过20位"——CRBSB 原版 UPPER 有截断 LOWER 漏；
   **14 号 v1.1（SUBSTRB 20 截断）修复稿已备**。
5. **vital_signs 修复包**：18 号 v1.1（CREATE_DATE=TO_CHAR(TO_DATE(14位),'标准串')，实测非空）+ tables.py map 恢复
   createDate 映射（已部署 staging+容器）——**待 DBA 执行 18 号 v1.1 后把 vital_signs 加回 ROLL_OUT_ORDER 上线**。
6. 回执量说明：receipts 中 ex_lab_item/order_item true 行数含 01/99 重试多行（批压下平台慢自动重试，最终成功），
   sent 统计以 cron/命令输出为准。

## 十、vital_signs 收口——13 接口全家桶上线完成（2026-11-15，INF-VITAL-GO）

v1.1 两视图 DBA 执行：ex_lab_item 下限超长 0/30 天、vital CREATE_DATE 异常 0。vital_signs 加回 13 表版
（selftest 68/68、切容器 19:51）→ limit3 3 条全 true → 放量 5000 条 sent=4757 failed=0。
**13 接口全部在推**：death/admission/first_course/daily_course/exam/exam_item/admission_info/discharge_info/
ex_lab/ex_lab_item/order/order_item/vital_signs。放量累计 ~199 万条受理 failed≈0
（尾差：01 主键冲突×7 已留观、04 下限超长×21 已由 v1.1 消除）。
