from pathlib import Path
import hashlib,json,subprocess
sql="""SELECT json_build_object(
 'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
 'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
 'active_jobs',(SELECT count(*) FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT')),
 'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
 'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=30)
assert p.returncode==0
out={'release':Path('/opt/blyb/current').resolve().name,'db':json.loads(p.stdout),
 'driver_sha256':hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
