import base64
import gzip
import time

started=time.monotonic()
data=json.loads(gzip.decompress(base64.b64decode(HISTORY_GZ)))
missing={(r[0],r[1]):r for r in data['missing']}
excluded={(r[0],r[1]):r for r in data['excluded']}
raw_counts=Counter();main_keys=set();state_details=[]
missing_code_rows={};out['diagnostic_batches']=[]
try:
    auth=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    if auth is None or auth[0]['LOGIN_USER']!='BLYB_DRUGTRACE' or auth[0]['SERVICE_NAME']!='his':raise RuntimeError('READER_MISMATCH')
    for group,pairs in [('excluded',excluded),('missing',missing)]:
        codes=sorted({code for bill,code in pairs})
        for start in range(0,len(codes),500):
            if time.monotonic()-started>300:raise RuntimeError('DIAGNOSTIC_BUDGET_REACHED')
            batch=codes[start:start+500];binds={'c'+str(i):code for i,code in enumerate(batch)}
            code_list=','.join(':'+k for k in binds)
            if group=='excluded':
                sql=('SELECT C.source_line_id,C.trace_code,C.source_bill_id,C.pharmacy_code,C.bill_time,B.source_bill_kind_raw,B.source_revision,B.finalized,B.is_void FROM PHARMACY.V_DRUG_TRACE_CODE C JOIN PHARMACY.V_DRUG_TRACE_BILL B ON B.source_system=C.source_system AND B.source_bill_id=C.source_bill_id AND B.pharmacy_code=C.pharmacy_code AND B.business_kind=C.business_kind AND B.bill_time=C.bill_time WHERE C.trace_code IN ('+code_list+') AND ROWNUM<=10000')
            else:
                sql='SELECT source_line_id,trace_code,business_kind,bill_time FROM PHARMACY.V_DRUG_TRACE_CODE WHERE trace_code IN ('+code_list+') AND ROWNUM<=10000'
            begin=time.monotonic();rows=c.execute_readonly(sql,binds,max_rows=10000)
            out['diagnostic_batches'].append({'group':group,'codes':len(batch),'rows':len(rows),'seconds':round(time.monotonic()-begin,4)})
            if len(rows)>=10000:raise RuntimeError('ROW_CAP_REACHED')
            for row in rows:
                bill=str(row['SOURCE_LINE_ID']);code=str(row['TRACE_CODE'])
                if group=='excluded' and (bill,code) in excluded:
                    raw=str(row['SOURCE_BILL_KIND_RAW']);revision=str(row['SOURCE_REVISION'])
                    refund=revision.rsplit('_R10_RF_',1)[-1] if '_R10_RF_' in revision else 'UNAVAILABLE'
                    raw_counts[(raw,refund,str(row['FINALIZED']),str(row['IS_VOID']))]+=1
                    main_keys.add((row['SOURCE_BILL_ID'],row['PHARMACY_CODE']))
                    state_details.append({'bill':bill,'code':code,'raw_kind':raw,'refund_raw':refund,'finalized_raw':str(row['FINALIZED']),'void_raw':str(row['IS_VOID'])})
                if group=='missing':
                    shape='14-digit-prefix' if re.fullmatch(r'[0-9]{14}_[0-9]+_[0-9]+',bill) else '20-digit-prefix' if re.fullmatch(r'[0-9]{20}_[0-9]+',bill) else 'other'
                    item=missing_code_rows.setdefault(code,{'rows':0,'line_shapes':Counter(),'kinds':Counter()})
                    item['rows']+=1;item['line_shapes'][shape]+=1;item['kinds'][str(row['BUSINESS_KIND'])]+=1
    out['excluded_raw_state_summary']=[dict(zip(('raw_kind','refund_raw','finalized_raw','void_raw','rows'),(*key,n))) for key,n in sorted(raw_counts.items())]
    out['excluded_distinct_main_bills']=len(main_keys)
    out['excluded_states_explained_rows']=len(state_details)
    out['missing_code_current_line_shapes']=dict(sum((item['line_shapes'] for item in missing_code_rows.values()),Counter()))
    out['missing_code_current_business_kinds']=dict(sum((item['kinds'] for item in missing_code_rows.values()),Counter()))
    annotated=[]
    for pair,row in missing.items():
        context=missing_code_rows.get(pair[1])
        annotated.append({'bill':pair[0],'code':pair[1],'historical_records':row[2],
                          'current_code_exists':context is not None,
                          'current_other_line_rows':context['rows'] if context else 0,
                          'current_line_shapes':dict(context['line_shapes']) if context else {},
                          'current_kinds':dict(context['kinds']) if context else {}})
    blob={'missing_annotated':annotated,'excluded_states':state_details}
    out['private_detail_gzip_base64']=base64.b64encode(gzip.compress(json.dumps(blob,ensure_ascii=False).encode())).decode()
    out['diagnosis_completed']=True
    out['total_elapsed_seconds']=round(time.monotonic()-started,3)
    out['source_writes']=0;out['uploads']=0
finally:c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
