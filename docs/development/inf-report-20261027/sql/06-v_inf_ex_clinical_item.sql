-- =============================================================================
-- 传染病前置软件上报 · 检查报告项目子表（emr_ex_clinical_item）取数视图 v0.3（2026-10-27 实测后修正）—— v0.5 对齐 crbsb 口径（2026-10-28）；**待 DBA 重建部署**
-- 任务：INF-DESIGN-20261027 ｜ 库：ODS ｜ 目标接口 /hclient/emr/receive/emrExClinicalItem
-- 源：HIS.EXAM_ITEMS(i)（主表 V_INF_EX_CLINICAL 按 EXAM_NO 关联）
-- 待核：EXAM_ITEM_CODE 非空率（常空时用名称占位并标注，值域代码规范率会受影响）
-- =============================================================================

CREATE OR REPLACE FORCE VIEW "ODS"."V_INF_EX_CLINICAL_ITEM" (
  "ID", "EX_CLINICAL_ID", "ITEM_CODE", "ITEM_NAME",
  "EXAMINATION_RESULT_CODE", "EXAMINATION_RESULT_NAME",
  "EXAMINATION_QUANTIFICATION", "EXAM_QUANT_UNIT",  -- 原名31字符超 ODS 30 上限
  "OPERATOR_ID", "OPERATION_TIME"
) AS
SELECT
  i.EXAM_NO || '_' || TO_CHAR(i.EXAM_ITEM_NO) AS ID,
  m.EXAM_NO AS EX_CLINICAL_ID,  -- 主子关联键=主表ID（crbsb 同款裸 EXAM_NO）
  SUBSTRB(NVL(TO_CHAR(i.EXAM_ITEM_CODE), TO_CHAR(i.EXAM_ITEM)), 1, 100) AS ITEM_CODE,  -- 编码空时名称占位
  SUBSTRB(TO_CHAR(i.EXAM_ITEM), 1, 100) AS ITEM_NAME,

  CAST(NULL AS VARCHAR2(5))  AS EXAMINATION_RESULT_CODE,         -- 检查结果代码，建议填，二期（阴性/阳性判定源待定）
  CAST(NULL AS VARCHAR2(20)) AS EXAMINATION_RESULT_NAME,
  CAST(NULL AS VARCHAR2(50)) AS EXAMINATION_QUANTIFICATION,      -- 定量结果：检查类多为定性，暂空
  CAST(NULL AS VARCHAR2(50)) AS EXAMINATION_QUANTIFICATION_UNIT,

  COALESCE(TO_CHAR(m.CONFIRM_DOCT), TO_CHAR(r.REPORTER), TO_CHAR(m.DOCTOR_USER)) AS OPERATOR_ID,  -- 确认医师空兜底报告医师/申请医师（MOCK 4.2万例卡空 2026-10-28）
  NVL(m.REPORT_DATE_TIME, m.REQ_DATE_TIME) AS OPERATION_TIME
FROM HIS.EXAM_ITEMS i
JOIN HIS.EXAM_MASTER m ON m.EXAM_NO = i.EXAM_NO
JOIN HIS.EXAM_REPORT r ON r.EXAM_NO = m.EXAM_NO  -- 与主表口径一致：只推已出报告的检查
JOIN HIS.PAT_VISIT pv ON pv.PATIENT_ID = m.PATIENT_ID AND pv.VISIT_ID = m.VISIT_ID  -- 住院判定=命中住院表（PATIENT_SOURCE 实测值域 NULL/1/2）
WHERE 1=1
  AND NVL(m.REPORT_DATE_TIME, m.REQ_DATE_TIME) >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
WITH READ ONLY;
