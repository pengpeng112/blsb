-- r11 视图 DDL v3（DTR-R11-DDL-FIX-20261028；实测修正版，替代 v1/v2）。
-- v1→v3 修复记录：
--   ① CODE_INP 第二 UNION 分支 d2 先引用后声明（ORA-00904）→ JOIN 顺序调整；
--   ② 授权语句 v1 仅在注释 → 改为可执行；
--   ③ PHA_INP_REQUEST_DRUG.PRESC_CREATE_TIME 实测为 VARCHAR2('YYYY-MM-DD HH24:MI:SS.m')
--     （evidence/r11fix-probe-r2-*.json inp_req_timefmt）→ 全部改
--     TO_DATE(SUBSTR(...,1,19),'YYYY-MM-DD HH24:MI:SS')；
--   ④ PHA_CLI_REQUEST_DRUG.PRESC_CREATE_TIME 实测为 TIMESTAMP(6)，码表 PRESC_DATE 为
--     DATE → 等值比较改 CAST(w.PRESC_CREATE_TIME AS DATE) 防小数秒失配。
-- 退药 IN 码键已实测验证（r11fix-probe-r2 in_key_detail）：码表(TRACE_BILLNO,PRESC_DATE,
-- STORE_CODE,IN) = 申请表(PRESC_NO,PRESC_CREATE_TIME,PHA_CODE,return) 三样本到秒一致。
-- 住院码实测（8/10、10-26 两期样本）：从未写入（结构性未接入）。
--
-- ============================ 执行身份与前置授权（DBA 必读） ============================
-- 视图建在 PHARMACY 模式、引用 DRAG_USER 的表。hisuser 直接执行会因缺
-- CREATE ANY VIEW/对象权限失败（ORA-01031/00942）。正确做法：
--   A.【第 0 部分】用 DRUG_USER 属主账号或 DBA 执行（GRANT 幂等，重复执行无害）。
--   B.【第 1、2 部分】用 PHARMACY 属主账号执行（= 2026-10-22 成功创建 r10 两视图的
--      同一账号）；或由 SYS/DBA 代建，但必须先完成第 0 部分（视图属主 PHARMACY 需要
--      对基表的直接授权，经角色的授权建视图无效）。
--   C.【第 3 部分】授权上报账号（PHARMACY 属主或 DBA）。
--   D.【第 4 部分】验证（BLYB_DRUGTRACE 登录执行）。
-- ======================================================================================

-- ================================================================ 第 0 部分：前置授权（DRUG_USER 属主/DBA；幂等）
GRANT SELECT ON DRUG_USER.PHA_INP_REQUEST_DRUG TO PHARMACY;
GRANT SELECT ON DRUG_USER.PHA_INP_DISPMASTER   TO PHARMACY;
GRANT SELECT ON DRUG_USER.PHA_CLI_REQUEST_DRUG TO PHARMACY;
GRANT SELECT ON DRUG_USER.MED_TRACE_RECORD     TO PHARMACY;

-- ================================================================ 第 1 部分：住院发药（K4）
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_BILL_INP AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'INP|' || w.REQUEST_NO || '|' || w.PHA_CODE AS source_bill_id,
    w.REQUEST_NO AS source_bill_no,
    TO_CHAR(TO_DATE(SUBSTR(w.PRESC_CREATE_TIME,1,19),'YYYY-MM-DD HH24:MI:SS'),'YYYYMMDDHH24MISS')
        || '_' || w.REQUEST_NO
        || '_' || w.PHA_CODE
        || '_' || TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYYMMDDHH24MISS')
        || '_INP' AS source_revision,
    CAST('INPATIENT_DISPENSE' AS VARCHAR2(32)) AS business_kind,
    w.PHA_CODE AS pharmacy_code,
    CAST(NULL AS VARCHAR2(64)) AS warehouse_code,
    CAST(NULL AS VARCHAR2(64)) AS supplier_code,
    w.DISP_EXECUTE_TIME AS bill_time,
    w.DISP_EXECUTE_TIME AS updated_at,
    CAST(NULL AS VARCHAR2(64)) AS source_event_seq,
    CAST(1 AS NUMBER(1)) AS finalized,
    CAST(0 AS NUMBER(1)) AS is_void,
    CAST(NULL AS NUMBER(12)) AS expected_code_count,
    CAST(NULL AS VARCHAR2(64)) AS patient_ref,
    CAST(NULL AS VARCHAR2(64)) AS encounter_ref,
    'REQ_NO=' || w.REQUEST_NO
        || CHR(59) || 'DIS_REC=' || TO_CHAR(d.DIS_RECORDNO)
        || CHR(59) || 'SRC=inpatient_request' AS source_bill_kind_raw
FROM DRUG_USER.PHA_INP_REQUEST_DRUG w
LEFT JOIN DRUG_USER.PHA_INP_DISPMASTER d
  ON w.REQUEST_NO = d.REQUEST_NO
 AND TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYY-MM-DD HH24:MI:SS') = d.DISP_DATE
WHERE w.DISP_FLAG = 'send'
  AND w.DISP_QUANTITY > 0
  AND w.DISP_EXECUTE_TIME IS NOT NULL
WITH READ ONLY;

-- 住院码视图：两候选键 UNION ALL（键已实测：REQUEST_NO 20 位字符串 / DIS_RECORDNO 数字串；
-- 当前均零命中=住院码从未写入，视图留待接入后自然流入）。
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_CODE_INP AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'INP|' || w.REQUEST_NO || '|' || w.PHA_CODE AS source_bill_id,
    w.PHA_CODE AS pharmacy_code,
    CAST('INPATIENT_DISPENSE' AS VARCHAR2(32)) AS business_kind,
    TO_CHAR(TO_DATE(SUBSTR(w.PRESC_CREATE_TIME,1,19),'YYYY-MM-DD HH24:MI:SS'),'YYYYMMDDHH24MISS')
        || '_' || w.REQUEST_NO
        || '_' || w.PHA_CODE
        || '_' || TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYYMMDDHH24MISS')
        || '_INP' AS source_revision,
    TO_CHAR(w.EXEC_SQN) AS source_line_id,
    T.RECORD_ID AS source_code_record_id,
    T.T_TRACE_CODE AS trace_code,
    w.DISP_QUANTITY AS quantity,
    CAST(NULL AS VARCHAR2(32)) AS unit,
    CAST(NULL AS VARCHAR2(16)) AS code_level,
    CAST(NULL AS VARCHAR2(255)) AS parent_code,
    T.MODIFY_TIME AS updated_at,
    T.IS_DELETED AS is_deleted,
    T.F_TRACE_CODE AS f_trace_code,
    T.S_TRACE_CODE AS s_trace_code,
    T.T_TRACE_CODE AS t_trace_code,
    w.DISP_EXECUTE_TIME AS bill_time
FROM DRUG_USER.MED_TRACE_RECORD T
JOIN DRUG_USER.PHA_INP_REQUEST_DRUG w
  ON T.TRACE_BILLNO = w.REQUEST_NO
WHERE w.DISP_FLAG = 'send'
  AND w.DISP_QUANTITY > 0
  AND w.DISP_EXECUTE_TIME IS NOT NULL
  AND T.IN_OUT_TYPE = 'OUT'
UNION ALL
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'INP|' || w.REQUEST_NO || '|' || w.PHA_CODE AS source_bill_id,
    w.PHA_CODE AS pharmacy_code,
    CAST('INPATIENT_DISPENSE' AS VARCHAR2(32)) AS business_kind,
    TO_CHAR(TO_DATE(SUBSTR(w.PRESC_CREATE_TIME,1,19),'YYYY-MM-DD HH24:MI:SS'),'YYYYMMDDHH24MISS')
        || '_' || w.REQUEST_NO
        || '_' || w.PHA_CODE
        || '_' || TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYYMMDDHH24MISS')
        || '_INP' AS source_revision,
    TO_CHAR(w.EXEC_SQN) AS source_line_id,
    T.RECORD_ID AS source_code_record_id,
    T.T_TRACE_CODE AS trace_code,
    w.DISP_QUANTITY AS quantity,
    CAST(NULL AS VARCHAR2(32)) AS unit,
    CAST(NULL AS VARCHAR2(16)) AS code_level,
    CAST(NULL AS VARCHAR2(255)) AS parent_code,
    T.MODIFY_TIME AS updated_at,
    T.IS_DELETED AS is_deleted,
    T.F_TRACE_CODE AS f_trace_code,
    T.S_TRACE_CODE AS s_trace_code,
    T.T_TRACE_CODE AS t_trace_code,
    w.DISP_EXECUTE_TIME AS bill_time
FROM DRUG_USER.MED_TRACE_RECORD T
JOIN DRUG_USER.PHA_INP_DISPMASTER d2
  ON T.TRACE_BILLNO = TO_CHAR(d2.DIS_RECORDNO)
JOIN DRUG_USER.PHA_INP_REQUEST_DRUG w
  ON w.REQUEST_NO = d2.REQUEST_NO
 AND TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYY-MM-DD HH24:MI:SS') = d2.DISP_DATE
WHERE w.DISP_FLAG = 'send'
  AND w.DISP_QUANTITY > 0
  AND w.DISP_EXECUTE_TIME IS NOT NULL
  AND T.IN_OUT_TYPE = 'OUT'
WITH READ ONLY;

-- ================================================================ 第 2 部分：门诊退药冲正（K2）
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_RETURN AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'RET|' || TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
        || '|' || TO_CHAR(w.PRESC_NO) || '|' || w.PHA_CODE AS source_bill_id,
    TO_CHAR(w.PRESC_NO) AS source_bill_no,
    TO_CHAR(w.CLI_EXECUTE_TIME, 'YYYYMMDDHH24MISS')
        || '_RET_' || TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
        || '_' || TO_CHAR(w.PRESC_NO)
        || '_' || w.PHA_CODE AS source_revision,
    CAST('PATIENT_RETURN' AS VARCHAR2(32)) AS business_kind,
    w.PHA_CODE AS pharmacy_code,
    CAST(NULL AS VARCHAR2(64)) AS warehouse_code,
    CAST(NULL AS VARCHAR2(64)) AS supplier_code,
    w.CLI_EXECUTE_TIME AS bill_time,
    w.CLI_EXECUTE_TIME AS updated_at,
    CAST(NULL AS VARCHAR2(64)) AS source_event_seq,
    CAST(1 AS NUMBER(1)) AS finalized,
    CAST(0 AS NUMBER(1)) AS is_void,
    CAST(NULL AS NUMBER(12)) AS expected_code_count,
    CAST(NULL AS VARCHAR2(64)) AS patient_ref,
    CAST(NULL AS VARCHAR2(64)) AS encounter_ref,
    'PRESC_NO=' || TO_CHAR(w.PRESC_NO)
        || CHR(59) || 'PAT_ID=' || w.PAT_ID
        || CHR(59) || 'SEND_EXEC=' || (
            SELECT MAX(TO_CHAR(s.CLI_EXECUTE_TIME, 'YYYY-MM-DD HH24:MI:SS'))
            FROM DRUG_USER.PHA_CLI_REQUEST_DRUG s
            WHERE s.PRESC_NO = w.PRESC_NO
              AND s.PAT_ID = w.PAT_ID
              AND s.PHA_CODE = w.PHA_CODE
              AND s.CLI_FLAG = 'send'
              AND s.SEND_DRUG_STATUS = 'Y')
        || CHR(59) || 'SRC=cli_return' AS source_bill_kind_raw
FROM DRUG_USER.PHA_CLI_REQUEST_DRUG w
WHERE w.CLI_FLAG = 'return'
  AND w.RETURN_DRUG_STATUS = 'Y'
  AND w.CLI_INP_FLAG = 1
  AND w.CLI_EXECUTE_TIME IS NOT NULL
WITH READ ONLY;

-- 退药 IN 码键（实测验证到秒一致）：TRACE_BILLNO=PRESC_NO、PRESC_DATE=处方开立时间、
-- STORE_CODE=PHA_CODE、IN_OUT_TYPE='IN'。TIMESTAMP→CAST 防小数秒失配。
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_RETURN_CODE AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'RET|' || TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
        || '|' || TO_CHAR(w.PRESC_NO) || '|' || w.PHA_CODE AS source_bill_id,
    w.PHA_CODE AS pharmacy_code,
    CAST('PATIENT_RETURN' AS VARCHAR2(32)) AS business_kind,
    TO_CHAR(w.CLI_EXECUTE_TIME, 'YYYYMMDDHH24MISS')
        || '_RET_' || TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
        || '_' || TO_CHAR(w.PRESC_NO)
        || '_' || w.PHA_CODE AS source_revision,
    TO_CHAR(w.PRESC_DETAIL_ID) AS source_line_id,
    T.RECORD_ID AS source_code_record_id,
    T.T_TRACE_CODE AS trace_code,
    w.RETURN_QUANTITY AS quantity,
    CAST(NULL AS VARCHAR2(32)) AS unit,
    CAST(NULL AS VARCHAR2(16)) AS code_level,
    CAST(NULL AS VARCHAR2(255)) AS parent_code,
    T.MODIFY_TIME AS updated_at,
    T.IS_DELETED AS is_deleted,
    T.F_TRACE_CODE AS f_trace_code,
    T.S_TRACE_CODE AS s_trace_code,
    T.T_TRACE_CODE AS t_trace_code,
    w.CLI_EXECUTE_TIME AS bill_time
FROM DRUG_USER.MED_TRACE_RECORD T
JOIN DRUG_USER.PHA_CLI_REQUEST_DRUG w
  ON T.TRACE_BILLNO = TO_CHAR(w.PRESC_NO)
 AND T.PRESC_DATE = CAST(w.PRESC_CREATE_TIME AS DATE)
 AND T.STORE_CODE = w.PHA_CODE
WHERE w.CLI_FLAG = 'return'
  AND w.RETURN_DRUG_STATUS = 'Y'
  AND w.CLI_INP_FLAG = 1
  AND w.CLI_EXECUTE_TIME IS NOT NULL
  AND T.IN_OUT_TYPE = 'IN'
WITH READ ONLY;

-- ================================================================ 第 3 部分：授权上报账号（PHARMACY 属主/DBA）
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_BILL_INP    TO BLYB_DRUGTRACE;
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_CODE_INP    TO BLYB_DRUGTRACE;
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_RETURN      TO BLYB_DRUGTRACE;
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_RETURN_CODE TO BLYB_DRUGTRACE;

-- ================================================================ 第 4 部分：验证（BLYB_DRUGTRACE 登录执行）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_BILL_INP;      -- 预期 >0（住院申请行）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_CODE_INP;      -- 预期 0（住院码从未写入）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_RETURN;        -- 预期 >0（退药 return 行）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_RETURN_CODE;   -- 预期 >0（含历史 IN 码行，
--   如 26641@2026-08-10、66035@2026-09-10 等实测样本应可见）
-- SELECT * FROM PHARMACY.V_DRUG_TRACE_BILL_INP WHERE ROWNUM <= 1;   -- 核对 17 列名
