"""关联错位诊断（用户 2026-10-27 持续授权的本业务只读核查延续）。

对 30 张样本：仅按 TRACE_BILLNO（含前导零变体 10/20 位）查 MED_TRACE_RECORD，
不做任何日期/药房/时间过滤——捕捉 CREATE_TIME 为空或偏离、PRESC_DATE 差异、
STORE_CODE 编码不同、前导零格式四类关联错位（上一轮 SQL03 ±24h 窗口的盲区）。
纯聚合输出（键分布+计数+时间范围），无码值/患者字段。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
PRIV = HERE.parents[2] / ".ai/private/drug-trace-sjzc-verify-20261027"

if "--authorized" not in sys.argv:
    raise SystemExit("Explicit user authorization required")

samples = json.loads(sorted(PRIV.glob("sample-keys-*.json"))[-1]
                     .read_text(encoding="utf8"))["samples"]

SQL = ("SELECT T.TRACE_BILLNO, T.STORE_CODE, T.IN_OUT_TYPE, T.IS_DELETED, "
       "T.SPLIT_FLAG, COUNT(*) N, "
       "TO_CHAR(MIN(T.PRESC_DATE),'YYYY-MM-DD HH24:MI:SS') MIN_PRESC_DATE, "
       "TO_CHAR(MAX(T.PRESC_DATE),'YYYY-MM-DD HH24:MI:SS') MAX_PRESC_DATE, "
       "TO_CHAR(MIN(T.CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') MIN_CREATE, "
       "TO_CHAR(MAX(T.CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') MAX_CREATE "
       "FROM DRUG_USER.MED_TRACE_RECORD T "
       "WHERE T.TRACE_BILLNO IN (:v0,:v1,:v2) "
       "GROUP BY T.TRACE_BILLNO, T.STORE_CODE, T.IN_OUT_TYPE, T.IS_DELETED, "
       "T.SPLIT_FLAG")

REMOTE_BODY = r'''
started=time.monotonic()
S=__SAMPLES__
out={'authorization':"User ongoing authorization 2026-10-27 ('授权执行') - association mismatch diagnostic, read-only",
     'source_writes':0,'uploads':0,'tables_accessed':['DRUG_USER.MED_TRACE_RECORD']}
def run(sql,binds,limit=50):
    b=time.monotonic()
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'rows':rows,'seconds':round(time.monotonic()-b,4)}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:200]}
out['identity']=run("SELECT USER AS LOGIN_USER FROM DUAL",{},1)
SQL=__SQL__
res=[]
for s in S:
    if time.monotonic()-started>300: break
    raw=s['presc_no_text']
    r=run(SQL,{'v0':raw,'v1':raw.rjust(10,'0'),'v2':raw.rjust(20,'0')},50)
    res.append({'grp':s['grp'],'tile':s['tile'],'presc_no':raw,
                'store_expected':s['store_code'],
                'presc_date_expected':s['presc_date_s'],'rows':r.get('rows',[]),
                'ok':r.get('ok'),'error':r.get('error')})
out['results']=res
out['samples_done']=len(res)
out['elapsed_seconds']=round(time.monotonic()-started,2)
print(json.dumps(out))
'''

remote_body = (REMOTE_BODY
               .replace("__SAMPLES__", json.dumps(samples, ensure_ascii=False))
               .replace("__SQL__", json.dumps(SQL)))

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
    "    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':15000})\n"
    "del _u,_p\n"
)
remote = imports + "\n" + setup + remote_body
compile(remote, "remote", "exec")

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
(HERE / "evidence" / f"assoc-check-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
hit = sum(1 for r in data.get("results", []) if r.get("rows"))
print(json.dumps({"file": f"assoc-check-{ts}.json", "samples": data.get("samples_done"),
                  "samples_with_rows": hit, "elapsed": data.get("elapsed_seconds"),
                  "identity": data.get("identity", {}).get("rows")},
                 ensure_ascii=False))
