-- r11 住院码视图 v2（DTR-R11-VIEWTUNE-20261029；修复 v1 的两个实测问题）。
-- 依据 evidence/viewtune-*.json：
--   ① 分支一重复行：PHA_INP_REQUEST_DRUG 一行=一药品行，T×w 未按行对齐 →
--     每条码被放大为 N 行（实测 1 码返回 25 行）；quantity/w.EXEC_SQN 取值任意。
--     → 改为「每单去重子查询」（GROUP BY REQUEST_NO, PHA_CODE），quantity 回归
--       r10 的 NULL 口径，source_line_id 用码行自带 T.PRESC_DETAILID（不再取任意行）。
--   ② UNION ALL 双分支导致执行计划漂移（同查询 0.12s/12.3s 不稳）+ 分支二
--     TO_CHAR(DIS_RECORDNO) 连接慢路径。→ 分支二移除（当前 0 行、DIS_RECORDNO 键
--     从未出现；将来 HIS 若改用该键，按 viewtune D 形式【先取键再 IN】加回）。
-- 执行：PHARMACY 属主账号（同 r10/r11 建视图账号）执行本条；GRANT 不变（视图重建
-- 不影响既有授权）。列名/类型与 r10 契约一致，reader/ingest 零改动。

CREATE OR REPLACE VIEW PHARMACY.V_DRUG_TRACE_CODE_INP AS
SELECT
    CAST('HIS' AS VARCHAR2(16)) AS source_system,
    'INP|' || w.REQUEST_NO || '|' || w.PHA_CODE AS source_bill_id,
    w.PHA_CODE AS pharmacy_code,
    CAST('INPATIENT_DISPENSE' AS VARCHAR2(32)) AS business_kind,
    TO_CHAR(TO_DATE(SUBSTR(w.PRESC_CREATE_TIME, 1, 19), 'YYYY-MM-DD HH24:MI:SS'),
            'YYYYMMDDHH24MISS')
        || '_' || w.REQUEST_NO
        || '_' || w.PHA_CODE
        || '_' || TO_CHAR(w.DISP_EXECUTE_TIME, 'YYYYMMDDHH24MISS')
        || '_INP' AS source_revision,
    T.PRESC_DETAILID AS source_line_id,
    T.RECORD_ID AS source_code_record_id,
    T.T_TRACE_CODE AS trace_code,
    CAST(NULL AS NUMBER(20, 6)) AS quantity,
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
JOIN (
    SELECT REQUEST_NO, PHA_CODE,
           MIN(PRESC_CREATE_TIME) AS PRESC_CREATE_TIME,
           MIN(DISP_EXECUTE_TIME) AS DISP_EXECUTE_TIME
    FROM DRUG_USER.PHA_INP_REQUEST_DRUG
    WHERE DISP_FLAG = 'send'
      AND DISP_QUANTITY > 0
      AND DISP_EXECUTE_TIME IS NOT NULL
    GROUP BY REQUEST_NO, PHA_CODE
) w
  ON T.TRACE_BILLNO = w.REQUEST_NO
WHERE T.IN_OUT_TYPE = 'OUT'
WITH READ ONLY;

-- 验证（任意只读账号；三应稳定在亚秒级且行数=码行数，不再随药品行数放大）：
-- SELECT COUNT(*) FROM PHARMACY.V_DRUG_TRACE_CODE_INP;                    -- ≈47295（总行）
-- SELECT source_code_record_id, source_line_id FROM
--   PHARMACY.V_DRUG_TRACE_CODE_INP
--  WHERE source_bill_id='INP|03032820261027113457|050121';               -- 1 行（v1 会给 25 行）
