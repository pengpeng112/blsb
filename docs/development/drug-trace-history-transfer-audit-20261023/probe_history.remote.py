import time

started=time.monotonic()
try:
    auth=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    if auth is None or auth[0]['LOGIN_USER']!='BLYB_DRUGTRACE' or auth[0]['SERVICE_NAME']!='his':
        raise RuntimeError('RESTRICTED_READER_IDENTITY_MISMATCH')
    privileges=query('privileges','SELECT PRIVILEGE FROM SESSION_PRIVS ORDER BY PRIVILEGE',limit=30)
    roles=query('roles','SELECT ROLE FROM SESSION_ROLES ORDER BY ROLE',limit=30)
    grants=query('grants','SELECT OWNER,TABLE_NAME,PRIVILEGE,GRANTABLE FROM USER_TAB_PRIVS_RECD ORDER BY OWNER,TABLE_NAME,PRIVILEGE',limit=30)
    expected={('PHARMACY','V_DRUG_TRACE_BILL','SELECT','NO'),('PHARMACY','V_DRUG_TRACE_CODE','SELECT','NO')}
    actual={tuple(r[k] for k in ('OWNER','TABLE_NAME','PRIVILEGE','GRANTABLE')) for r in grants or []}
    if privileges is None or {r['PRIVILEGE'] for r in privileges}!={'CREATE SESSION'} or roles!=[] or actual!=expected:
        raise RuntimeError('RESTRICTED_READER_GRANTS_CHANGED')
    query('view_definitions',"SELECT VIEW_NAME,TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN ('V_DRUG_TRACE_CODE','V_DRUG_TRACE_BILL') ORDER BY VIEW_NAME",limit=2)
    params={}
    for index,sample in enumerate(SAMPLES):
        params['legacy_code_'+str(index)]=sample['code']
        params['legacy_id_'+str(index)]=sample['bill']
    began=time.monotonic()
    rows=query('three_exact_pairs',PROBE_SQL,params,limit=101,public=False)
    out['probe_elapsed_seconds']=round(time.monotonic()-began,3)
    out['probe_row_cap_reached']=len(rows)==101 if rows is not None else None
    out['samples']=[]
    if rows is not None:
        for index,sample in enumerate(SAMPLES):
            matched=[r for r in rows if str(r['SOURCE_LINE_ID'])==sample['bill'] and str(r['TRACE_CODE'])==sample['code']]
            result={'category':sample['label'],'historical_occurrences':len(sample['times']),
                    'view_rows':len(matched),'source_record_ids_distinct':len({r['SOURCE_CODE_RECORD_ID'] for r in matched}),
                    'business_kinds':dict(Counter(str(r['BUSINESS_KIND']) for r in matched)),
                    'deleted_values':dict(Counter(str(r['IS_DELETED']) for r in matched))}
            header_results=[]
            keys={tuple(r[k] for k in ('SOURCE_SYSTEM','PHARMACY_CODE','BUSINESS_KIND','SOURCE_BILL_ID','BILL_TIME')) for r in matched}
            for j,key in enumerate(list(keys)[:3]):
                if time.monotonic()-started>600:raise RuntimeError('TEN_MINUTE_BUDGET_REACHED')
                binds=dict(zip(('ss','pharmacy','kind','bid','bt'),key))
                header=query('sample_'+str(index)+'_header_'+str(j),
                    'SELECT business_kind,finalized,is_void,bill_time,expected_code_count FROM PHARMACY.V_DRUG_TRACE_BILL WHERE source_system=:ss AND pharmacy_code=:pharmacy AND business_kind=:kind AND source_bill_id=:bid AND ((:bt IS NULL AND bill_time IS NULL) OR bill_time=:bt) AND ROWNUM<=3',binds,limit=3,public=False)
                if header is not None:header_results.append({'rows':len(header),'finalized_values':dict(Counter(str(r['FINALIZED']) for r in header)),'void_values':dict(Counter(str(r['IS_VOID']) for r in header))})
            result['header_checks']=header_results
            differences=[]
            for row in matched:
                if row['BILL_TIME'] is not None:
                    bt=datetime.fromisoformat(str(row['BILL_TIME']))
                    differences.extend((bt-datetime.fromisoformat(stamp)).total_seconds() for stamp in sample['times'])
            result['bill_time_minus_historical_time_seconds']={'min':min(differences) if differences else None,'max':max(differences) if differences else None}
            out['samples'].append(result)
    out['total_elapsed_seconds']=round(time.monotonic()-started,3)
    out['history_total_rows']=489613
    out['whole_history_compared']=False
    out['source_writes']=0;out['uploads']=0
finally:c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
