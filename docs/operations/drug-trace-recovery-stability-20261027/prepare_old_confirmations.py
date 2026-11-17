"""Prepare41 fixed query-only confirmations; execution is gated on recovered batch."""
from pathlib import Path
import csv,json
HERE=Path(__file__).resolve().parent
with (HERE.parent/'drug-trace-unknown-check-20261026/核查明细_最终版_无患者信息.csv').open(encoding='utf-8-sig',newline='') as f:
    targets=[r['submission_id'] for r in csv.DictReader(f) if r['local_state']=='ACCEPTED' and r['platform_state']=='CONFIRMED']
assert len(set(targets))==41
code='''from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
from urllib.parse import parse_qs
import hashlib,json,sys,time,uuid
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Submission,PackageRevision,Destination,DeliveryAttempt,ManualDecision,AuditEvent
from domains.drug_trace import service as S
from destinations.alihealth_drugtrace.protocol import QUERY_METHOD
TARGETS=TARGET_IDS
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
'''.replace('TARGET_IDS',repr(targets))
compile(code,'old41-query-only','exec');(HERE/'old41_container.py').write_text(code,encoding='utf8')
wrapper='''from pathlib import Path
import json,subprocess,re
folder=Path('/root/blyb-maintenance-backups/old41-confirmations-20261027');folder.mkdir(mode=0o700)
source=SOURCE
(folder/'query.py').write_text(source)
with (folder/'stdout.log').open('xb') as out,(folder/'stderr.log').open('xb') as err:
 p=subprocess.run(['docker','exec','-i','<worker-container>','python','-'],input=source.encode(),stdout=out,stderr=err,timeout=1200)
raw=(folder/'stdout.log').read_text();m=re.search(r'__OLD41__(.*?)__END__',raw,re.S)
(folder/'result.json').write_text(json.dumps({'exit_code':p.returncode,'result':json.loads(m.group(1)) if m else {'marker_missing':True}},indent=2))
'''.replace('SOURCE',repr(code))
compile(wrapper,'old41-wrapper','exec')
launch='''from pathlib import Path
import json,subprocess
assert Path('/opt/blyb/current').resolve().name=='stability-20261027T030000Z-r2','RELEASE_DRIFT'
final=json.loads(Path('/var/log/blyb/recovery-stability-20261027-resumed/final.json').read_text())
assert final['outcome']=='ALL_CONFIRMED','RECOVERY_NOT_FINISHED'
assert final['old_246_unchanged'] and final['checkpoint_unchanged'],'PROTECTED_BASELINE_DRIFT'
assert not Path('/root/blyb-maintenance-backups/old41-confirmations-20261027').exists(),'ONE_OFF_ALREADY_USED'
code=WRAPPER
target=Path('/opt/blyb/tools/blyb-old41-confirm-20261027.py');assert not target.exists();target.write_text(code);target.chmod(0o700)
p=subprocess.run(['systemd-run','--unit=blyb-old41-confirm-20261027','--property=RuntimeMaxSec=1500','--property=UMask=0077','/usr/bin/python3',str(target)],capture_output=True)
assert p.returncode==0
print('__MAINTENANCE__'+json.dumps({'launched':True,'targets':41,'uploads_allowed':False})+'__END__')
'''.replace('WRAPPER',repr(wrapper))
compile(launch,'old41-launch','exec');(HERE/'old41_launch_remote.py').write_text(launch,encoding='utf8')
print('Prepared41 fixed query-only confirmations; production recovery-complete gate required')
