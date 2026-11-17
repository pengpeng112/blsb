"""r11-fix 探查 r2：收口四个问题（用户授权只读延续）。
①住院申请表时间字段实际格式采样+索引；②退药 IN 码精确键（到秒对比）；
③DOCK_CALL_LOG 定向查 PROD_ID=3503/3505 的业务名；④住院历史码（索引允许时）。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
REMOTE = r'''
import json,time
started=time.monotonic()
out={'authorization':'User authorization 2026-10-28 read-only probes r2','source_writes':0,'uploads':0}
def run(sql,binds,limit):
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:180]}
# ① 住院申请表：时间字段原始值采样 + 全部索引
out['inp_req_timefmt']=run("SELECT PRESC_CREATE_TIME, TO_CHAR(DISP_EXECUTE_TIME,'YYYY-MM-DD HH24:MI:SS') DEE, REQUEST_NO FROM (SELECT PRESC_CREATE_TIME, DISP_EXECUTE_TIME, REQUEST_NO FROM DRUG_USER.PHA_INP_REQUEST_DRUG WHERE DISP_EXECUTE_TIME>=DATE '2026-10-26' AND DISP_EXECUTE_TIME<DATE '2026-10-27') WHERE ROWNUM<=5",{},5)
out['inp_req_indexes']=run("SELECT INDEX_NAME, COLUMN_NAME, COLUMN_POSITION FROM ALL_IND_COLUMNS WHERE TABLE_OWNER='DRUG_USER' AND TABLE_NAME='PHA_INP_REQUEST_DRUG' ORDER BY INDEX_NAME, COLUMN_POSITION",{},60)
# ② IN 码精确键：三个已知有 IN 的单号，全信息到秒
out['in_key_detail']=run("SELECT T.TRACE_BILLNO, T.IN_OUT_TYPE, TO_CHAR(T.PRESC_DATE,'YYYY-MM-DD HH24:MI:SS') T_PRESC_DATE, TO_CHAR(T.CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') T_CREATE, T.STORE_CODE FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN ('26641','31237','66035') AND T.IN_OUT_TYPE='IN'",{},30)
out['in_key_return_rows']=run("SELECT PRESC_NO, TO_CHAR(PRESC_CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') PD, TO_CHAR(CLI_EXECUTE_TIME,'YYYY-MM-DD HH24:MI:SS') CE, PHA_CODE, CLI_FLAG, RETURN_DRUG_STATUS FROM DRUG_USER.PHA_CLI_REQUEST_DRUG WHERE PRESC_NO IN ('26641','31237','66035') AND CLI_FLAG='return'",{},30)
# ③ 3503/3505 定性
out['dock_3503_3505']=run("SELECT PROD_ID, BUSINESS_ID, BUSINESS_NAME, COUNT(*) N, TO_CHAR(MAX(CALL_TIME),'YYYY-MM-DD HH24:MI:SS') LAST_CALL FROM DRUG_USER.DOCK_CALL_LOG WHERE PROD_ID IN ('3503','3505','3501') GROUP BY PROD_ID, BUSINESS_ID, BUSINESS_NAME",{},20)
# ④ 住院历史码：按 DISP_EXECUTE_TIME 索引允许则采样 8 月中旬
idx=out['inp_req_indexes']
lead={}
if idx['ok']:
    for r in idx['rows']: lead.setdefault(r['INDEX_NAME'],[]).append(r['COLUMN_NAME'])
has_exec=any(v and v[0]=='DISP_EXECUTE_TIME' for v in lead.values())
if has_exec:
    samp=run("SELECT REQUEST_NO, TO_CHAR(DISP_EXECUTE_TIME,'YYYY-MM-DD') DAY FROM (SELECT REQUEST_NO, DISP_EXECUTE_TIME FROM DRUG_USER.PHA_INP_REQUEST_DRUG WHERE DISP_EXECUTE_TIME>=DATE '2026-08-15' AND DISP_EXECUTE_TIME<DATE '2026-08-16' AND DISP_FLAG='send' AND DISP_QUANTITY>0) WHERE ROWNUM<=10",{},10)
    out['inp_aug_sample']=samp
    if samp['ok'] and samp['rows']:
        reqs=[str(r['REQUEST_NO']) for r in samp['rows']]
        binds={'v%d'%i:(reqs[i] if i<len(reqs) else reqs[0]) for i in range(10)}
        out['inp_aug_codes']=run("SELECT T.TRACE_BILLNO, T.IN_OUT_TYPE, COUNT(*) N FROM DRAG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN (:v0,:v1,:v2,:v3,:v4,:v5,:v6,:v7,:v8,:v9) GROUP BY T.TRACE_BILLNO, T.IN_OUT_TYPE".replace('DRAG_USER','DRUG_USER'),binds,30)
else:
    out['inp_aug_sample']={'ok':False,'skipped':'no DISP_EXECUTE_TIME leading index'}
# 授权可见性（修正列名 TABLE_SCHEMA）
out['grants_visible']=run("SELECT TABLE_SCHEMA, TABLE_NAME, GRANTEE, PRIVILEGE FROM ALL_TAB_PRIVS WHERE TABLE_SCHEMA='DRUG_USER' AND TABLE_NAME IN ('PHA_INP_REQUEST_DRUG','PHA_INP_DISPMASTER','PHA_CLI_REQUEST_DRUG','MED_TRACE_RECORD') AND GRANTEE IN ('PHARMACY','BLYB_DRUGTRACE') ORDER BY TABLE_NAME, GRANTEE",{},100)
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
(HERE / "evidence" / f"r11fix-probe-r2-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
ok = {k: (v.get("ok") if isinstance(v, dict) and "ok" in v else None) for k, v in data.items()}
print(json.dumps({"file": f"r11fix-probe-r2-{ts}.json", "ok_map": ok}, ensure_ascii=False))
