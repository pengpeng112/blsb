"""覆盖对照（用户持续授权的本业务只读核查）：

1) 050102 当日 1085 个单号分 11 批 IN 查 MED_TRACE_RECORD（TRACE_BILLNO 索引），
   按 单号×日×药房×方向×拆零 分组返回计数与创建时间范围（无码值/患者字段）；
   本地对照 VALID/WAITING：缺失单是否真的当日无行。
2) 30 样本复合单号假设测试：TRACE_BILLNO 落在 [date14||no, date14||no||':') 或
   [date14||'_'||no||'_', …||':') 区间（前缀范围扫，索引可用；标注为假设检验）。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
PRIV = HERE.parents[2] / ".ai/private/drug-trace-sjzc-verify-20261027"

if "--authorized" not in sys.argv:
    raise SystemExit("Explicit user authorization required")

bills = json.loads(sorted(PRIV.glob("day-bills-050121-*.json"))[-1]
                   .read_text(encoding="utf8"))["bills"]
samples = json.loads(sorted(PRIV.glob("sample-keys-*.json"))[-1]
                     .read_text(encoding="utf8"))["samples"]

GROUP = ("T.TRACE_BILLNO, TO_CHAR(TRUNC(T.PRESC_DATE),'YYYY-MM-DD') AS PD, "
         "T.STORE_CODE, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG")
GROUP_BY = ("T.TRACE_BILLNO, TO_CHAR(TRUNC(T.PRESC_DATE),'YYYY-MM-DD'), "
            "T.STORE_CODE, T.IN_OUT_TYPE, T.IS_DELETED, T.SPLIT_FLAG")

REMOTE_BODY = r'''
started=time.monotonic()
BILLS=__BILLS__
SAMPLES=__SAMPLES__
out={'authorization':"User ongoing authorization 2026-10-27 read-only coverage comparison",
     'source_writes':0,'uploads':0,'tables_accessed':['DRUG_USER.MED_TRACE_RECORD']}
def run(sql,binds,limit=10000):
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'rows':rows}
    except Exception as exc:
        return {'ok':False,'error':type(exc).__name__+': '+str(exc)[:200]}
out['identity']=run("SELECT USER AS LOGIN_USER FROM DUAL",{},1)
SEL=("SELECT "+__GROUP__+", COUNT(*) N, "
     "TO_CHAR(MIN(T.CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') MIN_CREATE, "
     "TO_CHAR(MAX(T.CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') MAX_CREATE "
     "FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN ({cl}) "
     "GROUP BY "+__GROUP_BY__)
batches=[]
for i in range(0,len(BILLS),100):
    batches.append([b['presc_no_text'] for b in BILLS[i:i+100]])
cov=[]
for bi,vals in enumerate(batches):
    if time.monotonic()-started>300: break
    binds={('v%d'%j):v for j,v in enumerate(vals)}
    cl=','.join(':v%d'%j for j in range(len(vals)))
    r=run(SEL.format(cl=cl),binds,10000)
    cov.append({'batch':bi,'ok':r['ok'],'n':len(r.get('rows',[])),
                'rows':r.get('rows',[]),'error':r.get('error')})
out['coverage']=cov
SEL3=("SELECT "+__GROUP__+", COUNT(*) N, "
      "TO_CHAR(MIN(T.CREATE_TIME),'YYYY-MM-DD HH24:MI:SS') MIN_CREATE "
      "FROM DRUG_USER.MED_TRACE_RECORD T "
      "WHERE (T.TRACE_BILLNO>=:p1 AND T.TRACE_BILLNO<:u1) "
      "   OR (T.TRACE_BILLNO>=:p2 AND T.TRACE_BILLNO<:u2) "
      "GROUP BY "+__GROUP_BY__)
comp=[]
for s in SAMPLES:
    if time.monotonic()-started>300: break
    d14=s['presc_date_s'][:10].replace('-','')+s['presc_date_s'][11:].replace(':','')
    p1=d14+s['presc_no_text']; p2=d14+'_'+s['presc_no_text']+'_'
    r=run(SEL3,{'p1':p1,'u1':p1+':','p2':p2,'u2':p2+':'},50)
    comp.append({'grp':s['grp'],'tile':s['tile'],'presc_no':s['presc_no_text'],
                 'prefix1':p1,'ok':r['ok'],'rows':r.get('rows',[]),'error':r.get('error')})
out['composite']=comp
out['elapsed_seconds']=round(time.monotonic()-started,2)
print(json.dumps(out))
'''

remote_body = (REMOTE_BODY
               .replace("__BILLS__", json.dumps(bills, ensure_ascii=False))
               .replace("__SAMPLES__", json.dumps(samples, ensure_ascii=False))
               .replace("__GROUP__", json.dumps(GROUP))
               .replace("__GROUP_BY__", json.dumps(GROUP_BY)))

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
(PRIV / f"coverage-050121-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
(HERE / "evidence" / f"coverage-summary-50121-{ts}.json").write_text(
    json.dumps({"captured_at": data.get("captured_at"),
                "elapsed": data.get("elapsed_seconds"),
                "batches_ok": sum(1 for b in data.get("coverage", []) if b.get("ok")),
                "total_rows": sum(b.get("n", 0) for b in data.get("coverage", [])),
                "composite_hits": sum(1 for r in data.get("composite", []) if r.get("rows"))},
               ensure_ascii=False, indent=1), encoding="utf8")
print(json.dumps({"elapsed": data.get("elapsed_seconds"),
                  "batches_ok": sum(1 for b in data.get("coverage", []) if b.get("ok")),
                  "total_rows": sum(b.get("n", 0) for b in data.get("coverage", [])),
                  "composite_hits": sum(1 for r in data.get("composite", []) if r.get("rows"))},
                 ensure_ascii=False))
