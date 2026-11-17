import base64
import gzip
import time

started=time.monotonic()
data=json.loads(gzip.decompress(base64.b64decode(HISTORY_GZ)))
excluded_pairs={(r[0],r[1]) for r in data['excluded_pairs']}
dup_pairs={(r[0],r[1]) for r in data['dup_pairs']}
missing_codes=list(data['missing_codes'])
del data
out['phase']='r2-two-views'
out['budget_seconds']=300
out['per_query_timeout_seconds']=30
out['source_tables_accessed']=['PHARMACY.V_DRUG_TRACE_CODE','PHARMACY.V_DRUG_TRACE_BILL']
private={'excluded_states':[],'dup_pair_view_rows':[],'whole_bills':[],'missing_codes_view':[]}
def as_dt(v):
    if isinstance(v,datetime):return v
    s=str(v)
    try:return datetime.fromisoformat(s)
    except ValueError:return datetime.strptime(s[:19],'%Y-%m-%d %H:%M:%S')
try:
    # Q00: identity / grants / view definitions at the new time point
    auth=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    priv=query('privileges','SELECT PRIVILEGE FROM SESSION_PRIVS ORDER BY PRIVILEGE',limit=30)
    roles=query('roles','SELECT ROLE FROM SESSION_ROLES ORDER BY ROLE',limit=30)
    grants=query('grants','SELECT OWNER,TABLE_NAME,PRIVILEGE,GRANTABLE FROM USER_TAB_PRIVS_RECD ORDER BY OWNER,TABLE_NAME,PRIVILEGE',limit=30)
    expected={('PHARMACY','V_DRUG_TRACE_BILL','SELECT','NO'),('PHARMACY','V_DRUG_TRACE_CODE','SELECT','NO')}
    actual={tuple(r[k] for k in ('OWNER','TABLE_NAME','PRIVILEGE','GRANTABLE')) for r in grants or []}
    out['restricted_reader_unchanged']=bool(auth and auth[0]['LOGIN_USER']=='BLYB_DRUGTRACE' and auth[0]['SERVICE_NAME']=='his' and priv and {r['PRIVILEGE'] for r in priv}=={'CREATE SESSION'} and roles==[] and actual==expected)
    if not out['restricted_reader_unchanged']:
        raise RuntimeError('RESTRICTED_READER_IDENTITY_OR_GRANTS_CHANGED')
    query('view_status',"SELECT OWNER,OBJECT_NAME,STATUS,TO_CHAR(LAST_DDL_TIME,'YYYY-MM-DD') AS LAST_DDL FROM ALL_OBJECTS WHERE OWNER='PHARMACY' AND OBJECT_TYPE='VIEW' AND OBJECT_NAME IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY OBJECT_NAME",limit=2)
    query('view_definitions',"SELECT VIEW_NAME,TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN ('V_DRUG_TRACE_CODE','V_DRUG_TRACE_BILL') ORDER BY VIEW_NAME",limit=2)

    # Q02: current raw state of the r1 1396 excluded pairs (re-verification)
    codes=sorted({code for bill,code in excluded_pairs})
    out['q02_batches']=[];rows_all=[];out['q02_stop']='ALL_CODES_QUERIED'
    for start in range(0,len(codes),500):
        if time.monotonic()-started>240:
            out['q02_stop']='BUDGET';break
        batch=codes[start:start+500]
        binds={'c'+str(i):code for i,code in enumerate(batch)}
        sql=('SELECT C.source_line_id,C.trace_code,C.source_bill_id,C.pharmacy_code,C.bill_time,B.source_bill_kind_raw,B.source_revision,B.finalized,B.is_void FROM PHARMACY.V_DRUG_TRACE_CODE C JOIN PHARMACY.V_DRUG_TRACE_BILL B ON B.source_system=C.source_system AND B.source_bill_id=C.source_bill_id AND B.pharmacy_code=C.pharmacy_code AND B.business_kind=C.business_kind AND B.bill_time=C.bill_time WHERE C.trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000')
        begin=time.monotonic()
        rows=c.execute_readonly(sql,binds,max_rows=10000)
        out['q02_batches'].append({'codes':len(batch),'rows':len(rows),'seconds':round(time.monotonic()-begin,4),'cap_reached':len(rows)>=10000})
        rows_all.extend(rows)
        if len(rows)>=10000:
            out['q02_stop']='ROW_CAP_REACHED';break
    raw_counts={};main_keys=set()
    for row in rows_all:
        bill=str(row['SOURCE_LINE_ID']);code=str(row['TRACE_CODE'])
        if (bill,code) not in excluded_pairs:continue
        raw=str(row['SOURCE_BILL_KIND_RAW']);revision=str(row['SOURCE_REVISION'])
        refund=revision.rsplit('_R10_RF_',1)[-1] if '_R10_RF_' in revision else 'UNAVAILABLE'
        key=(raw,refund,str(row['FINALIZED']),str(row['IS_VOID']))
        raw_counts[key]=raw_counts.get(key,0)+1
        main_keys.add((str(row['SOURCE_BILL_ID']),str(row['PHARMACY_CODE'])))
        private['excluded_states'].append({'bill':bill,'code':code,'raw_kind':raw,'refund_raw':refund,'finalized_raw':str(row['FINALIZED']),'void_raw':str(row['IS_VOID'])})
    out['q02_excluded_raw_state_summary']=[dict(zip(('raw_kind','refund_raw','finalized_raw','void_raw','rows'),(*k,n))) for k,n in sorted(raw_counts.items())]
    out['q02_pairs_explained']=len({(s['bill'],s['code']) for s in private['excluded_states']})
    out['q02_rows_explained']=len(private['excluded_states'])
    out['q02_distinct_main_bills']=len(main_keys)
    del rows_all

    # Q03: all 31 dup pairs — every current view row for their codes
    dup_codes=sorted({code for bill,code in dup_pairs})
    binds={'c'+str(i):code for i,code in enumerate(dup_codes)}
    begin=time.monotonic()
    dup_rows=c.execute_readonly('SELECT source_line_id,trace_code,source_code_record_id,source_bill_id,business_kind,is_deleted,bill_time,source_revision,pharmacy_code FROM PHARMACY.V_DRUG_TRACE_CODE WHERE trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000',binds,max_rows=10000)
    out['q03_dup_batch']={'codes':len(dup_codes),'rows':len(dup_rows),'seconds':round(time.monotonic()-begin,4),'cap_reached':len(dup_rows)>=10000}
    pair_records={};affected={}
    for row in dup_rows:
        bill=str(row['SOURCE_LINE_ID']);code=str(row['TRACE_CODE'])
        rec={'bill':bill,'code':code,'rid':str(row['SOURCE_CODE_RECORD_ID']),'kind':str(row['BUSINESS_KIND']),'deleted':str(row['IS_DELETED']),'main':str(row['SOURCE_BILL_ID']),'pharmacy':str(row['PHARMACY_CODE']),'bt':str(row['BILL_TIME'])}
        private['dup_pair_view_rows'].append(rec)
        if (bill,code) in dup_pairs:
            pair_records.setdefault((bill,code),[]).append(rec)
            affected.setdefault((str(row['SOURCE_BILL_ID']),str(row['PHARMACY_CODE']),str(row['BUSINESS_KIND']),str(row['BILL_TIME'])),[]).append(rec)
    out['q03_pairs_covered']=len(pair_records)
    out['q03_pair_record_shapes']={}
    for pair,recs in pair_records.items():
        shape=(len(recs),len({r['rid'] for r in recs}),sum(1 for r in recs if r['deleted']=='0'))
        out['q03_pair_record_shapes'][str(shape)]=out['q03_pair_record_shapes'].get(str(shape),0)+1
    out['q03_distinct_main_bills']=len(affected)

    # whole-bill snapshots for every affected bill (cap 40; truncation recorded)
    bills=list(affected)[:40]
    out['q03_whole_bill_batches']=[]
    for number,(bid,pharm,kind,bt) in enumerate(bills):
        if time.monotonic()-started>280:
            out['q03_whole_bills_stop']='BUDGET';break
        binds={'ss':'HIS','bid':bid,'kind':kind,'pharmacy':pharm,'bt':as_dt(bt)}
        condition=' WHERE source_system=:ss AND source_bill_id=:bid AND business_kind=:kind AND pharmacy_code=:pharmacy AND bill_time=:bt'
        begin=time.monotonic()
        try:
            header=c.execute_readonly('SELECT source_system,source_bill_id,source_revision,business_kind,pharmacy_code,bill_time,finalized,is_void,expected_code_count,updated_at,source_bill_kind_raw FROM PHARMACY.V_DRUG_TRACE_BILL'+condition+' AND ROWNUM<=3',binds,max_rows=3)
            lines=c.execute_readonly('SELECT source_line_id,source_code_record_id,trace_code,is_deleted,quantity,unit,code_level,parent_code,f_trace_code,s_trace_code,t_trace_code,bill_time FROM PHARMACY.V_DRUG_TRACE_CODE'+condition+' AND ROWNUM<=501',binds,max_rows=501)
        except Exception as exc:
            out['q03_whole_bill_batches'].append({'bill_index':number,'ok':False,**err(exc)})
            continue
        out['q03_whole_bill_batches'].append({'bill_index':number,'ok':True,'header_rows':len(header),'line_rows':len(lines),'line_cap_reached':len(lines)>=501,'seconds':round(time.monotonic()-begin,4)})
        if header:
            private['whole_bills'].append({'header':[dict(r) for r in header],'lines':[dict(r) for r in lines]})
    out['q03_whole_bills_fetched']=len(private['whole_bills'])
    out['q03_whole_bills_truncated']=len(affected)>len(bills)
    del dup_rows

    # Q01 view-side: current view rows for the 531 missing codes (new time point)
    out['q01_view_batches']=[];mc={};out['q01_view_stop']='ALL_CODES_QUERIED'
    for start in range(0,len(missing_codes),500):
        if time.monotonic()-started>295:
            out['q01_view_stop']='BUDGET';break
        batch=missing_codes[start:start+500]
        binds={'c'+str(i):code for i,code in enumerate(batch)}
        begin=time.monotonic()
        rows=c.execute_readonly('SELECT source_line_id,trace_code,business_kind,is_deleted FROM PHARMACY.V_DRUG_TRACE_CODE WHERE trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000',binds,max_rows=10000)
        out['q01_view_batches'].append({'codes':len(batch),'rows':len(rows),'seconds':round(time.monotonic()-begin,4),'cap_reached':len(rows)>=10000})
        for row in rows:
            code=str(row['TRACE_CODE'])
            mc.setdefault(code,[]).append({'bill':str(row['SOURCE_LINE_ID']),'kind':str(row['BUSINESS_KIND']),'deleted':str(row['IS_DELETED'])})
        if len(rows)>=10000:
            out['q01_view_stop']='ROW_CAP_REACHED';break
    for code in missing_codes:
        if code in mc:private['missing_codes_view'].append({'code':code,'rows':mc[code]})
    out['q01_codes_with_view_rows']=len(mc)
    out['q01_codes_without_view_rows']=len(missing_codes)-len(mc)

    out['run_completed']=bool(out.get('q02_stop')!='ROW_CAP_REACHED' and out.get('q03_pairs_covered')==len(dup_pairs) and not out.get('q03_whole_bills_truncated'))
    out['source_writes']=0;out['uploads']=0
    out['total_elapsed_seconds']=round(time.monotonic()-started,3)
finally:
    c.close()
blob={'excluded_states':private['excluded_states'],'dup_pair_view_rows':private['dup_pair_view_rows'],'whole_bills':private['whole_bills'],'missing_codes_view':private['missing_codes_view']}
out['private_rows']={k:len(v) for k,v in blob.items()}
out['private_detail_gzip_base64']=base64.b64encode(gzip.compress(json.dumps(blob,ensure_ascii=False,default=str).encode())).decode()
print(json.dumps(out,ensure_ascii=False,default=str))
