"""SD-06：拉取 205 张 UNKNOWN 提交逐单核查清单（<prod-host> 应用库只读事务）。

单号级键值按约定落 .ai/private/（ACL 当前用户+SYSTEM）；共享报告只含聚合
与受限文件指针。只读，无写入。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, re, shlex

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PRIV = ROOT / ".ai/private/drug-trace-scope-dev-20261027"

REMOTE = r'''
import json,subprocess
sql = """SELECT json_agg(row_to_json(x)) FROM (
 SELECT s.id AS submission_id, coalesce(pr.payload->>'bill_code','') AS bill_code,
   coalesce(b.source_bill_no,'') AS source_bill_no, s.state,
   to_char(s.created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS') AS created_gmt8,
   (SELECT to_char(max(da.started_at) AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD HH24:MI:SS')
      FROM delivery_attempt da WHERE da.submission_id=s.id) AS last_attempt_gmt8,
   (SELECT count(*) FROM delivery_attempt da WHERE da.submission_id=s.id) AS attempts
 FROM submission s
 JOIN package_revision pr ON pr.id=s.package_revision_id
 JOIN data_package dp ON dp.id=pr.package_id AND dp.domain='drug_trace'
 LEFT JOIN drug_trace_bill b ON b.id=(pr.provenance->>'bill_id')
 WHERE s.state='UNKNOWN'
 ORDER BY s.created_at) x"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=60)
print('__R205__'+json.dumps({'rc':p.returncode,'rows':json.loads(p.stdout.strip()) if p.returncode==0 and p.stdout.strip() else None,'err':p.stderr[-400:]})+'__END__')
'''
spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
conn = fb.deploy.connect_jump(fb.creds()["jump"])
rc, raw = fb.nested(conn, "python3 -c " + shlex.quote(REMOTE), timeout=180)
m = re.search(r"__R205__(\{.*?\})__END__", raw, re.S)
payload = json.loads(m.group(1))
assert payload["rc"] == 0, payload.get("err")
rows = payload["rows"]
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
(PRIV / f"sd06-unknown205-{ts}.json").write_text(
    json.dumps({"captured_at": ts, "count": len(rows), "submissions": rows},
               ensure_ascii=False, indent=1), encoding="utf8")
from collections import Counter
print("count:", len(rows))
print("by submit day:", Counter(r["created_gmt8"][:10] for r in rows))
print("with attempts:", sum(1 for r in rows if r["attempts"] > 0))
