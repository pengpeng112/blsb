"""SD-01 脚本A：<prod-host> 应用库只读事务拉取（DTR-SCOPE-DEV-20261027 授权范围）。

拉取内容（全部只读，无患者字段）：
- Q1 住院 WAITING 样本 ≤10（presc_no/pharmacy/presc_date）
- Q2 门诊退药（原 0/2 分支）3 张键
- Q4 260 缺码门诊 PRESC_NO 全量 + VALID 对照组样本（≤300）
- SD-06 205 UNKNOWN 提交的发送时间分布（按日聚合，不输出单号）

键值明细落 .ai/private/drug-trace-scope-dev-20261027/（沿用上一棒 ACL 约定），
共享 evidence 只放聚合分布与 ≤10 样本（his-meta 先例口径）。
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json, re, shlex

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PRIV = ROOT / ".ai/private/drug-trace-scope-dev-20261027"
PRIV.mkdir(parents=True, exist_ok=True)

REMOTE = r'''
import json,subprocess
def q(name, sql):
    p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=40)
    print('__%s__'%name + json.dumps({'rc':p.returncode,'rows':json.loads(p.stdout.strip()) if p.returncode==0 and p.stdout.strip() else None,'err':p.stderr[-400:]})+'__END__')

q('inpatient_waiting', """SELECT json_agg(row_to_json(x)) FROM (
 SELECT b.source_bill_no AS presc_no, b.pharmacy_code,
        split_part(b.source_bill_id,'|',1) AS presc_date_s, r.quality_state
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'
   AND r.quality_state='WAITING'
   AND r.header->>'source_bill_kind_raw'='PRESC_SOURCE=1;FLAG=0'
 ORDER BY b.source_bill_no LIMIT 10) x""")

q('inpatient_waiting_count', """SELECT json_agg(row_to_json(x)) FROM (
 SELECT count(*) AS total FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'
   AND r.quality_state='WAITING'
   AND r.header->>'source_bill_kind_raw'='PRESC_SOURCE=1;FLAG=0') x""")

q('return_bills', """SELECT json_agg(row_to_json(x)) FROM (
 SELECT b.source_bill_no AS presc_no, b.pharmacy_code,
        split_part(b.source_bill_id,'|',1) AS presc_date_s
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'
   AND r.header->>'source_bill_kind_raw'='PRESC_SOURCE=0;FLAG=2'
 ORDER BY b.source_bill_no) x""")

q('nocode_keys', """SELECT json_agg(row_to_json(x)) FROM (
 SELECT b.source_bill_no::int AS presc_no
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'
   AND r.quality_state='WAITING'
   AND r.header->>'source_bill_kind_raw'='PRESC_SOURCE=0;FLAG=1'
 ORDER BY b.source_bill_no) x""")

q('valid_sample', """SELECT json_agg(row_to_json(x)) FROM (
 SELECT b.source_bill_no::int AS presc_no
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'
   AND r.quality_state='VALID'
 ORDER BY b.source_bill_no LIMIT 300) x""")

q('unknown205_time', """SELECT json_agg(row_to_json(x)) FROM (
 SELECT to_char(s.created_at AT TIME ZONE 'Asia/Shanghai','YYYY-MM-DD') AS submit_day, count(*) AS n,
        to_char(min(s.created_at AT TIME ZONE 'Asia/Shanghai'),'YYYY-MM-DD HH24:MI:SS') AS first_created,
        to_char(max(s.created_at AT TIME ZONE 'Asia/Shanghai'),'YYYY-MM-DD HH24:MI:SS') AS last_created
 FROM submission s
 JOIN package_revision pr ON pr.id=s.package_revision_id
 JOIN data_package dp ON dp.id=pr.package_id AND dp.domain='drug_trace'
 WHERE s.state='UNKNOWN'
 GROUP BY 1 ORDER BY 1) x""")
'''
spec = importlib.util.spec_from_file_location(
    "fb", ROOT / "docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py")
fb = importlib.util.module_from_spec(spec); spec.loader.exec_module(fb)
conn = fb.deploy.connect_jump(fb.creds()["jump"])
rc, raw = fb.nested(conn, "python3 -c " + shlex.quote(REMOTE), timeout=240)
sections = {}
for m in re.finditer(r"__(\w+)__(\{.*?\})__END__", raw, re.S):
    sections[m.group(1)] = json.loads(m.group(2))
missing = [k for k in ("inpatient_waiting", "inpatient_waiting_count", "return_bills",
                       "nocode_keys", "valid_sample", "unknown205_time")
           if k not in sections or sections[k]["rc"] != 0]
assert not missing, {k: sections.get(k, {}).get("err") for k in missing}

def rows(name):
    return sections[name]["rows"] or []

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
inpat = rows("inpatient_waiting")
ret = rows("return_bills")
nocode = [r["presc_no"] for r in (rows("nocode_keys") or [])]
valid = [r["presc_no"] for r in (rows("valid_sample") or [])]
u205 = rows("unknown205_time")
summary = {
    "authorization": "DTR-SCOPE-DEV-20261027 用户授权（<prod-host> 只读事务拉样本键/205时间分布）",
    "appdb_reads_only": True, "writes": 0,
    "inpatient_waiting_total": rows("inpatient_waiting_count")[0]["total"],
    "inpatient_waiting_samples": inpat,
    "return_bills": ret,
    "nocode_presc_no_count": len(nocode),
    "valid_sample_count": len(valid),
    "unknown205_time_distribution": u205,
    "captured_at": ts,
}
(HERE / "evidence" / f"sd01-appdb-{ts}.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf8")
(PRIV / f"sd01-keys-{ts}.json").write_text(
    json.dumps({"captured_at": ts, "inpatient_waiting": inpat, "return_bills": ret,
                "nocode_presc_no": nocode, "valid_sample_presc_no": valid},
               ensure_ascii=False, indent=1), encoding="utf8")
print(json.dumps({k: (v if not isinstance(v, list) else len(v))
                  for k, v in summary.items()}, ensure_ascii=False))
print("inpatient pharmacies:", sorted({r["pharmacy_code"] for r in inpat}))
print("return keys:", [(r["presc_no"], r["pharmacy_code"]) for r in ret])
print("205 time span:", u205[0]["submit_day"] if u205 else None, "->",
      u205[-1]["submit_day"] if u205 else None)
