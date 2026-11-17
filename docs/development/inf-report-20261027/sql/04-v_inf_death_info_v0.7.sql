-- =============================================================================
-- 传染病前置软件上报 · 死亡信息（emr_death_info）取数视图 v0.7 —— 关联锚点过滤（方案 A 镜像关联，2026-11-10）
-- 任务：INF-LINKAGE-IMPL-20261110 ｜ 模式同 02-v_inf_first_course_v0.7.sql。
--   v0.6 全文（死亡 T+0 双驱动，2026-11-06 已上线）包为内联 t（两分支尾部各加 __PATIENT_ID/__SERIAL_NUMBER
--   辅助列，不输出），外层 INNER JOIN M_INF_FXHIS_ACTIVITY（patient_id+serial，住院活动镜像）与本地
--   HIS.PAT_MASTER_INDEX（患者锚点，同源实时）。输出列名/顺序与 v0.6 完全一致，推送器零改动；锚点无行不上报。
--   在院死亡（DISCHARGE 空）经他方"住院在院段"锚点关联；死亡当日保存的文书 T+0 行天然在窗内。
-- 回滚：执行 sql/04-v_inf_death_info_v0.6.sql（回到 v0.6）；如需回 v0.5 再执行 v0.6 文件内回滚段。
-- =============================================================================

CREATE OR REPLACE FORCE VIEW "ODS"."V_INF_DEATH_INFO" (
  "ID", "PATIENT_ID", "ACTIVITY_TYPE_CODE", "ACTIVITY_TYPE_NAME",
  "SERIAL_NUMBER", "PATIENT_NAME",
  "ID_CARD_TYPE_CODE", "ID_CARD_TYPE_NAME", "ID_CARD",
  "TREATMENT_DESC", "DEAD_DATE",
  "DIRECT_CAUSE_CODE", "DIRECT_CAUSE_NAME",
  "DEATH_DIAGNOSIS_CODE", "DEATH_DIAGNOSIS_NAME",
  "CHIEF_PHYSICIAN_ID",
  "ORG_CODE", "ORG_NAME", "DEPT_CODE", "DEPT_NAME",
  "OPERATOR_ID", "OPERATION_TIME"
) AS
WITH dw AS (
  SELECT w.PATIENT_ID, w.VISIT_ID, w.CREATOR_ID, w.PATIENT_CUR_DEPT_CODE,
         w.CREATE_DATE_TIME, w.LAST_MODIFY_DATE_TIME
  FROM (
    SELECT d.PATIENT_ID, d.VISIT_ID, d.CREATOR_ID, d.PATIENT_CUR_DEPT_CODE,
           d.CREATE_DATE_TIME, d.LAST_MODIFY_DATE_TIME,
           ROW_NUMBER() OVER (PARTITION BY d.PATIENT_ID, TO_CHAR(d.VISIT_ID)
                              ORDER BY d.CREATE_DATE_TIME, ROWID) AS rn
      FROM JHEMR.JHMR_FILE_INDEX d
     WHERE (d.TOPIC IN ('死亡记录','24小时内入院死亡记录') OR d.TOPIC LIKE '死亡记录模板%')
       AND d.CREATE_DATE_TIME >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
  ) w
  WHERE w.rn = 1
),
t AS (
-- ---------- 分支 1：死亡文书行（T+0 主驱动；v0.6 原文 + 辅助列） ----------
SELECT
  TO_CHAR(w.PATIENT_ID) || '_' || TO_CHAR(w.VISIT_ID) AS ID,

  TO_CHAR(w.PATIENT_ID) AS PATIENT_ID,
  '6' AS ACTIVITY_TYPE_CODE,
  '住院' AS ACTIVITY_TYPE_NAME,
  pmi.INP_NO || TO_CHAR(w.VISIT_ID) AS SERIAL_NUMBER,
  SUBSTRB(TO_CHAR(pmi.NAME), 1, 100) AS PATIENT_NAME,

  '01' AS ID_CARD_TYPE_CODE,
  '居民身份证' AS ID_CARD_TYPE_NAME,
  NVL(TO_CHAR(pmi.ID_NO), '-') AS ID_CARD,

  (SELECT SUBSTRB(MAX(d.TREAT_PROC_DESCR), 1, 2000)
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)) AS TREATMENT_DESC,

  NVL((SELECT MAX(d.DEATH_DATE)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      NVL(a.DISCHARGE_DATE_TIME, w.CREATE_DATE_TIME)) AS DEAD_DATE,

  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_NAME,

  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(w.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE TO_CHAR(dg.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
          AND TO_CHAR(dg.VISIT_ID) = TO_CHAR(w.VISIT_ID)
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_NAME,

  NVL(TO_CHAR(w.CREATOR_ID), TO_CHAR(a.ATTENDING_DOCTOR)) AS CHIEF_PHYSICIAN_ID,

  '370104000' AS ORG_CODE,
  '山东省第二人民医院' AS ORG_NAME,
  NVL((SELECT MAX(dd.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd
        WHERE TO_CHAR(dd.HIS_DEPT_CODE) = TO_CHAR(w.PATIENT_CUR_DEPT_CODE)),
      NVL((SELECT MAX(dd2.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd2
            WHERE TO_CHAR(dd2.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
          TO_CHAR(w.PATIENT_CUR_DEPT_CODE))) AS DEPT_CODE,
  NVL((SELECT MAX(x.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x
        WHERE TO_CHAR(x.HIS_DEPT_CODE) = TO_CHAR(w.PATIENT_CUR_DEPT_CODE)),
      NVL((SELECT MAX(x2.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x2
            WHERE TO_CHAR(x2.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
          NVL((SELECT MAX(dd.DEPT_NAME) FROM HIS.DEPT_DICT dd
                WHERE TO_CHAR(dd.DEPT_CODE) = TO_CHAR(w.PATIENT_CUR_DEPT_CODE)),
              (SELECT MAX(dd2.DEPT_NAME) FROM HIS.DEPT_DICT dd2
                WHERE TO_CHAR(dd2.DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM))))) AS DEPT_NAME,

  NVL(TO_CHAR(w.CREATOR_ID), TO_CHAR(a.ATTENDING_DOCTOR)) AS OPERATOR_ID,
  w.LAST_MODIFY_DATE_TIME AS OPERATION_TIME,

  TO_CHAR(w.PATIENT_ID) AS LINK_PATIENT_ID,                       -- v0.7 辅助列：锚点 JOIN 用
  pmi.INP_NO || TO_CHAR(w.VISIT_ID) AS LINK_SERIAL_NUMBER         -- v0.7 辅助列：锚点 JOIN 用
FROM dw w
LEFT JOIN HIS.PAT_VISIT a
       ON TO_CHAR(a.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)
      AND TO_CHAR(a.VISIT_ID)   = TO_CHAR(w.VISIT_ID)
LEFT JOIN HIS.PAT_MASTER_INDEX pmi ON TO_CHAR(pmi.PATIENT_ID) = TO_CHAR(w.PATIENT_ID)

UNION ALL
-- ---------- 分支 2：PAT_VISIT 离院方式=5（v0.6 原文 + 辅助列） ----------
SELECT
  a.PATIENT_ID || '_' || a.VISIT_ID AS ID,

  a.PATIENT_ID AS PATIENT_ID,
  '6' AS ACTIVITY_TYPE_CODE,
  '住院' AS ACTIVITY_TYPE_NAME,
  pmi.INP_NO || a.VISIT_ID AS SERIAL_NUMBER,
  SUBSTRB(TO_CHAR(pmi.NAME), 1, 100) AS PATIENT_NAME,

  '01' AS ID_CARD_TYPE_CODE,
  '居民身份证' AS ID_CARD_TYPE_NAME,
  NVL(TO_CHAR(pmi.ID_NO), '-') AS ID_CARD,

  (SELECT SUBSTRB(MAX(d.TREAT_PROC_DESCR), 1, 2000)
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS TREATMENT_DESC,

  NVL((SELECT MAX(d.DEATH_DATE)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      a.DISCHARGE_DATE_TIME) AS DEAD_DATE,

  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DIRECT_DEATH_REASON_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DIRECT_CAUSE_NAME,

  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_CODE), 1, 50)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标编码))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_CODE)), 1, 50)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_CODE,
  NVL((SELECT SUBSTRB(MAX(d.DEATH_DIAG_NAME), 1, 250)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      (SELECT SUBSTRB(MAX(NVL((SELECT MAX(TO_CHAR(cd.国标名称))
                                 FROM CDA.CDA_DICTIONARY cd
                                WHERE cd.字典名称 = 'ICD-10诊断编码' AND cd.系统标识 = 'HIS'
                                  AND cd.院标编码 = dg.DIAGNOSIS_CODE),
                              dg.DIAGNOSIS_DESC)), 1, 250)
         FROM HIS.DIAGNOSIS dg
        WHERE dg.PATIENT_ID = a.PATIENT_ID AND dg.VISIT_ID = a.VISIT_ID
          AND dg.DIAGNOSIS_TYPE = '3' AND dg.DIAGNOSIS_NO = 1)) AS DEATH_DIAGNOSIS_NAME,

  TO_CHAR(a.ATTENDING_DOCTOR) AS CHIEF_PHYSICIAN_ID,

  '370104000' AS ORG_CODE,
  '山东省第二人民医院' AS ORG_NAME,
  NVL((SELECT MAX(dd.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd WHERE TO_CHAR(dd.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)), TO_CHAR(a.DEPT_DISCHARGE_FROM)) AS DEPT_CODE,
  NVL((SELECT MAX(x.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x
      WHERE TO_CHAR(x.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
      (SELECT MAX(dd.DEPT_NAME) FROM HIS.DEPT_DICT dd
      WHERE TO_CHAR(dd.DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM))) AS DEPT_NAME,

  TO_CHAR(a.ATTENDING_DOCTOR) AS OPERATOR_ID,
  a.DISCHARGE_DATE_TIME AS OPERATION_TIME,

  TO_CHAR(a.PATIENT_ID) AS LINK_PATIENT_ID,                       -- v0.7 辅助列：锚点 JOIN 用
  pmi.INP_NO || a.VISIT_ID AS LINK_SERIAL_NUMBER                  -- v0.7 辅助列：锚点 JOIN 用
FROM HIS.PAT_VISIT a
JOIN HIS.PAT_MASTER_INDEX pmi ON pmi.PATIENT_ID = a.PATIENT_ID
WHERE a.DISCHARGE_DISPOSITION = '5'
  AND a.DISCHARGE_DATE_TIME >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
  AND NOT EXISTS (
        SELECT 1 FROM dw w2
         WHERE TO_CHAR(w2.PATIENT_ID) = TO_CHAR(a.PATIENT_ID)
           AND TO_CHAR(w2.VISIT_ID)   = TO_CHAR(a.VISIT_ID))
)
SELECT
  t.ID, t.PATIENT_ID, t.ACTIVITY_TYPE_CODE, t.ACTIVITY_TYPE_NAME,
  t.SERIAL_NUMBER, t.PATIENT_NAME,
  t.ID_CARD_TYPE_CODE, t.ID_CARD_TYPE_NAME, t.ID_CARD,
  t.TREATMENT_DESC, t.DEAD_DATE,
  t.DIRECT_CAUSE_CODE, t.DIRECT_CAUSE_NAME,
  t.DEATH_DIAGNOSIS_CODE, t.DEATH_DIAGNOSIS_NAME,
  t.CHIEF_PHYSICIAN_ID,
  t.ORG_CODE, t.ORG_NAME, t.DEPT_CODE, t.DEPT_NAME,
  t.OPERATOR_ID, t.OPERATION_TIME
FROM t
JOIN ODS.M_INF_FXHIS_ACTIVITY act
  ON TRIM(TO_CHAR(act.PATIENT_ID))     = TRIM(TO_CHAR(t.LINK_PATIENT_ID))
 AND TRIM(TO_CHAR(act.SERIAL_NUMBER))  = TRIM(TO_CHAR(t.LINK_SERIAL_NUMBER))
JOIN HIS.PAT_MASTER_INDEX pat
  ON TRIM(TO_CHAR(pat.PATIENT_ID)) = TRIM(TO_CHAR(t.LINK_PATIENT_ID))
WITH READ ONLY;

-- =============================================================================
-- 【DBA 执行后验证 SQL】（结果回传执行 AI 复核）
-- 1. 行数收缩（v0.6 为 344 行；预期≈窗内 64±：在院死亡行走他方"在院段"锚点、
--    11-06 补传的 1~4 月历史行（出院>60天）被过滤属预期——它们此前已推送，平台存量不受影响）：
-- SELECT COUNT(*) FROM ODS.V_INF_DEATH_INFO;
-- 2. 11 月行不受影响（T+0 行天然在窗内，预期全部保留）：
-- SELECT COUNT(*) FROM ODS.V_INF_DEATH_INFO WHERE OPERATION_TIME >= DATE'2026-11-01';
-- 3. 全行可关联验证（预期 ACT_HIT=PAT_HIT=TOTAL）：
-- SELECT COUNT(*) TOTAL,
--        SUM(CASE WHEN act.PATIENT_ID IS NOT NULL THEN 1 ELSE 0 END) ACT_HIT,
--        SUM(CASE WHEN pat.PATIENT_ID IS NOT NULL THEN 1 ELSE 0 END) PAT_HIT
--   FROM ODS.V_INF_DEATH_INFO t
--   LEFT JOIN (SELECT DISTINCT PATIENT_ID, SERIAL_NUMBER FROM ODS.M_INF_FXHIS_ACTIVITY) act
--     ON TRIM(TO_CHAR(act.PATIENT_ID))=TRIM(TO_CHAR(t.PATIENT_ID))
--    AND TRIM(TO_CHAR(act.SERIAL_NUMBER))=TRIM(TO_CHAR(t.SERIAL_NUMBER))
--   LEFT JOIN (SELECT DISTINCT PATIENT_ID FROM HIS.PAT_MASTER_INDEX) pat
--     ON TRIM(TO_CHAR(pat.PATIENT_ID))=TRIM(TO_CHAR(t.PATIENT_ID));
-- =============================================================================
-- 【回滚】执行 sql/04-v_inf_death_info_v0.6.sql（恢复 v0.6 双驱动全量口径）；
-- 如需回 v0.5：再执行 v0.6 文件末尾回滚段。镜像表保留无害。
-- =============================================================================
