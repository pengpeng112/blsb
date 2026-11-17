"""Validate evidence joins, full coverage, and new-document links offline."""
from pathlib import Path
from collections import Counter
import csv,json,importlib.util
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
summary=json.loads((HERE/'evidence/review-summary-final.json').read_text('utf8'))
assert summary['unique_targets']==247 and summary['ledger_unchanged'] and summary['uploads']==0
with (HERE/'核查明细_最终版_无患者信息.csv').open(encoding='utf-8-sig') as f:rows=list(csv.DictReader(f))
inventory=json.loads(sorted((HERE/'evidence').glob('app-inventory-*.json'))[-1].read_text('utf8'))['result']['rows']
assert len(rows)==247 and len({r['submission_id'] for r in rows})==247
assert {r['submission_id'] for r in rows}=={r['submission_id'] for r in inventory}
assert Counter((r['local_state'],r['platform_state']) for r in rows)==Counter({('UNKNOWN','NOT_FOUND'):206,('ACCEPTED','CONFIRMED'):41})
assert all(r['result_type']=='3' and r['sub_process_flag']=='51' for r in rows if r['local_state']=='ACCEPTED')
spec=importlib.util.spec_from_file_location('review_document_links',ROOT/'scripts/project_docs.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
for p in [HERE/'README.md',ROOT/'docs/ai/sessions/DTR-UNKNOWN-CHECK-20261026.md']:
 for link in module.link_targets(p.read_text('utf8')):
  if '://' in link or link.startswith('#'):continue
  assert (p.parent/link.split('#',1)[0]).exists(),(p.name,link)
print(json.dumps({'offline_evidence_validation':True,'unique_rows':247,'platform_success':41,'not_found_in_scope':206,'local_business_writes':0,'new_document_links_valid':True}))
