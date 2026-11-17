"""只读拉取 050102 在 10-26 发药窗的全部单据键+质量分类（应用库只读事务）。"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, re, shlex

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

REMOTE = r'''
import json,subprocess
sql = """SELECT json_agg(row_to_json(x)) FROM (
 SELECT r.quality_state,
   split_part(b.source_bill_id,'|',1) presc_date_s,
   split_part(b.source_bill_id,'|',2) presc_no_text,
   split_part(b.source_bill_id,'|',3) store_code
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' AND b.pharmacy_code='050121'
 ORDER BY b.bill_time, b.source_bill_id) x"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=30)
print('__DAY__'+json.dumps({'rc':p.returncode,'rows':json.loads(p.stdout.strip()) if p.returncode==0 else None,'err':p.stderr[-300:]})+'__END__')
'''
spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
conn = fb.deploy.connect_jump(fb.creds()["jump"])
rc, raw = fb.nested(conn, "python3 -c " + shlex.quote(REMOTE), timeout=120)
m = re.search(r"__DAY__(\{.*\})__END__", raw, re.S)
payload = json.loads(m.group(1))
assert payload["rc"] == 0, payload.get("err")
rows = payload["rows"]
from collections import Counter
print(Counter(r["quality_state"] for r in rows))
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
out = ROOT / ".ai/private/drug-trace-sjzc-verify-20261027" / f"day-bills-050121-{ts}.json"
out.write_text(json.dumps({"captured_utc": ts, "pharmacy": "050102", "window": "10-26", "bills": rows},
                          ensure_ascii=False, indent=1), encoding="utf8")
print("saved:", out.name, "total:", len(rows))
