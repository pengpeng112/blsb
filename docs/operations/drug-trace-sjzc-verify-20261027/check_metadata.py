"""平台元数据快照（不连HIS、不读凭据列）；复用 recovery-stability 20261027 的查询。"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util, json
spec = importlib.util.spec_from_file_location(
    'sjzc', r'C:\Users\Administrator\.zcode\skills\sjzc\scripts\sjzc_query.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
queries = {
 'registration': "SELECT json_agg(row_to_json(x)) FROM(SELECT source_code,system_code,db_type,target_host,port,service_name,enabled,write_policy FROM asset.asset_data_sources WHERE source_code='his_source_<his_host_id>' OR target_host='<his-host>' ORDER BY source_code LIMIT 20)x",
 'domains': "SELECT json_agg(row_to_json(x)) FROM(SELECT schema_name,table_name,column_name,code,meaning,status FROM asset.asset_column_value_domains WHERE source_code='his_source_<his_host_id>' AND ((schema_name='DRUG_USER' AND table_name='MED_TRACE_RECORD' AND column_name IN('IN_OUT_TYPE','IS_DELETED','SPLIT_FLAG')) OR (schema_name='PHARMACY' AND table_name='DRUG_PRESC_MASTER' AND column_name IN('PRESC_SOURCE','FLAG','REFUND_FLAG'))) ORDER BY table_name,column_name,code LIMIT 100)x",
}
out = {'utc': datetime.now(timezone.utc).isoformat(), 'business_source_calls': 0, 'metadata': {}}
for name, sql in queries.items():
    out['metadata'][name] = m._ssh_psql(
        'BEGIN READ ONLY; SET LOCAL statement_timeout=15000; ' + sql + '; COMMIT;', timeout=25)
dest = Path(__file__).resolve().parent / 'evidence' / (
    'source-metadata-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf8')
print(dest.name)
print('registration:', out['metadata']['registration'][:400])
print('domains_rows:', (out['metadata']['domains'] or '').count('column_name'))
