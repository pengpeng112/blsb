"""Aggregate-only ODS checks for the reviewed revision. No source writes/sends."""
from contextlib import redirect_stdout
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import re
import sys
import time

ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    cases=list((ROOT/'数据上报/4、传染病病历数据上报').glob('病例核查说明-*-20261115.md'))
    assert len(cases)==1
    keys=list(dict.fromkeys(re.findall(r'\b261114\d{4}\b',cases[0].read_text(encoding='utf-8'))));assert len(keys)==3
    key_sql=','.join("'%s'"%key for key in keys)
    six=json.loads((HERE.parent/'replay-6parent.json').read_text(encoding='utf-8'))['ids'];assert len(six)==6 and all(re.fullmatch(r'\d+',x) for x in six)
    sjzc=load('sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    validator=load('validator',Path('F:/python/数据资产/.agents/skills/ods-readonly-sql/scripts/validate_readonly_sql.py'))
    assert sjzc._ssh_psql("SELECT enabled, write_policy FROM asset.asset_data_sources WHERE source_code='ods_internal'")=='t|readonly'
    queries={
      'current_types_and_result_codes':"SELECT 'PARENT_TYPE' AS LAYER, ACTIVITY_TYPE_CODE AS CODE, COUNT(*) AS N FROM ODS.V_INF_EX_LAB WHERE ID IN (%s) GROUP BY ACTIVITY_TYPE_CODE UNION ALL SELECT 'ITEM_CODE', NVL(EXAMINATION_RESULT_CODE,'(NULL)'), COUNT(*) FROM ODS.V_INF_EX_LAB_ITEM WHERE EX_LAB_ID IN (%s) GROUP BY EXAMINATION_RESULT_CODE"%(key_sql,key_sql),
      'v13_join_multiplication':"SELECT CASE p.ID %s END AS REPORT_LABEL, COUNT(*) AS V13_REJOIN_ROWS, MAX(b.CAND_CNT) AS DECLARED_CANDIDATES FROM ODS.V_INF_EX_LAB p JOIN ODS.M_INF_FXHIS_ACTIVITY_V13 b ON b.SERIAL_NUMBER=p.SERIAL_NUMBER WHERE p.ID IN (%s) GROUP BY p.ID"%(' '.join("WHEN '%s' THEN 'P%d'"%(key,i+1) for i,key in enumerate(keys)),key_sql),
      'operator_department_fallback':"SELECT COUNT(*) AS TARGET_REPORTS, SUM(CASE WHEN a.TRANSCRIPTIONIST IS NULL THEN 1 ELSE 0 END) AS NO_TRANSCRIPTIONIST, SUM(CASE WHEN EXISTS (SELECT 1 FROM HIS.DEPT_DICT d WHERE d.DEPT_CODE=TO_CHAR(a.PERFORMED_BY)) THEN 1 ELSE 0 END) AS DEPT_HITS, SUM(CASE WHEN EXISTS (SELECT 1 FROM HIS.STAFF_DICT s WHERE s.EMP_NO=TO_CHAR(a.PERFORMED_BY)) THEN 1 ELSE 0 END) AS STAFF_HITS FROM HIS.LAB_TEST_MASTER a WHERE a.TEST_NO IN (%s)"%','.join("'%s'"%key for key in six),
    }
    output_path=HERE/'view-regression-evidence.json'
    evidence=json.loads(output_path.read_text(encoding='utf-8')) if len(sys.argv)>1 and output_path.exists() else {}
    for name,sql in queries.items():
        if len(sys.argv)>1 and name!=sys.argv[1]:continue
        assert not validator.validate(sql)
        buffer=io.StringIO();started=time.monotonic()
        with redirect_stdout(buffer):sjzc.cmd_live('ods_internal',sql,8)
        raw=buffer.getvalue()
        try:result=json.loads(raw)
        except json.JSONDecodeError:
            result={'ok':False,'connector_error_codes':re.findall(r'(?:ORA|DPY|DPI)-\d+',raw),'error_types':re.findall(r'\b\w+(?:Error|Exception)\b',raw),'note':'Connector returned non-JSON; raw output not retained.'}
        result.update(elapsed_seconds=round(time.monotonic()-started,2),sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),static_validation='PASS_BEFORE_EXECUTION')
        template=sql
        for index,key in enumerate(keys+six,1):template=template.replace("'%s'"%key,':key_'+str(index))
        result['sql_redacted_template']=template
        evidence[name]=result
        (HERE/'view-regression-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    encoded=json.dumps(evidence,ensure_ascii=False,indent=2)
    (HERE/'view-regression-evidence.json').write_text(encoded+'\n',encoding='utf-8')
    print(json.dumps({name:value.get('rows',value) for name,value in evidence.items()},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
