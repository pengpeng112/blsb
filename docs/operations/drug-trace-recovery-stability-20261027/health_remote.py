"""Validate upgraded DMZ with production mTLS, without upstream business calls."""
import json, subprocess

code = '''import json,http.client
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,Destination,DataPackage
from domains.drug_trace import service as S
engine=make_engine();sf=make_session_factory(engine)
try:
 with sf() as db:
  db.execute(text('SET TRANSACTION READ ONLY'))
  sub=(db.query(Submission).join(PackageRevision,PackageRevision.id==Submission.package_revision_id).join(DataPackage,DataPackage.id==PackageRevision.package_id).filter(DataPackage.domain=='drug_trace',Submission.state=='CONFIRMED').order_by(Submission.created_at).first())
  pr=db.get(PackageRevision,sub.package_revision_id);dest=db.get(Destination,sub.destination_id)
  client=S.build_client(db,sub.tenant_id,dest,config_release_id=pr.provenance['config_release_id'],profile=pr.schema_ref['profile'],purpose='query')
  t=client.transport
  conn=http.client.HTTPSConnection(t.host,t.port,context=t._ssl_ctx,timeout=15)
  try:
   conn.request('GET','/health');r=conn.getresponse();body=r.read(4096)
   assert r.status==200
   print(json.dumps({'dmz_mtls_health_status':r.status,'health':json.loads(body),'upstream_calls':0,'uploads':0,'business_writes':0}))
  finally:conn.close()
finally:engine.dispose()
'''
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=40)
if p.returncode:raise RuntimeError('DMZ_MTLS_HEALTH_FAILED')
print('__MAINTENANCE__'+json.dumps(json.loads(p.stdout))+'__END__')
