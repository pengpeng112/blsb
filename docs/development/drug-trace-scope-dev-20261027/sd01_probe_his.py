"""SD-01 脚本B：HIS 只读探查四问（DTR-SCOPE-DEV-20261027，用户已在任务入口授权）。

通道同 his-meta 先例：本机 SSH→<relay-jump> data-asset-api 容器 OracleConnector.execute_readonly，
平台托管凭据容器内 resolve()，本机不读凭据。单条 30s、每查询结果 ≤300 行、
无索引列不做范围扫（PHA_INP_REQUEST_DRUG 先查索引再选路径）。
连接器拒 -- 注释与 GROUP BY 别名：SQL 不含注释、GROUP BY 用表达式。

Q1 住院码真实键（REQUEST_NO vs DIS_RECORDNO → MED_TRACE_RECORD 命中者为准）
Q2 退药 IN 码存在性（57370/57371/57372 全日期+近窗）
Q3 BILL_TYPE/BUSINESS 列与 trace 配置键证据（只收集不决定）
Q4 260 缺码 vs VALID 对照组在 PHA_CLI_REQUEST_DRUG 标志位分布
SD-06 TRACE_UPLOAD_TIME_LOG 按日/按时聚合（10-18..10-28）
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PRIV = ROOT / ".ai/private/drug-trace-scope-dev-20261027"

keys = json.loads(sorted(PRIV.glob("sd01-keys-*.json"))[-1].read_text(encoding="utf8"))
INP_PHARMACIES = ('050103', '050107', '050121', '050110', '050109', '050101', '050102')
RETURN_KEYS = [str(r["presc_no"]) for r in keys["return_bills"]]
NOCODE = keys["nocode_presc_no"]
VALID = keys["valid_sample_presc_no"]

REMOTE = r'''
import json,time,re
started=time.monotonic()
S=__PAYLOAD__
out={'authorization':'DTR-SCOPE-DEV-20261027 user-authorized HIS read-only probe via sjzc controlled connector (<relay-jump>, READY_HIS precedent)','source_writes':0,'uploads':0}
def run(sql,binds=None,limit=50):
    b=time.monotonic()
    try:
        rows=c.execute_readonly(sql,binds or {},max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows,'seconds':round(time.monotonic()-b,3)}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:300],
                'codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\d+',str(exc))))}
def under_budget():
    return time.monotonic()-started < 420

out['identity']=run("SELECT USER AS LOGIN_USER, SYS_CONTEXT('USERENV','SESSION_USER') AS SESSION_USER FROM DUAL",{},1)

inp_cols=run("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND TABLE_NAME='PHA_INP_REQUEST_DRUG' ORDER BY COLUMN_ID",{},300)
out['q1_inp_columns']={'ok':inp_cols['ok'],'n':inp_cols.get('n'),
    'columns':[r['COLUMN_NAME'] for r in inp_cols.get('rows',[])] if inp_cols['ok'] else inp_cols.get('error')}
inp_idx=run("SELECT INDEX_NAME, COLUMN_NAME, COLUMN_POSITION FROM ALL_IND_COLUMNS WHERE TABLE_OWNER='DRUG_USER' AND TABLE_NAME='PHA_INP_REQUEST_DRUG' ORDER BY INDEX_NAME, COLUMN_POSITION",{},100)
out['q1_inp_indexes']=inp_idx
idx_rows=inp_idx.get('rows',[]) if inp_idx.get('ok') else []
leading={r['COLUMN_NAME'] for r in idx_rows if r['COLUMN_POSITION']==1}
cols=set(out['q1_inp_columns'].get('columns') or [])
request_nos=[]
path='none'
if under_budget() and 'REQUEST_NO' in cols:
    if 'DISP_EXECUTE_TIME' in leading:
        path='disp_execute_time_index'
        r=run("SELECT * FROM (SELECT w.REQUEST_NO FROM DRUG_USER.PHA_INP_REQUEST_DRUG w WHERE w.DISP_FLAG='send' AND w.DISP_QUANTITY>0 AND w.DISP_EXECUTE_TIME>=TIMESTAMP '2026-10-26 00:00:00' AND w.DISP_EXECUTE_TIME<TIMESTAMP '2026-10-27 00:00:00' AND w.PHA_CODE IN ('050103','050107','050121','050110','050109','050101','050102') ORDER BY w.REQUEST_NO) WHERE ROWNUM<=10",{},10)
    elif 'PRESC_CREATE_TIME' in leading and 'PRESC_CREATE_TIME' in cols:
        path='presc_create_time_index'
        r=run("SELECT * FROM (SELECT w.REQUEST_NO FROM DRUG_USER.PHA_INP_REQUEST_DRUG w WHERE w.DISP_FLAG='send' AND w.DISP_QUANTITY>0 AND w.PRESC_CREATE_TIME>=TIMESTAMP '2026-10-25 00:00:00' AND w.PRESC_CREATE_TIME<TIMESTAMP '2026-10-27 00:00:00' AND w.PHA_CODE IN ('050103','050107','050121','050110','050109','050101','050102') ORDER BY w.REQUEST_NO) WHERE ROWNUM<=10",{},10)
    elif 'PRESC_NO' in cols and 'PRESC_NO' in leading:
        path='presc_no_index'
        r=run("SELECT * FROM (SELECT w.REQUEST_NO FROM DRUG_USER.PHA_INP_REQUEST_DRUG w WHERE w.PRESC_NO IN (__INPAT_PRESC__) AND w.DISP_FLAG='send' AND w.DISP_QUANTITY>0 ORDER BY w.REQUEST_NO) WHERE ROWNUM<=10",{},10)
    else:
        path='no_indexed_path'
        r={'ok':False,'skipped':'no leading index on time/presc columns; full scan refused'}
    out['q1_request_no_sample']={'path':path,**r}
    if r.get('ok'):
        request_nos=[str(x['REQUEST_NO']) for x in r['rows']]
else:
    out['q1_request_no_sample']={'ok':False,'skipped':'REQUEST_NO column absent'}
dis_pairs=[]
if request_nos and under_budget():
    inl=",".join("'"+x+"'" for x in request_nos)
    dp=run("SELECT REQUEST_NO, TO_CHAR(DIS_RECORDNO) AS DIS_RECORDNO FROM DRUG_USER.PHA_INP_DISPMASTER WHERE REQUEST_NO IN ("+inl+")",{},10)
    out['q1_disp_recordno']=dp
    if dp.get('ok'):
        dis_pairs=[str(x['DIS_RECORDNO']) for x in dp['rows'] if x['DIS_RECORDNO']]
if request_nos and under_budget():
    inl=",".join("'"+x+"'" for x in request_nos)
    out['q1_mtr_by_request_no']=run("SELECT TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG, COUNT(*) AS CNT, TO_CHAR(MIN(CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') AS FIRST_C, TO_CHAR(MAX(CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') AS LAST_C FROM DRUG_USER.MED_TRACE_RECORD WHERE TRACE_BILLNO IN ("+inl+") GROUP BY TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG",{},50)
if dis_pairs and under_budget():
    inl=",".join("'"+x+"'" for x in dis_pairs)
    out['q1_mtr_by_dis_recordno']=run("SELECT TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG, COUNT(*) AS CNT, TO_CHAR(MIN(CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') AS FIRST_C, TO_CHAR(MAX(CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') AS LAST_C FROM DRUG_USER.MED_TRACE_RECORD WHERE TRACE_BILLNO IN ("+inl+") GROUP BY TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG",{},50)
if request_nos and under_budget():
    inl=",".join("'"+x+"'" for x in request_nos)
    out['q1_vendor_send_view']=run("SELECT * FROM (SELECT REQUEST_NO, TO_CHAR(DIS_RECORDNO) AS DIS_RECORDNO, PHA_CODE, DISP_QUANTITY, TO_CHAR(DISP_EXECUTE_TIME,'YYYY-MM-DD HH24:MI:SS') AS EXEC_AT FROM DRUG_USER.PHA_INP_TRACE_DRUG_SEND_VIEW WHERE REQUEST_NO IN ("+inl+")) WHERE ROWNUM<=10",{},10)

if under_budget():
    inl=",".join("'"+x+"'" for x in __RETURNS__)
    out['q2_mtr_return_all_dates']=run("SELECT TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG, STORE_CODE, COUNT(*) AS CNT, TO_CHAR(MIN(CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') AS FIRST_C, TO_CHAR(MAX(CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') AS LAST_C FROM DRUG_USER.MED_TRACE_RECORD WHERE TRACE_BILLNO IN ("+inl+") GROUP BY TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG, STORE_CODE",{},50)
    out['q2_mtr_return_recent']=run("SELECT TRACE_BILLNO, IN_OUT_TYPE, IS_DELETED, SPLIT_FLAG, STORE_CODE, CASE WHEN T_TRACE_CODE IS NULL THEN 0 ELSE 1 END AS HAS_T, CASE WHEN F_TRACE_CODE IS NULL THEN 0 ELSE 1 END AS HAS_F, CASE WHEN S_TRACE_CODE IS NULL THEN 0 ELSE 1 END AS HAS_S, TO_CHAR(CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') AS CREATED FROM DRUG_USER.MED_TRACE_RECORD WHERE TRACE_BILLNO IN ("+inl+") AND CREATE_TIME>=TIMESTAMP '2026-10-24 00:00:00'",{},50)

out['q3_billtype_columns']=run("SELECT TABLE_NAME, COLUMN_NAME FROM (SELECT TABLE_NAME, COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND (COLUMN_NAME LIKE '%BILL_TYPE%' OR COLUMN_NAME LIKE '%BUSINESS%')) WHERE ROWNUM<=100",{},100)
pub=run("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND TABLE_NAME='PUB_CONFIG' ORDER BY COLUMN_ID",{},80)
out['q3_pub_columns']={'ok':pub['ok'],'columns':[r['COLUMN_NAME'] for r in pub.get('rows',[])] if pub['ok'] else pub.get('error')}
if pub.get('ok'):
    names=[r['COLUMN_NAME'] for r in pub['rows']]
    kcol=next((x for x in names if 'KEY' in x), None)
    vcol=next((x for x in names if 'VALUE' in x or x.endswith('VAL')), None)
    if kcol and vcol and under_budget():
        out['q3_pub_trace_keys']=run("SELECT * FROM (SELECT "+kcol+" AS K, "+vcol+" AS V FROM DRUG_USER.PUB_CONFIG WHERE UPPER("+kcol+") LIKE '%TRACE%') WHERE ROWNUM<=50",{},50)
    else:
        out['q3_pub_trace_keys']={'ok':False,'skipped':'no key/value column: '+str(names)}
sysc=run("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND TABLE_NAME='SYSTEM_CONFIG' ORDER BY COLUMN_ID",{},80)
out['q3_sys_columns']={'ok':sysc['ok'],'columns':[r['COLUMN_NAME'] for r in sysc.get('rows',[])] if sysc['ok'] else sysc.get('error')}
if sysc.get('ok'):
    names=[r['COLUMN_NAME'] for r in sysc['rows']]
    kcol=next((x for x in names if 'KEY' in x or 'CODE' in x), None)
    vcol=next((x for x in names if 'VALUE' in x or x.endswith('VAL') or 'CONTENT' in x), None)
    if kcol and vcol and under_budget():
        out['q3_sys_trace_keys']=run("SELECT * FROM (SELECT "+kcol+" AS K, "+vcol+" AS V FROM DRUG_USER.SYSTEM_CONFIG WHERE UPPER("+kcol+") LIKE '%TRACE%') WHERE ROWNUM<=50",{},50)
    else:
        out['q3_sys_trace_keys']={'ok':False,'skipped':'no key/value column: '+str(names)}

cli=run("SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND TABLE_NAME='PHA_CLI_REQUEST_DRUG' ORDER BY COLUMN_ID",{},300)
cli_cols=[r['COLUMN_NAME'] for r in cli.get('rows',[])] if cli.get('ok') else []
out['q4_cli_columns']={'ok':cli['ok'],'n':len(cli_cols),'columns':cli_cols}
if cli_cols and under_budget():
    keys_wanted=[x for x in cli_cols if any(t in x for t in ('SCAN','PDA','UPLOAD','WINDOW','TRACE'))]
    keys_wanted+= [x for x in ('SEND_DRUG_STATUS','RETURN_DRUG_STATUS','CLI_FLAG','SPLIT_FLAG') if x in cli_cols and x not in keys_wanted]
    out['q4_compared_columns']=keys_wanted
    nocode=__NOCODE__
    valid=__VALID__
    nc=",".join(str(x) for x in nocode)
    vd=",".join(str(x) for x in valid)
    dist={}
    for col in keys_wanted[:10]:
        if not under_budget(): break
        g1=run("SELECT "+col+" AS V, COUNT(*) AS CNT FROM DRUG_USER.PHA_CLI_REQUEST_DRUG WHERE PRESC_NO IN ("+nc+") AND PRESC_CREATE_TIME>=DATE '2026-10-20' GROUP BY "+col,{},30)
        g2=run("SELECT "+col+" AS V, COUNT(*) AS CNT FROM DRUG_USER.PHA_CLI_REQUEST_DRUG WHERE PRESC_NO IN ("+vd+") AND PRESC_CREATE_TIME>=DATE '2026-10-20' GROUP BY "+col,{},30)
        dist[col]={'nocode':g1,'valid_sample':g2}
    out['q4_flag_distribution']=dist

if under_budget():
    out['sd06_upload_log_daily']=run("SELECT BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD') AS D, UPLOAD_STATUS, COUNT(*) AS LOGS, SUM(RECORD_COUNT) AS REC, SUM(SUCCESS_COUNT) AS SUCC, SUM(FAIL_COUNT) AS FAIL FROM DRUG_USER.TRACE_UPLOAD_TIME_LOG WHERE START_TIME>=TIMESTAMP '2026-10-18 00:00:00' GROUP BY BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD'), UPLOAD_STATUS ORDER BY BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD'), UPLOAD_STATUS",{},120)
if under_budget():
    out['sd06_upload_log_hourly']=run("SELECT BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD HH24') AS H, UPLOAD_STATUS, SUM(RECORD_COUNT) AS REC, SUM(SUCCESS_COUNT) AS SUCC, SUM(FAIL_COUNT) AS FAIL FROM DRUG_USER.TRACE_UPLOAD_TIME_LOG WHERE START_TIME>=TIMESTAMP '2026-10-23 00:00:00' AND START_TIME<TIMESTAMP '2026-10-26 00:00:00' GROUP BY BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD HH24'), UPLOAD_STATUS ORDER BY BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD HH24'), UPLOAD_STATUS",{},300)
out['run_completed']=under_budget()
out['elapsed_seconds']=round(time.monotonic()-started,2)
print(json.dumps(out))
'''

payload = {"note": "keys passed via replacement"}
remote = (REMOTE
          .replace("__PAYLOAD__", json.dumps(payload))
          .replace("__RETURNS__", json.dumps(RETURN_KEYS))
          .replace("__INPAT_PRESC__", ",".join(
              str(int(r["presc_no"])) for r in keys["inpatient_waiting"][:10]))
          .replace("__NOCODE__", json.dumps(NOCODE))
          .replace("__VALID__", json.dumps(VALID)))

sjzc_spec = importlib.util.spec_from_file_location(
    "live_sjzc", "C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py")
sjzc = importlib.util.module_from_spec(sjzc_spec); sjzc_spec.loader.exec_module(sjzc)
imports = "\n".join(line for line in sjzc.LIVE_CODE.splitlines()
                    if line.startswith("from app.") or line.startswith("import sys")
                    or line.startswith("from sqlalchemy import"))
setup = (
    "import base64,gzip,re,json,time\n"
    "code='his_source_<his_host_id>'\n"
    "db=SessionLocal()\n"
    "src=db.scalar(select(AssetDataSource).where(AssetDataSource.source_code==code))\n"
    "db.close()\n"
    "_u,_p=resolve(src.credential_ref)\n"
    "c=DB_CONNECTOR_MAP['oracle'](host=src.target_host,port=src.port,database=src.service_name,\n"
    "    user=_u or '',password=_p or '',connection_mode=src.connection_mode or 'direct',\n"
    "    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':30000})\n"
    "del _u,_p\n"
)
full = imports + "\n" + setup + remote
compile(full, "remote", "exec")
proc = subprocess.run(
    ["ssh", "-i", sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
     "docker exec -i -e PYTHONPATH=/app "
     "-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -"],
    input=full.encode(), capture_output=True, timeout=900)
try:
    data = json.loads(proc.stdout.decode().splitlines()[-1])
except Exception as exc:
    data = {"status": "REMOTE_ERROR", "error": f"{type(exc).__name__}: {exc}",
            "ssh_exit_code": proc.returncode,
            "stderr_tail": proc.stderr.decode(errors="replace")[-800:]}
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(HERE / "evidence" / f"sd01-his-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
print("run_completed:", data.get("run_completed"), "elapsed:", data.get("elapsed_seconds"))
for k in sorted(data):
    v = data[k]
    if isinstance(v, dict) and "ok" in v:
        print(f"{k}: ok={v['ok']} n={v.get('n')} err={v.get('error') or v.get('skipped') or ''}")
