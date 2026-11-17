"""r11 验证 r2：定性 CODE_INP 的 47,295 行（住院真码 vs 拆零碰撞）。只读。"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys
HERE = Path(__file__).resolve().parent
REMOTE = r'''
import json,time
started=time.monotonic()
out={'authorization':'2026-10-28 r11 CODE_INP 47k rows triage (read-only)','source_writes':0}
def run(sql,limit=50):
    try:
        rows=c.execute_readonly(sql,{},max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:180]}
out['sample']=run("SELECT source_bill_id, source_line_id, TO_CHAR(updated_at,'YYYY-MM-DD HH24:MI:SS') U, TO_CHAR(bill_time,'YYYY-MM-DD HH24:MI:SS') B FROM (SELECT source_bill_id, source_line_id, updated_at, bill_time FROM PHARMACY.V_DRUG_TRACE_CODE_INP ORDER BY updated_at DESC) WHERE ROWNUM<=15",15)
out['by_year']=run("SELECT TO_CHAR(updated_at,'YYYY') Y, COUNT(*) N FROM PHARMACY.V_DRUG_TRACE_CODE_INP GROUP BY TO_CHAR(updated_at,'YYYY') ORDER BY 1",20)
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
(HERE/"evidence"/f"r11-verify-r2-{ts}.json").write_text(json.dumps(data,ensure_ascii=False,indent=1),encoding="utf8")
print(json.dumps(data.get("by_year",{}),ensure_ascii=False)[:400])
for r in data.get("sample",{}).get("rows",[])[:10]: print(r)
