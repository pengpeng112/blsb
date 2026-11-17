"""r11 住院码视图调优·实测诊断（2026-10-29，只读）。

对同一张已知住院单（REQUEST_NO=03032820261027113457@050121）：
A. 当前视图完整查询（reader 形状：source_bill_id 等值 + 首屏）；
B. 分支一裸 SQL（TRACE_BILLNO=REQUEST_NO 直连）；
C. 分支二裸 SQL（经 PHA_INP_DISPMASTER TO_CHAR(DIS_RECORDNO) 连接）；
D. 分支二改写（先按 REQUEST_NO 取 DIS_RECORDNO 列表，再 IN 等值）；
E. 两分支行数归属；
F. 附：submission PREPARED 的域分布（晨间观察项）。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, subprocess, sys

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]

REMOTE = r'''
import json,time
out={'authorization':'2026-10-29 read-only view tuning diagnostics','source_writes':0}
def run(sql,limit=50):
    t0=time.perf_counter()
    try:
        rows=c.execute_readonly(sql,{},max_rows=limit)
        return {'ok':True,'n':len(rows),'s':round(time.perf_counter()-t0,3)}
    except Exception as exc:
        return {'ok':False,'s':round(time.perf_counter()-t0,3),'error':str(exc)[:150]}
BILL="INP|03032820261027113457|050121"
RN="03032820261027113457"
# A 视图完整（reader 形状）
out['A_view']=run("SELECT source_code_record_id FROM PHARMACY.V_DRUG_TRACE_CODE_INP WHERE source_system='HIS' AND pharmacy_code='050121' AND business_kind='INPATIENT_DISPENSE' AND source_bill_id='"+BILL+"' AND source_code_record_id > '' AND ROWNUM<=25",25)
# B 分支一
out['B_branch1']=run("SELECT T.RECORD_ID FROM DRUG_USER.MED_TRACE_RECORD T JOIN DRUG_USER.PHA_INP_REQUEST_DRUG w ON T.TRACE_BILLNO=w.REQUEST_NO WHERE w.REQUEST_NO='"+RN+"' AND w.DISP_FLAG='send' AND w.DISP_QUANTITY>0 AND w.DISP_EXECUTE_TIME IS NOT NULL AND T.IN_OUT_TYPE='OUT' AND ROWNUM<=25",25)
# C 分支二（现状）
out['C_branch2_current']=run("SELECT T.RECORD_ID FROM DRUG_USER.MED_TRACE_RECORD T JOIN DRUG_USER.PHA_INP_DISPMASTER d2 ON T.TRACE_BILLNO=TO_CHAR(d2.DIS_RECORDNO) JOIN DRUG_USER.PHA_INP_REQUEST_DRUG w ON w.REQUEST_NO=d2.REQUEST_NO AND TO_CHAR(w.DISP_EXECUTE_TIME,'YYYY-MM-DD HH24:MI:SS')=d2.DISP_DATE WHERE w.REQUEST_NO='"+RN+"' AND w.DISP_FLAG='send' AND w.DISP_QUANTITY>0 AND T.IN_OUT_TYPE='OUT' AND ROWNUM<=25",25)
# D 分支二改写：先取键再 IN
out['D_branch2_in']=run("SELECT T.RECORD_ID FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO IN (SELECT TO_CHAR(d2.DIS_RECORDNO) FROM DRUG_USER.PHA_INP_DISPMASTER d2 WHERE d2.REQUEST_NO='"+RN+"') AND T.IN_OUT_TYPE='OUT' AND ROWNUM<=25",25)
# E 行数归属
out['E_rows_b1']=run("SELECT COUNT(*) N FROM DRUG_USER.MED_TRACE_RECORD T WHERE T.TRACE_BILLNO='"+RN+"' AND T.IN_OUT_TYPE='OUT'",5)
out['E_rows_view']=run("SELECT COUNT(*) N FROM PHARMACY.V_DRUG_TRACE_CODE_INP WHERE source_bill_id='"+BILL+"'",5)
out['elapsed']=round(time.perf_counter(),1)
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
    data = {"status": "REMOTE_ERROR", "error": str(exc)[:300],
            "stderr_tail": proc.stderr.decode(errors="replace")[-400:]}
data["captured_at"] = datetime.now(timezone.utc).isoformat()
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(HERE / "evidence" / f"viewtune-{ts}.json").write_text(
    json.dumps(data, ensure_ascii=False, indent=1), encoding="utf8")
print(json.dumps(data, ensure_ascii=False)[:1200])
