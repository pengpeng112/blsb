"""Application-only readonly inventory; UUID and hash, no patient or trace data."""
import json,subprocess
sql="""SELECT coalesce(json_agg(row_to_json(x)),'[]'::json) FROM (
 SELECT s.id submission_id,s.state local_state,md5(p.payload->>'bill_code') bill_code_md5
 FROM submission s JOIN package_revision p ON p.id=s.package_revision_id JOIN data_package d ON d.id=p.package_id
 WHERE d.domain='drug_trace' AND s.state IN('UNKNOWN','ACCEPTED') ORDER BY s.state,s.created_at,s.id)x"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=25)
assert p.returncode==0,'READONLY_INVENTORY_FAILED'
rows=json.loads(p.stdout);assert len(rows)==247
print('__MAINTENANCE__'+json.dumps({'rows':rows,'count':len(rows),'platform_calls':0,'his_calls':0})+'__END__')
