"""Read aggregate attempt/receipt results, masking any raw trace strings."""
import json
import re
from collections import Counter
from core_db import make_engine,make_session_factory
from core_db.models import Tenant,Submission,DeliveryAttempt,ReceiptEvent,SubmissionItem,Job
from domains.drug_trace.models import DrugTraceSendQuota,DrugTraceProductionEvidence
from domains.drug_trace import contracts as C


def safe(value):
    if isinstance(value,dict):return {k:safe(v) for k,v in value.items() if k not in ('trace_code','trace_codes','bill_code')}
    if isinstance(value,list):return [safe(v) for v in value[:20]]
    if isinstance(value,str):return re.sub(r'\d{15,}','[CODE]',value)[:600]
    return value


out={'ok':False,'source_writes':0}
engine=make_engine()
try:
    with make_session_factory(engine)() as db:
        t=db.query(Tenant).filter_by(code='first-hospital').one()
        s=db.get(Submission,'7ed95aaa-ee66-4510-b80d-630f6c9dc381')
        assert s.tenant_id == t.id
        attempts=db.query(DeliveryAttempt).filter_by(submission_id=s.id).order_by(DeliveryAttempt.attempt_no).all()
        receipts=db.query(ReceiptEvent).filter_by(tenant_id=t.id,submission_ref=s.id).all()
        items=db.query(SubmissionItem).filter_by(tenant_id=t.id,submission_id=s.id).all()
        jobs=[j for j in db.query(Job).filter_by(tenant_id=t.id).all() if (j.payload or {}).get('submission_id') == s.id]
        ev=db.query(DrugTraceProductionEvidence).filter_by(tenant_id=t.id,
            destination_id=s.destination_id,capability='send',profile=C.PROFILE_RETAIL).first()
        quota=db.query(DrugTraceSendQuota).filter_by(tenant_id=t.id,evidence_id=ev.id).first() if ev else None
        out.update(ok=True,submission_id=s.id,state=s.state,
            attempts=[{'number':a.attempt_no,'outcome':a.outcome,'http_status':a.http_status,
                       'ended':a.ended_at is not None,'snapshot':safe(a.response_snapshot)} for a in attempts],
            item_states=dict(Counter(i.state for i in items)),
            receipts=[{'parsed':safe(r.parsed_result),'raw_summary':safe(r.raw_payload)} for r in receipts],
            jobs=[{'id':j.id,'type':j.job_type,'state':j.state,'attempts':j.attempts,'error':safe(j.last_error)} for j in jobs],
            quota={'used':quota.used,'max_bills':quota.max_bills} if quota else None,
            approval={'status':ev.status,'revoked':ev.revoked_at is not None,
                      'valid_until':str(ev.valid_until),'production_requirements_deferred':True} if ev else None)
except Exception as exc:out['error_type']=type(exc).__name__
finally:engine.dispose()
print('__SOURCE_RESULT__'+json.dumps(out,ensure_ascii=False)+'__END__')
if not out['ok']:raise SystemExit(1)
