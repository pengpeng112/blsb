"""Read-only runtime audit. No create/configure/restart or real business submission."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / 'docs/deployment/reporting-20261015'
sys.path.insert(0, str(BASE / 'revision-6'))
import probe_network as nested

nested.REMOTE = r'''
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime,timezone
import json,os,pathlib,socket,ssl,subprocess,time,urllib.request,urllib.error
def run(args,seconds=12):
    try:
        p=subprocess.run(args,capture_output=True,text=True,timeout=seconds)
        return {'exit_code':p.returncode,'stdout':p.stdout[:18000],'stderr':p.stderr[:2500]}
    except Exception as e:return {'error_type':type(e).__name__,'error':str(e)}
def tcp(host,port):
    try:
        with socket.create_connection((host,port),4):pass
        return {'host':host,'port':port,'connected':True}
    except OSError as e:return {'host':host,'port':port,'connected':False,'error_type':type(e).__name__,'error':str(e)}
commands={
 'release': ['readlink','-f','/opt/blyb/current'],
 'release_hashes': ['sh','-c','cd /opt/blyb/current && sha256sum --quiet -c SHA256SUMS'],
 'containers': ['docker','ps','-a','--filter','label=com.docker.compose.project=blyb','--format','{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}'],
 'listeners':['ss','-lnt'],
 'docker_version':['docker','version','--format','{{.Server.Version}}'],
 'compose_version':['docker','compose','version'],
 'time_sync':['timedatectl','show','--property=Timezone','--property=NTP','--property=NTPSynchronized'],
 'disk':['df','-hT','/','/home'],
 'protected_files':['stat','-c','%a %U:%G %n','/etc/blyb/runtime.env','/etc/blyb/certs/tls.key'],
 'certificate':['openssl','x509','-in','/etc/blyb/certs/tls.crt','-noout','-subject','-issuer','-dates','-ext','subjectAltName'],
 'backup_files':['find','<prod-path>/backups','-maxdepth','2','-type','f','-printf','%m %s %p\n'],
 'migration_steps':['docker','exec','blyb-db-1','psql','-U','blyb','-d','blyb','-At','-c','BEGIN READ ONLY; SELECT step,name FROM schema_migrations ORDER BY step; ROLLBACK;'],
 'core_counts':['docker','exec','blyb-db-1','psql','-U','blyb','-d','blyb','-At','-c',"BEGIN READ ONLY; SELECT 'tenant',count(*) FROM tenant UNION ALL SELECT 'connector',count(*) FROM connector_instance UNION ALL SELECT 'destination',count(*) FROM destination UNION ALL SELECT 'submission',count(*) FROM submission UNION ALL SELECT 'data_package',count(*) FROM data_package UNION ALL SELECT 'job',count(*) FROM job; ROLLBACK;"],
 'worker_heartbeat':['docker','exec','<worker-container>','python','-c',"import os,time,json;p='/tmp/<worker-container>-heartbeat.json';print(json.dumps({'exists':os.path.isfile(p),'age_seconds':round(time.time()-os.path.getmtime(p),3) if os.path.isfile(p) else None}))"],
 'core_token_flags':['docker','exec','blyb-core-1','python','-c',"import os,json;v=os.environ.get('BLYB_ENROLL_TOKEN','');print(json.dumps({'disable_dev':os.environ.get('BLYB_DISABLE_DEV_TOKENS')=='1','enroll_present':bool(v),'enroll_is_default':v=='dev-enroll-token'}))"],
 'restart_policy':['sh','-c',"ids=$(docker ps -q --filter label=com.docker.compose.project=blyb); if [ -n \"$ids\" ]; then docker inspect --format '{{.Name}}|restart={{.HostConfig.RestartPolicy.Name}}|count={{.RestartCount}}' $ids; fi"],
}
result={'time_utc':datetime.now(timezone.utc).isoformat(),'checks':{}}
with ThreadPoolExecutor(max_workers=5) as pool:
    fs={pool.submit(run,args):name for name,args in commands.items()}
    for host,port in [('<his-host>',1521),('<relay-net-host>',443),('<relay-net-host>',8443)]:fs[pool.submit(tcp,host,port)]='tcp_'+host+'_'+str(port)
    for f in as_completed(fs):
        name=fs[f]
        try:result['checks'][name]=f.result()
        except Exception as e:result['checks'][name]={'error':str(e)}
env={}
for line in pathlib.Path('/etc/blyb/runtime.env').read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        k,v=line.split('=',1);env[k.strip()]=v.strip().strip('"').strip("'")
ctx=ssl.create_default_context(cafile='/etc/blyb/certs/tls.crt')
def http(path,token=None):
    try:
        headers={'Authorization':'Bearer '+token} if token else {}
        req=urllib.request.Request('https://<prod-host>'+path,headers=headers,method='GET')
        with urllib.request.urlopen(req,context=ctx,timeout=6) as r:
            raw=r.read(80000);data={'status':r.status,'content_type':r.headers.get('Content-Type')}
            if path.endswith(('tenants','connectors','destinations')):
                obj=json.loads(raw);value=obj.get('data');data['count']=len(value) if isinstance(value,list) else None
            return data
    except urllib.error.HTTPError as e:return {'status':e.code}
    except Exception as e:return {'error_type':type(e).__name__,'error':str(e)}
for name,path,token in [('health','/health',None),('ui','/ui/',None),('storage','/storage/health',None),('exchange','/exchange/health',None),('bootstrap_tenants','/api/v1/tenants',env.get('BLYB_BOOTSTRAP_ADMIN_TOKEN')),('dev_rejected','/api/v1/tenants','dev-admin-token'),('connectors','/api/v1/connectors',env.get('BLYB_BOOTSTRAP_ADMIN_TOKEN')),('destinations','/api/v1/destinations',env.get('BLYB_BOOTSTRAP_ADMIN_TOKEN'))]:
    result['checks']['http_'+name]=http(path,token)
encoded=json.dumps(result,ensure_ascii=True)
for key in ['BLYB_DB_PASSWORD','BLYB_BOOTSTRAP_ADMIN_TOKEN','BLYB_ENROLL_TOKEN']:
    if env.get(key):encoded=encoded.replace(env[key],'<redacted>')
print('__NETWORK_JSON_BEGIN__'+encoded+'__NETWORK_JSON_END__',flush=True)
'''


def main():
    login = json.loads((ROOT / '.ai/private/reporting-20261015/deployment-login.private.json').read_text(encoding='utf-8'))
    profiles = {role: {'host': login[role]['host'], 'port': login[role]['port'],
        'user': login[role]['username'], 'password': login[role]['password']} for role in ('jump','target')}
    result = {'task': 'REPORTING-DRUGTRACE-PLAN-20261016', 'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'mode':'read_only_inventory_no_database_source_login_no_business_post', 'target':nested.from_target(profiles)}
    result['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    encoded=json.dumps(result,ensure_ascii=False,indent=2)
    for role in ('jump','target','dmz','his'):encoded=encoded.replace(login[role]['password'],'<redacted>')
    path=Path(__file__).with_name('runtime-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.json')
    path.write_text(encoded+'\n',encoding='utf-8')
    print('Evidence: '+path.relative_to(ROOT).as_posix())
    print(encoded)
    return 0 if result['target'].get('network') else 2


if __name__=='__main__':raise SystemExit(main())
