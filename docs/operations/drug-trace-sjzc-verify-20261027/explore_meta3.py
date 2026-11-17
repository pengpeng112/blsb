"""HIS 探查 r3：验证三张关键表（用户授权只读延续）。

1) 先查索引（决定后续查询是否走索引）；
2) PHARMACY.DRUG_PRESC_TRACE_CODE 有 (PRESC_DATE,PRESC_NO) 类索引才按样本单查
   ——验证 260 张缺码门诊+3 张退药+住院样本的码是否在此表；
3) CLI_TRACE_BILLNO 按单号查（有索引才查）；
4) TRACE_UPLOAD_TIME_LOG / DOCUMENT_UPLOAD_RECORD 按 PK 倒序取前 30（索引序，免排序）。
输出均为业务键/状态/计数，无患者身份证明文、无完整码串。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent

REMOTE = r'''
import json,time
started=time.monotonic()
out={'authorization':'User authorization 2026-10-27 HIS probe r3 (trace-code alt table + vendor upload logs, read-only)',
     'source_writes':0,'uploads':0}
def run(sql,binds,limit):
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:200]}
idx=run("SELECT TABLE_OWNER, TABLE_NAME, INDEX_NAME, COLUMN_NAME, COLUMN_POSITION "
        "FROM ALL_IND_COLUMNS WHERE (TABLE_OWNER='PHARMACY' AND TABLE_NAME='DRUG_PRESC_TRACE_CODE') "
        "OR (TABLE_OWNER='DRUG_USER' AND TABLE_NAME IN ('CLI_TRACE_BILLNO','TRACE_UPLOAD_TIME_LOG','DOCUMENT_UPLOAD_RECORD')) "
        "ORDER BY TABLE_NAME, INDEX_NAME, COLUMN_POSITION",{},200)
out['indexes']=idx
def cols(owner,tbl):
    return [r['COLUMN_NAME'] for r in idx.get('rows',[])
            if r['TABLE_OWNER']==owner and r['TABLE_NAME']==tbl]
ptc_ok = any(c in ('PRESC_DATE','PRESC_NO') for c in cols('PHARMACY','DRUG_PRESC_TRACE_CODE'))
if ptc_ok:
    out['presc_trace_codes']=run(
        "SELECT TO_CHAR(PRESC_DATE,'YYYY-MM-DD HH24:MI:SS') PD, PRESC_NO, ITEM_NO, "
        "PATIENT_ID, QUANTITY, UPDATE_STATS, "
        "CASE WHEN TRACE_CODE IS NULL THEN 0 ELSE LENGTH(TRACE_CODE) END TC_LEN, "
        "DOCUMENT_NO, TO_CHAR(ENTER_DATE,'YYYY-MM-DD HH24:MI:SS') ENTER_AT "
        "FROM PHARMACY.DRUG_PRESC_TRACE_CODE "
        "WHERE PRESC_NO IN (48649,52273,53306,57370,57371,57372,7214,7707,6852) "
        "AND PRESC_DATE >= DATE '2026-10-24' AND PRESC_DATE < DATE '2026-10-28' "
        "ORDER BY PRESC_NO, PRESC_DATE, ITEM_NO",{},200)
else:
    out['presc_trace_codes']={'ok':False,'skipped':'no date/no index on DRUG_PRESC_TRACE_CODE'}
cli_ok = 'TRACE_BILLNO' in cols('DRUG_USER','CLI_TRACE_BILLNO')
if cli_ok:
    out['cli_trace_billno']=run(
        "SELECT STORE_CODE, TRACE_BILLNO, TRACE_COUNT, "
        "CASE WHEN T_TRACE_CODE IS NULL THEN 0 ELSE LENGTH(T_TRACE_CODE) END TC_LEN "
        "FROM DRUG_USER.CLI_TRACE_BILLNO WHERE TRACE_BILLNO IN "
        "('48649','52273','53306','57370','57371','57372','7214','7707','6852') "
        "AND STORE_CODE IN ('050102','050121','050103','050107')",{},200)
else:
    out['cli_trace_billno']={'ok':False,'skipped':'no TRACE_BILLNO index'}
out['upload_log_recent']=run(
    "SELECT LOG_ID, BUSINESS_ID, TO_CHAR(START_TIME,'YYYY-MM-DD HH24:MI:SS') ST, "
    "TO_CHAR(END_TIME,'YYYY-MM-DD HH24:MI:SS') ET, TIME_STEP, UPLOAD_STATUS, "
    "RECORD_COUNT, SUCCESS_COUNT, FAIL_COUNT, REMARK, "
    "TO_CHAR(CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') CT FROM "
    "(SELECT * FROM DRUG_USER.TRACE_UPLOAD_TIME_LOG ORDER BY LOG_ID DESC) "
    "WHERE ROWNUM<=30",{},30)
out['doc_upload_recent']=run(
    "SELECT ID, BUSINESS_TYPE, BUSINESS_NAME, DOCUMENT_NUMBER, STATUS, "
    "TO_CHAR(CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') CT, "
    "TO_CHAR(UPDATE_TIME,'YYYY-MM-DD HH24:MI:SS') UT FROM "
    "(SELECT * FROM DRUG_USER.DOCUMENT_UPLOAD_RECORD ORDER BY ID DESC) "
    "WHERE ROWNUM<=30",{},30)
out['elapsed']=round(time.monotonic()-started,2)
print(json.dumps(out))
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
    input=remote.encode(), capture_output=True, timeout=600)
try:
    data = json.loads(proc.stdout.decode().splitlines()[-1])
except Exception as exc:
    data = {"status": "REMOTE_ERROR", "error": f"{type(exc).__name__}: {exc}",
            "ssh_exit_code": proc.returncode,
            "stderr_tail": proc.stderr.decode(errors="replace")[-600:]}
data["captured_at"] = datetime.now(timezone.utc).isoformat()
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(HERE / "evidence" / f"his-meta3-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
for k in ("presc_trace_codes", "cli_trace_billno", "upload_log_recent", "doc_upload_recent"):
    v = data.get(k, {})
    print(k, "->", ("ok n=" + str(v.get("n"))) if v.get("ok") else
          (v.get("error") or v.get("skipped")))
