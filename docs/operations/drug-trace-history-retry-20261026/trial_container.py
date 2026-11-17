"""One explicitly authorized historical retry. Never run for a batch.

Runs inside the existing worker with its normal production config/rate control.
The original payload, query generations and first delivery attempt are retained.
"""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json
import os
import sys
import uuid
from urllib.parse import parse_qs

TARGET='9b19170e-5ceb-4da4-99bb-7478c77d9695'
BILL_HASH='c59321aefc1d98e5246f1d622e1af4f3e86af2e85246d0d79e26162c167d618c'
TASK='DTR-HISTORY-RETRY-20261026'
DECISION=str(uuid.uuid5(uuid.NAMESPACE_URL,TASK+':'+TARGET))
ROOT=Path('/data/exchange/history-retry-20261026-one-r2')


def query_count(db):
    from core_db.models import Job
    from domains.drug_trace import contracts as C
    return db.query(Job).filter(Job.job_type==C.DomainJobType.QUERY,Job.dedup_key.like('q:'+TARGET+':%')).count()


def validate(candidate, lookup):
    required={'submission_id':TARGET,'state':'UNKNOWN','revision':3,'attempts':1,
              'last_http_status':429,'bill_hash':BILL_HASH,'codes_count':1,
              'same_config':True,'same_source':True,'same_tenant':True,
              'snapshot_matches':True,'destination_enabled':True,'real_destination':True,
              'active_jobs':0,'inflight':0,'send_approved':True,'in_scope':True,
              'profile':'alihealth_yljg_retail_v1','bill_type':'321','query_bill_type':'A'}
    if any(candidate.get(k)!=v for k,v in required.items()):
        raise RuntimeError('CANDIDATE_GUARD_FAILED')
    if lookup.get('state')!='NOT_FOUND' or lookup.get('query_status')!='OK' or lookup.get('submission_id')!=TARGET or lookup.get('bill_hash')!=BILL_HASH:
        raise RuntimeError('PLATFORM_NOT_VERIFIED_ABSENT')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(lookup['checked_utc'])).total_seconds()
    if not 0<=age<=120:raise RuntimeError('PLATFORM_LOOKUP_STALE')


class OneUploadTransport:
    def __init__(self, original):self.original=original;self.calls=0
    def __getattr__(self, name):return getattr(self.original,name)
    def post(self, body, content_type):
        fields=parse_qs(body.decode('utf8'),keep_blank_values=True)
        if fields.get('method')!=['alibaba.alihealth.drugtrace.top.yljg.uploadretail']:
            raise RuntimeError('NON_RETAIL_UPLOAD_BLOCKED')
        if self.calls:raise RuntimeError('SECOND_UPLOAD_BLOCKED')
        self.calls+=1
        return self.original.post(body,content_type)


def now():return datetime.now(timezone.utc).isoformat()


def persist(data):
    temp=ROOT/'progress.next.json'
    temp.write_text(json.dumps(data,indent=2)+'\n')
    os.replace(temp,ROOT/'progress.json')


def run(mode):
    from sqlalchemy import text,update
    from core_db import make_engine,make_session_factory,utcnow
    from core_db.models import (Submission,PackageRevision,Destination,Job,
        DeliveryAttempt,ManualDecision,ReceiptEvent,AuditEvent)
    from domains.drug_trace.models import DrugTraceBillRevision,DrugTraceBill
    from domains.drug_trace import service as S
    from destinations.alihealth_drugtrace.rate_control import preflight_config,platform_error
    engine=make_engine();sf=make_session_factory(engine)
    if mode=='send':
        ROOT.mkdir(mode=0o700)  # Exclusive marker: rerunning cannot send again.
        progress={'task':TASK,'target':TARGET,'started_utc':now(),'stage':'precheck','upload_calls':0}
        persist(progress)
    else:
        progress=json.loads((ROOT/'progress.json').read_text())
        if mode=='status':engine.dispose();return progress
        assert mode=='query'
        if progress.get('stage') not in ('sent','queried'):
            engine.dispose();raise RuntimeError('NO_COMPLETED_TRIAL_SEND')
        elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(progress['sent_utc'])).total_seconds()
        earliest=max(120,progress.get('next_query_after_seconds',120))
        if progress.get('queried_utc'):
            elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(progress['queried_utc'])).total_seconds()
        if elapsed<earliest:engine.dispose();return {**progress,'wait_seconds':earliest-elapsed}
        n=progress.get('manual_queries',0)+1
        if n>3:engine.dispose();raise RuntimeError('MANUAL_QUERY_BUDGET_EXHAUSTED')
        with (ROOT/('query-'+str(n)+'.claimed')).open('x') as f:f.write(now())
    try:
        with sf() as db:
            sub=db.get(Submission,TARGET);pr=db.get(PackageRevision,sub.package_revision_id)
            dest=db.get(Destination,sub.destination_id);p=pr.payload;profile=pr.schema_ref['profile']
            cfg=pr.provenance.get('config_release_id')
            assert sub.tenant_id==pr.tenant_id==dest.tenant_id
            assert hashlib.sha256(str(p['bill_code']).encode()).hexdigest()==BILL_HASH
            if mode=='query':
                before_jobs=query_count(db)
                result=S.perform_query(db,sub.tenant_id,sub)
                cls=result['classification'];safe={k:cls[k] for k in ('state','result_type','sub_process_flag','reason','rate_limit') if k in cls}
                if cls.get('error'):safe['error']=platform_error(cls['error'])
                progress.update(stage='queried',manual_queries=n,queried_utc=now(),query=safe,local_state=sub.state,
                    next_query_after_seconds=max(180,float((cls.get('rate_limit') or {}).get('retry_after') or 0)))
                after_jobs=query_count(db)
                assert before_jobs==after_jobs==12,'HISTORICAL_QUERY_LEDGER_CHANGED'
                persist(progress);return progress

            # First verify absence against original enterprise/type/bill/date windows.
            S.require_production_evidence(db,sub.tenant_id,profile=profile,capability='query',destination_id=dest.id)
            client=S.build_client(db,sub.tenant_id,dest,config_release_id=cfg,profile=profile,purpose='query')
            full=S._query_full_windows(client,{'ref_ent_id':p['ref_ent_id'],'bill_code':p['bill_code'],'bill_type':p['query_bill_type']},p,sub.created_at,'both')
            cls=S._classify_query(full,p['bill_code'])
            lookup={'submission_id':TARGET,'bill_hash':BILL_HASH,'state':cls.get('state'),'query_status':full.get('status'),
                    'checked_utc':now(),'pages':full.get('pages_read'),'windows':full.get('windows_queried')}
            progress['lookup']=lookup;persist(progress)
            db.rollback()  # Refresh guard data after the network read.
            sub=db.get(Submission,TARGET);db.refresh(sub);pr=db.get(PackageRevision,sub.package_revision_id)
            dest=db.get(Destination,sub.destination_id);db.refresh(dest);p=pr.payload
            rev=db.get(DrugTraceBillRevision,pr.provenance['bill_revision_id']);bill=db.get(DrugTraceBill,rev.bill_id)
            attempts=db.query(DeliveryAttempt).filter_by(submission_id=TARGET).all()
            ev=S.evidence_state(db,sub.tenant_id,profile=profile,capability='send',destination_id=dest.id,config_release_id=cfg)
            S.require_production_evidence(db,sub.tenant_id,profile=profile,capability='send',destination_id=dest.id,config_release_id=cfg)
            S._check_send_scope(ev.get('scope'),p)
            candidate={'submission_id':TARGET,'state':sub.state,'revision':sub.submission_revision,'attempts':len(attempts),
                'last_http_status':attempts[-1].http_status if len(attempts)==1 else None,
                'bill_hash':hashlib.sha256(str(p['bill_code']).encode()).hexdigest(),'codes_count':len(p.get('codes') or []),
                'same_config':cfg==dest.config_revision,'same_source':rev.id==bill.current_revision_id,
                'same_tenant':sub.tenant_id==pr.tenant_id==dest.tenant_id==rev.tenant_id==bill.tenant_id,
                'snapshot_matches':sub.snapshot_digest==pr.digest,'destination_enabled':dest.status=='ENABLED','real_destination':not dest.is_mock,
                'active_jobs':db.query(Job).filter(Job.state.in_(['PENDING','LEASED','RETRY_WAIT'])).count(),
                'inflight':db.query(DeliveryAttempt).filter(DeliveryAttempt.ended_at.is_(None)).count(),
                'send_approved':ev.get('status')=='APPROVED','in_scope':True,'profile':profile,
                'bill_type':p.get('bill_type'),'query_bill_type':p.get('query_bill_type')}
            validate(candidate,lookup)
            assert db.get(ManualDecision,DECISION) is None,'RECOVERY_ALREADY_CONSUMED'
            c=S._load_config_revision(db,sub.tenant_id,cfg,owner_id=dest.id,purpose='send')
            preflight_config(c.content_json,S.C.PROFILES[profile]['method'])
            before={'state':sub.state,'revision':sub.submission_revision,'attempts':len(attempts),
                    'snapshot_digest':pr.digest,'query_generations':12,'bill_hash':BILL_HASH}
            assert query_count(db)==12,'HISTORICAL_QUERY_LEDGER_CHANGED'
            progress.update(stage='authorized_prepared',candidate=candidate,before=before,decision_id=DECISION)
            # Audit, fresh platform evidence and exact UNKNOWN CAS commit together.
            reason='User explicitly authorized historical resend test in this turn; one bill/one POST only after fresh full NOT_FOUND. '+TASK
            db.add(ManualDecision(id=DECISION,tenant_id=sub.tenant_id,entity_ref='submission:'+TARGET,action='HISTORY_RETRY_ONCE',
                before_ref=before,after_ref={'state':'PREPARED','revision':4,'max_uploads':1,'lookup':lookup},reason=reason,actor='user-authorized-ai'))
            db.add(ReceiptEvent(id=str(uuid.uuid4()),tenant_id=sub.tenant_id,destination_id=dest.id,submission_ref=TARGET,
                external_receipt_id='history-retry:'+DECISION,raw_payload=lookup,parsed_result={'state':'NOT_FOUND','manual':True}))
            db.add(AuditEvent(tenant_id=sub.tenant_id,actor='user-authorized-ai',action='drug_trace.history_retry.authorize',target='submission:'+TARGET,
                old_ref=before,new_ref={'state':'PREPARED','max_uploads':1,'decision_id':DECISION},reason=reason))
            changed=db.execute(update(Submission).where(Submission.id==TARGET,Submission.tenant_id==sub.tenant_id,
                Submission.state=='UNKNOWN',Submission.submission_revision==3,Submission.snapshot_digest==pr.digest)
                .values(state='PREPARED',submission_revision=4,updated_at=utcnow()))
            if changed.rowcount!=1:db.rollback();raise RuntimeError('RECOVERY_CAS_LOST')
            db.commit();db.refresh(sub);persist(progress)
            original=S.build_client;wire=[]
            def bounded(*args,**kwargs):
                c=original(*args,**kwargs);t=OneUploadTransport(c.transport);c.transport=t;wire.append(t);return c
            S.build_client=bounded
            try:result=S.perform_send(db,sub.tenant_id,sub)
            finally:S.build_client=original
            db.refresh(sub)
            safe={k:result[k] for k in ('outcome','http_status','rate_limit','fenced') if k in result}
            if result.get('error'):safe['error']=platform_error(result['error'])
            progress.update(stage='sent',sent_utc=now(),upload_calls=sum(t.calls for t in wire),result=safe,local_state=sub.state,
                attempts=db.query(DeliveryAttempt).filter_by(submission_id=TARGET).count(),manual_queries=0)
            assert progress['upload_calls']<=1
            persist(progress);return progress
    except BaseException as exc:
        progress.update(error_type=type(exc).__name__,failed_utc=now())
        if isinstance(exc,RuntimeError) and str(exc).isupper():progress['error_code']=str(exc)
        persist(progress);raise
    finally:engine.dispose()


if __name__=='__main__':
    try:
        result=run(sys.argv[1]);print('__TRIAL__'+json.dumps(result)+'__END__')
    except BaseException as exc:
        result={'ok':False,'error_type':type(exc).__name__}
        if isinstance(exc,RuntimeError) and str(exc).isupper():result['error_code']=str(exc)
        print('__TRIAL__'+json.dumps(result)+'__END__');sys.exit(1)
