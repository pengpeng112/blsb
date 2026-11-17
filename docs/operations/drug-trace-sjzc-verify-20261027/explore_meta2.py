"""HIS 元数据探查 r2：拉原厂家追溯视图 DDL + 关键表结构（用户授权的只读探查延续）。"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent

QUERIES = [
    ("v_drug_user_trace",
     "SELECT VIEW_NAME, TEXT FROM ALL_VIEWS WHERE OWNER='DRUG_USER' AND VIEW_NAME IN ("
     "'CLI_TRACE_DRUG_SEND_VIEW','CLI_TRACE_DRUG_RETURN_VIEW',"
     "'CLI_INP_TRACE_DRUG_SEND_VIEW','CLI_INP_TRACE_DRUG_RETURN_VIEW',"
     "'PHA_INP_TRACE_DRUG_SEND_VIEW','PHA_INP_TRACE_DRUG_RETURN_VIEW',"
     "'INP_TRACE_DRUG_SEND_VIEW','CLI_TRACE_DRUG_VIEW')", 20),
    ("v_pharmacy_ours",
     "SELECT VIEW_NAME, TEXT FROM ALL_VIEWS WHERE OWNER='PHARMACY' AND VIEW_NAME IN ("
     "'V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE')", 5),
    ("c_drug_presc_master",
     "SELECT COLUMN_NAME, COMMENTS FROM ALL_COL_COMMENTS WHERE OWNER='PHARMACY' "
     "AND TABLE_NAME='DRUG_PRESC_MASTER' ORDER BY COLUMN_NAME", 120),
    ("t_key_columns",
     "SELECT OWNER, TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE "
     "(OWNER='DRUG_USER' AND TABLE_NAME IN ('TRACE_UPLOAD_TIME_LOG',"
     "'DOCUMENT_UPLOAD_RECORD','CLI_TRACE_BILLNO','PHA_INP_DISPMASTER',"
     "'PHA_INP_DISPDETAIL')) OR (OWNER='PHARMACY' AND TABLE_NAME="
     "'DRUG_PRESC_TRACE_CODE') ORDER BY OWNER, TABLE_NAME, COLUMN_ID", 400),
]

REMOTE = ("import json,time\n"
          "started=time.monotonic()\n"
          "out={'authorization':'User authorization 2026-10-27 HIS metadata exploration r2 (view DDL + key table columns, read-only)',\n"
          "     'source_writes':0,'uploads':0}\n"
          "def run(sql,limit):\n"
          "    try:\n"
          "        rows=c.execute_readonly(sql,{},max_rows=limit)\n"
          "        return {'ok':True,'n':len(rows),'rows':rows}\n"
          "    except Exception as exc:\n"
          "        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:200]}\n"
          "Q=" + json.dumps([{"k": k, "sql": s, "lim": l} for k, s, l in QUERIES]) + "\n"
          "for q in Q:\n"
          "    out[q['k']]=run(q['sql'],q['lim'])\n"
          "out['elapsed']=round(time.monotonic()-started,2)\n"
          "print(json.dumps(out))\n")

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
(HERE / "evidence" / f"his-meta2-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
summary = {}
for k, v in data.items():
    if isinstance(v, dict) and "ok" in v:
        if k.startswith("v_"):
            summary[k] = {r.get("VIEW_NAME"): len(r.get("TEXT") or "")
                          for r in v.get("rows", [])}
        else:
            summary[k] = v.get("n")
print(json.dumps({"file": f"his-meta2-{ts}.json", "summary": summary,
                  "elapsed": data.get("elapsed")}, ensure_ascii=False))
