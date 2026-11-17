"""Inspect the user-installed r10 views with the dedicated readonly account."""
from pathlib import Path
import ast
import contextlib
import importlib.util
import io
import json

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def main():
    previous=load('r10_reader',ROOT/'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    previous.HERE=HERE
    remote=previous.REMOTE
    remote=remote.replace('AND ROWNUM<=100",params,limit=100,public=False)',
        "AND business_kind='PATIENT_DISPENSE' AND ROWNUM<=100\",params,limit=100,public=False)")
    remote=remote.replace('source_revision,source_line_id,source_code_record_id,trace_code,quantity,unit,code_level,is_deleted FROM V_DRUG_TRACE_CODE',
        'source_revision,source_line_id,source_code_record_id,trace_code,quantity,unit,code_level,is_deleted,f_trace_code,s_trace_code,t_trace_code,parent_code FROM V_DRUG_TRACE_CODE')
    source=(ROOT/'implementation/plugins/domains/drug_trace/quality.py').read_text('utf-8')
    wanted={'_QTY_RE','_TRUE_SET','_FALSE_SET','as_bool','check_quantity','evaluate_revision'}
    selected=[]
    for node in ast.parse(source).body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in wanted for t in node.targets):selected.append(ast.unparse(node))
        if isinstance(node,ast.FunctionDef) and node.name in wanted:selected.append(ast.unparse(node))
    prelude="from decimal import Decimal,InvalidOperation\nfrom types import SimpleNamespace\nclass ApiError(Exception):pass\nC=SimpleNamespace(BillQualityState=SimpleNamespace(INVALID='INVALID',WAITING='WAITING',VALID='VALID'))\n"
    remote=remote.replace('try:\n    auth=',prelude+'\n\n'.join(selected)+'\ntry:\n    auth=',1)
    quality="""                    if len(whole)==1:
                        h={**{k:v for k,v in key.items() if k not in ('begin_time','end_time')},**{k.lower():v for k,v in whole[0].items()}}
                        normalized=[]
                        for ln in lines:
                            item={k.lower():v for k,v in ln.items()}
                            item['is_deleted']=as_bool(item.get('is_deleted'),'is_deleted')
                            normalized.append(item)
                        state,issues=evaluate_revision(h,normalized,profile='alihealth_yljg_retail_v1',code_limit_guard=3500)
                        out['one_bill_quality']={'state':state,'rule_codes':[x['rule'] for x in issues],
                            'blocking_rules':[x['rule'] for x in issues if x['severity']=='BLOCKING'],
                            'origin':'actual local quality function; data stays remote; no import or freeze'}
"""
    marker="                    cursor_key={**key,'after_record_id':''}"
    assert marker in remote
    remote=remote.replace(marker,quality+marker)
    compile(remote,'r10_remote','exec');previous.REMOTE=remote
    with contextlib.redirect_stdout(io.StringIO()):code=previous.main()
    receipt=json.loads((HERE/'evidence/reader-r1.json').read_text('utf-8'))
    summary={k:v for k,v in receipt.items() if k not in ('queries',)}
    summary['view_status']=receipt.get('queries',{}).get('view_status',{}).get('rows')
    summary['identity']=receipt.get('queries',{}).get('identity',{}).get('rows')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return code

if __name__=='__main__':raise SystemExit(main())
