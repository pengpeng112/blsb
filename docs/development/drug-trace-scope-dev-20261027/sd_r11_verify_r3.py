"""r11 验证 r3：住院码按天分布+端到端样本核验。只读。"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys
HERE = Path(__file__).resolve().parent
REMOTE = r'''
import json,time
started=time.monotonic()
out={'authorization':'2026-10-28 r11 inpatient-code daily distribution (read-only)','source_writes':0}
def run(sql,limit=50):
    try:
        rows=c.execute_readonly(sql,{},max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:180]}
out['by_write_day']=run("SELECT TO_CHAR(updated_at,'YYYY-MM-DD') D, COUNT(*) N FROM PHARMACY.V_DRUG_TRACE_CODE_INP GROUP BY TO_CHAR(updated_at,'YYYY-MM-DD') ORDER BY 1 DESC",40)
out['by_disp_day']=run("SELECT TO_CHAR(bill_time,'YYYY-MM-DD') D, COUNT(*) N FROM PHARMACY.V_DRUG_TRACE_CODE_INP GROUP BY TO_CHAR(bill_time,'YYYY-MM-DD') ORDER BY 1 DESC",40)
out['sample_req_rows']=run("SELECT REQUEST_NO, DISP_FLAG, DISP_QUANTITY, PHA_CODE, TO_CHAR(DISP_EXECUTE_TIME,'YYYY-MM-DD HH24:MI:SS') DE FROM DRUG_USER.PHA_INP_REQUEST_DRUG WHERE REQUEST_NO='03032820261027113457' AND ROWNUM<=8",8)
out['sample_code_rows']=run("SELECT T.TRACE_BILLNO, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG, T.STORE_CODE, TO_CHAR(T.PRESC_DATE,'YYYY-MM-DD HH24:MI:SS') PD, TO_CHAR(T.CREATE_TIME,'YYYY-MM-DD HH24:MI:SS') CT FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO='03032820261027113457' AND ROWNUM<=8",8)
out['elapsed']=round(time.monotonic()-started,2)
print(json.dumps(out, default=str))
'''
sjzc_spec = importlib.util.spec_from_file_location(
    "live_sjzc", "C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py")
sjzc = importlib.util.module_from_spec(sjzc_spec); sjzc_spec.loader.exec_module(sjzc)
imports = "\n".join(line for line in sjzc.LIVE_CODE.splitlines()
                    if line.startswith("from app.") or line.startswith("import sys")
                    or line.startswith("from sqlalchemy import"))
setup = ("import base64,gzip,re,json,time\n"
    "code='his_source_<his_host_id>'\n"
    "db=SessionLocal()\n"
    "src=db.scalar(select(AssetDataSource).where(AssetDataSource.source_code==code))\n"
    "db.close()\n"
    "if src is None or not src.enabled or (src.target_host,src.port,src.service_name)!=('<his-host>',1521,'his'):\n"
    "    raise RuntimeError('Registered source endpoint mismatch')\n"
    "_u,_p=resolve(src.credential_ref)\n"
    "c=DB_CONNECTOR_MAP['oracle'](host=src.target_host,port=src.port,database=src.service_name,\n"
    "    user=_u or '',password=_p or '',connection_mode=src.connection_mode or 'direct',\n"
    "    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':120000})\n"
    "del _u,_p\n")
remote = imports + "\n" + setup + REMOTE
compile(remote, "remote", "exec")
proc = subprocess.run(["ssh","-i",sjzc.SSH_KEY,*sjzc.SSH_OPTS,sjzc.SSH_TARGET,
    "docker exec -i -e PYTHONPATH=/app -e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -"],
    input=remote.encode(),capture_output=True,timeout=900)
try:
    data=json.loads(proc.stdout.decode().splitlines()[-1])
except Exception as exc:
    data={"status":"REMOTE_ERROR","error":f"{type(exc).__name__}: {exc}",
          "ssh_exit_code":proc.returncode,"stderr_tail":proc.stderr.decode(errors="replace")[-600:]}
data["captured_at"]=datetime.now(timezone.utc).isoformat()
ts=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(HERE/"evidence"/f"r11-verify-r3-{ts}.json").write_text(json.dumps(data,ensure_ascii=False,indent=1),encoding="utf8")
for k in ("by_write_day","by_disp_day"):
    print(k, "->")
    for r in data.get(k,{}).get("rows",[])[:12]: print("  ", r)
for k in ("sample_req_rows","sample_code_rows"):
    print(k, "->")
    for r in data.get(k,{}).get("rows",[]): print("  ", r)
