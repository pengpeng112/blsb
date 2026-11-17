"""Indexed, bounded readonly HIS comparison; working SQLite is RAM-only."""
import base64
import gc
import gzip
import os
import sqlite3
import time

started=time.monotonic()
work=sqlite3.connect(':memory:')
work.execute('CREATE TABLE e(bill TEXT,code TEXT,n INTEGER,PRIMARY KEY(bill,code)) WITHOUT ROWID')
work.executemany('INSERT INTO e VALUES(?,?,?)',json.loads(gzip.decompress(base64.b64decode(HISTORY_GZ))))
del HISTORY_GZ
gc.collect()
work.execute('CREATE INDEX e_code ON e(code)')
work.execute('CREATE TABLE a(bill TEXT,code TEXT,rid TEXT,main TEXT,kind TEXT,deleted TEXT,bt TEXT,rev TEXT,pharmacy TEXT)')
work.execute('CREATE TABLE q(code TEXT PRIMARY KEY) WITHOUT ROWID')
out['authorization']='User explicitly authorized current daytime readonly comparison on 2026-10-23'
out['batches']=[]
out['budget_seconds']=599
out['per_query_timeout_seconds']=30
out['source_tables_accessed']=['PHARMACY.V_DRUG_TRACE_CODE','PHARMACY.V_DRUG_TRACE_BILL']
try:
    auth=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    priv=query('privileges','SELECT PRIVILEGE FROM SESSION_PRIVS ORDER BY PRIVILEGE',limit=30)
    roles=query('roles','SELECT ROLE FROM SESSION_ROLES ORDER BY ROLE',limit=30)
    grants=query('grants','SELECT OWNER,TABLE_NAME,PRIVILEGE,GRANTABLE FROM USER_TAB_PRIVS_RECD ORDER BY OWNER,TABLE_NAME,PRIVILEGE',limit=30)
    expected={('PHARMACY','V_DRUG_TRACE_BILL','SELECT','NO'),('PHARMACY','V_DRUG_TRACE_CODE','SELECT','NO')}
    actual={tuple(r[k] for k in ('OWNER','TABLE_NAME','PRIVILEGE','GRANTABLE')) for r in grants or []}
    if auth is None or auth[0]['LOGIN_USER']!='BLYB_DRUGTRACE' or auth[0]['SERVICE_NAME']!='his' or priv is None or {r['PRIVILEGE'] for r in priv}!={'CREATE SESSION'} or roles!=[] or actual!=expected:
        raise RuntimeError('RESTRICTED_READER_IDENTITY_OR_GRANTS_CHANGED')
    query('view_definitions',"SELECT VIEW_NAME,TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN ('V_DRUG_TRACE_CODE','V_DRUG_TRACE_BILL') ORDER BY VIEW_NAME",limit=2)
    cursor=work.execute('SELECT DISTINCT code FROM e ORDER BY code')
    index=0
    out['stop_reason']='ALL_REQUESTED_CODES_QUERIED'
    while True:
        if time.monotonic()-started>569:
            out['stop_reason']='INITIAL_QUERY_BUDGET';break
        batch=cursor.fetchmany(100 if index==0 else 500)
        if not batch:break
        codes=[r[0] for r in batch]
        binds={'c'+str(i):code for i,code in enumerate(codes)}
        sql=('SELECT source_line_id,trace_code,source_code_record_id,source_bill_id,business_kind,is_deleted,bill_time,source_revision,pharmacy_code FROM PHARMACY.V_DRUG_TRACE_CODE WHERE trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000')
        begin=time.monotonic()
        try:
            rows=c.execute_readonly(sql,binds,max_rows=10000)
        except Exception as exc:
            out['batches'].append({'batch':index,'codes':len(codes),'ok':False,**err(exc)})
            out['stop_reason']='QUERY_ERROR';break
        elapsed=time.monotonic()-begin
        out['batches'].append({'batch':index,'codes':len(codes),'rows':len(rows),'seconds':round(elapsed,4),'ok':True,'cap_reached':len(rows)>=10000})
        if len(rows)>=10000:
            out['stop_reason']='ROW_CAP_REACHED';break
        work.executemany('INSERT INTO a VALUES(?,?,?,?,?,?,?,?,?)',[(r['SOURCE_LINE_ID'],r['TRACE_CODE'],r['SOURCE_CODE_RECORD_ID'],r['SOURCE_BILL_ID'],r['BUSINESS_KIND'],str(r['IS_DELETED']),str(r['BILL_TIME']) if r['BILL_TIME'] is not None else None,r['SOURCE_REVISION'],r['PHARMACY_CODE']) for r in rows])
        work.executemany('INSERT INTO q VALUES(?)',[(code,) for code in codes])
        index+=1
        if index==1 and elapsed>5:
            out['stop_reason']='PILOT_TOO_SLOW_FOR_DAYTIME_EXPANSION';break
    work.execute('CREATE INDEX a_pair ON a(bill,code)')
    work.execute('CREATE TABLE g AS SELECT bill,code,COUNT(*) AS n,SUM(CASE WHEN deleted=\'0\' AND kind=\'PATIENT_DISPENSE\' THEN 1 ELSE 0 END) AS eligible,SUM(CASE WHEN deleted=\'1\' THEN 1 ELSE 0 END) AS deleted_count,SUM(CASE WHEN kind=\'UNKNOWN\' THEN 1 ELSE 0 END) AS unknown_count FROM a GROUP BY bill,code')
    work.execute('CREATE UNIQUE INDEX g_pair ON g(bill,code)')
    scalar=lambda sql:work.execute(sql).fetchone()[0]
    covered=' FROM e JOIN q ON e.code=q.code LEFT JOIN g ON g.bill=e.bill AND g.code=e.code '
    summary={'historical_rows':scalar('SELECT SUM(n) FROM e'),'historical_pairs':scalar('SELECT COUNT(*) FROM e'),
             'historical_codes':scalar('SELECT COUNT(DISTINCT code) FROM e'),
             'covered_codes':scalar('SELECT COUNT(*) FROM q'),
             'covered_pairs':scalar('SELECT COUNT(*)'+covered),
             'covered_historical_rows':scalar('SELECT SUM(e.n)'+covered),
             'view_rows_for_requested_codes':scalar('SELECT COUNT(*) FROM a'),
             'distinct_source_record_ids':scalar('SELECT COUNT(DISTINCT rid) FROM a'),
             'pairs_equal_count':scalar('SELECT COUNT(*)'+covered+' WHERE g.n=e.n'),
             'pairs_absent_from_view':scalar('SELECT COUNT(*)'+covered+' WHERE g.n IS NULL'),
             'historical_rows_of_absent_pairs':scalar('SELECT SUM(e.n)'+covered+' WHERE g.n IS NULL'),
             'pairs_fewer_view_records':scalar('SELECT COUNT(*)'+covered+' WHERE g.n<e.n'),
             'pairs_more_view_records':scalar('SELECT COUNT(*)'+covered+' WHERE g.n>e.n'),
             'historical_pairs_with_unknown_kind':scalar('SELECT COUNT(*)'+covered+' WHERE g.unknown_count>0'),
             'historical_pairs_with_deleted_rows':scalar('SELECT COUNT(*)'+covered+' WHERE g.deleted_count>0'),
             'eligible_normal_pairs':scalar('SELECT COUNT(*)'+covered+' WHERE g.eligible>0'),
             'matched_pairs_not_currently_eligible':scalar('SELECT COUNT(*)'+covered+' WHERE g.n IS NOT NULL AND g.eligible=0'),
             'new_pairs_for_same_codes':scalar('SELECT COUNT(*) FROM g LEFT JOIN e ON g.bill=e.bill AND g.code=e.code WHERE e.bill IS NULL'),
             'source_record_id_duplicate_rows':scalar('SELECT COUNT(*)-COUNT(DISTINCT rid) FROM a')}
    summary['all_historical_pairs_queried']=summary['covered_pairs']==summary['historical_pairs']
    out['summary']=summary
    out['current_view_kinds']=[dict(zip(('kind','rows'),r)) for r in work.execute('SELECT kind,COUNT(*) FROM a GROUP BY kind')]
    out['current_deleted_values']=[dict(zip(('raw_value','rows'),r)) for r in work.execute('SELECT deleted,COUNT(*) FROM a GROUP BY deleted')]
    missing=list(work.execute('SELECT e.bill,e.code,e.n,0,0,0,0'+covered+' WHERE g.n IS NULL'))
    mismatched=list(work.execute('SELECT e.bill,e.code,e.n,g.n,g.eligible,g.deleted_count,g.unknown_count'+covered+' WHERE g.n<>e.n'))
    excluded=list(work.execute('SELECT e.bill,e.code,e.n,g.n,g.eligible,g.deleted_count,g.unknown_count'+covered+' WHERE g.n=e.n AND g.eligible=0'))
    classifications=Counter()
    found_codes={r[0] for r in work.execute('SELECT DISTINCT code FROM a')}
    for bill,code,*rest in missing:
        shape='14-digit-prefix' if re.fullmatch(r'[0-9]{14}_[0-9]+_[0-9]+',bill) else '20-digit-prefix' if re.fullmatch(r'[0-9]{20}_[0-9]+',bill) else 'other'
        classifications[shape+' / '+('code_exists_under_other_line' if code in found_codes else 'code_absent_from_view')]+=1
    out['missing_pair_classifications']=dict(classifications)
    out['historical_repeated_pairs']=[dict(zip(('expected_records','view_records','eligible_records','pairs'),r)) for r in work.execute('SELECT e.n,COALESCE(g.n,0),COALESCE(g.eligible,0),COUNT(*)'+covered+' WHERE e.n>1 GROUP BY e.n,COALESCE(g.n,0),COALESCE(g.eligible,0)')]
    out['view_time_buckets']=[dict(zip(('period','rows'),r)) for r in work.execute("SELECT CASE WHEN bt IS NULL THEN 'NULL' WHEN bt<'2026-07-23' THEN 'before historical file dates' WHEN bt>='2026-10-12' THEN 'after historical file dates' ELSE 'within historical file dates (dispense time only)' END,COUNT(*) FROM a GROUP BY 1")]
    # A few real whole-bill snapshots for the existing pure quality function.
    sample_results=[]
    for number,sample in enumerate(SAMPLES):
        source=work.execute('SELECT main,kind,bt,pharmacy FROM a WHERE bill=? AND code=? LIMIT 1',(sample['bill'],sample['code'])).fetchone()
        if source is None:continue
        main,kind,bt,pharmacy=source
        if bt is None:continue
        binds={'ss':'HIS','bid':main,'kind':kind,'pharmacy':pharmacy,'bt':datetime.fromisoformat(bt)}
        condition=' WHERE source_system=:ss AND source_bill_id=:bid AND business_kind=:kind AND pharmacy_code=:pharmacy AND bill_time=:bt'
        header=query('quality_header_'+str(number),'SELECT source_system,source_bill_id,source_revision,business_kind,pharmacy_code,bill_time,finalized,is_void,expected_code_count FROM PHARMACY.V_DRUG_TRACE_BILL'+condition+' AND ROWNUM<=3',binds,limit=3,public=False)
        lines=query('quality_codes_'+str(number),'SELECT source_line_id,source_code_record_id,trace_code,is_deleted,quantity,unit,code_level,parent_code,f_trace_code,s_trace_code,t_trace_code FROM PHARMACY.V_DRUG_TRACE_CODE'+condition+' AND ROWNUM<=501',binds,limit=501,public=False)
        if header is not None and lines is not None:
            sample_results.append({'category':sample['label'],'header':header,'lines':lines,'row_cap_reached':len(lines)>=501})
    detail={'missing_pairs':missing,'mismatched_pairs':mismatched,'currently_excluded_pairs':excluded,'quality_samples':sample_results}
    out['private_detail_gzip_base64']=base64.b64encode(gzip.compress(json.dumps(detail,ensure_ascii=False,default=str).encode())).decode()
    out['same_create_time_bidirectional_audit']=False
    out['source_writes']=0;out['uploads']=0
    out['total_elapsed_seconds']=round(time.monotonic()-started,3)
finally:
    work.close()
    c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
