SELECT source_system,source_bill_id,source_line_id,source_code_record_id,
       pharmacy_code,business_kind,trace_code,is_deleted,bill_time,updated_at
FROM PHARMACY.V_DRUG_TRACE_CODE
-- 三个精确历史“原码＋明细号”候选，不靠明细号单独筛大表。
-- 2026-10-19索引快照确认T_TRACE_CODE索引；本期视图trace_code直取该列。
-- 索引快照不是当次执行计划保证；仍须30秒超时，碰到101行停止扩批。
WHERE ((trace_code=:legacy_code_0 AND source_line_id=:legacy_id_0)
    OR (trace_code=:legacy_code_1 AND source_line_id=:legacy_id_1)
    OR (trace_code=:legacy_code_2 AND source_line_id=:legacy_id_2))
  AND ROWNUM<=101
