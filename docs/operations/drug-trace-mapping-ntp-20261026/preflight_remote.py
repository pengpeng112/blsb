from pathlib import Path
import json,subprocess

t=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import collections,json,sys,urllib.request
from core_db import make_engine,make_session_factory
from core_db.models import Tenant,Submission,PackageRevision
from domains.drug_trace.models import DrugTraceBill,DrugTraceBillRevision
from domains.drug_trace.payload import lookup_mapping
from domains.drug_trace import contracts as C
from sqlalchemy import text
t=sys.stdin.readline().strip()
req=urllib.request.Request('http://localhost:8000/api/v1/auth/login',data=json.dumps({'token':t}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=15) as r:login=json.load(r)['data']
req=urllib.request.Request('http://localhost:8000/api/v1/drug-trace/collection-runs?limit=200',headers={'Authorization':'Bearer '+t})
with urllib.request.urlopen(req,timeout=20) as r:runs=json.load(r)['data']
engine=make_engine();db=make_session_factory(engine)()
out={'login':{k:login[k] for k in ['code','display_name']},'windows':{},'source_database_queries':0,'platform_calls':0}
try:
 tenant=db.query(Tenant).filter_by(code='first-hospital').one()
 for day,end in [('2026-10-25','2026-10-26'),('2026-10-24','2026-10-25')]:
  run=next(r for r in runs if r.get('window')==[day+'T00:00:00',end+'T00:00:00'])
  assert run['state']=='DONE'
  rows=db.execute(text("""SELECT b.pharmacy_code,count(*) FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
   WHERE b.tenant_id=:t AND b.connector_id=:c AND b.bill_time>=:w AND b.bill_time<:e
    AND b.business_kind='PATIENT_DISPENSE' AND b.finalized=true AND b.is_void=false AND r.quality_state='VALID'
    AND NOT EXISTS(SELECT 1 FROM submission s JOIN package_revision pr ON pr.id=s.package_revision_id WHERE s.tenant_id=b.tenant_id AND pr.provenance->>'bill_revision_id'=r.id AND s.destination_id=:d)
   GROUP BY b.pharmacy_code"""),{'t':tenant.id,'c':'7d51abd7-3f32-4c8e-b2fa-9b652a511808','w':day,'e':end,'d':'345df8f4-410e-4c89-a258-a34957eb4151'}).fetchall()
  counts={p:n for p,n in rows}
  assert set(counts)<=set(['050121','050116'])
  for pharmacy in counts:
   value=lookup_mapping(db,tenant.id,C.DICT_NS_REF_ENT_ID,pharmacy)
   assert value==lookup_mapping(db,tenant.id,C.DICT_NS_REF_ENT_ID,'050102')
  out['windows'][day]={'collection_id':run['id'],'collection_state':run['state'],'unsent_no_submission_by_pharmacy':counts,'missing_mappings':0}
finally:db.close();engine.dispose()
print(json.dumps(out))
'''
p=subprocess.run(['docker','exec','-i','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-core-1','python','-c',code],input=(t+'\n').encode(),capture_output=True,timeout=60)
del t
if p.returncode:raise RuntimeError('PREFLIGHT_FAILED_'+str(p.returncode))
out=json.loads(p.stdout)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
