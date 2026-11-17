from pathlib import Path
from datetime import datetime,timezone
import json,subprocess

def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=25)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()

folder=Path('/var/log/blyb/mapping-recovery-20261026')
out={'utc':datetime.now(timezone.utc).isoformat(),'units':run(['systemctl','show','blyb-mapping-recovery-20261026.service','blyb-daily-upload.timer','chronyd.service','-p','Id','-p','ActiveState','-p','SubState','-p','ExecMainStatus','-p','UnitFileState']),
     'ntp_sources':run(['chronyc','-n','sources']),'ntp_tracking':run(['chronyc','tracking']),
     'containers':run(['docker','ps','--format','{{.Names}} {{.Status}}'])}
for name in ['progress.json','final.json','error.json']:
    if (folder/name).exists():out[name]=json.loads((folder/name).read_text())
if (folder/'baseline.json').exists():
    baseline=json.loads((folder/'baseline.json').read_text())
    sql="""SELECT json_build_object(
    'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
    'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
    'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
    'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
    'new_pharmacy_submissions',(SELECT json_agg(row_to_json(x)) FROM(
      SELECT pr.payload->>'pharmacy_code' pharmacy,s.state,count(*) n
      FROM submission s JOIN package_revision pr ON pr.id=s.package_revision_id
      WHERE pr.payload->>'profile'='alihealth_yljg_retail_v1' AND pr.payload->>'pharmacy_code' IN('050121','050116')
      GROUP BY 1,2)x),
    'new_pharmacy_attempts',(SELECT json_agg(row_to_json(x)) FROM(
      SELECT a.http_status,a.outcome,count(*) n FROM delivery_attempt a JOIN submission s ON s.id=a.submission_id JOIN package_revision pr ON pr.id=s.package_revision_id
      WHERE a.ended_at IS NOT NULL AND pr.payload->>'pharmacy_code' IN('050121','050116') GROUP BY 1,2)x))"""
    data=json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))
    data['old_247_unchanged']=data.pop('protected_old_hash')==baseline['protected_old_hash']
    data['checkpoint_unchanged']=data['checkpoint']==baseline['checkpoint']
    out['data']=data
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
