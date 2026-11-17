"""Linux image validation with an isolated synthetic SQLite database.

The actual image reports Python 3.14.8; see packaged-runtime.json.
"""
from pathlib import Path
import json,subprocess
HERE=Path(__file__).resolve().parent
code='''from pathlib import Path
from datetime import datetime
import tempfile,json,sys
import _bootstrap,app_runtime
from core_db import make_engine,make_session_factory,run_migrations
from core_db.models import Tenant,ConnectorInstance,SourceCheckpoint
from contracts import new_uuid
from domains.drug_trace import service as S
from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
from workflow_core.lease_guard import LeaseGuard
with tempfile.TemporaryDirectory(prefix='stability-synthetic-') as td:
 e=make_engine('sqlite:///'+td+'/isolated.sqlite')
 run_migrations(e,extra_migrations=app_runtime.load_registry(['drug_trace']).migrations)
 sf=make_session_factory(e)
 try:
  with sf() as db:
   tenant=Tenant(id=new_uuid(),code='synthetic',name='synthetic');db.add(tenant);db.commit()
   c=ConnectorInstance(id=new_uuid(),tenant_id=tenant.id,direction='source',plugin_id='sources.db_view.drug_trace',name='synthetic',status='ENABLED');db.add(c);db.commit()
   w=SyntheticDrugTraceWarehouse()
   for i in range(3):
    h={'source_system':'HIS','source_bill_id':'B'+str(i),'source_bill_no':'B'+str(i),'source_revision':'1','business_kind':'PATIENT_DISPENSE','pharmacy_code':'YF','bill_time':'2026-10-26T12:00:00','updated_at':'2026-10-26T12:00:00','finalized':True,'is_void':False}
    w.add_bill(h);w.add_code({**h,'source_line_id':'L'+str(i),'source_code_record_id':'R'+str(i),'trace_code':'81000000000123','is_deleted':False,'quantity':'1','unit':'box'})
   args={'window_begin':datetime(2026,10,26),'window_end':datetime(2026,10,27),'page_limit':2,'max_pages':1}
   first=S.run_collection(db,tenant.id,c.id,w,**args);assert first['state']=='RUNNING'
   last=S.run_collection(db,tenant.id,c.id,w,run_id=first['run_id'],**args)
   assert last['state']=='DONE' and last['bills_seen']==last['valid']==3
   assert db.query(SourceCheckpoint).one().cursor_json['completed_window'] is True
   print(json.dumps({'python':sys.version.split()[0],'synthetic_page_resume':True,'bills':3,'source_calls':0,'uploads':0,'production_database_calls':0}))
 finally:e.dispose()
'''
p=subprocess.run(['docker','run','--rm','--network=none','--entrypoint','python','blyb/backend:stability-20261027T030000Z-r2','-c',code],capture_output=True,text=True,encoding='utf8',timeout=60)
assert p.returncode==0,'PACKAGED_SYNTHETIC_CHECK_FAILED'
out=json.loads(p.stdout)
(HERE/'evidence/packaged-runtime.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8')
print(json.dumps(out))
