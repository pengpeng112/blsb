-- r10 DRAFT: normal outpatient branch only, not approved or installed.
-- Review the documented status predicate with HIS/pharmacy before executing.
-- Execute as an authorized DBA in PL/SQL Developer, statement by statement.
-- Existing account/grants remain; this file creates NO user and sends NO data.
-- Explicit batch use only: late code edits/refunds still need a change source.
CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_BILL AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,

    TO_CHAR(M.PRESC_DATE, 'YYYY-MM-DD HH24:MI:SS')
        || '|' || TO_CHAR(M.PRESC_NO)
        || '|' || M.DISPENSARY AS source_bill_id,

    TO_CHAR(M.PRESC_NO) AS source_bill_no,

    TO_CHAR(M.PRESC_DATE, 'YYYYMMDDHH24MISS')
        || '_' || TO_CHAR(M.PRESC_NO)
        || '_' || M.DISPENSARY
        || '_' || TO_CHAR(M.DISPENSING_DATETIME, 'YYYYMMDDHH24MISS')
        || '_' || NVL(TO_CHAR(M.PRESC_SOURCE), 'NULL')
        || '_' || NVL(TO_CHAR(M.FLAG), 'NULL') || '_R10_RF_' || NVL(TO_CHAR(M.REFUND_FLAG), 'NULL') AS source_revision,

    CASE WHEN M.PRESC_SOURCE = 0 AND M.FLAG = 1 AND M.DISPENSING_DATETIME IS NOT NULL AND NVL(M.REFUND_FLAG, 0) = 0 THEN 'PATIENT_DISPENSE' ELSE 'UNKNOWN' END AS business_kind,
    M.DISPENSARY AS pharmacy_code,
    CAST(NULL AS VARCHAR2(64)) AS warehouse_code,
    CAST(NULL AS VARCHAR2(64)) AS supplier_code,
    M.DISPENSING_DATETIME AS bill_time,
    M.DISPENSING_DATETIME AS updated_at,
    CAST(NULL AS VARCHAR2(64)) AS source_event_seq,
    CASE WHEN M.PRESC_SOURCE = 0 AND M.FLAG = 1 AND M.DISPENSING_DATETIME IS NOT NULL AND NVL(M.REFUND_FLAG, 0) = 0 THEN 1 ELSE CAST(NULL AS NUMBER(1)) END AS finalized,
    CASE WHEN M.PRESC_SOURCE = 0 AND M.FLAG = 1 AND M.DISPENSING_DATETIME IS NOT NULL AND NVL(M.REFUND_FLAG, 0) = 0 THEN 0 ELSE CAST(NULL AS NUMBER(1)) END AS is_void,
    CAST(NULL AS NUMBER(12)) AS expected_code_count,
    CAST(NULL AS VARCHAR2(64)) AS patient_ref,
    CAST(NULL AS VARCHAR2(64)) AS encounter_ref,

    'PRESC_SOURCE=' || TO_CHAR(M.PRESC_SOURCE)
        || CHR(59) || 'FLAG=' || TO_CHAR(M.FLAG)
        AS source_bill_kind_raw

FROM PHARMACY.DRUG_PRESC_MASTER M
WITH READ ONLY;


CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_CODE AS
SELECT
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
