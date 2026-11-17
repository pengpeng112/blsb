"""User-authorized limited HIS inspection via the <relay-jump> controlled connector.

Loads only the newly supplied reader's local DPAPI credential. Existing hosted
credentials are never read. No source writes, platform registration changes or
uploads. Business identifiers and trace codes stay inside the remote process.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
PRIVATE = ROOT / '.ai/private/drug-trace-view-verify-20261022'

REMOTE = r'''
import json, re, hashlib
from collections import Counter
from datetime import datetime
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.asset import AssetDataSource
from app.services.db_connectors import DB_CONNECTOR_MAP, transport_kwargs

out={'channel':'sjzc/<relay-jump>/data-asset-api/OracleConnector.execute_readonly',
     'source_writes':0,'uploads':0,'registration_changes':0,'queries':{},
     'business_identifiers_returned':False}
def err(exc):
    return {'error_type':type(exc).__name__,
            'oracle_codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\d+',str(exc))))}
db=SessionLocal()
src=db.scalar(select(AssetDataSource).where(AssetDataSource.source_code=='his_source_<his_host_id>'))
db.close()
if src is None or (src.target_host,src.port,src.service_name)!=('<his-host>',1521,'his') or not src.enabled:
    raise RuntimeError('Registered source endpoint mismatch')
out['registration']={'source_code':src.source_code,'host':src.target_host,
                     'port':src.port,'service':src.service_name,'write_policy':src.write_policy}
c=DB_CONNECTOR_MAP['oracle'](host=src.target_host,port=src.port,database=src.service_name,
    user=CREDS['user'],password=CREDS['password'],connection_mode=src.connection_mode or 'direct',
    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':45000})
def query(key,sql,params=None,limit=100,public=True):
    try:
        rows=c.execute_readonly(sql,params or {},max_rows=limit)
        out['queries'][key]={'ok':True,'sql':sql,'params':params if public else '[business key retained remotely]',
                             'rows_returned':len(rows),'truncated':len(rows)>limit}
        if public:out['queries'][key]['rows']=rows[:limit]
        return rows
    except Exception as exc:
        out['queries'][key]={'ok':False,'sql':sql,**err(exc)}
        return None
try:
    auth=query('identity',"SELECT USER AS LOGIN_USER, SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    if auth is not None:
        query('system_privileges','SELECT PRIVILEGE FROM SESSION_PRIVS ORDER BY PRIVILEGE')
        query('roles','SELECT ROLE FROM SESSION_ROLES ORDER BY ROLE')
        query('direct_grants',"SELECT OWNER,TABLE_NAME,PRIVILEGE,GRANTABLE FROM USER_TAB_PRIVS_RECD ORDER BY OWNER,TABLE_NAME,PRIVILEGE")
        query('synonyms',"SELECT SYNONYM_NAME,TABLE_OWNER,TABLE_NAME FROM USER_SYNONYMS WHERE SYNONYM_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY SYNONYM_NAME")
        query('view_status',"SELECT OWNER,OBJECT_NAME,STATUS FROM ALL_OBJECTS WHERE OWNER='PHARMACY' AND OBJECT_TYPE='VIEW' AND OBJECT_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY OBJECT_NAME")
        query('view_columns',"SELECT OWNER,TABLE_NAME,COLUMN_ID,COLUMN_NAME,DATA_TYPE,DATA_LENGTH FROM ALL_TAB_COLUMNS WHERE OWNER='PHARMACY' AND TABLE_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY TABLE_NAME,COLUMN_ID")
        query('view_definition',"SELECT OWNER,VIEW_NAME,TEXT_LENGTH,TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY VIEW_NAME",limit=2)
        query('header_parse',"SELECT source_system,source_bill_id,source_bill_no,source_revision,business_kind,pharmacy_code,warehouse_code,supplier_code,bill_time,updated_at,source_event_seq,finalized,is_void,expected_code_count,patient_ref,encounter_ref,source_bill_kind_raw FROM V_DRUG_TRACE_BILL WHERE 1=0",limit=1)
        query('code_parse',"SELECT source_system,source_bill_id,pharmacy_code,business_kind,source_revision,source_line_id,source_code_record_id,trace_code,quantity,unit,code_level,parent_code,updated_at,is_deleted,f_trace_code,s_trace_code,t_trace_code,bill_time FROM V_DRUG_TRACE_CODE WHERE 1=0",limit=1)
        windows=[('today',datetime(2026,10,22),datetime(2026,10,23)),
                 ('original_last_day',datetime(2026,10,11),datetime(2026,10,12))]
        for name,begin,end in windows:
            params={'begin_time':begin,'end_time':end}
            headers=query(name+'_headers',"SELECT source_system,source_bill_id,source_revision,business_kind,pharmacy_code,finalized,is_void,expected_code_count,source_bill_kind_raw FROM V_DRUG_TRACE_BILL WHERE bill_time>=:begin_time AND bill_time<:end_time AND ROWNUM<=20",params,limit=20,public=False)
            codes=query(name+'_codes',"SELECT source_system,source_bill_id,source_revision,business_kind,pharmacy_code,source_line_id,source_code_record_id,trace_code,t_trace_code,quantity,unit,code_level,is_deleted,bill_time FROM V_DRUG_TRACE_CODE WHERE bill_time>=:begin_time AND bill_time<:end_time AND ROWNUM<=100",params,limit=100,public=False)
            if headers is not None:
                out[name+'_header_summary']={'sample_rows':len(headers),'business_kind':dict(Counter(str(r['BUSINESS_KIND']) for r in headers)),
                    'raw_kind':dict(Counter(str(r['SOURCE_BILL_KIND_RAW']) for r in headers)),
                    'null_fields':{k:sum(r[k] is None for r in headers) for k in ('FINALIZED','IS_VOID','EXPECTED_CODE_COUNT')},
                    'duplicate_sample_keys':len(headers)-len({(r['SOURCE_SYSTEM'],r['PHARMACY_CODE'],r['BUSINESS_KIND'],r['SOURCE_BILL_ID']) for r in headers})}
            if codes is not None:
                out[name+'_code_summary']={'sample_rows':len(codes),
                    'code_lengths':dict(Counter(str(len(str(r['TRACE_CODE']))) if r['TRACE_CODE'] is not None else 'NULL' for r in codes)),
                    'twenty_digit_codes':sum(bool(re.fullmatch(r'\d{20}',str(r['TRACE_CODE'] or ''))) for r in codes),
                    'raw_deleted_values':dict(Counter(str(r['IS_DELETED']) for r in codes)),
                    'null_fields':{k:sum(r[k] is None for r in codes) for k in ('SOURCE_LINE_ID','SOURCE_CODE_RECORD_ID','TRACE_CODE','QUANTITY','UNIT','CODE_LEVEL')},
                    'trace_alias_mismatch':sum(r['TRACE_CODE']!=r['T_TRACE_CODE'] for r in codes)}
            if codes:
                chosen=codes[0]
                key={k.lower():chosen[k] for k in ('SOURCE_SYSTEM','PHARMACY_CODE','BUSINESS_KIND','SOURCE_BILL_ID')}
                key.update({'begin_time':begin,'end_time':end})
                where=' WHERE source_system=:source_system AND pharmacy_code=:pharmacy_code AND business_kind=:business_kind AND source_bill_id=:source_bill_id AND bill_time>=:begin_time AND bill_time<:end_time'
                whole=query('one_bill_header','SELECT source_revision,business_kind,finalized,is_void,expected_code_count,source_bill_kind_raw FROM V_DRUG_TRACE_BILL'+where,key,limit=2,public=False)
                lines=query('one_bill_codes','SELECT source_revision,source_line_id,source_code_record_id,trace_code,quantity,unit,code_level,is_deleted FROM V_DRUG_TRACE_CODE'+where+' ORDER BY source_code_record_id',key,limit=1000,public=False)
                if whole is not None and lines is not None:
                    out['one_bill_summary']={'window':name,'header_rows':len(whole),'code_rows':len(lines),'code_rows_truncated':len(lines)>1000,
                       'header_null_fields':{k:sum(r[k] is None for r in whole) for k in ('FINALIZED','IS_VOID','EXPECTED_CODE_COUNT')},
                       'business_kind':dict(Counter(str(r['BUSINESS_KIND']) for r in whole)),
                       'revision_mismatch_rows':sum(r['SOURCE_REVISION']!=whole[0]['SOURCE_REVISION'] for r in lines) if len(whole)==1 else None,
                       'duplicate_record_ids':len(lines)-len({r['SOURCE_CODE_RECORD_ID'] for r in lines}),
                       'duplicate_nonnull_trace_codes':len([r for r in lines if r['TRACE_CODE'] is not None])-len({r['TRACE_CODE'] for r in lines if r['TRACE_CODE'] is not None}),
                       'raw_deleted_values':dict(Counter(str(r['IS_DELETED']) for r in lines)),
                       'code_null_fields':{k:sum(r[k] is None for r in lines) for k in ('TRACE_CODE','QUANTITY','UNIT','CODE_LEVEL')}}
                    cursor_key={**key,'after_record_id':''}
                    old=query('oracle_first_page_old','SELECT COUNT(*) AS N FROM V_DRUG_TRACE_CODE'+where+' AND source_code_record_id>:after_record_id',cursor_key,limit=1,public=False)
                    fixed=query('oracle_first_page_null_safe','SELECT COUNT(*) AS N FROM V_DRUG_TRACE_CODE'+where+' AND (:after_record_id IS NULL OR source_code_record_id>:after_record_id)',cursor_key,limit=1,public=False)
                    out['first_page_comparison']={'deployed_predicate_count':old[0]['N'] if old else None,'null_safe_predicate_count':fixed[0]['N'] if fixed else None}
                break
finally:
    c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
'''


def main():
    phase=sys.argv[1] if len(sys.argv)>1 else 'r1'
    if not re.fullmatch(r'r\d+',phase):raise SystemExit('revision required')
    output=HERE/'evidence'/('reader-'+phase+'.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    credential_path=PRIVATE/'his-reader.credential.clixml'
    ps="$c=Import-Clixml -LiteralPath '"+str(credential_path).replace("'","''")+"'; @{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress"
    proc=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],capture_output=True,text=True,timeout=15,check=True)
    creds=json.loads(proc.stdout)
    spec=importlib.util.spec_from_file_location('reader_sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    sjzc=importlib.util.module_from_spec(spec);spec.loader.exec_module(sjzc)
    # Exact import path follows the installed sjzc LIVE_CODE, avoiding a new driver.
    installed=sjzc.LIVE_CODE
    imports='\n'.join(line for line in installed.splitlines() if line.startswith('from app.'))
    remote=REMOTE
    for line in remote.splitlines():
        if line.startswith('from app.core.database ') or line.startswith('from app.models.asset '):
            remote=remote.replace(line,'')
    remote=imports+'\nCREDS='+repr(creds)+'\n'+remote
    p=subprocess.run(['ssh','-i',sjzc.SSH_KEY,*sjzc.SSH_OPTS,sjzc.SSH_TARGET,
        'docker exec -i -e PYTHONPATH=/app -e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
        input=remote.encode('utf-8'),capture_output=True,timeout=240)
    try:
        result=json.loads(p.stdout.decode('utf-8').splitlines()[-1])
    except Exception as exc:
        result={'ok':False,'error_type':type(exc).__name__,'ssh_exit_code':p.returncode,
                'codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\d+',p.stderr.decode('utf-8',errors='replace'))))}
    result['captured_at']=datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get('queries',{}).get('identity',{}).get('ok') else 1


if __name__=='__main__':raise SystemExit(main())
