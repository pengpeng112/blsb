"""Read-only deployment review. No HIS login, configuration write, or upload."""
from pathlib import Path
import base64
import importlib.util
import json
import shlex
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


WORKER = r'''
import os,json,ssl,http.client
from sources.db_view.oracle_client import ensure_oracle_client,oracle_client_status
from destinations.alihealth_drugtrace.transport import RealTransport
from destinations.alihealth_drugtrace.protocol import assert_signing_allowed,SIGNATURE_VERIFIED
out={"uid":os.getuid(),"secret_present":bool(os.getenv("BLYB_ALIHEALTH_APP_SECRET")),"oracle_dsn_present":bool(os.getenv("BLYB_DRUGTRACE_ORACLE_DSN")),"plugins":os.getenv("BLYB_PLUGINS"),"signature_verified":SIGNATURE_VERIFIED}
out["client_key_readable"]=os.access('/etc/blyb/certs/client.key',os.R_OK)
out["client_key_writable"]=os.access('/etc/blyb/certs/client.key',os.W_OK)
try:
 ensure_oracle_client();out["oracle_client"]=oracle_client_status()
except Exception as exc:out["oracle_client_error"]=type(exc).__name__
ctx=ssl.create_default_context(cafile='/etc/blyb/certs/dmz-ca.crt')
ctx.load_cert_chain('/etc/blyb/certs/client.crt','/etc/blyb/certs/client.key')
conn=http.client.HTTPSConnection('<relay-net-host>',443,context=ctx,timeout=8)
try:
 conn.request('GET','/health');resp=conn.getresponse();body=resp.read(8192)
 out['mtls_health']={'http_status':resp.status,'body':json.loads(body)}
except Exception as exc:out['mtls_health']={'error_type':type(exc).__name__}
finally:conn.close()
transport=RealTransport('https://<relay-net-host>',ca_file='/etc/blyb/certs/dmz-ca.crt',cert_file='/etc/blyb/certs/client.crt',key_file='/etc/blyb/certs/client.key',allow_hosts=('<relay-net-host>',))
try:
 assert_signing_allowed(transport,os.getenv('BLYB_ALIHEALTH_APP_SECRET',''));out['real_signing_gate']='ALLOWED'
except Exception as exc:
 out['real_signing_gate']='BLOCKED';out['gate_error_type']=type(exc).__name__
print(json.dumps(out))
'''

CORE = r'''
import json
from apps.init_drug_trace import _connect,_tenant,check_state
from domains.drug_trace.models import DrugTraceBill,DrugTraceBillRevision,DrugTraceProductionEvidence,DrugTraceCollectionRun,DrugTraceSendQuota
from core_db.models import Submission
sf,engine=_connect()
with sf() as db:
 from sqlalchemy import text
 db.execute(text('SET TRANSACTION READ ONLY'))
 tenant=_tenant(db,'first-hospital')
 out={'tenant':tenant.code,'state':check_state(db,tenant.id)}
 out['counts']={cls.__name__:db.query(cls).filter_by(tenant_id=tenant.id).count() for cls in (DrugTraceBill,DrugTraceBillRevision,DrugTraceProductionEvidence,DrugTraceCollectionRun,DrugTraceSendQuota)}
 out['evidence_statuses']=[{'capability':e.capability,'status':e.status} for e in db.query(DrugTraceProductionEvidence).filter_by(tenant_id=tenant.id).all()]
 # Only aggregate counts; never print patient, bill, code, or payload columns.
 out['all_domain_submission_count']=db.query(Submission).filter_by(tenant_id=tenant.id).count()
 print(json.dumps(out,default=str))
engine.dispose()
'''


def run_target():
    probe = load('review_credentials', 'docs/deployment/reporting-20261015/probe_ssh.py')
    deploy = load('review_jump', 'docs/deployment/reporting-20261015/deploy_remote.py')
    profiles = probe.credentials()
    script = r'''
import json,socket,subprocess,time
out={}
def run(args,timeout=20):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
 return {'exit_code':p.returncode,'stdout':p.stdout}
out['release']=run(['readlink','-f','/opt/blyb/current'])
out['services']=run(['docker','ps','--format','{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'])
out['clock_epoch']=time.time()
out['tcp']=[]
for host,port in [('<relay-net-host>',443),('<his-host>',1521)]:
 s=socket.socket();s.settimeout(5);s.bind(('<prod-host>',0));r={'source':'<prod-host>','destination':host,'port':port}
 try:s.connect((host,port));r['connected']=True
 except OSError as exc:r.update(connected=False,error_type=type(exc).__name__)
 finally:s.close()
 out['tcp'].append(r)
out['worker']=run(['docker','exec','<worker-container>','python','-c',WORKER],40)
out['application_metadata']=run(['docker','exec','blyb-core-1','python','-c',CORE],30)
out['migrations']=run(['docker','exec','blyb-db-1','psql','-U','blyb','-d','blyb','-At','-c','BEGIN READ ONLY; SELECT step,name FROM schema_migrations ORDER BY step; ROLLBACK;'])
print('__BLYB_DATA_BEGIN__'+json.dumps(out)+'__BLYB_DATA_END__')
'''
    script = 'WORKER=' + repr(WORKER) + '\nCORE=' + repr(CORE) + '\n' + script
    code = base64.b64encode(script.encode()).decode()
    command = 'python3 -c ' + shlex.quote("import base64;exec(base64.b64decode('" + code + "'))")
    jump = deploy.connect_jump(profiles['jump'])
    try:
        rc, transcript = deploy.nested_exec(jump, profiles, command, timeout=110)
        if '__BLYB_DATA_BEGIN__' not in transcript or '__BLYB_DATA_END__' not in transcript:
            return {'ssh_exit_code': rc, 'error': 'NO_STRUCTURED_RESULT'}
        out = json.loads(transcript.split('__BLYB_DATA_BEGIN__', 1)[1].split('__BLYB_DATA_END__', 1)[0])
        # Decode the explicitly allowlisted container outputs only.
        for key in ('worker', 'application_metadata'):
            if out[key]['exit_code'] == 0:
                out[key]['data'] = json.loads(out[key].pop('stdout'))
        return {'ssh_exit_code': rc, 'inventory': out}
    finally:
        jump.close()


def run_dmz():
    helper = load('review_dmz', 'docs/deployment/drug-trace-install-20261021/deploy_dmz.py')
    client = helper.connect()
    script = r'''
import json,time,os,subprocess
c=json.load(open('/etc/blyb-dmz/forwarder.json'))
out={'clock_epoch':time.time(),'release':os.path.realpath('/opt/blyb-dmz/current'),'config':{k:c.get(k) for k in ('listen_host','listen_port','allow_ips','allowed_top_methods','enable_inout_upload')}}
for key,args in [('units',['systemctl','show','blyb-dmz-forwarder.socket','blyb-dmz-forwarder.service','-p','ActiveState','-p','User']),('ntp',['timedatectl','status']),('upstream',['curl','-4','--noproxy','*','--silent','--show-error','--connect-timeout','5','--max-time','15','--head','--output','/dev/null','--write-out','%{http_code}|%{remote_ip}|%{ssl_verify_result}','https://eco.taobao.com/router/rest'])]:
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=20);out[key]={'exit_code':p.returncode,'stdout':p.stdout}
print(json.dumps(out))
'''
    code = base64.b64encode(script.encode()).decode()
    command = '/opt/blyb-dmz/runtime/bin/python3.11 -c ' + shlex.quote("import base64;exec(base64.b64decode('" + code + "'))")
    try:
        rc, stdout, _ = helper.run(client, command, timeout=35)
        return {'ssh_exit_code': rc, 'inventory': json.loads(stdout) if rc == 0 else None}
    finally:
        client.close()


def run_followup():
    """Correct the ledger column and use the systemd-219 time-status syntax."""
    probe = load('followup_credentials', 'docs/deployment/reporting-20261015/probe_ssh.py')
    deploy = load('followup_jump', 'docs/deployment/reporting-20261015/deploy_remote.py')
    profiles = probe.credentials()
    jump = deploy.connect_jump(profiles['jump'])
    try:
        rc, transcript = deploy.nested_exec(jump, profiles,
            "docker exec blyb-db-1 psql -U blyb -d blyb -At -c 'BEGIN READ ONLY; SELECT step,name FROM schema_migrations ORDER BY step; ROLLBACK;'", timeout=30)
        # Ledger-only query: output is constrained to migration names and versions.
        out = {'ssh_exit_code': rc, 'migration_ledger': transcript}
    finally:
        jump.close()
    helper = load('followup_dmz', 'docs/deployment/drug-trace-install-20261021/deploy_dmz.py')
    client = helper.connect()
    try:
        rc, stdout, _ = helper.run(client, 'LC_ALL=C timedatectl status', timeout=15)
        out['ntp'] = {'exit_code': rc, 'stdout': stdout}
    finally:
        client.close()
    return out


def main():
    target = sys.argv[1]
    if target not in ('target', 'dmz', 'followup'):
        raise SystemExit('target, dmz or followup required')
    out_path = HERE / 'evidence' / (target + '.json')
    out_path.parent.mkdir(exist_ok=True)
    receipt = {'captured_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
               'remote_config_changes': 0, 'his_queries': 0, 'upload_requests': 0}
    with out_path.open('x', encoding='utf-8') as f:
        json.dump({'status': 'RESERVED'}, f)
    try:
        receipt.update({'target': run_target, 'dmz': run_dmz, 'followup': run_followup}[target]())
        receipt['status'] = 'CAPTURED' if receipt['ssh_exit_code'] == 0 else 'REMOTE_ERROR'
    except Exception as exc:
        receipt.update(status='CHECK_FAILED', error_type=type(exc).__name__)
    out_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt['status'] == 'CAPTURED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
