SELECT source_system,source_bill_id,source_line_id,source_code_record_id,
       pharmacy_code,business_kind,trace_code,is_deleted,bill_time,updated_at
FROM PHARMACY.V_DRUG_TRACE_CODE
-- 历史单号候选对应source_line_id；3个精确输入键限制，不扫描所有历史。
-- 历史单据时间可能是扫码CREATE_TIME，不能据其直接限制发药bill_time。
WHERE source_line_id IN (:legacy_id_0,:legacy_id_1,:legacy_id_2)
  AND ROWNUM<=101
