"""Read-only aggregate of frozen method/type flags; no codes or patient data."""
import json,subprocess
sql="""SELECT json_agg(row_to_json(x)) FROM(
 SELECT p.schema_ref->>'profile' profile,p.payload->>'bill_type' bill_type,
 p.payload->>'query_bill_type' query_bill_type,p.payload->>'ignore_part_success_flag' ignore_part_success_flag,s.state,count(*) n
 FROM submission s JOIN package_revision p ON p.id=s.package_revision_id JOIN data_package d ON d.id=p.package_id
 WHERE d.domain='drug_trace' GROUP BY 1,2,3,4,5)x"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=20)
assert p.returncode==0
print('__MAINTENANCE__'+json.dumps({'frozen_contract_groups':json.loads(p.stdout),'business_writes':0,'platform_calls':0})+'__END__')
