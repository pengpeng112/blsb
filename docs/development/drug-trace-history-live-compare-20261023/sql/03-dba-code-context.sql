-- 未执行：精确旧明细号未找到时，仅按3个原码核查源端上下文。
-- 由已批准的源表只读审计通道或DBA执行；不是生产reader权限扩展。
-- 不含患者字段，输出仅保存受限位置，不贴聊天。达到101行视为可能截断。
-- 执行前确认T_TRACE_CODE索引依据与超时；绑定变量不得写入共享证据。
SELECT T.RECORD_ID,T.PRESC_DETAILID,T.T_TRACE_CODE,
       T.TRACE_BILLNO,T.PRESC_DATE,T.STORE_CODE,
       T.CREATE_TIME,T.MODIFY_TIME,T.IN_OUT_TYPE,T.IS_DELETED,T.SPLIT_FLAG
FROM DRUG_USER.MED_TRACE_RECORD T
WHERE T.T_TRACE_CODE IN (:code_0,:code_1,:code_2)
  AND ROWNUM<=101
