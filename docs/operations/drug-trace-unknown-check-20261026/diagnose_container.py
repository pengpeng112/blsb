"""One additional status lookup to identify the platform's query rejection."""
import json
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,Destination
from domains.drug_trace import service as S
from destinations.alihealth_drugtrace.protocol import QUERY_METHOD
from urllib.parse import parse_qs
engine=make_engine();sf=make_session_factory(engine)
try:
 with sf() as db:
  db.execute(text('SET TRANSACTION READ ONLY'))
  sub=db.get(Submission,'5aaa9241-82f6-4bdb-b47a-608e55fd43ef')
  assert sub.state=='ACCEPTED'
  pr=db.get(PackageRevision,sub.package_revision_id);dest=db.get(Destination,sub.destination_id)
  profile=pr.schema_ref['profile'];pinned=pr.provenance['config_release_id'];payload=pr.payload
  S.require_production_evidence(db,sub.tenant_id,profile=profile,capability='query',destination_id=dest.id)
  client=S.build_client(db,sub.tenant_id,dest,config_release_id=pinned,profile=profile,purpose='query')
  original=client.transport;errors=[];statuses=[]
  class Guard:
   def post(self,body,ctype):
    assert parse_qs(body.decode())['method']==[QUERY_METHOD]
    result=original.post(body,ctype);statuses.append(result.http_status)
    try:
     error=json.loads(result.body).get('error_response')
     if error:errors.append({k:error.get(k) for k in ['code','msg','sub_code','sub_msg']})
    except (ValueError,TypeError):pass
    return result
  client.transport=Guard()
  full=S._query_full_windows(client,{'ref_ent_id':payload['ref_ent_id'],'bill_code':payload['bill_code'],'bill_type':payload['query_bill_type']},payload,sub.created_at,'both')
  result={'classification':S._classify_query(full,payload['bill_code']),'http':statuses,'top_errors':errors,'business_writes':0,'uploads':0}
finally:engine.dispose()
print('__MANUAL_REVIEW__'+json.dumps(result)+'__END__')
