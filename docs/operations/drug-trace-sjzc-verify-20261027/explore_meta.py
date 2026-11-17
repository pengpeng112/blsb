"""HIS 数据字典探查（用户 2026-10-27 授权：连 hisuser 库看表/列备注，找退药关联与住院扫码线索）。

全部为 Oracle 数据字典查询（ALL_TAB_COMMENTS/ALL_COL_COMMENTS/ALL_TAB_COLUMNS），
不扫业务大表；目的：
- MED_TRACE_RECORD 列注释（IN_OUT_TYPE 方向取值、TRACE_BILLNO 含义等）；
- DRUG_PRESC_MASTER 全列注释（找退药↔原单关联字段）；
- 两 schema 下表级候选（退/追溯/扫码/住院/TRACE/RET/INP）；
- 跨表引用型列名（ORIG/RELAT/REFER/PARENT/OLD/PRESC_NO）。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent

QUERIES = [
    ("t_drug_user_all",
     "SELECT TABLE_NAME, COMMENTS FROM ALL_TAB_COMMENTS WHERE OWNER='DRUG_USER' "
     "ORDER BY TABLE_NAME", 500),
    ("t_pharmacy_all",
     "SELECT TABLE_NAME, COMMENTS FROM ALL_TAB_COMMENTS WHERE OWNER='PHARMACY' "
     "ORDER BY TABLE_NAME", 500),
    ("c_med_trace_record",
     "SELECT COLUMN_NAME, COMMENTS FROM ALL_COL_COMMENTS WHERE OWNER='DRUG_USER' "
     "AND TABLE_NAME='MED_TRACE_RECORD' ORDER BY COLUMN_NAME", 80),
    ("c_drug_presc_master",
     "SELECT COLUMN_NAME, COMMENTS FROM ALL_COL_COMMENTS WHERE OWNER='PHARMACY' "
     "AND TABLE_NAME='DRUG_PRESC_MASTER' ORDER BY COLUMN_ID", 120),
    ("t_candidates",
     "SELECT OWNER, TABLE_NAME, COMMENTS FROM ALL_TAB_COMMENTS WHERE OWNER IN "
     "('DRUG_USER','PHARMACY') AND (COMMENTS LIKE '%退%' OR COMMENTS LIKE '%追溯%' "
     "OR COMMENTS LIKE '%扫码%' OR COMMENTS LIKE '%住院%' OR TABLE_NAME LIKE '%TRACE%' "
     "OR TABLE_NAME LIKE '%RET%' OR TABLE_NAME LIKE '%INP%') ORDER BY OWNER, TABLE_NAME", 300),
    ("c_reference_cols",
     "SELECT OWNER, TABLE_NAME, COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER IN "
     "('DRUG_USER','PHARMACY') AND (COLUMN_NAME LIKE '%ORIG%' OR COLUMN_NAME LIKE "
     "'%RELAT%' OR COLUMN_NAME LIKE '%REFER%' OR COLUMN_NAME LIKE '%PARENT%' OR "
     "COLUMN_NAME LIKE '%OLD%' OR COLUMN_NAME LIKE '%PRESC%') ORDER BY OWNER, "
     "TABLE_NAME, COLUMN_NAME", 400),
]

REMOTE = ("import json,time\n"
          "started=time.monotonic()\n"
          "out={'authorization':\"User authorization 2026-10-27: HIS data dictionary "
          "exploration for return-link and inpatient-scan clues (read-only)\",\n"
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
(HERE / "evidence" / f"his-meta-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
print(json.dumps({"file": f"his-meta-{ts}.json",
                  "ok": {k: v.get("ok") for k, v in data.items() if isinstance(v, dict)},
                  "elapsed": data.get("elapsed")}, ensure_ascii=False))
