
try:
    query('identity',"SELECT USER AS LOGIN_USER, SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    rows=query('bounded_time_lag',"SELECT bill_time,updated_at FROM PHARMACY.V_DRUG_TRACE_CODE WHERE bill_time>=:begin_time AND bill_time<:end_time AND ROWNUM<=100",{'begin_time':datetime(2026,10,22),'end_time':datetime(2026,10,23)},limit=100,public=False)
    lags=[];nulls=0;parse_failures=0
    for r in rows or []:
        if r['BILL_TIME'] is None or r['UPDATED_AT'] is None:nulls+=1;continue
        try:
            a=datetime.fromisoformat(str(r['BILL_TIME']));b=datetime.fromisoformat(str(r['UPDATED_AT']));lags.append((b-a).total_seconds())
        except (ValueError,TypeError):parse_failures+=1
    out['lag_summary']={'sample_rows':len(rows or []),'parsed_rows':len(lags),'null_rows':nulls,'parse_failures':parse_failures,'positive_lag_rows':sum(x>0 for x in lags),'min_seconds':min(lags) if lags else None,'max_seconds':max(lags) if lags else None,'over_60_seconds':sum(x>60 for x in lags),'over_300_seconds':sum(x>300 for x in lags)}
finally:c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
