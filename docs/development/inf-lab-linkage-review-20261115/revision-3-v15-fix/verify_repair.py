"""Validate proposed view SELECT only; never run CREATE/ALTER/GRANT or HTTP."""
from pathlib import Path
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import re
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    sjzc=load('sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    validator=load('validator',Path('F:/python/数据资产/.agents/skills/ods-readonly-sql/scripts/validate_readonly_sql.py'))
    assert sjzc._ssh_psql("SELECT enabled, write_policy FROM asset.asset_data_sources WHERE source_code='ods_internal'")=='t|readonly'
    query=(HERE/'proposed-select.sql').read_text(encoding='utf-8').strip()
    boundary=query.index('-- ---------- 住院段')
    ctes,body=query[:boundary],query[boundary:]
    assert query.count('FROM HIS.LAB_TEST_MASTER a')==3
    cases=list((ROOT/'数据上报/4、传染病病历数据上报').glob('病例核查说明-*-20261115.md'));assert len(cases)==1
    keys=list(dict.fromkeys(re.findall(r'\b261114\d{4}\b',cases[0].read_text(encoding='utf-8'))));assert len(keys)==3
    quoted=','.join("'%s'"%key for key in keys)
    bounded=query.replace("WHERE a.VISIT_ID = '0'", "WHERE a.TEST_NO IN (%s) AND a.VISIT_ID = '0'"%quoted)
    bounded=bounded.replace('WHERE a.RESULTS_RPT_DATE_TIME IS NOT NULL','WHERE a.TEST_NO IN (%s) AND a.RESULTS_RPT_DATE_TIME IS NOT NULL'%quoted)
    assert bounded.count('a.TEST_NO IN')==3
    boundary=bounded.index('-- ---------- 住院段');bounded_ctes,bounded_body=bounded[:boundary],bounded[boundary:]
    labels=' '.join("WHEN '%s' THEN 'P%d'"%(key,i+1) for i,key in enumerate(keys))
    queries={
      'zero_row_oracle_parse':ctes+'SELECT COUNT(*) AS PARSE_ROWS FROM (\n'+body+'\n) r WHERE 1=0',
      'bounded_case_preview':bounded_ctes+"SELECT CASE r.ID %s END AS REPORT_LABEL, r.ACTIVITY_TYPE_CODE, COUNT(*) AS N, SUM(CASE WHEN EXISTS (SELECT 1 FROM ODS.M_INF_FXHIS_ACTIVITY act WHERE act.PATIENT_ID=r.PATIENT_ID AND act.SERIAL_NUMBER=r.SERIAL_NUMBER AND act.ACTIVITY_TYPE_CODE=r.ACTIVITY_TYPE_CODE) THEN 1 ELSE 0 END) AS ACTIVITY_HITS, SUM(CASE WHEN r.OPERATOR_ID IS NULL THEN 1 ELSE 0 END) AS OP_EMPTY FROM (\n"%labels+bounded_body+'\n) r GROUP BY r.ID,r.ACTIVITY_TYPE_CODE',
      'current_item_codes':"SELECT NVL(EXAMINATION_RESULT_CODE,'(NULL)') AS CODE, COUNT(*) AS N FROM ODS.V_INF_EX_LAB_ITEM WHERE EX_LAB_ID IN (%s) GROUP BY EXAMINATION_RESULT_CODE"%quoted,
    }
    evidence={}
    for name,sql in queries.items():
        # The controlled connector rejects SQL comments. These generated queries
        # contain no double-hyphen string literals; remove comments mechanically.
        sql=re.sub(r'--[^\n]*','',sql)
        assert not validator.validate(sql),(name,validator.validate(sql))
        capture=io.StringIO();started=time.monotonic()
        with redirect_stdout(capture):sjzc.cmd_live('ods_internal',sql,10)
        raw=capture.getvalue()
        try:result=json.loads(raw)
        except json.JSONDecodeError:
            last_line=raw.strip().splitlines()[-1] if raw.strip() else 'EMPTY'
            for key in keys:last_line=last_line.replace(key,'[REDACTED]')
            result={'ok':False,'oracle_errors':re.findall(r'ORA-\d+',raw),'error_last_line':last_line[:250],'note':'Raw output not retained.'}
        result.update(elapsed_seconds=round(time.monotonic()-started,2),sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),static_validation='PASS_BEFORE_EXECUTION')
        evidence[name]=result
        (HERE/'select-verification.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(name,json.dumps(result,ensure_ascii=False))
        assert result.get('ok') is True,name
    preview=evidence['bounded_case_preview']['rows']
    assert len(preview)==3 and all(x['ACTIVITY_TYPE_CODE']=='2' and x['N']=='1' and x['ACTIVITY_HITS']=='1' for x in preview)
    print('READ_ONLY_ORACLE_PARSE_AND_CASE_PREVIEW_OK_NO_DDL')


if __name__=='__main__':main()
