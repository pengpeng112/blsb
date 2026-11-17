"""Read-only candidate checks, without HIS or platform calls."""
from pathlib import Path
import json,subprocess
code='''import json,hashlib
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,Destination,Job,DeliveryAttempt
from domains.drug_trace.models import DrugTraceBillRevision,DrugTraceBill
from domains.drug_trace import service as S
engine=make_engine();sf=make_session_factory(engine)
out={}
try:
 with sf() as db:
  db.execute(text('SET TRANSACTION READ ONLY'))
  out['states']=dict(db.execute(text('SELECT state,count(*) n FROM submission GROUP BY state')).all())
  out['active_jobs']=db.query(Job).filter(Job.state.in_(['PENDING','LEASED','RETRY_WAIT'])).count()
  out['inflight']=db.query(DeliveryAttempt).filter(DeliveryAttempt.ended_at.is_(None)).count()
  candidates=[]
  for s in db.query(Submission).filter(Submission.state=='UNKNOWN').order_by(Submission.created_at,Submission.id).all():
   pr=db.get(PackageRevision,s.package_revision_id);d=db.get(Destination,s.destination_id)
   p=pr.payload;cfg=pr.provenance.get('config_release_id')
   rev=db.get(DrugTraceBillRevision,pr.provenance.get('bill_revision_id'));bill=db.get(DrugTraceBill,rev.bill_id)
   a=db.query(DeliveryAttempt).filter_by(submission_id=s.id).order_by(DeliveryAttempt.attempt_no.desc()).first()
   ev=S.evidence_state(db,s.tenant_id,profile=pr.schema_ref['profile'],capability='send',destination_id=d.id,config_release_id=cfg)
   row={'submission_id':s.id,'state':s.state,'revision':s.submission_revision,'bill_sha256':hashlib.sha256(str(p['bill_code']).encode()).hexdigest(),'codes_count':len(p.get('codes') or []),'last_http_status':a.http_status,'attempts':db.query(DeliveryAttempt).filter_by(submission_id=s.id).count(),'same_config':cfg==d.config_revision,'same_source_revision':rev.id==bill.current_revision_id,'send_evidence':ev.get('status'),'profile':pr.schema_ref['profile'],'upload_bill_type':p.get('bill_type'),'query_bill_type':p.get('query_bill_type')}
   try:S._check_send_scope(ev.get('scope'),p);row['in_scope']=True
   except Exception:row['in_scope']=False
   candidates.append(row)
  out['candidate_count']=len(candidates)
  out['eligible_count']=sum(x['same_config'] and x['same_source_revision'] and x['send_evidence']=='APPROVED' and x['in_scope'] and x['last_http_status']==429 for x in candidates)
  out['sample']=[x for x in candidates if x['same_config'] and x['same_source_revision'] and x['send_evidence']=='APPROVED' and x['in_scope'] and x['last_http_status']==429][:3]
finally:engine.dispose()
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=45)
if p.returncode:raise RuntimeError('HISTORY_READONLY_INSPECT_FAILED')
out=json.loads(p.stdout);out['release']=Path('/opt/blyb/current').resolve().name
q=subprocess.run(['systemctl','show','blyb-daily-upload.service','blyb-daily-upload.timer','-p','Id','-p','ActiveState','-p','SubState'],capture_output=True,text=True,timeout=10)
out['units']=q.stdout
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
