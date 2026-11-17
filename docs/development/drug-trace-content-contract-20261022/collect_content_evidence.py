"""Current task's bounded metadata and aggregate HIS SELECTs via sjzc.

No DDL/DML, no raw patient/trace/bill values returned, no source registration or
credential changes. The current user's ongoing read-only authorization is
limited to this reporting analysis, not production collection qualification.
"""
from datetime import datetime,timezone
from pathlib import Path
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys

HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def main():
    revision=sys.argv[1] if len(sys.argv)>1 else 'r1'
    if not re.fullmatch(r'r\d+',revision):raise SystemExit('revision required')
    phase=sys.argv[2] if len(sys.argv)>2 else 'sample'
    if phase not in ('sample','parse'):raise SystemExit('sample or parse required')
    output=HERE/'evidence'/('his-content-'+('parse-' if phase=='parse' else '')+revision+'.json')
    with output.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    sjzc=load('content_sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    validate=load('content_validator',Path('F:/python/数据资产/.agents/skills/hisuser-readonly-sql/scripts/validate_his_sql.py')).validate
    out={'captured_at':datetime.now(timezone.utc).isoformat(),
         'authorization':'Ongoing user-requested readonly HIS reporting-content analysis; bounded same-day samples',
         'source_writes':0,'registration_changes':0,'uploads':0,
         'channel':'sjzc/<relay-jump>/data-asset-api/execute_readonly','queries':{}}
    gate="SELECT row_to_json(t) FROM (SELECT source_code,target_host,port,service_name,enabled,write_policy FROM asset.asset_data_sources WHERE source_code='his_source_<his_host_id>' LIMIT 1) t"
    registration=json.loads(sjzc._ssh_psql(gate))
    if not(registration.get('target_host')=='<his-host>' and registration.get('port')==1521 and registration.get('service_name')=='his' and registration.get('enabled')):
        raise SystemExit('Registered endpoint mismatch')
    out['registration']=registration
    names=(('source_comments','normal_scope_sample','normal_trace_sample') if phase=='sample'
           else ('parse_v_drug_trace_bill','parse_v_drug_trace_code'))
    for name in names:
        reviewed=(HERE/'sql'/(name+'.sql')).read_text('utf-8')
        if validate(reviewed):raise SystemExit('Static validation failed: '+name)
        sql=re.sub(r'--[^\n]*','',reviewed).strip().rstrip(';')
        buffer=io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):sjzc.cmd_live('his_source_<his_host_id>',sql,100)
            result=json.loads(buffer.getvalue().splitlines()[-1])
        except Exception as exc:
            result={'ok':False,'error_type':type(exc).__name__,
                    'oracle_codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\d+',buffer.getvalue())))}
        if name=='source_comments' and result.get('ok'):
            for row in result.get('rows',[]):
                dump=row.get('COMMENT_BYTES','')
                if ':' in str(dump):
                    try:
                        raw=bytes(int(x.strip(),16) for x in dump.split(':',1)[1].split(','))
                        row['COMMENT_DECODED_GBK']=raw.decode('gbk',errors='strict')
                    except (ValueError,UnicodeDecodeError):row['COMMENT_DECODED_GBK']='UNDECODED'
        out['queries'][name]={'sql':sql,'sha256':hashlib.sha256(sql.encode()).hexdigest(),'result':result}
        output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({name:value['result'] for name,value in out['queries'].items()},ensure_ascii=False,indent=2))
    return 0 if all(x['result'].get('ok') for x in out['queries'].values()) else 1

if __name__=='__main__':raise SystemExit(main())
