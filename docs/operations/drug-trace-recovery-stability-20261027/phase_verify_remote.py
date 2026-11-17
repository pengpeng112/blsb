from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,re,ssl,socket,subprocess,http.client

def run(args,data=None):
    p=subprocess.run(args,input=data,capture_output=True,timeout=30)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.decode().strip()

if Path('/opt/blyb/current').resolve().name!='stability-20261027T064000Z-r3':raise RuntimeError('WRONG_RELEASE')
baseline=json.loads(Path('/root/blyb-maintenance-backups/upgrade-stability-20261027T064000Z-r3/baseline.json').read_text())
token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
code='''import hashlib,json,sys,urllib.request
import contracts.passwords
t=sys.stdin.readline().strip()
r=urllib.request.Request('http://localhost:8000/api/v1/drug-trace/send-scope',headers={'Authorization':'Bearer '+t})
with urllib.request.urlopen(r,timeout=20) as response:
 d=json.load(response)['data']
 assert response.status==200 and d['evidence']['status']=='APPROVED'
 print(json.dumps({'legacy_scope_status':response.status,'evidence_status':d['evidence']['status'],'quota':d['quota'],'password_module_sha256':hashlib.sha256(open(contracts.passwords.__file__,'rb').read()).hexdigest()}))
'''
out=json.loads(run(['docker','exec','-i','blyb-core-1','python','-c',code],(token+'\n').encode()));del token
sql="""SELECT json_build_object(
'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
'ledger_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text||':'||snapshot_digest,',' ORDER BY id)) FROM submission),
'auth_migration',(SELECT count(*) FROM schema_migrations WHERE step=13 AND name='013_console_password_sessions'))"""
after=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))
out.update(old_205_unchanged=after['protected_old_hash']==baseline['protected_old_hash'],checkpoint_unchanged=after['checkpoint']==baseline['checkpoint'],quota_unchanged=after['quota']==baseline['quota'],auth_migration=after['auth_migration'])
assert out['old_205_unchanged'] and out['checkpoint_unchanged'] and out['quota_unchanged'] and out['auth_migration']==1
out['all_4818_submissions_unchanged']=after['ledger_hash']==baseline['ledger_hash'];assert out['all_4818_submissions_unchanged']
out['nginx_config']=run(['docker','exec','blyb-nginx-1','nginx','-t'])
context=ssl.create_default_context(cafile='/etc/blyb/certs/tls.crt')
def get(path):
    with socket.create_connection(('<prod-host>',443),timeout=8) as tcp:
        with context.wrap_socket(tcp,server_hostname='blyb-<prod-host>') as tls:
            tls.sendall(('GET '+path+' HTTP/1.1\r\nHost: <prod-host>\r\nConnection: close\r\n\r\n').encode())
            response=http.client.HTTPResponse(tls);response.begin();return response.status,response.read()
status,html=get('/ui/');assets=re.findall(r'(?:src|href)="([^"]+assets/[^"]+)"',html.decode())
out['ui']={'status':status,'assets':assets,'asset_statuses':[get(path)[0] for path in assets],'trust':'existing explicit certificate trust and CN; IP SAN and management network still not accepted'}
assert status==200 and all(x==200 for x in out['ui']['asset_statuses'])
container_dist='/app/apps/admin_web/dist'
out['ui']['index_matches_release']=hashlib.sha256(html).hexdigest()==run(['docker','exec','blyb-core-1','sha256sum',container_dist+'/index.html']).split()[0]
out['ui']['asset_hashes_match']=all(hashlib.sha256(get(path)[1]).hexdigest()==run(['docker','exec','blyb-core-1','sha256sum',container_dist+'/'+path.split('/ui/',1)[-1]]).split()[0] for path in assets)
out['ui']['new_overview_present']=any('上报总览'.encode() in get(path)[1] for path in assets if path.endswith('.js'))
assert out['ui']['index_matches_release'] and out['ui']['asset_hashes_match'] and out['ui']['new_overview_present']
out['ui']['collection_failure_present']=any('等待采集完成超时'.encode() in get(path)[1] and '未统计'.encode() in get(path)[1] for path in assets if path.endswith('.js'));assert out['ui']['collection_failure_present']
out['containers']=run(['docker','ps','--format','{{.Names}} {{.Status}}'])
assert sum('healthy' in x for x in out['containers'].splitlines())==6
out['timer']=run(['systemctl','show','blyb-daily-upload.timer','-p','ActiveState','-p','SubState'])
assert 'ActiveState=active' in out['timer']
out['utc']=datetime.now(timezone.utc).isoformat()
Path('/root/blyb-maintenance-backups/upgrade-stability-20261027T064000Z-r3/ui-verified.json').write_text(json.dumps(out,indent=2)+'\n')
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
