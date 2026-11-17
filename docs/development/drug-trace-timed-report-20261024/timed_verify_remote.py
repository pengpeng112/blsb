import base64
import gzip
import time
from datetime import date, timedelta

started=time.monotonic()
data=json.loads(gzip.decompress(base64.b64decode(PAYLOAD_GZ)))
excluded={(r[0],r[1]) for r in data['excluded']}
dup={(r[0],r[1]) for r in data['dup']}
out={'authorization':"User authorization 2026-10-24: DB re-verify + timed-report scope analysis",
     'budget_seconds':300,'per_query_timeout_seconds':30,
     'source_tables_accessed':['PHARMACY.V_DRUG_TRACE_CODE','PHARMACY.V_DRUG_TRACE_BILL'],
     'source_writes':0,'uploads':0,'queries':{}}
private={'excluded_states':[],'dup_states':[]}
try:
    auth=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    if auth is None or auth[0]['LOGIN_USER']!='BLYB_DRUGTRACE' or auth[0]['SERVICE_NAME']!='his':
        raise RuntimeError('READER_MISMATCH')

    # Part 1a: current raw states of 1396 refund pairs (join code+bill views)
    codes=sorted({c for b,c in excluded})
    raw_counts={};main_keys=set();out['q_excluded_batches']=[]
    for start in range(0,len(codes),500):
        if time.monotonic()-started>120:out['q_excluded_stop']='BUDGET';break
        batch=codes[start:start+500]
        binds={'c'+str(i):c for i,c in enumerate(batch)}
        sql=('SELECT C.source_line_id,C.trace_code,C.source_bill_id,C.pharmacy_code,B.source_bill_kind_raw,B.source_revision,B.finalized,B.is_void FROM PHARMACY.V_DRUG_TRACE_CODE C JOIN PHARMACY.V_DRUG_TRACE_BILL B ON B.source_system=C.source_system AND B.source_bill_id=C.source_bill_id AND B.pharmacy_code=C.pharmacy_code AND B.business_kind=C.business_kind AND B.bill_time=C.bill_time WHERE C.trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000')
        begin=time.monotonic()
        rows=c.execute_readonly(sql,binds,max_rows=10000)
        out['q_excluded_batches'].append({'codes':len(batch),'rows':len(rows),'seconds':round(time.monotonic()-begin,4),'cap':len(rows)>=10000})
        if len(rows)>=10000:out['q_excluded_stop']='ROW_CAP';break
        for row in rows:
            bill=str(row['SOURCE_LINE_ID']);code=str(row['TRACE_CODE'])
            if (bill,code) not in excluded:continue
            rev=str(row['SOURCE_REVISION'])
            refund=rev.rsplit('_R10_RF_',1)[-1] if '_R10_RF_' in rev else 'UNAVAILABLE'
            key=(str(row['SOURCE_BILL_KIND_RAW']),refund,str(row['FINALIZED']),str(row['IS_VOID']))
            raw_counts[key]=raw_counts.get(key,0)+1
            main_keys.add((str(row['SOURCE_BILL_ID']),str(row['PHARMACY_CODE'])))
            private['excluded_states'].append({'bill':bill,'code':code,'raw':key[0],'refund':key[1],'fin':key[2],'void':key[3]})
    if 'q_excluded_stop' not in out:out['q_excluded_stop']='DONE'
    out['excluded_state_summary']=[dict(zip(('raw','refund','fin','void','rows'),(*k,n))) for k,n in sorted(raw_counts.items())]
    out['excluded_pairs_seen']=len({(s['bill'],s['code']) for s in private['excluded_states']})
    out['excluded_main_bills']=len(main_keys)

    # Part 1b: dup pairs current state (2 records each still?)
    dup_codes=sorted({c for b,c in dup})
    binds={'c'+str(i):c for i,c in enumerate(dup_codes)}
    begin=time.monotonic()
    drows=c.execute_readonly('SELECT source_line_id,trace_code,source_code_record_id,business_kind,is_deleted FROM PHARMACY.V_DRUG_TRACE_CODE WHERE trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000',binds,max_rows=10000)
    out['q_dup_batch']={'codes':len(dup_codes),'rows':len(drows),'seconds':round(time.monotonic()-begin,4),'cap':len(drows)>=10000}
    pair_counts={}
    for row in drows:
        k=(str(row['SOURCE_LINE_ID']),str(row['TRACE_CODE']))
        if k in dup:
            pair_counts[k]=pair_counts.get(k,0)+1
            private['dup_states'].append({'bill':k[0],'code':k[1],'rid':str(row['SOURCE_CODE_RECORD_ID']),'deleted':str(row['IS_DELETED']),'kind':str(row['BUSINESS_KIND'])})
    out['dup_pairs_seen']=len(pair_counts)
    out['dup_pairs_still_2_records']=sum(1 for v in pair_counts.values() if v==2)

    # Part 2: scope analysis per day 2026-10-12 .. 2026-10-25
    out['scope_days']=[]
    d0=date(2026,10,12);d1=date(2026,10,25)
    d=d0;errs=0
    sql_day=("SELECT TO_CHAR(TRUNC(B.bill_time),'YYYY-MM-DD') AS D,B.business_kind AS KIND,B.finalized AS FIN,COUNT(*) AS BILLS,"
             "SUM(CASE WHEN B.finalized=1 AND B.is_void=0 THEN 1 ELSE 0 END) AS SEND_ELIGIBLE "
             "FROM PHARMACY.V_DRUG_TRACE_BILL B WHERE B.bill_time>=:b AND B.bill_time<:e "
             "GROUP BY TRUNC(B.bill_time),B.business_kind,B.finalized")
    sql_codes=("SELECT COUNT(*) AS CODE_ROWS,SUM(CASE WHEN C.is_deleted='1' THEN 1 ELSE 0 END) AS DELETED_ROWS "
               "FROM PHARMACY.V_DRUG_TRACE_CODE C WHERE C.bill_time>=:b AND C.bill_time<:e")
    while d<d1:
        if time.monotonic()-started>280:out['scope_stop']='BUDGET';break
        binds={'b':d,'e':d+timedelta(days=1)}
        begin=time.monotonic()
        try:
            rows=c.execute_readonly(sql_day,binds,max_rows=30)
            crow=c.execute_readonly(sql_codes,binds,max_rows=5)
        except Exception as exc:
            errs+=1;out['scope_days'].append({'day':str(d),'ok':False,'oracle_codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\d+',str(exc))))})
            if errs>=3:out['scope_stop']='ERRORS';break
            d+=timedelta(days=1);continue
        out['scope_days'].append({'day':str(d),'ok':True,'seconds':round(time.monotonic()-begin,4),
                                  'groups':[dict(x) for x in rows],'code_rows':dict(crow[0]) if crow else None})
        d+=timedelta(days=1)
    if 'scope_stop' not in out:out['scope_stop']='DONE'
    out['scope_days_queried']=len(out['scope_days'])
    out['run_completed']=bool(out['q_excluded_stop'] in('DONE','BUDGET','ROW_CAP') and out['dup_pairs_seen']==len(dup) and out['scope_stop']=='DONE')
    out['total_elapsed_seconds']=round(time.monotonic()-started,3)
finally:
    c.close()
out['private_row_counts']={k:len(v) for k,v in private.items()}
out['private_detail_gzip_base64']=base64.b64encode(gzip.compress(json.dumps(private,ensure_ascii=False,default=str).encode())).decode()
print(json.dumps(out,ensure_ascii=False,default=str))
