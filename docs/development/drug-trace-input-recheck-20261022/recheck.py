"""Read-only recheck; one bill-status query, no uploads or runtime changes."""
from pathlib import Path
import base64
import importlib.util
import json
import shlex
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def metadata():
    sjzc = load('current_sjzc', Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    queries = {
        'his_source_registration': "SELECT source_code,system_code,db_type,target_host,port,service_name,enabled,write_policy,updated_at FROM asset.asset_data_sources WHERE source_code='his_source_<his_host_id>' LIMIT 1",
        'same_endpoint_sources': "SELECT source_code,system_code,target_host,port,service_name,enabled,write_policy FROM asset.asset_data_sources WHERE target_host='<his-host>' AND port=1521 ORDER BY source_code LIMIT 20",
        'contract_view_catalog': "SELECT system_code,source_code,schema_name,table_name,column_count,updated_at FROM asset.asset_tables WHERE system_code IN ('HIS','HIS_SOURCE') AND table_name IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE') ORDER BY schema_name,table_name LIMIT 20",
    }
    result = {'channel': 'sjzc/<relay-jump>/platform_metadata', 'credential_contents_read': False, 'queries': {}}
    for key, sql in queries.items():
        try:
            output = sjzc._ssh_psql('SELECT row_to_json(t) FROM ('+sql+') t')
            result['queries'][key] = {'sql': sql, 'rows': [json.loads(line) for line in output.splitlines() if line], 'status': 'SUCCESS'}
        except Exception as exc:
            result['queries'][key] = {'status': 'ERROR', 'error_type': type(exc).__name__}
    rows = result['queries']['his_source_registration'].get('rows', [])
    result['his_live_attempted'] = False
    result['source_policy_meets_readonly_requirement'] = bool(rows and rows[0].get('write_policy') == 'readonly')
    return result


WORKER = r'''
import os,json,time
from datetime import datetime,timedelta,timezone
from uuid import uuid4
from sqlalchemy import text
from core_db import make_engine,make_session_factory
from core_db.models import Tenant,Destination,ConfigRevision,DictionaryRevision,ConnectorInstance,Submission
from domains.drug_trace.models import DrugTraceBill,DrugTraceProductionEvidence,DrugTraceSendQuota,DrugTraceCollectionRun
from domains.drug_trace import service as S
from destinations.alihealth_drugtrace.protocol import SIGNATURE_VERIFIED
engine=make_engine();sf=make_session_factory(engine)
out={'uid':os.getuid(),'clock_epoch':time.time(),'signature_verified':SIGNATURE_VERIFIED,'oracle_dsn_present':bool(os.getenv('BLYB_DRUGTRACE_ORACLE_DSN')),'app_secret_present':bool(os.getenv('BLYB_ALIHEALTH_APP_SECRET'))}
with sf() as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 tenant=db.query(Tenant).filter_by(code='first-hospital').one()
 dest=db.query(Destination).filter_by(tenant_id=tenant.id,code='alihealth-drugtrace-retail').one()
 cfg=db.get(ConfigRevision,dest.config_revision) if dest.config_revision else None
 out['destination']={'code':dest.code,'status':dest.status,'is_mock':dest.is_mock,'config_revision':dest.config_revision,'config_state':cfg.state if cfg else None,'config_type':cfg.config_type if cfg else None}
 out['connectors']=[{'status':c.status,'active_config':bool(c.active_config_revision)} for c in db.query(ConnectorInstance).filter_by(tenant_id=tenant.id,plugin_id='sources.db_view.drug_trace').all()]
 out['dictionaries']=[{'namespace':d.namespace,'version':d.version,'approved':bool(d.approved_by),'item_count':len(d.items or [])} for d in db.query(DictionaryRevision).filter_by(tenant_id=tenant.id).all() if d.namespace.startswith('drugtrace.')]
 out['counts']={cls.__name__:db.query(cls).filter_by(tenant_id=tenant.id).count() for cls in (DrugTraceBill,DrugTraceProductionEvidence,DrugTraceSendQuota,DrugTraceCollectionRun,Submission)}
 out['evidence']={cap:S.evidence_state(db,tenant.id,profile='alihealth_yljg_retail_v1',capability=cap,destination_id=dest.id,config_release_id=dest.config_revision if cap=='send' else None) for cap in ('collect','send','query','reconcile')}
 # Keep statuses and missing keys only; no evidence contents or business data.
 out['evidence']={cap:{'status':v.get('status'),'missing':[x.get('key') if isinstance(x,dict) else str(x) for x in v.get('missing',[])]} for cap,v in out['evidence'].items()}
 if dest.is_mock:raise RuntimeError('Mock destination cannot qualify as a real probe')
 # Match perform_query: destination-scoped query approval; source/send gates stay intact.
 S.require_production_evidence(db,tenant.id,profile='alihealth_yljg_retail_v1',capability='query',destination_id=dest.id)
 client=S.build_client(db,tenant.id,dest,profile='alihealth_yljg_retail_v1',purpose='query')
 cn=datetime.now(timezone(timedelta(hours=8)))
 probe='BLYB_RECHECK_'+uuid4().hex[:14].upper()
 result=client.query_bill_status({'ref_ent_id':REF_ENT_ID,'bill_code':probe,'bill_type':'A','begin_date':cn.strftime('%Y-%m-%d'),'end_date':cn.strftime('%Y-%m-%d'),'page':'1','page_size':'1'})
 out['query']={'method':'query.billstatus','request_count':1,'probe_bill_code':probe,'found':result.get('found'),'total_num':result.get('total_num'),'unknown':result.get('unknown'),'reason':result.get('reason'),'entry_count':len((result.get('bills') or {}).get('list') or [])}
print(json.dumps(out,default=str))
engine.dispose()
'''


def application():
    profiles = load('recheck_creds', ROOT/'docs/deployment/reporting-20261015/probe_ssh.py').credentials()
    deploy = load('recheck_jump', ROOT/'docs/deployment/reporting-20261015/deploy_remote.py')
    profile = json.loads((ROOT/'.ai/private/alihealth-drugtrace-20261020/profile.json').read_text('utf-8'))
    body = 'REF_ENT_ID='+repr(profile['ref_ent_id'])+'\n'+WORKER
    worker = '''import json,traceback
scope={}
try:exec(BODY,scope)
except Exception as exc:
 result=scope.get('out',{})
 result['diagnostic_error']={'type':type(exc).__name__,'code':getattr(exc,'code',None),'frames':[{'file':x.filename.rsplit('/',1)[-1],'line':x.lineno,'name':x.name} for x in traceback.extract_tb(exc.__traceback__)[-4:]]}
 print(json.dumps(result,default=str))
'''.replace('BODY',repr(body))
    remote = r'''
import json,subprocess
def run(args,timeout=30):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
 return {'exit_code':p.returncode,'stdout':p.stdout}
out={'release':run(['readlink','-f','/opt/blyb/current']),'services':run(['docker','ps','--format','{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'])}
out['worker']=run(['docker','exec','<worker-container>','python','-c',WORKER],70)
if out['worker']['exit_code']==0:out['worker']['data']=json.loads(out['worker'].pop('stdout'))
else:out['worker'].pop('stdout',None)
out['ntp']=run(['timedatectl','status'])
print('__BLYB_RECHECK_BEGIN__'+json.dumps(out)+'__BLYB_RECHECK_END__')
'''
    script='WORKER='+repr(worker)+'\n'+remote
    command='python3 -c '+shlex.quote("import base64;exec(base64.b64decode('"+base64.b64encode(script.encode()).decode()+"'))")
    jump=deploy.connect_jump(profiles['jump'])
    try:
        rc,transcript=deploy.nested_exec(jump,profiles,command,timeout=110)
        if '__BLYB_RECHECK_BEGIN__' not in transcript:
            return {'ssh_exit_code':rc,'status':'NO_STRUCTURED_RESULT'}
        raw=transcript.split('__BLYB_RECHECK_BEGIN__',1)[1].split('__BLYB_RECHECK_END__',1)[0]
        result=json.loads(raw)
        return {'ssh_exit_code':rc,'inventory':result}
    finally:jump.close()


def dmz():
    helper=load('recheck_dmz',ROOT/'docs/deployment/drug-trace-install-20261021/deploy_dmz.py')
    client=helper.connect()
    script=r'''
import json,time,subprocess,os
c=json.load(open('/etc/blyb-dmz/forwarder.json'))
out={'clock_epoch':time.time(),'release':os.path.realpath('/opt/blyb-dmz/current'),'config':{k:c.get(k) for k in ('listen_host','listen_port','allow_ips','allowed_top_methods','enable_inout_upload')}}
for key,args in [('ntp',['timedatectl','status']),('ntp_sources',['chronyc','sources']),('upstream_date',['curl','-4','--noproxy','*','--silent','--show-error','--connect-timeout','5','--max-time','15','--head','https://eco.taobao.com/router/rest']),('cert',['openssl','x509','-in','/etc/blyb-dmz/certs/forwarder.crt','-noout','-subject','-issuer','-dates'])]:
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20)
 value=p.stdout
 if key=='upstream_date':value='\n'.join(x for x in value.splitlines() if x.lower().startswith(('http/','date:')))
 out[key]={'exit_code':p.returncode,'stdout':value}
print(json.dumps(out))
'''
    command='/opt/blyb-dmz/runtime/bin/python3.11 -c '+shlex.quote("import base64;exec(base64.b64decode('"+base64.b64encode(script.encode()).decode()+"'))")
    try:
        rc,stdout,_=helper.run(client,command,timeout=55)
        return {'ssh_exit_code':rc,'inventory':json.loads(stdout) if rc==0 else None}
    finally:client.close()


def main():
    mode=sys.argv[1]
    if mode not in ('metadata','application','application-r2','application-r3','dmz'):raise SystemExit('metadata/application/application-r2/application-r3/dmz required')
    path=HERE/'evidence'/(mode+'.json');path.parent.mkdir(exist_ok=True)
    out={'captured_at':datetime.now(timezone.utc).isoformat(),'upload_requests':0,'his_live_queries':0,'remote_config_changes':0,'read_only':True}
    with path.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    try:out.update({'metadata':metadata,'application':application,'application-r2':application,'application-r3':application,'dmz':dmz}[mode]());out.setdefault('status','CAPTURED')
    except Exception as exc:out.update(status='ERROR',error_type=type(exc).__name__)
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out['status']=='CAPTURED' else 1


if __name__=='__main__':raise SystemExit(main())
