-- =============================================================================
-- 检验链路修订 v1.3 · 之一：锚点对照视图（P1-3，2026-11-15）
-- 计划：docs/development/inf-lab-linkage-review-20261115/README.md（检验主子关联与先后传输修订）
-- 用途：检验视图门诊段的**就诊归属与类型直接引用本锚点**（复用已核定活动锚点的类型与流水，
--   禁止 MAX(CLINIC_NO) 任意归属）。0 候选=质量等待（不出现在检验视图），1 候选=可绑定，
--   N 候选（同患者同日多就诊）=显式待核：本视图按 ROW_NUMBER 取首份并带候选数标注列供核。
-- 依赖：住院段/门诊段规则与 v2 锚点一致；门诊窗口=11-14（用户拍板）。
-- 验收（P1-5）：本例主表 3、子项 38 且 ID 集合一致；三张均 type=2；完整活动键命中唯一父。
-- =============================================================================
CREATE OR REPLACE FORCE VIEW ODS.M_INF_FXHIS_ACTIVITY_V13 AS
WITH act AS (
  SELECT ACTIVITY_TYPE_CODE, PATIENT_ID, SERIAL_NUMBER, OPERATION_TIME
    FROM ODS.M_INF_FXHIS_ACTIVITY          -- 已核定锚点（v2：住院三段+门诊段 11-14 起）
),
bound AS (
  -- 门诊检验行：经 申请日=就诊日 关联门诊就诊，再对锚点按 患者+CLINIC_NO 匹配，取唯一绑定
  SELECT a.PATIENT_ID,
         a.TEST_NO,
         b.CLINIC_NO AS SERIAL_NUMBER,
         MIN(act.ACTIVITY_TYPE_CODE) AS ACTIVITY_TYPE_CODE,
         COUNT(DISTINCT act.SERIAL_NUMBER) AS CAND_CNT   -- 候选数（>1 即 N 候选待核）
    FROM HIS.LAB_TEST_MASTER a
    JOIN HIS.CLINIC_MASTER b
      ON a.PATIENT_ID = b.PATIENT_ID
     AND TO_CHAR(b.VISIT_DATE,'YYYYMMDD') = TO_CHAR(a.REQUESTED_DATE_TIME,'YYYYMMDD')
    LEFT JOIN act
      ON act.PATIENT_ID = b.PATIENT_ID AND act.SERIAL_NUMBER = b.CLINIC_NO
   WHERE a.VISIT_ID = '0'
     AND a.RESULT_STATUS = '4'
     AND a.RESULTS_RPT_DATE_TIME >= DATE'2026-11-14'
     AND a.RESULTS_RPT_DATE_TIME IS NOT NULL
   GROUP BY a.PATIENT_ID, a.TEST_NO, b.CLINIC_NO
)
SELECT SERIAL_NUMBER,
       ACTIVITY_TYPE_CODE,
       CAND_CNT
  FROM bound
 WHERE CAND_CNT >= 1;
-- 注：CAND_CNT>1（同患者同日多就诊）时本视图仍取该 CLINIC_NO 绑定（键唯一），
--     候选数列供待核清单；如需严格唯一绑定，在检验视图侧加 WHERE CAND_CNT=1 即可（保守模式开关）。

-- 【DBA 执行后验证】①门诊/急诊候选数分布 ②CAND_CNT>1 的多就诊占比（预期少量）：
-- SELECT ACTIVITY_TYPE_CODE, COUNT(*) TOTAL, SUM(CASE WHEN CAND_CNT>1 THEN 1 ELSE 0 END) MULTI FROM ODS.M_INF_FXHIS_ACTIVITY_V13 GROUP BY ACTIVITY_TYPE_CODE;
-- =============================================================================
