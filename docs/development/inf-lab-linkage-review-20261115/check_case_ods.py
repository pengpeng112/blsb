"""Narrow, aggregate-only ODS verification via the existing sjzc connector."""
from contextlib import redirect_stdout
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import re
import time

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    cases=list((ROOT/'数据上报/4、传染病病历数据上报').glob('病例核查说明-*-20261115.md'))
    if len(cases)!=1:raise RuntimeError('Case document is ambiguous; select its scope explicitly')
    case=cases[0]
    keys=list(dict.fromkeys(re.findall(r'\b261114\d{4}\b',case.read_text(encoding='utf-8'))))
    assert len(keys)==3
    sjzc=load('sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    validator=load('sql_validator',Path('F:/python/数据资产/.agents/skills/ods-readonly-sql/scripts/validate_readonly_sql.py'))
    registry=sjzc._ssh_psql("SELECT source_code, enabled, write_policy FROM asset.asset_data_sources WHERE source_code='ods_internal'")
    assert registry=='ods_internal|t|readonly', 'Source registration did not meet readonly preconditions'
    sql="""SELECT CASE a.TEST_NO __LABELS__ END AS REPORT_LABEL,
      (SELECT COUNT(*) FROM HIS.LAB_RESULT c WHERE c.TEST_NO=a.TEST_NO) AS SOURCE_ITEMS,
      (SELECT COUNT(*) FROM HIS.LAB_TEST_ITEMS b JOIN HIS.LAB_RESULT c ON c.TEST_NO=b.TEST_NO AND c.ITEM_NO=b.ITEM_NO WHERE b.TEST_NO=a.TEST_NO AND c.REPORT_ID IS NOT NULL) AS ELIGIBLE_ITEMS,
      (SELECT COUNT(DISTINCT c.TEST_NO||c.PRINT_ORDER) FROM HIS.LAB_TEST_ITEMS b JOIN HIS.LAB_RESULT c ON c.TEST_NO=b.TEST_NO AND c.ITEM_NO=b.ITEM_NO WHERE b.TEST_NO=a.TEST_NO AND c.REPORT_ID IS NOT NULL) AS UNIQUE_ITEM_IDS,
      (SELECT COUNT(*) FROM ODS.V_INF_EX_LAB p WHERE p.ID=a.TEST_NO) AS PARENT_VIEW_ROWS,
      (SELECT COUNT(*) FROM ODS.V_INF_EX_LAB_ITEM i WHERE i.EX_LAB_ID=a.TEST_NO) AS CHILD_VIEW_ROWS,
      (SELECT COUNT(*) FROM ODS.V_INF_EX_LAB p JOIN ODS.M_INF_FXHIS_ACTIVITY act ON act.PATIENT_ID=p.PATIENT_ID AND act.SERIAL_NUMBER=p.SERIAL_NUMBER AND act.ACTIVITY_TYPE_CODE=p.ACTIVITY_TYPE_CODE WHERE p.ID=a.TEST_NO) AS FULL_ACTIVITY_KEY_HITS
      FROM HIS.LAB_TEST_MASTER a WHERE a.TEST_NO IN (__KEYS__)"""
    sql=sql.replace('__LABELS__',' '.join("WHEN '%s' THEN 'P%d'"%(key,i+1) for i,key in enumerate(keys))).replace('__KEYS__',','.join("'%s'"%key for key in keys))
    assert not validator.validate(sql)
    output=io.StringIO()
    started=time.monotonic()
    with redirect_stdout(output):sjzc.cmd_live('ods_internal',sql,3)
    evidence=json.loads(output.getvalue())
    assert evidence.get('ok') is True and evidence.get('row_count')==3
    evidence.update(mode='READ_ONLY_AGGREGATE_NO_SEND',elapsed_seconds=round(time.monotonic()-started,2),sql_sha256=hashlib.sha256(sql.encode()).hexdigest(),static_validation='PASS_BEFORE_EXECUTION',source_registration=registry)
    template=sql
    for i,key in enumerate(keys,1):template=template.replace("'%s'"%key,':report_'+str(i))
    evidence['sql_redacted_template']=template
    evidence['template_note']='Only a mechanically redacted template; bind values come from the user case document and are not copied into this artifact.'
    encoded=json.dumps(evidence,ensure_ascii=False,indent=2)
    (HERE/'ods-case-summary.json').write_text(encoded+'\n',encoding='utf-8')
    print(encoded)


if __name__=='__main__':main()
