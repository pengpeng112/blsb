"""Compare stored view snapshots, frozen payloads, item rows and send digests.

All raw data stays inside the worker's RAM. Only aggregate consistency counts
leave the container. This neither queries HIS nor contacts TOP.
"""
from datetime import datetime,timezone
import json,subprocess

code='''from collections import Counter,defaultdict
from datetime import datetime
import json
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,SubmissionItem,DeliveryAttempt,ReceiptEvent
from domains.drug_trace.models import DrugTraceBill,DrugTraceBillRevision
from domains.drug_trace import service as S,quality as Q
engine=make_engine();sf=make_session_factory(engine)
out={'business_date':'2026-10-26','source_calls':0,'platform_calls':0,'writes':0,'checked_bills':0,'codes':0,'mismatches':Counter()}
try:
 with sf() as db:
  db.execute(text('SET TRANSACTION READ ONLY'))
  db.execute(text('SET LOCAL statement_timeout=15000'))
  out['transaction_read_only']=db.execute(text('SHOW transaction_read_only')).scalar()
  rows=(db.query(Submission,PackageRevision,DrugTraceBillRevision,DrugTraceBill)
   .join(PackageRevision,PackageRevision.id==Submission.package_revision_id)
   .join(DrugTraceBillRevision,DrugTraceBillRevision.id==PackageRevision.provenance['bill_revision_id'].as_string())
   .join(DrugTraceBill,DrugTraceBill.id==DrugTraceBillRevision.bill_id)
   .filter(DrugTraceBill.bill_time>=datetime(2026,10,26),DrugTraceBill.bill_time<datetime(2026,10,27)).all())
  ids=[s.id for s,pr,r,b in rows];items=defaultdict(list);attempts=defaultdict(list)
  for item in db.query(SubmissionItem).filter(SubmissionItem.submission_id.in_(ids)).all():items[item.submission_id].append(item)
  for attempt in db.query(DeliveryAttempt).filter(DeliveryAttempt.submission_id.in_(ids)).all():attempts[attempt.submission_id].append(attempt)
  confirmed_receipts=set()
  for receipt in db.query(ReceiptEvent).filter(ReceiptEvent.submission_ref.in_(ids)).all():
   parsed=receipt.parsed_result or {}
   if parsed.get('state')=='CONFIRMED' and str(parsed.get('result_type'))=='3' and str(parsed.get('sub_process_flag'))=='51':confirmed_receipts.add(receipt.submission_ref)
  def pairs(lines):return Counter((str(c.get('source_code_record_id','')),str(c.get('trace_code',''))) for c in lines)
  out['submission_states']=dict(Counter(s.state for s,pr,r,b in rows))
  out['items_states']=dict(Counter(i.state for group in items.values() for i in group))
  out['attempt_states']=dict(Counter(a.outcome for group in attempts.values() for a in group))
  out['attempt_digests_checked']=0
  out['confirmed_bills_with_platform_3_51']=sum(sub.state=='CONFIRMED' and sub.id in confirmed_receipts for sub,pr,r,b in rows)
  for sub,pr,revision,bill in rows:
   out['checked_bills']+=1;p=pr.payload;codes=p.get('codes') or []
   active=[line for line in (revision.lines or []) if not Q.as_bool(line.get('is_deleted'),'is_deleted')]
   biz=S.build_biz_params(p)
   checks={
    'tenant_chain':sub.tenant_id==pr.tenant_id==revision.tenant_id==bill.tenant_id,
    'current_revision':bill.current_revision_id==revision.id,
    'quality_valid':revision.quality_state=='VALID',
    'snapshot_digest':sub.snapshot_digest==pr.digest,
    'frozen_payload_digest':S.digest_of(p)==pr.digest,
    'source_provenance_digest':pr.provenance.get('source_digest')==revision.content_digest,
    'snapshot_to_payload':pairs(active)==pairs(codes),
    'payload_count':p.get('code_count')==len(codes)==len(active),
    'payload_to_items':pairs(codes)==pairs([i.data_ref for i in items[sub.id]]),
    'wire_code_list':Counter(c['trace_code'] for c in codes)==Counter(biz['trace_codes'].split(',')),
    'frozen_profile_and_types':p.get('profile')=='alihealth_yljg_retail_v1' and p.get('bill_type')=='321' and p.get('query_bill_type')=='A',
    'frozen_pharmacy_and_time':p.get('pharmacy_code')==bill.pharmacy_code and p.get('bill_time')==revision.header.get('bill_time'),
    'frozen_source_revision':pr.provenance.get('source_revision')==revision.source_revision,
    'upload_query_hospital_identity':bool(p.get('ref_ent_id')) and p.get('ref_user_id')==p.get('ref_ent_id'),
    'platform_confirmation_evidence':sub.state!='CONFIRMED' or sub.id in confirmed_receipts,
   }
   for name,ok in checks.items():
    if not ok:out['mismatches'][name]+=1
   out['codes']+=len(codes)
   for attempt in attempts[sub.id]:
    out['attempt_digests_checked']+=1
    if attempt.request_digest!=S.digest_of(biz)[:64]:out['mismatches']['send_business_digest']+=1
  out['expected_code_count_unknown']=sum(r.header.get('expected_code_count') is None for s,pr,r,b in rows)
 out['mismatches']=dict(out['mismatches'])
 out['consistent']=out['checked_bills']==1995 and not out['mismatches'] and out['transaction_read_only']=='on'
 print(json.dumps(out))
finally:engine.dispose()
'''
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=60)
if p.returncode:raise RuntimeError('READONLY_CONTENT_CHECK_FAILED')
out=json.loads(p.stdout);out['utc']=datetime.now(timezone.utc).isoformat()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
assert out['consistent'],'CONTENT_MISMATCH_REQUIRES_REVIEW'
