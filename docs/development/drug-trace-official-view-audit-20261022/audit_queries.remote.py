from time import monotonic

started=monotonic()
try:
    query('identity', "SELECT USER AS LOGIN_USER, SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    query('privileges','SELECT PRIVILEGE FROM SESSION_PRIVS ORDER BY PRIVILEGE',limit=30)
    query('roles','SELECT ROLE FROM SESSION_ROLES ORDER BY ROLE',limit=30)
    query('grants','SELECT OWNER,TABLE_NAME,PRIVILEGE,GRANTABLE FROM USER_TAB_PRIVS_RECD ORDER BY OWNER,TABLE_NAME,PRIVILEGE',limit=30)
    query('view_status',"SELECT OWNER,OBJECT_NAME,STATUS FROM ALL_OBJECTS WHERE OWNER='PHARMACY' AND OBJECT_TYPE='VIEW' AND OBJECT_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY OBJECT_NAME",limit=2)
    query('columns',"SELECT TABLE_NAME,COLUMN_ID,COLUMN_NAME,DATA_TYPE,DATA_LENGTH FROM ALL_TAB_COLUMNS WHERE OWNER='PHARMACY' AND TABLE_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY TABLE_NAME,COLUMN_ID",limit=40)
    query('definitions',"SELECT VIEW_NAME,TEXT_LENGTH,TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY VIEW_NAME",limit=2)
    begin=datetime(2026,10,22);end=datetime(2026,10,23)
    window={'begin_time':begin,'end_time':end}
    hs=query('headers_sample','SELECT source_system,source_bill_id,source_revision,business_kind,pharmacy_code,bill_time,updated_at,finalized,is_void,expected_code_count,source_bill_kind_raw FROM PHARMACY.V_DRUG_TRACE_BILL WHERE bill_time>=:begin_time AND bill_time<:end_time AND ROWNUM<=30',window,limit=30,public=False)
    cs=query('codes_sample','SELECT source_system,source_bill_id,source_revision,business_kind,pharmacy_code,source_line_id,source_code_record_id,trace_code,f_trace_code,s_trace_code,t_trace_code,is_deleted,bill_time,updated_at FROM PHARMACY.V_DRUG_TRACE_CODE WHERE bill_time>=:begin_time AND bill_time<:end_time AND ROWNUM<=100',window,limit=100,public=False)
    if hs is not None:
        out['header_sample']={'rows':len(hs),'kind':dict(Counter(str(r['BUSINESS_KIND']) for r in hs)),
          'raw_kind':dict(Counter(str(r['SOURCE_BILL_KIND_RAW']) for r in hs)),
          'normal_not_final':sum(r['BUSINESS_KIND']=='PATIENT_DISPENSE' and (r['FINALIZED']!=1 or r['IS_VOID']!=0) for r in hs),
          'empty_key':sum(any(r[k] is None for k in ('SOURCE_SYSTEM','SOURCE_BILL_ID','PHARMACY_CODE','SOURCE_REVISION','BILL_TIME')) for r in hs),
          'header_updated_equals_bill_time':sum(r['BILL_TIME']==r['UPDATED_AT'] for r in hs),
          'expected_code_count_null':sum(r['EXPECTED_CODE_COUNT'] is None for r in hs)}
    if cs is not None:
        out['code_sample']={'rows':len(cs),'kind':dict(Counter(str(r['BUSINESS_KIND']) for r in cs)),
          'deleted_values':dict(Counter(str(r['IS_DELETED']) for r in cs)),
          'trace_lengths':dict(Counter(str(len(str(r['TRACE_CODE']))) if r['TRACE_CODE'] is not None else 'NULL' for r in cs)),
          'twenty_ascii_digits':sum(bool(re.fullmatch(r'[0-9]{20}',str(r['TRACE_CODE'] or ''))) for r in cs),
          'empty_record_id':sum(r['SOURCE_CODE_RECORD_ID'] is None for r in cs),
          'empty_detail_id':sum(r['SOURCE_LINE_ID'] is None for r in cs),
          'trace_alias_mismatch':sum(r['TRACE_CODE']!=r['T_TRACE_CODE'] for r in cs),
          'late_modified_after_dispense':sum(r['UPDATED_AT'] is not None and r['BILL_TIME'] is not None and str(r['UPDATED_AT'])>str(r['BILL_TIME']) for r in cs),
          'f_nonnull':sum(r['F_TRACE_CODE'] is not None for r in cs),'s_nonnull':sum(r['S_TRACE_CODE'] is not None for r in cs)}
    candidates={}
    for r in (cs or [])+(hs or []):
        if r['BUSINESS_KIND']=='PATIENT_DISPENSE':
            key=tuple(r[k] for k in ('SOURCE_SYSTEM','PHARMACY_CODE','BUSINESS_KIND','SOURCE_BILL_ID'))
            candidates.setdefault(key,r)
    out['whole_bills']=[]
    for index,(key,_) in enumerate(list(candidates.items())[:8],1):
        if monotonic()-started>150:
            out['budget_stop']=True;break
        params=dict(zip(('source_system','pharmacy_code','business_kind','source_bill_id'),key));params.update(window)
        where=' WHERE source_system=:source_system AND pharmacy_code=:pharmacy_code AND business_kind=:business_kind AND source_bill_id=:source_bill_id AND bill_time>=:begin_time AND bill_time<:end_time'
        heads=query('whole_header_'+str(index),'SELECT source_revision,business_kind,finalized,is_void,bill_time,updated_at,expected_code_count FROM PHARMACY.V_DRUG_TRACE_BILL'+where+' AND ROWNUM<=3',params,limit=3,public=False)
        lines=query('whole_codes_'+str(index),'SELECT source_revision,source_line_id,source_code_record_id,trace_code,is_deleted,bill_time,updated_at FROM PHARMACY.V_DRUG_TRACE_CODE'+where+' AND ROWNUM<=501',params,limit=501,public=False)
        if heads is None or lines is None:continue
        active=[r for r in lines if str(r['IS_DELETED'])=='0']
        digest=hashlib.sha256(repr(sorted((str(r['SOURCE_CODE_RECORD_ID']),str(r['TRACE_CODE']),str(r['IS_DELETED'])) for r in lines)).encode()).hexdigest()
        out['whole_bills'].append({'sample_index':index,'header_rows':len(heads),'code_rows':len(lines),'row_cap_reached':len(lines)==501,
           'active_code_rows':len(active),'deleted_values':dict(Counter(str(r['IS_DELETED']) for r in lines)),
           'revision_mismatch':sum(not heads or r['SOURCE_REVISION']!=heads[0]['SOURCE_REVISION'] for r in lines),
           'time_mismatch':sum(not heads or r['BILL_TIME']!=heads[0]['BILL_TIME'] for r in lines),
           'duplicate_record_ids':len(lines)-len({r['SOURCE_CODE_RECORD_ID'] for r in lines}),
           'duplicate_active_codes':len(active)-len({r['TRACE_CODE'] for r in active}),
           'empty_active_codes':sum(not r['TRACE_CODE'] for r in active),
           'invalid_active_code_format':sum(not re.fullmatch(r'[0-9]{20}',str(r['TRACE_CODE'] or '')) for r in active),
           'sample_digest_sha256':digest})
    out['elapsed_seconds']=round(monotonic()-started,3)
    out['sample_is_exhaustive']=False
    out['base_tables_read']=False
finally:
    c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
