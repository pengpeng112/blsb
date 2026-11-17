from pathlib import Path
import hashlib,json,subprocess
def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=30)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()
out={'release':Path('/opt/blyb/current').resolve().name,
     'containers':run(['docker','ps','--format','{{.Names}} {{.Status}}']),
     'units':run(['systemctl','show','blyb-mapping-recovery-20261026.service','blyb-daily-upload.service','blyb-daily-upload.timer','-p','Id','-p','ActiveState','-p','SubState','-p','ExecMainStatus']),
     'ntp':run(['chronyc','-n','sources'])}
folder=Path('/var/log/blyb/mapping-recovery-20261026')
for name in ['progress.json','final.json','error.json']:
    if (folder/name).exists():out[name]=json.loads((folder/name).read_text())
resumed=Path('/var/log/blyb/mapping-recovery-auth-20261026')
if resumed.exists():
    out['resumed_unit']=run(['systemctl','show','blyb-mapping-recovery-auth-20261026.service','-p','ActiveState','-p','SubState','-p','ExecMainStatus'])
    out['resumed']={name:json.loads((resumed/name).read_text()) for name in ['baseline.json','progress.json','final.json','error.json'] if (resumed/name).exists()}
sql="""SELECT json_build_object('states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
'pending_sends',(SELECT count(*) FROM job WHERE job_type='drug_trace.send' AND state IN('PENDING','LEASED','RETRY_WAIT')),
'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x))"""
out['db']=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))
if resumed.exists() and (resumed/'baseline.json').exists():
    baseline=json.loads((resumed/'baseline.json').read_text())
    out['db']['old_247_unchanged']=out['db'].pop('protected_old_hash')==baseline['protected_old_hash']
    out['db']['checkpoint_unchanged']=out['db']['checkpoint']==baseline['checkpoint']
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
