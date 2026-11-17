import time

started=time.monotonic()
data=json.loads(__import__('gzip').decompress(__import__('base64').b64decode(AUDIT_GZ)))
samples=data['samples'];missing_codes=data['missing_codes'];dup_codes=data['dup_codes']
out={'authorization':"User explicit authorization 2026-10-23 ('授权你执行') for read-only source-table audit via sjzc controlled connector",
     'budget_seconds':600,'per_query_timeout_seconds':30,'source_writes':0,'uploads':0,
     'tables_accessed':['DRUG_USER.MED_TRACE_RECORD','PHARMACY.DRUG_PRESC_MASTER','data dictionary']}
private={'sql01':[],'sql02':[],'sql04_missing':[],'sql04_dup':[]}
def run(key,sql,binds,limit):
    begin=time.monotonic()
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'key':key,'rows':len(rows),'seconds':round(time.monotonic()-begin,4),'rows_out':rows}
    except Exception as exc:
        return {'ok':False,'key':key,**{'error_type':type(exc).__name__,'oracle_codes':sorted(set(__import__('re').findall(r'(?:ORA|DPY|DPI)-\d+',str(exc))))}}
try:
    # ---- Stage A: identity + SQL06 index metadata ----
    ident=run('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",{},1)
    out['identity']={'ok':ident['ok'],'user':ident['rows_out'][0]['LOGIN_USER'] if ident['ok'] else None,'service':ident['rows_out'][0]['SERVICE_NAME'] if ident['ok'] else None}
    idx=run('sql06',"SELECT IC.TABLE_NAME,IC.INDEX_NAME,IC.COLUMN_NAME,IC.COLUMN_POSITION,I.UNIQUENESS,I.STATUS FROM ALL_IND_COLUMNS IC JOIN ALL_INDEXES I ON I.OWNER=IC.INDEX_OWNER AND I.INDEX_NAME=IC.INDEX_NAME WHERE (IC.TABLE_OWNER='DRUG_USER' AND IC.TABLE_NAME='MED_TRACE_RECORD') OR (IC.TABLE_OWNER='PHARMACY' AND IC.TABLE_NAME='DRUG_PRESC_MASTER') ORDER BY IC.TABLE_NAME,IC.INDEX_NAME,IC.COLUMN_POSITION",{},300)
    out['sql06']={'ok':idx['ok'],'rows':idx.get('rows'),'oracle_codes':idx.get('oracle_codes')}
    out['indexes']=idx.get('rows_out',[]) if idx['ok'] else []
    t_date_indexed=False
    if idx['ok']:
        for r in idx['rows_out']:
            if r['TABLE_NAME']=='MED_TRACE_RECORD' and r['COLUMN_NAME'] in ('PRESC_DATE','CREATE_TIME') and int(r['COLUMN_POSITION'])==1:
                t_date_indexed=True
    out['sql06_t_date_indexed']=t_date_indexed

    # ---- Stage B: SQL01/SQL02 for S01-S03 ----
    sbinds={}
    for i,s in enumerate(samples):
        sbinds['code_%d'%i]=s['code'];sbinds['line_%d'%i]=s['line']
    pair_where="((T.T_TRACE_CODE=:code_0 AND T.PRESC_DETAILID=:line_0) OR (T.T_TRACE_CODE=:code_1 AND T.PRESC_DETAILID=:line_1) OR (T.T_TRACE_CODE=:code_2 AND T.PRESC_DETAILID=:line_2))"
    sql01=("SELECT T.RECORD_ID,T.PRESC_DETAILID,T.T_TRACE_CODE,T.TRACE_BILLNO,T.PRESC_DATE,T.STORE_CODE,T.CREATE_TIME,T.MODIFY_TIME,T.IN_OUT_TYPE,T.IS_DELETED,T.SPLIT_FLAG FROM DRUG_USER.MED_TRACE_RECORD T WHERE "+pair_where+" AND ROWNUM<=101")
    sql02=("SELECT T.RECORD_ID,T.PRESC_DETAILID,T.T_TRACE_CODE,T.TRACE_BILLNO,T.PRESC_DATE,T.STORE_CODE,T.IN_OUT_TYPE,T.IS_DELETED,T.SPLIT_FLAG,CASE WHEN M.PRESC_NO IS NULL THEN 0 ELSE 1 END AS MASTER_JOIN_HIT,M.PRESC_NO,M.DISPENSARY,M.PRESC_SOURCE,M.FLAG,M.REFUND_FLAG,M.DISPENSING_DATETIME FROM DRUG_USER.MED_TRACE_RECORD T LEFT JOIN PHARMACY.DRUG_PRESC_MASTER M ON T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO) AND T.PRESC_DATE=M.PRESC_DATE AND T.STORE_CODE=M.DISPENSARY WHERE "+pair_where+" AND ROWNUM<=101")
    for key,sql in (('sql01',sql01),('sql02',sql02)):
        r=run(key,sql,sbinds,101)
        out[key+'_meta']={'ok':r['ok'],'rows':r.get('rows'),'truncated_101':r.get('rows')==101,'oracle_codes':r.get('oracle_codes')}
        if r['ok']:private[key]=[dict(x) for x in r['rows_out']]

    # ---- Stage C: SQL04 batches for 531 missing + 31 dup codes ----
    sql04=("SELECT T.RECORD_ID,T.PRESC_DETAILID,T.T_TRACE_CODE,T.TRACE_BILLNO,T.PRESC_DATE,T.STORE_CODE,T.CREATE_TIME,T.MODIFY_TIME,T.IN_OUT_TYPE,T.IS_DELETED,T.SPLIT_FLAG,CASE WHEN M.PRESC_NO IS NULL THEN 0 ELSE 1 END AS MASTER_JOIN_HIT,M.PRESC_SOURCE,M.FLAG,M.REFUND_FLAG,M.DISPENSING_DATETIME FROM DRUG_USER.MED_TRACE_RECORD T LEFT JOIN PHARMACY.DRUG_PRESC_MASTER M ON T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO) AND T.PRESC_DATE=M.PRESC_DATE AND T.STORE_CODE=M.DISPENSARY WHERE T.T_TRACE_CODE IN ({cl}) AND ROWNUM<=10000")
    def batch_codes(group,codes):
        outk='sql04_'+group;out[outk]=[];dest=private[outk]
        sizes=[3,25]+[100]*len(codes)
        i=0;step=0
        while i<len(codes):
            if time.monotonic()-started>480:
                out[outk+'_stop']='BUDGET';break
            size=sizes[step] if step<len(sizes) else 100
            batch=codes[i:i+size]
            binds={'c%d'%j:code for j,code in enumerate(batch)}
            cl=','.join(':'+k for k in binds)
            r=run(outk,sql04.format(cl=cl),binds,10000)
            out[outk].append({'batch':step,'codes':len(batch),'ok':r['ok'],'rows':r.get('rows'),'seconds':r.get('seconds'),'cap_reached':r.get('rows')==10000,'oracle_codes':r.get('oracle_codes')})
            if not r['ok']:
                out[outk+'_stop']='QUERY_ERROR';break
            if r.get('rows')==10000:
                out[outk+'_stop']='ROW_CAP_REACHED';break
            dest.extend([dict(x) for x in r['rows_out']])
            i+=len(batch);step+=1
        if i>=len(codes) and (outk+'_stop') not in out:out[outk+'_stop']='ALL_CODES_QUERIED'
    batch_codes('missing',missing_codes)
    batch_codes('dup',dup_codes)

    # ---- Stage D: SQL05 per-day coverage (master-driven; T-driven if indexed) ----
    out['daily_coverage']=[]
    from datetime import date,timedelta
    d0=date(2026,7,23);d1=date(2026,10,12)
    sql_m=("SELECT TO_CHAR(M.PRESC_DATE,'YYYY-MM-DD') AS PRESC_DAY,M.PRESC_SOURCE AS PRESC_SOURCE,COUNT(*) AS JOIN_ROWS,SUM(CASE WHEN T.RECORD_ID IS NULL THEN 1 ELSE 0 END) AS MASTERS_NO_OUT,SUM(CASE WHEN T.RECORD_ID IS NOT NULL THEN 1 ELSE 0 END) AS OUT_ROWS FROM PHARMACY.DRUG_PRESC_MASTER M LEFT JOIN DRUG_USER.MED_TRACE_RECORD T ON T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO) AND T.PRESC_DATE=M.PRESC_DATE AND T.STORE_CODE=M.DISPENSARY AND T.IN_OUT_TYPE='OUT' WHERE M.PRESC_DATE>=:day_begin AND M.PRESC_DATE<:day_end GROUP BY M.PRESC_DATE,M.PRESC_SOURCE")
    sql_t=("SELECT 'T' AS SIDE,T.IN_OUT_TYPE AS IN_OUT,CASE WHEN M.PRESC_NO IS NULL THEN 'JOIN_MISS' ELSE 'JOIN_HIT' END AS JOIN_STATE,COUNT(*) AS REC_ROWS FROM DRUG_USER.MED_TRACE_RECORD T LEFT JOIN PHARMACY.DRUG_PRESC_MASTER M ON T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO) AND T.PRESC_DATE=M.PRESC_DATE AND T.STORE_CODE=M.DISPENSARY WHERE T.PRESC_DATE>=:day_begin AND T.PRESC_DATE<:day_end GROUP BY T.IN_OUT_TYPE,CASE WHEN M.PRESC_NO IS NULL THEN 'JOIN_MISS' ELSE 'JOIN_HIT' END")
    d=d0;errs=0
    while d<d1:
        if time.monotonic()-started>570:
            out['daily_stop']='BUDGET';break
        binds={'day_begin':d,'day_end':d+timedelta(days=1)}
        rm=run('daily_m',sql_m,binds,50)
        if not rm['ok']:
            errs+=1;out['daily_coverage'].append({'day':str(d),'ok':False,'oracle_codes':rm.get('oracle_codes')})
            if errs>=3:out['daily_stop']='MASTER_QUERY_ERRORS';break
            d+=timedelta(days=1);continue
        day_rows=[dict(x) for x in rm['rows_out']]
        entry={'day':str(d),'master':day_rows}
        if t_date_indexed:
            rt=run('daily_t',sql_t,binds,50)
            entry['t_side']=[dict(x) for x in rt['rows_out']] if rt['ok'] else {'ok':False,'oracle_codes':rt.get('oracle_codes')}
        out['daily_coverage'].append(entry)
        d+=timedelta(days=1)
    if 'daily_stop' not in out:out['daily_stop']='ALL_DAYS_QUERIED'
    out['daily_days_queried']=len(out['daily_coverage'])
    out['daily_mode']='master_driven%s' %('+t_driven' if t_date_indexed else '_only_no_t_date_index')

    out['run_completed']=bool(out.get('identity',{}).get('ok') and out.get('sql01_meta',{}).get('ok') is not None and out.get('sql04_missing_stop')=='ALL_CODES_QUERIED' and out.get('sql04_dup_stop')=='ALL_CODES_QUERIED' and out.get('daily_stop')=='ALL_DAYS_QUERIED')
    out['total_elapsed_seconds']=round(time.monotonic()-started,3)
finally:
    try:c.close()
    except Exception:pass
out['private_row_counts']={k:len(v) for k,v in private.items()}
out['private_detail_gzip_base64']=base64.b64encode(gzip.compress(json.dumps(private,ensure_ascii=False,default=str).encode())).decode()
print(json.dumps(out,ensure_ascii=False,default=str))
