from pathlib import Path
from datetime import datetime,timezone
import json,os,subprocess

token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import hashlib,json,sys,urllib.request
from core_db import make_engine,make_session_factory
from core_db.models import Tenant
from domains.drug_trace.payload import lookup_mapping_with_rev
t=sys.stdin.readline().strip()
def call(path,body=None):
 req=urllib.request.Request('http://localhost:8000/api/v1'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+t,'Content-Type':'application/json'})
 with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)['data']
ns='drugtrace.ref_ent_id';version='hospital-pharmacies-20261026-r1'
before=[d for d in call('/dictionaries') if d['namespace']==ns]
base=next(d for d in before if d['id']=='e9219d75-ddf2-4adb-bea0-97174a918d44' and d['approved_by'])
assert len(base['items'])==1 and base['items'][0]['code']=='050102'
ref=base['items'][0]['target']
items=list(base['items'])+[{'code':c,'target':ref,'label':'Hospital pharmacy confirmed by hospital user 2026-10-26','evidence':'docs/operations/drug-trace-mapping-ntp-20261026/README.md; explicit user confirmation: both pharmacies belong to this hospital'} for c in ['050121','050116']]
found=next((d for d in before if d['version']==version),None)
if found:
 assert found['approved_by'] and found['items']==items
 result={'dictionary_id':found['id'],'already_created':True}
else:result=call('/dictionaries',{'namespace':ns,'version':version,'items':items,'approve':True})
after=[d for d in call('/dictionaries') if d['namespace']==ns]
assert all(d in after for d in before),'Original dictionary modified'
engine=make_engine();db=make_session_factory(engine)()
try:
 tenant=db.query(Tenant).filter_by(code='first-hospital').one()
 checks={}
 for c in ['050102','050121','050116']:
  value,rid,v=lookup_mapping_with_rev(db,tenant.id,ns,c)
  assert value==ref and v==version
  checks[c]={'version':v,'revision_id':rid,'matches_existing_hospital':True}
finally:db.close();engine.dispose()
print(json.dumps({'result':result,'version':version,'checks':checks,'original_dictionaries_unchanged':True,'before':before,'after':after,'send_or_query_calls':0}))
'''
p=subprocess.run(['docker','exec','-i','blyb-core-1','python','-c',code],input=(token+'\n').encode(),capture_output=True,timeout=70)
del token
if p.returncode:raise RuntimeError('MAPPING_API_OR_VERIFY_EXIT_'+str(p.returncode))
out=json.loads(p.stdout)
folder=Path('/root/blyb-maintenance-backups/mapping-20261026');folder.mkdir(mode=0o700,exist_ok=True)
name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json'
f=folder/name;f.write_text(json.dumps(out,indent=2)+'\n');os.chmod(str(f),0o600)
out.pop('before');out.pop('after');out['evidence_backup']=str(f)
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
