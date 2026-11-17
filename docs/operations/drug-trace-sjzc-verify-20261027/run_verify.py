"""执行 HIS 源表核查（用户 2026-10-27 明确授权「授权执行」，同 10-23 通道先例）。

- 通道：本机 SSH→<relay-jump> data-asset-api 容器 OracleConnector.execute_readonly；
  平台托管只读凭据由容器内 resolve() 解析，本机不读取凭据内容。
- 对象：30 张样本（20 无码门诊/7 原1.0/3 原0.2），SQL01/02 每样本，
  SQL02 零行才跑 SQL03（±24h 窗口聚合行）。
- 预算：单条 15s（connector timeout_ms=15000）、累计 300s、单查询 ≤50 行；
  超预算即停并标 run_completed=false。
- SQL 来自上一棒备好的 sql/ 三文件（只读、纯聚合、无患者/原码输出）。
- 结果：共享 evidence 含 grp/tile 级结果（无单据键）；完整键值联表入受限目录。
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SQLDIR = ROOT / "docs/operations/drug-trace-recovery-stability-20261027/sql"
PRIV = ROOT / ".ai/private/drug-trace-sjzc-verify-20261027"

if "--authorized" not in sys.argv:
    raise SystemExit("Explicit user authorization required")

keys_file = sorted(PRIV.glob("sample-keys-*.json"))[-1]
samples_raw = json.loads(keys_file.read_text(encoding="utf8"))["samples"]

from datetime import datetime as _dt
samples = []
for s in samples_raw:
    snap = _dt.strptime(s["snapshot_time_gmt8"], "%Y-%m-%d %H:%M:%S")
    samples.append({
        "grp": s["grp"], "tile": s["tile"],
        "presc_date_s": s["presc_date_s"], "presc_no_text": s["presc_no_text"],
        "store_code": s["store_code"], "trace_billno": s["trace_billno"],
        "snapshot_time_gmt8": s["snapshot_time_gmt8"],
        "begin_gmt8": (snap - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
        "end_gmt8": (snap + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"),
    })

def _strip_comments(sql: str) -> str:
    """连接器只读校验拒绝含 -- 注释的 SQL：剥掉注释行，SQL 主体逐字保留。"""
    lines = [ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")]
    return "\n".join(lines).strip()

sql1 = _strip_comments((SQLDIR / "01-waiting-master.sql").read_text("utf8"))
sql2 = _strip_comments((SQLDIR / "02-waiting-trace-exact.sql").read_text("utf8"))
sql3 = _strip_comments((SQLDIR / "03-waiting-trace-nearby.sql").read_text("utf8"))

REMOTE_BODY = r'''
started=time.monotonic()
S=__SAMPLES__
out={'authorization':"User explicit authorization 2026-10-27 ('授权执行') read-only verification via sjzc controlled connector (READY_HIS precedent 2026-10-23)",
     'budget_seconds':300,'per_query_timeout_seconds':15,'source_writes':0,'uploads':0,
     'tables_accessed':['PHARMACY.DRUG_PRESC_MASTER','DRUG_USER.MED_TRACE_RECORD']}
def run(sql,binds,limit=50):
    b=time.monotonic()
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'rows':rows,'seconds':round(time.monotonic()-b,4)}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:200],
                'codes':sorted(set(__import__('re').findall(r'(?:ORA|DPY|DPI)-\d+',str(exc))))}
out['identity']=run("SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",{},1)
out['trace_index']=run("SELECT IC.INDEX_NAME,IC.COLUMN_NAME,IC.COLUMN_POSITION FROM ALL_IND_COLUMNS IC WHERE IC.TABLE_OWNER='DRUG_USER' AND IC.TABLE_NAME='MED_TRACE_RECORD' AND IC.COLUMN_NAME IN ('TRACE_BILLNO','PRESC_DATE') ORDER BY IC.INDEX_NAME,IC.COLUMN_POSITION",{},50)
SQL1=__SQL1__
SQL2=__SQL2__
SQL3=__SQL3__
res=[]
stopped=False
for s in S:
    if time.monotonic()-started>300:
        stopped=True
        break
    r1=run(SQL1,{'presc_date_s':s['presc_date_s'],'presc_no':int(s['presc_no_text']),'store_code':s['store_code']},5)
    e={'grp':s['grp'],'tile':s['tile'],'sql01':r1}
    r2=run(SQL2,{'trace_billno':s['trace_billno'],'presc_date_s':s['presc_date_s'],
                 'store_code':s['store_code'],'snapshot_time_gmt8':s['snapshot_time_gmt8']},50)
    e['sql02']=r2
    if r2['ok'] and sum(int(r['TRACE_ROWS']) for r in r2['rows'])==0:
        e['sql03']=run(SQL3,{'trace_billno':s['trace_billno'],'begin_time':s['begin_gmt8'],
                             'end_time':s['end_gmt8'],'presc_date_s':s['presc_date_s'],
                             'store_code':s['store_code']},5)
    res.append(e)
out['results']=res
out['run_completed']=not stopped
out['samples_done']=len(res)
out['elapsed_seconds']=round(time.monotonic()-started,2)
print(json.dumps(out))
'''

remote_body = (REMOTE_BODY
               .replace("__SAMPLES__", json.dumps(samples, ensure_ascii=False))
               .replace("__SQL1__", json.dumps(sql1))
               .replace("__SQL2__", json.dumps(sql2))
               .replace("__SQL3__", json.dumps(sql3)))

sjzc = importlib.util.module_from_spec(importlib.util.spec_from_file_location(
    "live_sjzc", "C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py"))
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
data["authorization_local_record"] = "用户 2026-10-27 会话明确指令『授权执行』；范围限本业务只读核查"

out_path = HERE / "evidence" / f"verify-{ts}.json"
out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")
join_path = PRIV / f"verify-with-keys-{ts}.json"
joined = []
for s, r in zip(samples, data.get("results", [])):
    joined.append({**{k: s[k] for k in ("grp", "tile", "presc_date_s", "presc_no_text",
                                        "store_code", "trace_billno", "snapshot_time_gmt8")},
                   **{k: r.get(k) for k in ("sql01", "sql02", "sql03")}})
join_path.write_text(json.dumps({"captured_at": data.get("captured_at"),
                                 "samples": joined}, ensure_ascii=False, indent=2),
                     encoding="utf8")
print(json.dumps({"file": out_path.name, "join": str(join_path),
                  "run_completed": data.get("run_completed"),
                  "samples_done": data.get("samples_done"),
                  "identity": data.get("identity"),
                  "trace_index_rows": len((data.get("trace_index") or {}).get("rows") or []),
                  "elapsed": data.get("elapsed_seconds")},
                 ensure_ascii=False))
sys.exit(0 if data.get("run_completed") else 1)
