"""Offline verification of the review contract and SQL; no DB connections."""
from pathlib import Path
import importlib.util
import json
import re
import sqlite3

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def projection_names(sql):
    projection=re.split(r'\bFROM\b',sql,flags=re.I)[0].strip()[len('SELECT'):]
    parts=[];start=0;depth=0;quote=False;index=0
    while index<len(projection):
        char=projection[index]
        if char=="'":
            if quote and index+1<len(projection) and projection[index+1]=="'":
                index+=2;continue
            quote=not quote
        elif not quote:
            if char=='(':depth+=1
            elif char==')':depth-=1
            elif char==',' and depth==0:parts.append(projection[start:index]);start=index+1
        index+=1
    parts.append(projection[start:])
    return [re.findall(r'\bAS\s+([A-Za-z_]\w*)',part,re.I)[-1].upper() for part in parts]

def main():
    output=HERE/'evidence/local-validation.json'
    with output.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    checks={}
    builder=load('contract_builder_validation',HERE/'build_contract.py')
    validator=load('his_validator_contract',Path('F:/python/数据资产/.agents/skills/hisuser-readonly-sql/scripts/validate_his_sql.py')).validate
    for name in ('build_contract.py','collect_content_evidence.py','validate_contract.py'):
        compile((HERE/name).read_text('utf-8'),str(HERE/name),'exec');checks[name+'_compile']=True
    static={}
    for name in ('original_user','original_equivalent','source_comments','normal_scope_sample','normal_trace_sample','parse_v_drug_trace_bill','parse_v_drug_trace_code'):
        static[name]=validator((HERE/'sql'/(name+'.sql')).read_text('utf-8'))
    checks['all_seven_select_files_static_valid']=all(not x for x in static.values())
    prior=json.loads((ROOT/'docs/development/drug-trace-view-verify-20261022/evidence/reader-r1.json').read_text('utf-8'))
    expected={name:[r['COLUMN_NAME'] for r in prior['queries']['view_columns']['rows'] if r['TABLE_NAME']==name]
              for name in ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE')}
    checks['draft_exact_header_column_names_order']=projection_names((HERE/'sql/parse_v_drug_trace_bill.sql').read_text('utf-8'))==expected['V_DRUG_TRACE_BILL']
    checks['draft_exact_code_column_names_order']=projection_names((HERE/'sql/parse_v_drug_trace_code.sql').read_text('utf-8'))==expected['V_DRUG_TRACE_CODE']
    boundary=[]
    cases=[(0,1,'2026-10-22 08:00:00',None,True),(0,1,'2026-10-22 08:00:00',0,True),
           (0,1,None,0,False),(1,0,'2026-10-22 08:00:00',None,False),
           (0,2,'2026-10-22 08:00:00',None,False),(0,3,'2026-10-22 08:00:00',None,False),
           (0,1,'2026-10-22 08:00:00',1,False),(0,1,'2026-10-22 08:00:00',9,False),
           (None,1,'2026-10-22 08:00:00',0,False),(2,1,'2026-10-22 08:00:00',0,False),
           (0,None,'2026-10-22 08:00:00',0,False),(0,0,'2026-10-22 08:00:00',0,False)]
    with sqlite3.connect(':memory:') as conn:
        conn.create_function('NVL',2,lambda value,default:default if value is None else value)
        sql='SELECT '+','.join((builder.KIND_CASE,builder.FINAL_CASE,builder.VOID_CASE))+\
            ' FROM (SELECT ? AS PRESC_SOURCE,? AS FLAG,? AS DISPENSING_DATETIME,? AS REFUND_FLAG) M'
        for source,flag,date,refund,eligible in cases:
            actual=conn.execute(sql,(source,flag,date,refund)).fetchone()
            wanted=('PATIENT_DISPENSE',1,0) if eligible else ('UNKNOWN',None,None)
            boundary.append({'source':source,'flag':flag,'has_dispense_time':date is not None,
                             'refund_flag':refund,'matched_candidate_expectation':actual==wanted})
    checks['candidate_twelve_boundary_cases_match']=all(x['matched_candidate_expectation'] for x in boundary)
    live=json.loads((HERE/'evidence/his-content-r1.json').read_text('utf-8'))
    parse=json.loads((HERE/'evidence/his-content-parse-r1.json').read_text('utf-8'))
    checks['five_live_selects_succeeded']=all(x['result'].get('ok') for receipt in (live,parse) for x in receipt['queries'].values())
    checks['live_source_comments_decoded']=sum('COMMENT_DECODED_GBK' in r and r['COMMENT_DECODED_GBK']!='UNDECODED'
        for r in live['queries']['source_comments']['result']['rows'])==15
    contract=json.loads((HERE/'upload-content-contract-r10.json').read_text('utf-8'))
    checks['exact_five_retail_required_fields']=set(contract['required_business_form_fields'])=={'bill_code','bill_time','bill_type','ref_user_id','trace_codes'}
    checks['draft_not_approved_or_installed']=contract['runtime_approved'] is False and contract['installed'] is False
    runtime=json.loads((HERE/'evidence/runtime-dictionaries-r2.json').read_text('utf-8'))
    dictionaries=runtime['result']['inventory']['worker']['data']['dictionaries']
    approved=[r for r in dictionaries if r['approved']]
    checks['actual_raw_status_mapping_missing']=all(not any(x.get('code')=='PRESC_SOURCE=0;FLAG=1' for x in r['items']) for r in approved)
    checks['source_and_upload_writes_zero']=all(receipt['source_writes']==0 and receipt['uploads']==0 for receipt in (live,parse,runtime))
    broken=[];link_count=0
    for file in (HERE/'README.md',ROOT/'docs/ai/sessions/DTR-UPLOAD-CONTENT-CONTRACT-20261022.md'):
        for item in re.findall(r'\]\((<[^>]+>|[^)]+)\)',file.read_text('utf-8')):
            item=item.strip('<>')
            if item.startswith(('https:','http:','#')):continue
            link_count+=1
            if not (file.parent/item.split('#')[0]).resolve().exists():broken.append(item)
    checks['document_links_valid']=not broken
    out={'passed':all(checks.values()),'checks':checks,'static_selects':static,'candidate_rule_cases':boundary,
         'candidate_rule_is_approved':False,'local_link_count':link_count,'broken_links':broken,
         'his_ddl_executed':False,'real_uploads':0,'app_code_changed_this_task':False}
    output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({k:v for k,v in out.items() if k not in ('candidate_rule_cases','static_selects')},ensure_ascii=False,indent=2))
    return 0 if out['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
