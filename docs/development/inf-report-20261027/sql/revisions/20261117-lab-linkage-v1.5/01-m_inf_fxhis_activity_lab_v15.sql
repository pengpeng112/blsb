-- =============================================================================
-- 检验链路视图 v1.5 组合修订 · 01：门诊检验报告 ↔ 诊疗活动 候选对照视图
-- （PT-01-20261117 步骤 1，修 F08+F01 锚定粒度）
--
-- 语义（0/1/N 铁律，取代 revisions/20261115-lab-linkage-v1.3 的有放大缺陷锚点稿）：
--   * 以**报告完整键 (PATIENT_ID, TEST_NO)** 为归属粒度（v1.3 先按 clinic_no
--     分组导致计数恒 1、输出又丢弃报告键导致同一就诊 3 报告膨胀 9 行——均废弃）；
--   * CAND_CNT = 该患者"报告申请日当天"的全部门诊/急诊候选就诊数
--     COUNT(DISTINCT act.SERIAL_NUMBER)（锚点 LEFT 关联，0 候选也保留报告行）；
--   * CAND_CNT = 1  → SELECTED_SERIAL_NUMBER/ACTIVITY_TYPE_CODE/NAME 唯一绑定
--     （急诊='2'、门诊='1'，直接来自锚点 M_INF_FXHIS_ACTIVITY，绝不在主视图写死）；
--   * CAND_CNT = 0 或 >1 → 三个绑定列全部置 NULL（02 号主视图输出空
--     SERIAL_NUMBER/ACTIVITY_TYPE_CODE → 推送器必填预检拦截进入可见等待），
--     不任取 MIN/MAX，也不静默丢行；
--   * 明细视图（03 号）与主视图共享同一候选集口径，主子齐套。
-- 依赖：ODS.M_INF_FXHIS_ACTIVITY（锚点 v2，门诊段 SERIAL_NUMBER=CLINIC_NO）。
-- =============================================================================
CREATE OR REPLACE FORCE VIEW ODS.M_INF_FXHIS_ACTIVITY_LAB_V15 (
  PATIENT_ID, TEST_NO,
  SELECTED_SERIAL_NUMBER, ACTIVITY_TYPE_CODE, ACTIVITY_TYPE_NAME,
  CAND_CNT
) AS
SELECT cand.PATIENT_ID,
       cand.TEST_NO,
       CASE WHEN COUNT(DISTINCT cand.SERIAL_NUMBER) = 1
            THEN MAX(cand.SERIAL_NUMBER) END AS SELECTED_SERIAL_NUMBER,
       CASE WHEN COUNT(DISTINCT cand.SERIAL_NUMBER) = 1
            THEN MAX(cand.ACTIVITY_TYPE_CODE) END AS ACTIVITY_TYPE_CODE,
       CASE WHEN COUNT(DISTINCT cand.SERIAL_NUMBER) = 1
            THEN CASE MAX(cand.ACTIVITY_TYPE_CODE)
                       WHEN '2' THEN '急诊'
                       WHEN '6' THEN '住院'
                       ELSE '门诊' END
       END AS ACTIVITY_TYPE_NAME,
       COUNT(DISTINCT cand.SERIAL_NUMBER) AS CAND_CNT
FROM (
  -- 候选明细：每张门诊检验报告 × 该患者"申请日当天"的全部门诊/急诊锚点活动。
  -- LEFT JOIN：无锚点活动的报告（CAND_CNT=0）也保留，等待集可见、不静默漏。
  SELECT a.PATIENT_ID,
         a.TEST_NO,
         act.SERIAL_NUMBER,
         act.ACTIVITY_TYPE_CODE
    FROM HIS.LAB_TEST_MASTER a
    LEFT JOIN ODS.M_INF_FXHIS_ACTIVITY act
           ON TRIM(act.PATIENT_ID) = TRIM(a.PATIENT_ID)
          AND act.ACTIVITY_TYPE_CODE IN ('1', '2')          -- 仅门诊/急诊锚点段
          AND TRUNC(act.OPERATION_TIME) = TRUNC(a.REQUESTED_DATE_TIME)  -- 同日就诊消歧边界
   WHERE a.VISIT_ID = '0'                                   -- 门诊检验
     AND a.RESULT_STATUS = '4'                              -- 已出报告
     AND a.RESULTS_RPT_DATE_TIME IS NOT NULL
     AND a.RESULTS_RPT_DATE_TIME >= DATE'2026-11-14'        -- 门诊窗口（与主视图一致，用户拍板）
) cand
GROUP BY cand.PATIENT_ID, cand.TEST_NO
WITH READ ONLY;

-- 【DBA 执行后验证】
-- ① 行数 = 11-14 起出报告的门诊检验单数（每张报告恰好 1 行，不膨胀）：
--   SELECT COUNT(*) FROM ODS.M_INF_FXHIS_ACTIVITY_LAB_V15;
-- ② 同一就诊 3 报告场景：3 行、CAND_CNT=1、SELECTED 非空、急诊='2'（不膨胀 9 行）：
--   SELECT PATIENT_ID, TEST_NO, SELECTED_SERIAL_NUMBER, ACTIVITY_TYPE_CODE, CAND_CNT
--     FROM ODS.M_INF_FXHIS_ACTIVITY_LAB_V15
--    WHERE PATIENT_ID = '<本例三单患者>' ORDER BY TEST_NO;
-- ③ 候选分布（0/N 等待集可见，不得为凑数放宽）：
--   SELECT CAND_CNT, COUNT(*) FROM ODS.M_INF_FXHIS_ACTIVITY_LAB_V15 GROUP BY CAND_CNT;
-- 【回滚】DROP VIEW ODS.M_INF_FXHIS_ACTIVITY_LAB_V15;
-- =============================================================================
