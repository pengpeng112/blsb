"""Read public AppKey identifiers only; no secret references or secret values."""
import subprocess,json
sql="""SELECT json_agg(row_to_json(x)) FROM (
 SELECT c.content_json->>'app_key' app_key,p.schema_ref->>'profile' profile,p.payload->>'query_bill_type' query_bill_type,count(*) n
 FROM submission s JOIN package_revision p ON p.id=s.package_revision_id
 JOIN data_package d ON d.id=p.package_id JOIN config_revision c ON c.id=p.provenance->>'config_release_id'
 WHERE s.state IN('UNKNOWN','ACCEPTED') AND d.domain='drug_trace' GROUP BY 1,2,3)x"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=25)
assert p.returncode==0,'CONFIG_METADATA_QUERY_FAILED'
print('__MAINTENANCE__'+json.dumps({'identities':json.loads(p.stdout),'platform_calls':0,'secret_values_read':0})+'__END__')
