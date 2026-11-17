"""One authorized, finite manual platform lookup; no ledger writes or upload API.

Automatic twelve-generation polling is not reset or extended. This independent
manual review keeps its evidence separately; it never invokes perform_query.
"""
from datetime import datetime,timezone
from collections import Counter
import hashlib,json,sys,time
from urllib.parse import parse_qs
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,Destination,DataPackage
from domains.drug_trace import service as S
from destinations.alihealth_drugtrace.protocol import QUERY_METHOD

mode=sys.argv[1] if len(sys.argv)>1 else 'sample'
assert mode in ('sample','all')
engine=make_engine();sf=make_session_factory(engine)
started=datetime.now(timezone.utc).isoformat()
def snapshot(db):
    return dict(db.execute(text("""SELECT
      (SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission) state_digest,
      (SELECT count(*) FROM delivery_attempt) attempts,
      (SELECT count(*) FROM job) jobs,
      (SELECT count(*) FROM receipt_event) receipts,
      (SELECT sum(used) FROM drug_trace_send_quota) quota""")).mappings().one())
def readonly(db):db.execute(text('SET TRANSACTION READ ONLY'))
class QueryOnlyTransport:
    def __init__(self,transport): self.original=transport;self.calls=0;self.http=Counter()
    def post(self,body,ctype):
        method=parse_qs(body.decode('utf8'),keep_blank_values=True).get('method')
        assert method==[QUERY_METHOD],'NON_QUERY_METHOD_BLOCKED'
        self.calls+=1
        assert self.calls<=4,'PER_BILL_QUERY_REQUEST_BOUND'
        result=self.original.post(body,ctype)
        self.http[str(result.http_status)]+=1
        return result

rows=[];summary={};before=None
try:
    with sf() as db:
        readonly(db)
        assert db.execute(text("SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT')")).scalar()==0,'WORKER_NOT_IDLE'
        before=snapshot(db)
        targets=(db.query(Submission.id,Submission.state)
          .join(PackageRevision,PackageRevision.id==Submission.package_revision_id)
          .join(DataPackage,DataPackage.id==PackageRevision.package_id)
          .filter(DataPackage.domain=='drug_trace',Submission.state.in_(['UNKNOWN','ACCEPTED']))
          .order_by(Submission.state,Submission.created_at,Submission.id).all())
        assert Counter(x.state for x in targets)==Counter({'UNKNOWN':206,'ACCEPTED':41}),'TARGET_SET_DRIFT'
        control=(db.query(Submission.id,Submission.state).join(PackageRevision,PackageRevision.id==Submission.package_revision_id)
          .join(DataPackage,DataPackage.id==PackageRevision.package_id)
          .filter(DataPackage.domain=='drug_trace',Submission.state=='CONFIRMED')
          .order_by(Submission.created_at).first())
        if mode=='sample':
            unknown=[x for x in targets if x.state=='UNKNOWN']
            accepted=[x for x in targets if x.state=='ACCEPTED']
            targets=[control,unknown[0],unknown[-1],accepted[0]]
        else:targets=[control]+targets
        targets=[{'id':r.id,'state':r.state} for r in targets]
    print(json.dumps({'event':'start','mode':mode,'targets':len(targets),'started_utc':started}),flush=True)
    for index,target in enumerate(targets):
        with sf() as db:
            readonly(db)
            sub=db.get(Submission,target['id']);pr=db.get(PackageRevision,sub.package_revision_id)
            dest=db.get(Destination,sub.destination_id);payload=pr.payload or {}
            assert dest is not None and not dest.is_mock and dest.status=='ENABLED'
            assert sub.tenant_id==pr.tenant_id==dest.tenant_id
            profile=(pr.schema_ref or {}).get('profile','')
            pinned=str((pr.provenance or {}).get('config_release_id') or '')
            S.require_production_evidence(db,sub.tenant_id,profile=profile,capability='query',destination_id=dest.id)
            client=S.build_client(db,sub.tenant_id,dest,config_release_id=pinned or None,profile=profile,purpose='query')
            guarded=QueryOnlyTransport(client.transport);client.transport=guarded
            cfg=S._load_config_revision(db,sub.tenant_id,pinned or dest.config_revision,owner_id=dest.id,purpose='query')
            semantics=S._query_date_semantics(cfg.content_json)
            assert payload.get('ref_ent_id') and payload.get('bill_code') and payload.get('query_bill_type')
            full=S._query_full_windows(client,{'ref_ent_id':str(payload['ref_ent_id']),'bill_code':str(payload['bill_code']),'bill_type':str(payload['query_bill_type'])},payload,sub.created_at,semantics)
            cls=S._classify_query(full,str(payload['bill_code']))
            row={'index':index,'submission_id':sub.id,'local_state':sub.state,'bill_code_sha256':hashlib.sha256(str(payload['bill_code']).encode()).hexdigest(),
              'platform_state':cls.get('state'),'result_type':cls.get('result_type'),'sub_process_flag':cls.get('sub_process_flag'),
              'query_status':full.get('status'),'total_num':full.get('total_num'),'pages':full.get('pages_read'),'windows':full.get('windows_queried'),
              'reason':cls.get('reason'),'error_code':(cls.get('error') or {}).get('code'),'msg_code':(cls.get('error') or {}).get('msg_code'),
              'platform_calls':guarded.calls,'http':dict(guarded.http),'checked_utc':datetime.now(timezone.utc).isoformat()}
            rows.append(row);print(json.dumps({'event':'result','data':row}),flush=True)
        if index==0 and row['platform_state']!='CONFIRMED':raise RuntimeError('KNOWN_SUCCESS_CONTROL_NOT_CONFIRMED')
        if row['platform_state'] in ('QUERY_ERROR','UNKNOWN','MANUAL_REVIEW'):raise RuntimeError('UNVERIFIED_QUERY_STOP')
        if mode=='sample':time.sleep(2)
    with sf() as db:
        readonly(db);after=snapshot(db)
    summary={'ok':True,'mode':mode,'started_utc':started,'finished_utc':datetime.now(timezone.utc).isoformat(),
      'target_counts':dict(Counter(r['local_state'] for r in rows)),
      'platform_counts':dict(Counter(r['platform_state'] for r in rows)),
      'pairs':{a+'->'+b:n for (a,b),n in Counter((r['local_state'],r['platform_state']) for r in rows).items()},
      'platform_calls':sum(r['platform_calls'] for r in rows),'ledger_unchanged':before==after,
      'uploads':0,'his_calls':0,'poll_budget_reset':False,'business_writes':0}
    assert before==after,'LEDGER_CHANGED_DURING_MANUAL_REVIEW'
except Exception as exc:
    summary={'ok':False,'mode':mode,'error_type':type(exc).__name__,'error_code':str(exc) if isinstance(exc,(RuntimeError,AssertionError)) else '',
      'completed':len(rows),'uploads':0,'his_calls':0,'business_writes':0}
finally:engine.dispose()
print('__MANUAL_REVIEW__'+json.dumps(summary)+'__END__',flush=True)
if not summary.get('ok'):sys.exit(1)
