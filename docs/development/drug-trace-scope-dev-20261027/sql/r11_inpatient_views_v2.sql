-- r11 视图 DDL v2（DTR-R11-DDL-FIX-20261028；修复 v1 三问题：
--   ① CODE_INP 第二 UNION 分支 d2 先引用后声明（ORA-00904）→ 调整 JOIN 顺序；
--   ② 授权语句改为可执行（v1 仅注释）；
--   ③ 明确执行身份与前置授权（hisuser 直接执行会 ORA-01031/00942）。
-- 列契约与 r10 逐列一致（17/18 列，机器比对测试在 test_drug_trace_scope_dev.py）。
--
-- ============================ 执行身份与前置授权（DBA 必读） ============================
-- 视图建在 PHARMACY 模式、引用 DRAG_USER 的表。执行要求：
--   A. 用 PHARMACY 属主账号执行【第 0、1、2 部分】（即 2026-10-22 成功创建
--      V_DRUG_TRACE_BILL/V_DRUG_TRACE_CODE 的同一账号；用 hisuser 直接建会因
--      缺 CREATE ANY VIEW/对象权限失败）。或由 SYS/DBA 代建，但 PHARMACY 属主
--      必须已具备下面 B 的对象权限，否则建视图时即报 ORA-00942。
--   B. 前置授权（用 DRUG_USER 属主账号或 DBA 执行；r10 只授过 MED_TRACE_RECORD，
--      下面前三张是新引用的表，缺授权时创建报 ORA-00942）：
--        GRANT SELECT ON DRUG_USER.PHA_INP_REQUEST_DRUG TO PHARMACY;
--        GRANT SELECT ON DRUG_USER.PHA_INP_DISPMASTER   TO PHARMACY;
--        GRANT SELECT ON DRUG_USER.PHA_CLI_REQUEST_DRUG TO PHARMACY;
--        -- MED_TRACE_RECORD 若 r10 时已授可跳过：
--        -- GRANT SELECT ON DRUG_USER.MED_TRACE_RECORD  TO PHARMACY;
--      注意：必须是直接授权（不能经角色），否则建视图仍报 ORA-01031。
--   C. 建完后执行【第 3 部分】把 4 个视图授给上报账号。
--   D. 验证：【第 4 部分】四条 SELECT 应各返回列头且行数 0（当前无码属预期）。
--      再用 BLYB_DRUGTRACE 登录各查一次确认授权生效。
-- ======================================================================================

-- ================================================================ 第 0 部分：前置授权（DRUG_USER 属主/DBA 执行）
GRANT SELECT ON DRUG_USER.PHA_INP_REQUEST_DRUG TO PHARMACY;
GRANT SELECT ON DRUG_USER.PHA_INP_DISPMASTER   TO PHARMACY;
GRANT SELECT ON DRUG_USER.PHA_CLI_REQUEST_DRUG TO PHARMACY;

-- ================================================================ 第 1 部分：住院发药（K4）
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_BILL_INP AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'INP|' || w.REQUEST_NO || '|' || w.PHA_CODE AS source_bill_id,
    w.REQUEST_NO AS source_bill_no,
    TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
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

-- 住院码视图：两候选键 UNION ALL。
-- v2 修复：第二分支 JOIN 顺序改为 T→d2→w（v1 在 d2 声明前引用 DIS_RECORDNO 报 ORA-00904）。
-- 第一分支中 d 不参与输出，已移除无用 JOIN。
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_CODE_INP AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'INP|' || w.REQUEST_NO || '|' || w.PHA_CODE AS source_bill_id,
    w.PHA_CODE AS pharmacy_code,
    CAST('INPATIENT_DISPENSE' AS VARCHAR2(32)) AS business_kind,
    TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
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
    TO_CHAR(w.PRESC_CREATE_TIME, 'YYYYMMDDHH24MISS')
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
 AND T.PRESC_DATE = w.PRESC_CREATE_TIME
 AND T.STORE_CODE = w.PHA_CODE
WHERE w.CLI_FLAG = 'return'
  AND w.RETURN_DRUG_STATUS = 'Y'
  AND w.CLI_INP_FLAG = 1
  AND w.CLI_EXECUTE_TIME IS NOT NULL
  AND T.IN_OUT_TYPE = 'IN'
WITH READ ONLY;

-- ================================================================ 第 3 部分：授权上报账号（PHARMACY 属主/DBA 执行）
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_BILL_INP    TO BLYB_DRUGTRACE;
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_CODE_INP    TO BLYB_DRUGTRACE;
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_RETURN      TO BLYB_DRUGTRACE;
GRANT SELECT ON PHARMACY.V_DRUG_TRACE_RETURN_CODE TO BLYB_DRUGTRACE;

-- ================================================================ 第 4 部分：验证（BLYB_DRUGTRACE 登录执行）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_BILL_INP;      -- 预期 >0（住院申请行）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_CODE_INP;      -- 预期 0（码未写入，属源库事实）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_RETURN;        -- 预期 >0（退药 return 行）
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_RETURN_CODE;   -- 预期 0（IN 码未写入）
-- 每视图再取一行核对 17/18 列名与 r10 同构：
-- SELECT * FROM PHARMACY.V_DRUG_TRACE_BILL_INP WHERE ROWNUM <= 1;
