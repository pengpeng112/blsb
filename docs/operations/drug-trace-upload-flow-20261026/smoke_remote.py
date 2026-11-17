"""Synthetic runtime check + one known-confirmed readonly upstream lookup."""
from pathlib import Path
import subprocess,json,re,hashlib
code='''import json,os,sys,tempfile,io
from pathlib import Path
from unittest.mock import patch
from connector_sdk import RequestDeferred
from destinations.alihealth_drugtrace import transport as T
from destinations.alihealth_drugtrace.rate_control import SharedRequestPacer
out={}
real_directory=os.environ.get('BLYB_DRUGTRACE_RATE_STATE_DIR')
assert real_directory=='/data/exchange/drugtrace-rate'
out['shared_state_directory']=real_directory
with tempfile.TemporaryDirectory(prefix='blyb-flow-synthetic-') as td:
 os.environ['BLYB_DRUGTRACE_RATE_STATE_DIR']=td
 hits=[]
 class Response:
  status=200
  def __init__(self):self.stream=io.BytesIO(b'{"error_response":{"code":7,"msg":"App Call Limited","sub_code":"accesscontrol.limited-by-api-access-count","sub_msg":"This ban will last for 601 more seconds"}}')
  def getheader(self,k,default=None):return default
  def read1(self,n):return self.stream.read(n)
 class Connection:
  sock=None
  def __init__(self,*a,**k):pass
  def request(self,*a,**k):hits.append(1)
  def getresponse(self):return Response()
  def close(self):pass
 with patch.object(T.ssl,'create_default_context',return_value=None),patch.object(T.http.client,'HTTPSConnection',Connection):
  c=T.RealTransport('https://synthetic.invalid')
  response=c.post(b'app_key=synthetic&method=synthetic.query.billstatus','form')
  assert response.rate_limit['retry_after']>=601
  try:c.post(b'app_key=synthetic&method=synthetic.query.billstatus','form')
  except RequestDeferred:pass
  else:raise RuntimeError('SYNTHETIC_COOLDOWN_MISSING')
 assert len(hits)==1
 out['synthetic_top7_blocks_next_request']=True
os.environ['BLYB_DRUGTRACE_RATE_STATE_DIR']=real_directory
from core_db import make_engine,make_session_factory
from sqlalchemy import text
from core_db.models import Submission,PackageRevision,Destination,DataPackage
from domains.drug_trace import service as S
engine=make_engine();sf=make_session_factory(engine)
try:
 with sf() as db:
  db.execute(text('SET TRANSACTION READ ONLY'))
  sub=(db.query(Submission).join(PackageRevision,PackageRevision.id==Submission.package_revision_id).join(DataPackage,DataPackage.id==PackageRevision.package_id).filter(DataPackage.domain=='drug_trace',Submission.state=='CONFIRMED').order_by(Submission.created_at).first())
  pr=db.get(PackageRevision,sub.package_revision_id);dest=db.get(Destination,sub.destination_id)
  payload=pr.payload;profile=pr.schema_ref['profile']
  S.require_production_evidence(db,sub.tenant_id,profile=profile,capability='query',destination_id=dest.id)
  client=S.build_client(db,sub.tenant_id,dest,config_release_id=pr.provenance['config_release_id'],profile=profile,purpose='query')
  assert not dest.is_mock and client.transport._shared_pacer is not None
  result=S._query_full_windows(client,{'ref_ent_id':payload['ref_ent_id'],'bill_code':payload['bill_code'],'bill_type':payload['query_bill_type']},payload,sub.created_at,'both')
  cls=S._classify_query(result,payload['bill_code'])
  out['real_known_bill_query']={'classification':cls,'pages':result.get('pages_read'),'windows':result.get('windows_queried'),'query_status':result.get('status')}
  out['upstream_confirmed']=cls.get('state')=='CONFIRMED'
  out['business_writes']=0;out['uploads']=0;out['his_calls']=0
finally:engine.dispose()
path=Path(real_directory)
out['persistent_state_created']=path.is_dir() and bool(list(path.glob('*.json')))
out['state_directory_mode']=oct(path.stat().st_mode & 0o777) if path.exists() else None
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=150)
if p.returncode:raise RuntimeError('FLOW_RUNTIME_SMOKE_FAILED')
out=json.loads(p.stdout)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
