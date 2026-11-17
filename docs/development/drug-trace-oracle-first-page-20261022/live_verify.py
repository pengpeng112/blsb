"""Bounded real Oracle SELECT proof for the locally corrected SQL template.

Only the user-created two views are read through the platform's controlled
connector. Real codes/bill identifiers stay in the remote process; no upload.
"""
from pathlib import Path
import ast
from datetime import datetime, timezone
import importlib.util
import json
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

BODY=r'''
try:
    identity=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    if not identity:raise RuntimeError('Reader authentication failed')
    params={'begin_time':datetime(2026,10,22),'end_time':datetime(2026,10,23)}
    sample=query('choose_one_bill','SELECT source_system,pharmacy_code,business_kind,source_bill_id FROM V_DRUG_TRACE_CODE WHERE bill_time>=:begin_time AND bill_time<:end_time AND ROWNUM<=1',params,limit=1,public=False)
    if not sample:raise RuntimeError('No candidate in authorized window')
    key={k.lower():v for k,v in sample[0].items()}
    bound={**key,**params}
    baseline=query('baseline','SELECT source_code_record_id,source_revision FROM V_DRUG_TRACE_CODE WHERE source_system=:source_system AND pharmacy_code=:pharmacy_code AND business_kind=:business_kind AND source_bill_id=:source_bill_id AND bill_time>=:begin_time AND bill_time<:end_time ORDER BY source_code_record_id',bound,limit=20,public=False)
    if not baseline or len(baseline)>20:raise RuntimeError('Candidate size outside 1..20 verification budget')
    expected=[r['SOURCE_CODE_RECORD_ID'] for r in baseline]
    first_pages={}
    for name,initial in [('null',None),('empty','')]:
        rows=query('first_'+name,SQL,{**bound,'after_record_id':initial},limit=2,public=False)
        if rows is None:raise RuntimeError('First page query failed')
        first_pages[name]=[r['SOURCE_CODE_RECORD_ID'] for r in rows]
    all_rows=[];after='';pages=[]
    for number in range(12):
        rows=query('page_'+str(number+1),SQL,{**bound,'after_record_id':after},limit=2,public=False)
        if rows is None:raise RuntimeError('Page query failed')
        pages.append(len(rows));all_rows.extend(rows)
        if len(rows)<2:break
        new_after=rows[-1]['SOURCE_CODE_RECORD_ID']
        if new_after==after:raise RuntimeError('Non advancing cursor')
        after=new_after
    else:raise RuntimeError('Page budget exceeded')
    ids=[r['SOURCE_CODE_RECORD_ID'] for r in all_rows]
    out['checks']={'authenticated_as_new_reader':identity[0]['LOGIN_USER']=='BLYB_DRUGTRACE',
        'null_first_page_matches_baseline':first_pages['null']==expected[:2],
        'empty_first_page_matches_baseline':first_pages['empty']==expected[:2],
        'whole_bill_matches_baseline_in_order':ids==expected,
        'no_duplicate_record_ids':len(ids)==len(set(ids)),
        'single_revision':len({r['SOURCE_REVISION'] for r in baseline+all_rows})==1,
        'all_returned_keys_match':all(all(r[k.upper()]==v for k,v in key.items()) for r in all_rows)}
    out['bill_code_rows']=len(expected)
    out['page_sizes']=pages
    out['passed']=all(out['checks'].values())
    out['sql_origin']='actual corrected local drugtrace.bill_codes template, with additive same-day guard'
    out['template_sha256']=TEMPLATE_SHA
except Exception as exc:
    out['passed']=False;out['failure']=err(exc)
finally:c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
'''


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    import hashlib
    revision=sys.argv[1] if len(sys.argv)>1 else 'r1'
    if not re.fullmatch(r'r\d+',revision):raise SystemExit('revision required')
    output=HERE/'evidence'/('oracle-live-'+revision+'.json')
    with output.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    source=(ROOT/'implementation/plugins/sources/db_view/drug_trace.py').read_text('utf-8')
    templates=next(ast.literal_eval(node.value) for node in ast.parse(source).body
        if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id=='TEMPLATES')
    template=templates['drugtrace.bill_codes']['oracle']
    sql=template.format(code_view='V_DRUG_TRACE_CODE',limit=2)
    sql=sql.replace(' ORDER BY source_code_record_id)',
        ' AND bill_time>=:begin_time AND bill_time<:end_time ORDER BY source_code_record_id)')
    previous=load('previous_reader_probe',ROOT/'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    prefix=previous.REMOTE.split('try:\n    auth=',1)[0]
    sjzc=load('first_page_sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    imports='\n'.join(line for line in sjzc.LIVE_CODE.splitlines() if line.startswith('from app.'))
    for line in prefix.splitlines():
        if line.startswith('from app.core.database ') or line.startswith('from app.models.asset '):prefix=prefix.replace(line,'')
    credential=ROOT/'.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'
    ps="$c=Import-Clixml -LiteralPath '"+str(credential).replace("'","''")+"'; @{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress"
    creds=json.loads(subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],capture_output=True,text=True,timeout=15,check=True).stdout)
    remote=imports+'\nCREDS='+repr(creds)+'\nSQL='+repr(sql)+'\nTEMPLATE_SHA='+repr(hashlib.sha256(template.encode()).hexdigest())+'\n'+prefix+BODY
    compile(remote,'controlled_remote','exec')
    proc=subprocess.run(['ssh','-i',sjzc.SSH_KEY,*sjzc.SSH_OPTS,sjzc.SSH_TARGET,
        'docker exec -i -e PYTHONPATH=/app -e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
        input=remote.encode('utf-8'),capture_output=True,timeout=180)
    try:out=json.loads(proc.stdout.decode('utf-8').splitlines()[-1])
    except Exception as exc:out={'passed':False,'error_type':type(exc).__name__,'ssh_exit_code':proc.returncode}
    out['captured_at']=datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({k:v for k,v in out.items() if k!='queries'},ensure_ascii=False,indent=2))
    return 0 if out.get('passed') else 1

if __name__=='__main__':raise SystemExit(main())
