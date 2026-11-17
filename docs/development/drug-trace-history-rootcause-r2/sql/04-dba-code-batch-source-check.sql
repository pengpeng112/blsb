-- 未执行：Q01.3 全部531个差异码的源端分批核验模板（DBA/已批准审计身份执行）。
-- 绑定值清单在受限文件 dba-sample-package.json 同目录的 all-missing-codes-*.json（仅本机受限目录）。
-- 分批计划：先3码试查（即S01—S03），再25码试批；行数/耗时稳定后最多100码/批；
-- 每批单独执行本模板，绑定变量按批展开，不使用 OFFSET/全表排序补页。
-- 返回行以 RECORD_ID 保留；恰好达到10000行视为截断，须拆批重跑，不能签认完整。
-- T_TRACE_CODE 有索引（2026-10-19快照）；执行前请DBA确认索引仍然存在。
SELECT T.RECORD_ID,T.PRESC_DETAILID,T.T_TRACE_CODE,
       T.TRACE_BILLNO,T.PRESC_DATE,T.STORE_CODE,
       T.CREATE_TIME,T.MODIFY_TIME,T.IN_OUT_TYPE,T.IS_DELETED,T.SPLIT_FLAG,
       CASE WHEN M.PRESC_NO IS NULL THEN 0 ELSE 1 END AS MASTER_JOIN_HIT
FROM DRUG_USER.MED_TRACE_RECORD T
LEFT JOIN PHARMACY.DRUG_PRESC_MASTER M
  ON T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO)
 AND T.PRESC_DATE=M.PRESC_DATE
 AND T.STORE_CODE=M.DISPENSARY
WHERE T.T_TRACE_CODE IN (:code_0,:code_1,:code_2 /* ...按批最多100个绑定 */)
  AND ROWNUM<=10000
