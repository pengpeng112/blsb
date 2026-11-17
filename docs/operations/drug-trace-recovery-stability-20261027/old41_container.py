from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
from urllib.parse import parse_qs
import hashlib,json,sys,time,uuid
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,Destination,DeliveryAttempt,ManualDecision,AuditEvent
from domains.drug_trace import service as S
from destinations.alihealth_drugtrace.protocol import QUERY_METHOD
TARGETS=['0653be8d-7e2d-43fc-9b6d-8e636d3347e8', '0ac5bd41-2b87-4f88-867b-ece7645c3b83', '0ec8390f-ef83-4247-9f85-9491d4761164', '1509ee95-30cc-4f49-b9d3-cda7a61c41d9', '206b3f36-7464-44c8-8f87-63fdce71a291', '234acdeb-cd58-4071-9eed-7135fe84fa7e', '3a699f8b-ea82-4875-91a2-e813808f4f90', '3a709d25-39a0-4714-8ab1-dc64e351ddfa', '47d0937c-c58d-4b57-940a-00cef738674e', '5824801d-d2a4-4d8c-8fb3-5bbc49828d49', '5aaa9241-82f6-4bdb-b47a-608e55fd43ef', '619f00cb-2063-4c3e-952b-d5afdafd4dd1', '6acba7dd-f1b6-47a9-bc71-05bf4c127720', '6f516e22-41c2-4c73-9991-6fbeb849f517', '74b8569c-e12c-42a3-aaac-61a81ea7789e', '7685fae7-8d34-4b6b-b1bd-38a07a961e3f', '845a764a-f037-4b1f-b8fb-170b9aac37cd', '8705879b-e0dd-4d10-bd10-fa4f777c73a5', '87f07b9c-6328-4732-bcab-5c3bdc0b2245', '8a663282-393c-4d5c-87e3-1d207b4a4b82', '8e33c34c-c77b-4c2f-a075-33f17988c86d', '90389da5-a293-481f-a851-422d875d7d33', '91ba4015-98a8-4ac4-a1f2-c88a2f4a724d', '98619f90-addb-4de4-8ab6-ec246bcd5d9a', '9e5eb634-fd15-47bb-a49d-17dbce10e056', '9f14aafe-0ce8-437e-bcee-7cd02221f2e4', 'a37a30ed-12a4-4085-a249-36233d4287e2', 'b9b52293-9180-4cda-915b-3a52707488f3', 'ce8cd60d-b97c-49f9-a14e-8a246fef1af6', 'd7cf0e5a-869b-4694-bb10-2a21f09c547a', 'df3ec371-e7db-47c7-8889-b8846e0f5c49', 'e2ec9e7c-09f7-4db1-8e71-ae8fc3962dc2', 'e368260f-3524-4b4f-a83b-614864a762c4', 'e9b8e8e5-0fdb-405b-83f6-7f9c909326ee', 'ed3f1ffb-c4cd-4c82-9594-5ac0d8e20f2a', 'edd795c2-7c45-45c9-9acd-09b285c1587a', 'f32177ba-e368-450d-8f5d-746bfd201e07', 'f9e2bae1-9fa1-4926-9545-fe0b386aaf06', 'fc8872b4-1f51-421e-be22-e7e9f41d8933', 'fd389858-19de-4d9a-bb1b-bf414f0a1688', 'ff1afbd0-b33f-41d1-9ae4-e0bebe6bc8b2']
engine=make_engine();sf=make_session_factory(engine)
folder=Path('/data/exchange/old41-confirmations-20261027');folder.mkdir(mode=0o700)
def save(data):
 temp=folder/'progress.next.json';temp.write_text(json.dumps(data,indent=2));temp.replace(folder/'progress.json')
def snap(db):
 return dict(db.execute(text("SELECT (SELECT sum(used) FROM drug_trace_send_quota) quota,(SELECT count(*) FROM delivery_attempt) attempts,(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE state='UNKNOWN') unknown_hash")).mappings().one())
class QueryOnly:
 def __init__(self,t):self.t=t;self.calls=0
 def __getattr__(self,n):return getattr(self.t,n)
 def post(self,body,ctype):
  assert parse_qs(body.decode()).get('method')==[QUERY_METHOD],'UPLOAD_BLOCKED'
  self.calls+=1;assert self.calls<=4
  return self.t.post(body,ctype)
def existing_query_count(db,tenant_id,sid):
 # service helper returns the next receipt generation, i.e. existing jobs + 1.
 return S._query_generation(db,tenant_id,sid)-1
state={'task':'DTR-RECOVERY-STABILITY-20261027','started_utc':datetime.now(timezone.utc).isoformat(),'rows':[],'uploads':0,'query_budget_reset':False}
try:
 with sf() as db:before=snap(db)
 for sid in TARGETS:
  with sf() as db:
   sub=db.get(Submission,sid)
   assert sub.state in ('ACCEPTED','CONFIRMED')
   if sub.state=='CONFIRMED':state['rows'].append({'submission_id':sid,'state':'CONFIRMED','already_confirmed':True});continue
   pr=db.get(PackageRevision,sub.package_revision_id);dest=db.get(Destination,sub.destination_id)
   assert sub.tenant_id==pr.tenant_id==dest.tenant_id and not dest.is_mock
   profile=pr.schema_ref['profile'];cfg=pr.provenance['config_release_id']
   S.require_production_evidence(db,sub.tenant_id,profile=profile,capability='query',destination_id=dest.id)
   client=S.build_client(db,sub.tenant_id,dest,config_release_id=cfg,profile=profile,purpose='query')
   t=QueryOnly(client.transport);client.transport=t
   decision=ManualDecision(id=str(uuid.uuid5(uuid.NAMESPACE_URL,'old41-confirm-20261027:'+sid)),tenant_id=sub.tenant_id,entity_ref='submission:'+sid,
     action='CONFIRM_PLATFORM_RECEIPT',before_ref={'state':'ACCEPTED'},after_ref={'state':'PENDING_QUERY'},actor='user-authorized-ai',reason='User approved recommended recovery; query only and confirm actual platform receipt, never resend.')
   db.add(decision);db.commit()
   before_generation=existing_query_count(db,sub.tenant_id,sid)
   assert before_generation==12,'HISTORICAL_QUERY_LEDGER_CHANGED'
   result=S.perform_query(db,sub.tenant_id,sub,client=client);cls=result['classification']
   after_generation=existing_query_count(db,sub.tenant_id,sid)
   assert after_generation==before_generation,'HISTORICAL_QUERY_LEDGER_CHANGED'
   row={'submission_id':sid,'state':sub.state,'result_type':cls.get('result_type'),'sub_process_flag':cls.get('sub_process_flag'),'classification':cls.get('state'),'calls':t.calls,'query_generation_unchanged':True}
   decision.after_ref=row
   db.add(AuditEvent(tenant_id=sub.tenant_id,actor='user-authorized-ai',action='drug_trace.history_receipt.confirm',target='submission:'+sid,
    old_ref={'state':'ACCEPTED'},new_ref=row,reason='Fresh official query; original payload/attempt/query jobs retained.'))
   db.commit();state['rows'].append(row);state['updated_utc']=datetime.now(timezone.utc).isoformat();save(state)
   if row['state']!='CONFIRMED' or cls.get('state')!='CONFIRMED':raise RuntimeError('UNCONFIRMED_PLATFORM_RESULT_STOP')
 with sf() as db:after=snap(db)
 assert before==after,'UNKNOWN_QUOTA_ATTEMPTS_CHANGED'
 state.update(outcome='ALL_41_CONFIRMED',before=before,after=after,finished_utc=datetime.now(timezone.utc).isoformat())
 save(state)
except BaseException as exc:
 state.update(outcome='STOPPED_FOR_REVIEW',error_type=type(exc).__name__);save(state)
finally:engine.dispose()
print('__OLD41__'+json.dumps(state)+'__END__')
if state.get('outcome')!='ALL_41_CONFIRMED':sys.exit(1)
