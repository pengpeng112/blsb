"""r11 视图验证（2026-10-28，用户告知视图已创建；只读通道确认）。

①存在性与 VALID 状态（ALL_OBJECTS/ALL_VIEWS）；
②DDL 是否 v3 修正版（TO_DATE(SUBSTR(PRESC_CREATE_TIME...、CAST(... AS DATE)、JOIN 顺序）；
③READY_HIS 能否直接 COUNT（无授权则如实记，BLYB_DRUGTRACE 侧验证由 DBA 回执）。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
REMOTE = r'''
import json,time
started=time.monotonic()
out={'authorization':'User authorization 2026-10-28 r11 view verification (read-only)',
     'source_writes':0,'uploads':0}
def run(sql,limit=50):
    try:
        rows=c.execute_readonly(sql,{},max_rows=limit)
        return {'ok':True,'n':len(rows),'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:180]}
V4="('V_DRUG_TRACE_BILL_INP','V_DRUG_TRACE_CODE_INP','V_DRUG_TRACE_RETURN','V_DRUG_TRACE_RETURN_CODE')"
out['objects']=run("SELECT OBJECT_NAME, STATUS, TO_CHAR(LAST_DDL_TIME,'YYYY-MM-DD HH24:MI:SS') CREATED FROM ALL_OBJECTS WHERE OWNER='PHARMACY' AND OBJECT_TYPE='VIEW' AND OBJECT_NAME IN "+V4,10)
out['views_text']=run("SELECT VIEW_NAME, TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN "+V4,10)
counts={}
for v in ('V_DRUG_TRACE_BILL_INP','V_DRUG_TRACE_CODE_INP','V_DRUG_TRACE_RETURN','V_DRUG_TRACE_RETURN_CODE'):
    counts[v]=run("SELECT COUNT(*) N FROM PHARMACY."+v,5)
out['counts_ready_his']=counts
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
    "    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':60000})\n"
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
(HERE / "evidence" / f"r11-verify-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
print(json.dumps({"objects": data.get("objects", {}),
                  "counts": {k: (v.get("rows") if v.get("ok") else v.get("error", "")[:80])
                             for k, v in data.get("counts_ready_his", {}).items()}},
                 ensure_ascii=False)[:1200])
