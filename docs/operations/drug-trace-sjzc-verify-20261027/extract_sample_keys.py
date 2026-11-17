"""只读提取 10-26 批 WAITING 样本三键（SOURCE-REVIEW 取样规则的落地）。

- <prod-host> 应用库只读事务（PGOPTIONS read-only + statement_timeout），零写入。
- 样本：normal_no_code(原0/1) NTILE 20 张、orig_1_0(原1/0) 7 张、orig_0_2(原0/2) 3 张，
  按药房+采集先后分层；三键从 source_bill_id('date|no|disp') 反解，
  snapshot_time_gmt8=revision.created_at(UTC)+8h。
- 完整样本键 → .ai/private/drug-trace-sjzc-verify-20261027/（受限 ACL）；
  共享 evidence 只留分组计数/药房分布，不留单据键。
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import importlib.util, json, re, shlex

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PRIV = ROOT / ".ai" / "private" / "drug-trace-sjzc-verify-20261027"

REMOTE = r'''
import json,subprocess
sql = """WITH w AS (
 SELECT b.id,b.pharmacy_code,b.source_bill_id,b.source_bill_no,r.created_at,
        substring(r.header->>'source_bill_kind_raw' from 'PRESC_SOURCE=([0-9]+|NULL)') ps,
        substring(r.header->>'source_bill_kind_raw' from 'FLAG=([0-9]+|NULL)') fl
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' AND r.quality_state='WAITING'
), g AS (
 SELECT *, CASE WHEN ps='0' AND fl='1' THEN 'normal_no_code'
                WHEN ps='1' AND fl='0' THEN 'orig_1_0'
                WHEN ps='0' AND fl='2' THEN 'orig_0_2'
                ELSE 'other_'||coalesce(ps,'N')||'_'||coalesce(fl,'N') END grp
 FROM w
), t AS (
 SELECT g.*, NTILE(CASE grp WHEN 'normal_no_code' THEN 20 WHEN 'orig_1_0' THEN 7
                            ELSE 3 END)
   OVER (PARTITION BY grp ORDER BY pharmacy_code, created_at, id) tile
 FROM g WHERE grp IN ('normal_no_code','orig_1_0','orig_0_2')
), pick AS (
 SELECT t.*, row_number() OVER (PARTITION BY grp, tile
   ORDER BY pharmacy_code, created_at, id) rn FROM t
)
SELECT json_build_object(
 'group_counts',(SELECT json_object_agg(grp,n) FROM (SELECT grp,count(*) n FROM g GROUP BY 1)x),
 'pharmacy_dist',(SELECT json_agg(row_to_json(x)) FROM (SELECT grp,pharmacy_code,count(*) n
   FROM g GROUP BY 1,2 ORDER BY 1,2)x),
 'samples',(SELECT json_agg(row_to_json(x)) FROM (SELECT grp,tile,
   split_part(source_bill_id,'|',1) presc_date_s,
   split_part(source_bill_id,'|',2) presc_no_text,
   split_part(source_bill_id,'|',3) store_code,
   source_bill_no trace_billno,
   to_char(created_at + interval '8 hour','YYYY-MM-DD HH24:MI:SS') snapshot_time_gmt8
   FROM pick WHERE rn=1 ORDER BY grp,tile)x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=30)
print('__SJZC__'+json.dumps({'rc':p.returncode,'psql':p.stdout.strip(),'err':p.stderr[-400:]})+'__END__')
'''

spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)

conn = fb.deploy.connect_jump(fb.creds()["jump"])
rc, raw = fb.nested(conn, "python3 -c " + shlex.quote(REMOTE), timeout=120)
m = re.search(r"__SJZC__(\{.*\})__END__", raw, re.S)
if rc != 0 or not m:
    raise RuntimeError(f"nested rc={rc} raw_tail={raw[-400:]!r}")
payload = json.loads(m.group(1))
if payload["rc"] != 0:
    raise RuntimeError(f"remote psql rc={payload['rc']} err={payload.get('err')}")
data = json.loads(payload["psql"])

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
counts = data["group_counts"]
samples = data["samples"]
per_grp = {}
for s in samples:
    per_grp[s["grp"]] = per_grp.get(s["grp"], 0) + 1

PRIV.mkdir(parents=True, exist_ok=True)
(PRIV / f"sample-keys-{ts}.json").write_text(
    json.dumps({"captured_utc": datetime.now(timezone.utc).isoformat(),
                "rule": "SOURCE-REVIEW 20261027 首轮样本：20/7/3 分层",
                "group_counts": counts, "samples": samples},
               ensure_ascii=False, indent=2), encoding="utf8")

agg = {"captured_gmt8": datetime.now(timezone(timedelta(hours=8))).isoformat(),
       "transport": "jump+nested 固定指纹 SSH（deploy_fb05 通道），应用库只读事务",
       "group_counts": counts, "sample_counts": per_grp,
       "pharmacy_dist": data["pharmacy_dist"],
       "keys_location": ".ai/private/drug-trace-sjzc-verify-20261027/ (受限)"}
(HERE / "evidence" / f"extract-aggregate-{ts}.json").write_text(
    json.dumps(agg, ensure_ascii=False, indent=2), encoding="utf8")
print(json.dumps(agg, ensure_ascii=False, indent=1))
