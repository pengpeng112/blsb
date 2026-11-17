-- 方案 2（零 schema 变更）：V_DRUG_TRACE_CODE v2（内嵌 hint）——仅码明细查询受益
-- DTR-VIEW-TUNING-20261029；执行者：DBA；未执行。
-- 2026-10-31 实测降级声明（evidence/hint-test-20261031T023339Z.txt）：
--   外层对视图加同款 hint 无效（1.13s vs 现状 1.10s，未绑定被忽略）；视图内嵌 hint 机制不同
--   仍可能生效，但只读通道无法验证。**必须**按下面流程：建视图→立即跑验证 SQL→
--   无改善立即回滚。预期收益：码明细 ~1.1s→~0.5-0.9s（不确定）；单头查询无视图解法（保持 ~1.1s）。
-- 回滚=执行文末"原版文本"。
-- V_DRUG_TRACE_BILL 不改：单表拼接谓词无索引可用，hint 无从发挥。

CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_CODE AS
SELECT
    /*+ LEADING(M) USE_NL(T) INDEX(T med_trace_record_trace_billno) */
    CAST('HIS' AS VARCHAR2(16)) AS source_system,

    TO_CHAR(M.PRESC_DATE, 'YYYY-MM-DD HH24:MI:SS')
        || '|' || TO_CHAR(M.PRESC_NO)
        || '|' || M.DISPENSARY AS source_bill_id,

    M.DISPENSARY AS pharmacy_code,
    CASE WHEN M.PRESC_SOURCE = 0 AND M.FLAG = 1 AND M.DISPENSING_DATETIME IS NOT NULL AND NVL(M.REFUND_FLAG, 0) = 0 THEN 'PATIENT_DISPENSE' ELSE 'UNKNOWN' END AS business_kind,

    TO_CHAR(M.PRESC_DATE, 'YYYYMMDDHH24MISS')
        || '_' || TO_CHAR(M.PRESC_NO)
        || '_' || M.DISPENSARY
        || '_' || TO_CHAR(M.DISPENSING_DATETIME, 'YYYYMMDDHH24MISS')
        || '_' || NVL(TO_CHAR(M.PRESC_SOURCE), 'NULL')
        || '_' || NVL(TO_CHAR(M.FLAG), 'NULL') || '_R10_RF_' || NVL(TO_CHAR(M.REFUND_FLAG), 'NULL') AS source_revision,

    T.PRESC_DETAILID AS source_line_id,
    T.RECORD_ID AS source_code_record_id,
    T.T_TRACE_CODE AS trace_code,
    CAST(NULL AS NUMBER(20,6)) AS quantity,
    CAST(NULL AS VARCHAR2(32)) AS unit,
    CAST(NULL AS VARCHAR2(16)) AS code_level,
    CAST(NULL AS VARCHAR2(255)) AS parent_code,
    T.MODIFY_TIME AS updated_at,
    T.IS_DELETED AS is_deleted,
    T.F_TRACE_CODE AS f_trace_code,
    T.S_TRACE_CODE AS s_trace_code,
    T.T_TRACE_CODE AS t_trace_code,
    M.DISPENSING_DATETIME AS bill_time

FROM DRUG_USER.MED_TRACE_RECORD T
JOIN PHARMACY.DRUG_PRESC_MASTER M
  ON T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO)
 AND T.PRESC_DATE = M.PRESC_DATE
 AND T.STORE_CODE = M.DISPENSARY

WHERE T.IN_OUT_TYPE = 'OUT'
WITH READ ONLY;

-- ===== 验证 SQL（DBA 先 EXPLAIN 再替换；替换后跑等价校验） =====
-- 1) 计划验证（期望 NESTED LOOPS，T 侧走 med_trace_record_trace_billno）：
EXPLAIN PLAN FOR
SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_CODE
 WHERE source_system='HIS' AND pharmacy_code='050121'
   AND business_kind='PATIENT_DISPENSE'
   AND source_bill_id = '2026-10-26 09:31:22|50235|050121';
SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY());
-- 2) 等价校验（v2 与原版在同窗口同计数，10-28 全天应 = 原值）：
--    SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_CODE WHERE bill_time >= DATE'2026-10-28' AND bill_time < DATE'2026-10-29';
-- 3) 计时对照（应从 ~1s 降到 ~0.5s 量级；FBI 方案落地后本方案可回滚为原版）。

-- ===== 原版文本（回滚用；与部署现版一致，r10 草案 token 已核） =====
-- CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_CODE AS
-- SELECT CAST('HIS' AS VARCHAR2(16)) AS source_system,
--   TO_CHAR(M.PRESC_DATE, 'YYYY-MM-DD HH24:MI:SS') || '|' || TO_CHAR(M.PRESC_NO) || '|' || M.DISPENSARY AS source_bill_id,
--   M.DISPENSARY AS pharmacy_code,
--   CASE WHEN M.PRESC_SOURCE = 0 AND M.FLAG = 1 AND M.DISPENSING_DATETIME IS NOT NULL AND NVL(M.REFUND_FLAG, 0) = 0 THEN 'PATIENT_DISPENSE' ELSE 'UNKNOWN' END AS business_kind,
--   TO_CHAR(M.PRESC_DATE, 'YYYYMMDDHH24MISS') || '_' || TO_CHAR(M.PRESC_NO) || '_' || M.DISPENSARY || '_' || TO_CHAR(M.DISPENSING_DATETIME, 'YYYYMMDDHH24MISS') || '_' || NVL(TO_CHAR(M.PRESC_SOURCE), 'NULL') || '_' || NVL(TO_CHAR(M.FLAG), 'NULL') || '_R10_RF_' || NVL(TO_CHAR(M.REFUND_FLAG), 'NULL') AS source_revision,
--   T.PRESC_DETAILID AS source_line_id, T.RECORD_ID AS source_code_record_id, T.T_TRACE_CODE AS trace_code,
--   CAST(NULL AS NUMBER(20,6)) AS quantity, CAST(NULL AS VARCHAR2(32)) AS unit, CAST(NULL AS VARCHAR2(16)) AS code_level,
--   CAST(NULL AS VARCHAR2(255)) AS parent_code, T.MODIFY_TIME AS updated_at, T.IS_DELETED AS is_deleted,
--   T.F_TRACE_CODE AS f_trace_code, T.S_TRACE_CODE AS s_trace_code, T.T_TRACE_CODE AS t_trace_code,
--   M.DISPENSING_DATETIME AS bill_time
-- FROM DRUG_USER.MED_TRACE_RECORD T
-- JOIN PHARMACY.DRUG_PRESC_MASTER M
--   ON T.TRACE_BILLNO = TO_CHAR(M.PRESC_NO) AND T.PRESC_DATE = M.PRESC_DATE AND T.STORE_CODE = M.DISPENSARY
-- WHERE T.IN_OUT_TYPE = 'OUT'
-- WITH READ ONLY;
