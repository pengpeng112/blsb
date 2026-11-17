-- HIS源端<his-host>:1521/his，原表已解析为DRUG_USER.MED_TRACE_RECORD。
-- RECORD_ID为已启用主键，每个分组仅一行；本文件保持原日期字段及BETWEEN边界。
-- 只是原SQL的扫码记录级化简，不是最终上报单据/完成口径；不直接发送。
SELECT
    T.RECORD_ID AS source_code_record_id,
    T.PRESC_DETAILID AS 单据号,
    T.CREATE_TIME AS 单据时间,
    T.T_TRACE_CODE AS 追溯码
FROM DRUG_USER.MED_TRACE_RECORD T
WHERE T.IN_OUT_TYPE = 'OUT'
  AND T.CREATE_TIME BETWEEN TO_DATE('2026-07-23 00:00:00', 'yyyy-mm-dd hh24:mi:ss')
                        AND TO_DATE('2026-10-12 00:00:00', 'yyyy-mm-dd hh24:mi:ss')
  AND T.PRESC_DETAILID IS NOT NULL;
