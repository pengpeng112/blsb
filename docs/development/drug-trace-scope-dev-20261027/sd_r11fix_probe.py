"""DTR-R11-DDL-FIX-20261028 只读探查（用户授权延续）：
①DDL 引用列名核对（PHA_INP_REQUEST_DRUG/PHA_CLI_REQUEST_DRUG）+ 授权可见性；
②DOCK_CALL_LOG（原厂家三方对接日志）结构+近期行→3503 在传什么；
③TRACE_UPLOAD_TIME_LOG 历史跨度（两 biz 起止）；
④住院码历史上是否写过（老 DIS_RECORDNO/REQUEST_NO 样本查码表）；
⑤退药 IN 码历史上是否写过（老 return 样本查码表，按方向分组）。
全部索引化/字典查询，无患者明文输出。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent

REMOTE = r'''
import json,time
started=time.monotonic()
out={'authorization':'User authorization 2026-10-28 read-only probes (DDL columns/grants, vendor log, historical inpatient/return codes)',
     'source_writes':0,'uploads':0}
def run(sql,binds,limit):
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:180]}
# ① DDL 引用列核对
need={'PHA_INP_REQUEST_DRUG':['DISP_FLAG','DISP_QUANTITY','DISP_EXECUTE_TIME','REQUEST_NO','PRESC_CREATE_TIME','PHA_CODE','EXEC_SQN'],
      'PHA_CLI_REQUEST_DRUG':['CLI_FLAG','RETURN_DRUG_STATUS','CLI_INP_FLAG','CLI_EXECUTE_TIME','PRESC_CREATE_TIME','PRESC_NO','PHA_CODE','PAT_ID','PRESC_DETAIL_ID','RETURN_QUANTITY','SEND_DRUG_STATUS']}
cols=run("SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND TABLE_NAME IN ('PHA_INP_REQUEST_DRUG','PHA_CLI_REQUEST_DRUG')",{},400)
have={}
if cols['ok']:
    for r in cols['rows']: have.setdefault(r['TABLE_NAME'],{})[r['COLUMN_NAME']]=r['DATA_TYPE']
out['ddl_columns_check']={t:{cn:(cn in have.get(t,{})) for cn in cns} for t,cns in need.items()}
out['ddl_columns_types']={t:{cn:have.get(t,{}).get(cn) for cn in ('REQUEST_NO','PRESC_NO','PRESC_CREATE_TIME','DISP_EXECUTE_TIME','CLI_EXECUTE_TIME') if cn in need.get(t,{})} for t in need}
# ①b 授权可见性（ALL_TAB_PRIVS 可见范围有限，尽力）
out['grants_visible']=run("SELECT GRANTEE, TABLE_NAME, PRIVILEGE FROM ALL_TAB_PRIVS WHERE OWNER='DRUG_USER' AND TABLE_NAME IN ('PHA_INP_REQUEST_DRUG','PHA_INP_DISPMASTER','PHA_CLI_REQUEST_DRUG','MED_TRACE_RECORD') AND GRANTEE IN ('PHARMACY','BLYB_DRUGTRACE') ORDER BY TABLE_NAME, GRANTEE",{},100)
# ①c PHA_INP_DISPMASTER PK/索引确认
out['inp_master_indexes']=run("SELECT INDEX_NAME, COLUMN_NAME, COLUMN_POSITION FROM ALL_IND_COLUMNS WHERE TABLE_OWNER='DRUG_USER' AND TABLE_NAME='PHA_INP_DISPMASTER' ORDER BY INDEX_NAME, COLUMN_POSITION",{},50)
# ② DOCK_CALL_LOG 结构
dcl_cols=run("SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER='DRUG_USER' AND TABLE_NAME='DOCK_CALL_LOG' ORDER BY COLUMN_ID",{},60)
out['dock_call_log_columns']=dcl_cols
if dcl_cols['ok']:
    names=[r['COLUMN_NAME'] for r in dcl_cols['rows']]
    idcol='ID' if 'ID' in names else ('LOG_ID' if 'LOG_ID' in names else None)
    if idcol:
        sel=','.join(n for n in names if n not in ('REQUEST_BODY','RESPONSE_BODY','PARAMS','CONTENT','BODY'))
        out['dock_call_log_recent']=run("SELECT * FROM (SELECT "+sel+" FROM DRUG_USER.DOCK_CALL_LOG ORDER BY "+idcol+" DESC) WHERE ROWNUM<=30",{},30)
# ③ 上传日志历史跨度
out['upload_span']=run("SELECT BUSINESS_ID, COUNT(*) LOGS, TO_CHAR(MIN(START_TIME),'YYYY-MM-DD') FIRST_DAY, TO_CHAR(MAX(START_TIME),'YYYY-MM-DD') LAST_DAY, SUM(RECORD_COUNT) REC_SUM, SUM(SUCCESS_COUNT) OK_SUM FROM DRUG_USER.TRACE_UPLOAD_TIME_LOG GROUP BY BUSINESS_ID",{},20)
# ④ 住院历史样本（DIS_RECORDNO PK 范围中段取 20）
mid=run("SELECT MIN(DIS_RECORDNO) MN, MAX(DIS_RECORDNO) MX FROM DRUG_USER.PHA_INP_DISPMASTER",{},1)
if mid['ok'] and mid['rows']:
    mn=mid['rows'][0]['MN']; mx=mid['rows'][0]['MX']
    span=(mx-mn)//2 if mx and mn else 0
    samp=run("SELECT DIS_RECORDNO, REQUEST_NO, TO_CHAR(DISP_BEGIN,'YYYY-MM-DD') DISP_DAY FROM (SELECT DIS_RECORDNO, REQUEST_NO, DISP_BEGIN FROM DRUG_USER.PHA_INP_DISPMASTER WHERE DIS_RECORDNO>=:lo AND DIS_RECORDNO<:hi) WHERE ROWNUM<=20",{'lo':span,'hi':span+5000},20)
    out['inp_master_sample']=samp
    if samp['ok'] and samp['rows']:
        reqs=[r['REQUEST_NO'] for r in samp['rows'] if r['REQUEST_NO']]
        dis=[str(r['DIS_RECORDNO']) for r in samp['rows']]
        out['inp_hist_codes_by_requestno']=run("SELECT T.TRACE_BILLNO, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG, COUNT(*) N, TO_CHAR(MIN(T.CREATE_TIME),'YYYY-MM-DD') FIRST_C FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN (:v0,:v1,:v2,:v3,:v4,:v5,:v6,:v7,:v8,:v9) GROUP BY T.TRACE_BILLNO, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG",{'v%d'%i:(reqs[i] if i<len(reqs) else reqs[0]) for i in range(10)},50)
        out['inp_hist_codes_by_disrec']=run("SELECT T.TRACE_BILLNO, T.IN_OUT_TYPE, COUNT(*) N FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN (:v0,:v1,:v2,:v3,:v4,:v5,:v6,:v7,:v8,:v9) GROUP BY T.TRACE_BILLNO, T.IN_OUT_TYPE",{'v%d'%i:(dis[i] if i<len(dis) else dis[0]) for i in range(10)},50)
# ⑤ 退药历史样本（索引 PRESC_CREATE_TIME 前导）→ 按 PRESC_NO 全日期方向分组
for tag,lo,hi in (('aug','2026-08-10','2026-08-11'),('sep','2026-09-10','2026-09-11')):
    rs=run("SELECT PRESC_NO, TO_CHAR(PRESC_CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') PD, PHA_CODE FROM (SELECT PRESC_NO, PRESC_CREATE_TIME, PHA_CODE FROM DRUG_USER.PHA_CLI_REQUEST_DRUG WHERE PRESC_CREATE_TIME>=TO_DATE(:lo,'YYYY-MM-DD') AND PRESC_CREATE_TIME<TO_DATE(:hi,'YYYY-MM-DD') AND CLI_FLAG='return' AND RETURN_DRUG_STATUS='Y' AND CLI_INP_FLAG=1) WHERE ROWNUM<=10",{'lo':lo,'hi':hi},10)
    out['return_sample_'+tag]=rs
    if rs['ok'] and rs['rows']:
        nos=[str(r['PRESC_NO']) for r in rs['rows']]
        binds={'v%d'%i:(nos[i] if i<len(nos) else nos[0]) for i in range(10)}
        out['return_hist_codes_'+tag]=run("SELECT T.TRACE_BILLNO, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG, COUNT(*) N, TO_CHAR(MIN(T.PRESC_DATE),'YYYY-MM-DD') FIRST_PD FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN (:v0,:v1,:v2,:v3,:v4,:v5,:v6,:v7,:v8,:v9) GROUP BY T.TRACE_BILLNO, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG",binds,80)
out['elapsed']=round(time.monotonic()-started,2)
print(json.dumps(out, default=str))
'''

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
    "if src is None or not src.enabled or (src.target_host,src.port,src.service_name)!=('<his-host>',1521,'his'):\n"
    "    raise RuntimeError('Registered source endpoint mismatch')\n"
    "_u,_p=resolve(src.credential_ref)\n"
    "c=DB_CONNECTOR_MAP['oracle'](host=src.target_host,port=src.port,database=src.service_name,\n"
    "    user=_u or '',password=_p or '',connection_mode=src.connection_mode or 'direct',\n"
    "    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':30000})\n"
    "del _u,_p\n"
)
remote = imports + "\n" + setup + REMOTE
compile(remote, "remote", "exec")
proc = subprocess.run(
    ["ssh", "-i", sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
     "docker exec -i -e PYTHONPATH=/app "
     "-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -"],
    input=remote.encode(), capture_output=True, timeout=900)
try:
    data = json.loads(proc.stdout.decode().splitlines()[-1])
except Exception as exc:
    data = {"status": "REMOTE_ERROR", "error": f"{type(exc).__name__}: {exc}",
            "ssh_exit_code": proc.returncode,
            "stderr_tail": proc.stderr.decode(errors="replace")[-600:]}
data["captured_at"] = datetime.now(timezone.utc).isoformat()
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(HERE / "evidence" / f"r11fix-probe-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
ok = {k: (v.get("ok") if isinstance(v, dict) and "ok" in v else None) for k, v in data.items()}
print(json.dumps({"file": f"r11fix-probe-{ts}.json", "ok_map": ok,
                  "elapsed": data.get("elapsed")}, ensure_ascii=False))
