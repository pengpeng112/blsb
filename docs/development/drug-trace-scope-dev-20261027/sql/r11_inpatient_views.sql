-- r11 视图（DTR-SCOPE-DEV-20261027 SD-02/SD-03；DBA 在 PHARMACY 下执行，执行 AI 不连生产建）。
-- 列契约与 r10（docs/development/drug-trace-content-contract-20261022/sql/
-- rollback_to_observed_views.sql 的 17/18 列）逐列一致 → reader 模板
-- poll_bills/bill_codes/bill_header 与 ingest/质量全链路零改动复用。
-- 证据：docs/development/drug-trace-scope-dev-20261027/evidence/SD-01-findings.md
--   - 住院：REQUEST_NO/DIS_RECORDNO 在 MED_TRACE_RECORD 均零命中（码未写入）；
--     code 视图两候选键 UNION，将来任一键写入即流入。
--   - 门诊退药：IN 码未写入；code 视图按三键+IN 方向建（当前自然 0 行），
--     冲正码取应用侧原发药 CONFIRMED 冻结 payload（只读）。
-- 不改 r10；全部新建；WITH READ ONLY。
-- 授权（同 r10 权限形状）：
--   GRANT SELECT ON PHARMACY.V_DRUG_TRACE_BILL_INP TO BLYB_DRUGTRACE;
--   GRANT SELECT ON PHARMACY.V_DRUG_TRACE_CODE_INP TO BLYB_DRUGTRACE;
--   GRANT SELECT ON PHARMACY.V_DRUG_TRACE_RETURN TO BLYB_DRUGTRACE;
--   GRANT SELECT ON PHARMACY.V_DRUG_TRACE_RETURN_CODE TO BLYB_DRUGTRACE;

-- ================================================================ 住院发药（K4）
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


-- 住院码视图：两候选键 UNION ALL（各自等值连接走 TRACE_BILLNO 索引；
-- 同一 MED_TRACE_RECORD 行的 TRACE_BILLNO 只可能等于其一，UNION ALL 不重复）。
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
LEFT JOIN DRUG_USER.PHA_INP_DISPMASTER d
  ON w.REQUEST_NO = d.REQUEST_NO
 AND TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYY-MM-DD HH24:MI:SS') = d.DISP_DATE
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
JOIN DRUG_USER.PHA_INP_REQUEST_DRUG w
  ON T.TRACE_BILLNO = TO_CHAR(d2.DIS_RECORDNO)
JOIN DRUG_USER.PHA_INP_DISPMASTER d2
  ON w.REQUEST_NO = d2.REQUEST_NO
 AND TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYY-MM-DD HH24:MI:SS') = d2.DISP_DATE
WHERE w.DISP_FLAG = 'send'
  AND w.DISP_QUANTITY > 0
  AND w.DISP_EXECUTE_TIME IS NOT NULL
  AND T.IN_OUT_TYPE = 'OUT'
WITH READ ONLY;


-- ================================================================ 门诊退药冲正（K2）
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


-- 退药码视图：IN 方向 + 三键（处方号/开立时间/药房）。SD-01.2 实测 IN 码未写入
-- （当前自然 0 行）；HIS 开始写 IN 码后按本键流入。PRESC_CREATE_TIME 与
-- MED_TRACE_RECORD.PRESC_DATE 语义如不完全一致仍恒 0 行（fail-safe 不产假数据）。
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
