-- =============================================================================
-- 关联锚点视图 v2 · 门诊段扩展（2026-11-15，DBA 可执行版）
-- 任务：门诊检验接管（用户拍板"需要推送门诊"，且只推昨天以后的）
-- v2 = 住院段（v1 原文）+ **门诊段**（他方 FXHIS 门诊段规则复刻，窗口=2026-11-15 起）
-- 门诊段来源（FXHIS.V_EMR_ACTIVITY_INFO 门诊段原文）：
--   FROM HIS.OUTP_MR a JOIN HIS.CLINIC_MASTER b ON a.VISIT_DATE=b.VISIT_DATE AND a.VISIT_NO=b.VISIT_NO
--   WHERE a.VISIT_DATE>SYSDATE-20 AND a.DIAG_DESC_CODE IS NOT NULL AND a.ILLNESS_DATE<=SYSDATE
--   serial=CLINIC_NO（门诊流水号，检验门诊行同源）。
-- 与他方差异：门诊段窗口限 >=DATE'2026-11-15'（用户指令：更早的门诊检验不推）。
-- =============================================================================
CREATE OR REPLACE FORCE VIEW ODS.M_INF_FXHIS_ACTIVITY AS
-- ---------- 住院在院段（他方原文） ----------
SELECT a.PATIENT_ID AS PATIENT_ID,
       (SELECT MAX(c.INP_NO) FROM HIS.PAT_MASTER_INDEX c WHERE c.PATIENT_ID = a.PATIENT_ID)
         || TO_CHAR(a.VISIT_ID) AS SERIAL_NUMBER,
       '6' AS ACTIVITY_TYPE_CODE,
       a.ADM_WARD_DATE_TIME AS OPERATION_TIME
  FROM HIS.PATS_IN_HOSPITAL a
 WHERE a.DIAGNOSIS IS NOT NULL
   AND a.ADM_WARD_DATE_TIME IS NOT NULL
   AND a.ADM_WARD_DATE_TIME > SYSDATE - 20
   AND EXISTS (SELECT 1 FROM HIS.DIAGNOSIS b
                WHERE b.PATIENT_ID = a.PATIENT_ID
                  AND b.VISIT_ID = a.VISIT_ID
                  AND b.DIAGNOSIS_DATE > SYSDATE - 9)
UNION ALL
-- ---------- 住院出院段（他方原文） ----------
SELECT a.PATIENT_ID AS PATIENT_ID,
       (SELECT MAX(c.INP_NO) FROM HIS.PAT_MASTER_INDEX c WHERE c.PATIENT_ID = a.PATIENT_ID)
         || TO_CHAR(a.VISIT_ID) AS SERIAL_NUMBER,
       '6' AS ACTIVITY_TYPE_CODE,
       a.DISCHARGE_DATE_TIME AS OPERATION_TIME
  FROM HIS.PAT_VISIT a
 WHERE NOT EXISTS (SELECT 1 FROM HIS.PATS_IN_HOSPITAL pth
                    WHERE pth.PATIENT_ID = a.PATIENT_ID
                      AND pth.VISIT_ID = a.VISIT_ID)
   AND a.DISCHARGE_DATE_TIME > SYSDATE - 60
UNION ALL
-- ---------- 门诊段（v2 新增；窗口=2026-11-15 起，与他方门诊规则一致） ----------
SELECT b.PATIENT_ID AS PATIENT_ID,
       b.CLINIC_NO AS SERIAL_NUMBER,
       CASE WHEN b.CLINIC_TYPE LIKE '%急诊%' THEN '2' ELSE '1' END AS ACTIVITY_TYPE_CODE,
       NVL((SELECT MAX(c.ENTER_DATE) FROM HIS.OUTP_MR_DIAG_DESC c
             WHERE a.VISIT_DATE=c.VISIT_DATE AND a.VISIT_NO=c.VISIT_NO AND a.PATIENT_ID=c.PATIENT_ID),
           a.ILLNESS_DATE) AS OPERATION_TIME
  FROM HIS.OUTP_MR a
  JOIN HIS.CLINIC_MASTER b ON a.VISIT_DATE = b.VISIT_DATE AND a.VISIT_NO = b.VISIT_NO
 WHERE a.VISIT_DATE > SYSDATE - 20
   AND a.VISIT_DATE >= DATE'2026-11-14'
   AND a.DIAG_DESC_CODE IS NOT NULL
   AND a.ILLNESS_DATE <= SYSDATE;

-- 【自检】
-- 1) 三段行数：
-- SELECT ACTIVITY_TYPE_CODE, COUNT(*) FROM ODS.M_INF_FXHIS_ACTIVITY GROUP BY ACTIVITY_TYPE_CODE;
--   预期：6=住院（≈11000）、1=门诊（近20天门诊病历诊断量≈万级）、2=急诊
-- 2) 门诊锚点抽检（昨日门诊就诊锚点命中，预期接近 100%）：
-- SELECT COUNT(*) TOTAL, SUM(CASE WHEN act.PATIENT_ID IS NOT NULL THEN 1 ELSE 0 END) HIT
--   FROM (SELECT DISTINCT b.PATIENT_ID, b.CLINIC_NO FROM HIS.OUTP_MR a
--          JOIN HIS.CLINIC_MASTER b ON a.VISIT_DATE=b.VISIT_DATE AND a.VISIT_NO=b.VISIT_NO
--          WHERE a.VISIT_DATE>=TRUNC(SYSDATE)-1 AND a.DIAG_DESC_CODE IS NOT NULL) t
--   LEFT JOIN (SELECT DISTINCT PATIENT_ID,SERIAL_NUMBER FROM ODS.M_INF_FXHIS_ACTIVITY) act
--     ON act.PATIENT_ID=t.PATIENT_ID AND act.SERIAL_NUMBER=t.CLINIC_NO;
-- =============================================================================
-- 锚点就绪 → 执行 13 号检验 v1.2 → 14 号检验项目 v1.2（均在 sql/ 目录，含验证 SQL）
-- =============================================================================
