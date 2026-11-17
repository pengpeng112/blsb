"""Build a reviewable Oracle repair from the frozen v1.5 source. No DB I/O."""
from pathlib import Path
import hashlib
import json
import re

HERE=Path(__file__).resolve().parent


def main():
    source=(HERE/'baseline/02-检验报告视图.sql').read_text(encoding='utf-8')
    start=source.index('CREATE OR REPLACE FORCE VIEW')
    end=source.index('WITH READ ONLY;',start)+len('WITH READ ONLY;')
    statement=source[start:end]
    header,body=statement.split(') AS',1)
    header=header.replace('CREATE OR REPLACE FORCE VIEW','CREATE OR REPLACE VIEW')+') AS'
    inpatient,outpatient=body.split('UNION ALL',1)
    assert inpatient.count('AS OPERATOR_ID')==1
    assert 'PERFORMED_BY), TO_CHAR(a.ORDERING_PROVIDER)' not in statement
    outpatient=outpatient.replace('act.ACTIVITY_TYPE_CODE','actv.ACTIVITY_TYPE_CODE').replace('act.SERIAL_NUMBER','actv.SERIAL_NUMBER')
    outpatient=outpatient.replace('NVL(pmi.NAME, cm.NAME)','NVL(pmi.NAME, actv.CLINIC_NAME)')
    outpatient=outpatient.replace('-- ---------- 门诊段（v1.3：经对照视图绑定唯一 CLINIC_NO；类型复用锚点值） ----------',
                                '-- ---------- 门诊段（v1.5-r1：报告级唯一候选，使用有效V15字段） ----------')
    old_from="""FROM HIS.LAB_TEST_MASTER a
JOIN HIS.CLINIC_MASTER cm ON a.PATIENT_ID=cm.PATIENT_ID
     AND TO_CHAR(cm.VISIT_DATE,'YYYYMMDD')=TO_CHAR(a.REQUESTED_DATE_TIME,'YYYYMMDD')
LEFT JOIN HIS.PAT_MASTER_INDEX pmi ON pmi.PATIENT_ID=a.PATIENT_ID
JOIN ODS.M_INF_FXHIS_ACTIVITY_V15 actv ON actv.TEST_NO = a.TEST_NO
     AND actv.CLINIC_NO = cm.CLINIC_NO"""
    assert old_from in outpatient
    outpatient=outpatient.replace(old_from,"""FROM HIS.LAB_TEST_MASTER a
JOIN outp_bound actv ON actv.TEST_NO = a.TEST_NO
     AND actv.PATIENT_ID = a.PATIENT_ID
     AND actv.CAND_CNT = 1
LEFT JOIN HIS.PAT_MASTER_INDEX pmi ON pmi.PATIENT_ID=a.PATIENT_ID""")
    ctes="""WITH outp_candidates AS (
  SELECT DISTINCT
         a.PATIENT_ID,
         a.TEST_NO,
         cm.NAME AS CLINIC_NAME,
         act.SERIAL_NUMBER,
         act.ACTIVITY_TYPE_CODE
    FROM HIS.LAB_TEST_MASTER a
    JOIN HIS.CLINIC_MASTER cm
      ON cm.PATIENT_ID = a.PATIENT_ID
     AND TO_CHAR(cm.VISIT_DATE,'YYYYMMDD') = TO_CHAR(a.REQUESTED_DATE_TIME,'YYYYMMDD')
    JOIN ODS.M_INF_FXHIS_ACTIVITY_V15 act
      ON act.PATIENT_ID = a.PATIENT_ID
     AND act.SERIAL_NUMBER = cm.CLINIC_NO
   WHERE a.VISIT_ID = '0'
     AND a.RESULT_STATUS = '4'
     AND a.RESULTS_RPT_DATE_TIME IS NOT NULL
     AND a.RESULTS_RPT_DATE_TIME >= DATE'2026-11-14'
     AND act.ACTIVITY_TYPE_CODE IN ('1','2')
),
outp_bound AS (
  SELECT c.PATIENT_ID, c.TEST_NO, c.CLINIC_NAME,
         c.SERIAL_NUMBER, c.ACTIVITY_TYPE_CODE,
         COUNT(*) OVER (PARTITION BY c.PATIENT_ID, c.TEST_NO) AS CAND_CNT
    FROM outp_candidates c
)
"""
    query=ctes+inpatient.strip()+'\nUNION ALL\n'+outpatient.strip()
    # An executable full replacement. FORCE is deliberately absent.
    note="""-- v1.5-r1：修复 V_INF_EX_LAB 无效标识符（交用户/DBA执行；本轮未执行DDL）
-- 数据库：ODS <ods-host>/orcl；以ODS用户或具备对应权限的DBA运行。
-- 前提：ODS.M_INF_FXHIS_ACTIVITY_V15 当前四列为
-- SERIAL_NUMBER / ACTIVITY_TYPE_CODE / PATIENT_ID / OPERATION_TIME，且VALID。
-- 仅替换检验主视图：不改锚点、不改明细视图、不写源表、不执行上传。
-- 报告级绑定在本视图CTE内形成；0候选不入窗，1候选绑定，N候选等待。
-- DISTINCT去除重复锚点；同活动但姓名冲突也等待，不任取姓名。
-- 保留11-14门诊窗口、住院分支、真实操作人映射；不使用科室兜底。
-- 没有FORCE：定义不合法时直接报错，不强制覆盖成INVALID。
-- 遇任一错误立即停止，保留错误原文；不要继续进行真实补传。

"""
    sql=note+header+'\n'+query+'\n'
    (HERE/'01-修复检验主视图-v1.5-r1.sql').write_text(sql,encoding='utf-8')
    (HERE/'proposed-select.sql').write_text(query.removesuffix('WITH READ ONLY;').strip()+'\n',encoding='utf-8')
    (HERE/'repair-manifest.json').write_text(json.dumps({'revision':'v1.5-r1','source_sha256':hashlib.sha256(source.encode()).hexdigest(),'repair_sha256':hashlib.sha256(sql.encode()).hexdigest(),'executed_ddl':False},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('REPAIR_BUILT_NO_DDL')


if __name__=='__main__':main()
