-- =============================================================================
-- 传染病前置软件上报 · 死亡信息（emr_death_info）取数视图 v0.3（2026-10-27 实测后修正）—— v0.5 对齐 crbsb 口径（2026-10-28）；**待 DBA 重建部署**
-- 任务：INF-DESIGN-20261027 / INF-VERIFY-20261027 ｜ 库：ODS ｜ 目标接口 /hclient/emr/receive/emrDeathInfo
-- 驱动：HIS.PAT_VISIT 离院方式=5（148号口径：5=死亡；勿用治疗结果字典）
--       —— PAT_VISIT.DEATH_DATE_TIME 源端不填（平台已知陷阱），死亡时间不依赖它
-- v0.2 核对修正（sjzc 元数据，2026-10-27）：
--   * 医师列：ATTENDING_DOCTOR；PAT_VISIT 无 OPERATOR 列
--   * CDA.TT_ESBHL_DEATH_ROD 实列：DEATH_DATE / DIRECT_DEATH_REASON_CODE+NAME（直接死因）/
--     DEATH_DIAG_CODE+NAME（死亡诊断）/ TREAT_PROC_DESCR（诊疗过程）——与规范字段直接对应
--   * CDA 表关联键：无 PATIENT_ID，用 身份证号 PAT_ID_NUMBER + VISIT_ID 关联（INHOS_NO 语义未验，不用）
--   * 兜底口径沿用三甲 V_EMR_DEATH_RECORD：死亡时间=出院时间；死因=出院主诊断经
--     CDA.CDA_DICTIONARY 院标→国标 ICD10 映射
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
SELECT
  a.PATIENT_ID || '_' || a.VISIT_ID AS ID,

  a.PATIENT_ID AS PATIENT_ID,
  '6' AS ACTIVITY_TYPE_CODE,                                     -- 诊疗活动类型代码表：6=住院
  '住院' AS ACTIVITY_TYPE_NAME,
  pmi.INP_NO || a.VISIT_ID AS SERIAL_NUMBER,  -- crbsb 口径=病案号||住院次数
  SUBSTRB(TO_CHAR(pmi.NAME), 1, 100) AS PATIENT_NAME,

  '01' AS ID_CARD_TYPE_CODE,
  '居民身份证' AS ID_CARD_TYPE_NAME,
  NVL(TO_CHAR(pmi.ID_NO), '-') AS ID_CARD,  -- crbsb 同款兜底

  (SELECT SUBSTRB(MAX(d.TREAT_PROC_DESCR), 1, 2000)               -- 诊疗过程描述（建议填）
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS TREATMENT_DESC,

  -- 死亡时间（必填）：CDA 死亡记录优先，兜底出院时间（三甲验证口径）
  NVL((SELECT MAX(d.DEATH_DATE)
         FROM CDA.TT_ESBHL_DEATH_ROD d
        WHERE d.PAT_ID_NUMBER = pmi.ID_NO
          AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)),
      a.DISCHARGE_DATE_TIME) AS DEAD_DATE,

  -- 直接死因（必填，院内ICD10死因编码）：CDA 直接死因优先，
  -- 兜底=出院主诊断经院标→国标 ICD10 映射（三甲死亡视图同模式）
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

  -- 死亡诊断（传染病死亡时必填）：CDA 死亡诊断，无则空（前置机校验反馈后补）
  (SELECT SUBSTRB(MAX(d.DEATH_DIAG_CODE), 1, 50)
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS DEATH_DIAGNOSIS_CODE,
  (SELECT SUBSTRB(MAX(d.DEATH_DIAG_NAME), 1, 250)
     FROM CDA.TT_ESBHL_DEATH_ROD d
    WHERE d.PAT_ID_NUMBER = pmi.ID_NO
      AND d.VISIT_ID = TO_CHAR(a.VISIT_ID)) AS DEATH_DIAGNOSIS_NAME,

  TO_CHAR(a.ATTENDING_DOCTOR) AS CHIEF_PHYSICIAN_ID,             -- 主治医师（平台核对列名）

  '370104000' AS ORG_CODE,
  '山东省第二人民医院' AS ORG_NAME,
  NVL((SELECT MAX(dd.INFECT_DEPT_CODE) FROM HIS.INFECT_DEPT_DICT dd WHERE TO_CHAR(dd.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)), TO_CHAR(a.DEPT_DISCHARGE_FROM)) AS DEPT_CODE,  -- 死亡科室=出院科室，INFECT 映射+兜底
  NVL((SELECT MAX(x.INFECT_DEPT_NAME) FROM HIS.INFECT_DEPT_DICT x
      WHERE TO_CHAR(x.HIS_DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM)),
      (SELECT MAX(dd.DEPT_NAME) FROM HIS.DEPT_DICT dd
      WHERE TO_CHAR(dd.DEPT_CODE) = TO_CHAR(a.DEPT_DISCHARGE_FROM))) AS DEPT_NAME,

  TO_CHAR(a.ATTENDING_DOCTOR) AS OPERATOR_ID,                    -- PAT_VISIT 无 OPERATOR 列
  a.DISCHARGE_DATE_TIME AS OPERATION_TIME                        -- 增量水位
FROM HIS.PAT_VISIT a
JOIN HIS.PAT_MASTER_INDEX pmi ON pmi.PATIENT_ID = a.PATIENT_ID
WHERE a.DISCHARGE_DISPOSITION = '5'                              -- 离院方式5=死亡（148号口径）；列已核对存在
  AND a.DISCHARGE_DATE_TIME >= TO_DATE('2026-01-01', 'YYYY-MM-DD')
WITH READ ONLY;
