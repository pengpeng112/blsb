"""Validate this inspection's receipts and artifacts without touching servers."""
from pathlib import Path
import ast
import json
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

def main():
    revision=sys.argv[1] if len(sys.argv)>1 else 'r3'
    if not re.fullmatch(r'r\d+',revision):raise SystemExit('revision required')
    output=HERE/'evidence'/('local-validation-'+revision+'.json')
    with output.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    reader=json.loads((HERE/'evidence/reader-r1.json').read_text('utf-8'))
    checks={}
    for name in ('verify_reader.py','recheck_runtime.py','validate_local.py'):
        source=(HERE/name).read_text('utf-8')
        compile(source,str(HERE/name),'exec')
        checks[name+'_compile']=True
        for node in ast.parse(source).body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REMOTE' for t in node.targets):
                compile(ast.literal_eval(node.value),'REMOTE','exec')
                checks['remote_code_compile']=True
    original=(ROOT/'docs/deployment/his-ddl-clock-20261022/sql/20_create_views_r8.sql').read_text('utf-8')
    expected={m.group(1):m.group(2)+'\nWITH READ ONLY' for m in re.finditer(
        r'CREATE VIEW BLYB_RO\.(\w+) AS\s*(.*?)\nWITH READ ONLY;',original,re.S)}
    # Preserve every literal and token; ignore only formatting between SQL tokens.
    tokens=lambda sql:re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|\d+|\|\||<>|>=|<=|\S",sql)
    checks['actual_definitions_match_delivered_select_tokens']=all(
        tokens(row['TEXT'])==tokens(expected[row['VIEW_NAME']])
        for row in reader['queries']['view_definition']['rows'])
    checks['all_16_live_selects_succeeded']=len(reader['queries'])==16 and all(x['ok'] for x in reader['queries'].values())
    columns=reader['queries']['view_columns']['rows']
    checks['header_17_code_18_columns']=sum(x['TABLE_NAME']=='V_DRUG_TRACE_BILL' for x in columns)==17 and sum(x['TABLE_NAME']=='V_DRUG_TRACE_CODE' for x in columns)==18
    checks['real_oracle_old_0_fixed_4']=reader['first_page_comparison']=={'deployed_predicate_count':0,'null_safe_predicate_count':4}
    checks['one_header_four_codes']=reader['one_bill_summary']['header_rows']==1 and reader['one_bill_summary']['code_rows']==4
    app=json.loads((HERE/'evidence/application-r2.json').read_text('utf-8'))
    worker=app['result']['inventory']['worker']
    checks['actual_deployed_bug_present']=worker['exit_code']==0 and worker['data']['oracle_first_page_null_safe'] is False
    checks['actual_deployed_query_succeeded']=worker['data']['query']['unknown'] is False and worker['data']['query']['reason'] is None
    checks['zero_uploads_source_writes_remote_changes']=all(app[k]==0 for k in ('upload_requests','source_writes','remote_config_changes'))
    checks['private_files_ignored']=subprocess.run(['git','check-ignore',
        '.ai/private/drug-trace-view-verify-20261022/connection.json',
        '.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'],
        cwd=ROOT,capture_output=True,text=True).returncode==0
    links=[];broken=[]
    for file in list(HERE.glob('*.md'))+[ROOT/'docs/ai/sessions/DTR-VIEW-VERIFY-FIRSTBILL-20261022.md']:
        for ref in re.findall(r'\]\(([^)]+)\)',file.read_text('utf-8')):
            if ref.startswith(('http:','https:','#')):continue
            path=(file.parent/ref.split('#')[0]).resolve();links.append(str(path))
            if not path.exists():broken.append(str(path))
    checks['local_document_links_valid']=not broken
    out={'checks':checks,'passed':all(checks.values()),'live_select_count':len(reader['queries']),
         'local_link_count':len(links),'broken_links':broken,'business_regression_run':False,
         'business_source_writes':0,'upload_requests':0,'application_code_changed':False,
         'deployed_new_release':False}
    output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
