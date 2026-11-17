"""Collect whitelisted platform metadata through sjzc; never connect business DBs.

No credential fields, credential files or connection_options are requested.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

SKILL_SCRIPT = Path(r"C:\Users\Administrator\.zcode\skills\sjzc\scripts\sjzc_query.py")
QUERIES = {
    "source_catalog": """SELECT source_code,system_code,db_type,target_host,port,
        enabled,write_policy FROM asset.asset_data_sources ORDER BY source_code LIMIT 200""",
    "source_registration": """SELECT source_code,system_code,db_type,target_host,port,
        service_name,service_mode,connection_mode,enabled,write_policy,updated_at
        FROM asset.asset_data_sources
        WHERE source_code='his_source_<his_host_id>' LIMIT 1""",
    "same_endpoint_sources": """SELECT source_code,system_code,db_type,target_host,port,
        service_name,service_mode,connection_mode,enabled,write_policy
        FROM asset.asset_data_sources WHERE target_host='<his-host>' AND port=1521
        ORDER BY source_code LIMIT 20""",
    "trace_table_registration": """SELECT system_code,source_code,schema_name,table_name,
        column_count,source,updated_at FROM asset.asset_tables
        WHERE system_code IN ('HIS','HIS_SOURCE') AND schema_name='DRUG_USER'
        AND table_name='MED_TRACE_RECORD' LIMIT 10""",
    "trace_columns": """SELECT system_code,source_code,schema_name,table_name,column_name,
        data_type,nullable,column_id
        FROM asset.asset_columns
        WHERE system_code IN ('HIS','HIS_SOURCE') AND schema_name='DRUG_USER'
        AND table_name='MED_TRACE_RECORD' ORDER BY column_id LIMIT 50""",
    "trace_value_domains": """SELECT source_code,system_code,schema_name,table_name,
        column_name,code,meaning,status,conflict_status,current_version_id,updated_at
        FROM asset.asset_column_value_domains
        WHERE source_code='his_source_<his_host_id>'
        AND schema_name='DRUG_USER' AND table_name='MED_TRACE_RECORD'
        AND column_name IN ('IN_OUT_TYPE','KIND_TYPE','IS_DELETED','SPLIT_FLAG')
        ORDER BY column_name,code LIMIT 100""",
    "contract_view_catalog": """SELECT system_code,source_code,schema_name,table_name,column_count
        FROM asset.asset_tables WHERE system_code IN ('HIS','HIS_SOURCE')
        AND table_name IN ('V_DRUG_TRACE_BILL','V_DRUG_TRACE_CODE')
        ORDER BY schema_name,table_name LIMIT 20""",
}


def main():
    spec = importlib.util.spec_from_file_location("sjzc", SKILL_SCRIPT)
    sjzc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sjzc)
    result = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "channel": "sjzc/<relay-jump>/platform_metadata_only",
        "readonly": True, "business_source_writes": 0,
        "business_source_connections": 0, "credential_contents_read": False,
        "queries": {},
    }
    for key, query in QUERIES.items():
        # JSON rows retain NULL/bool types without reading any credential columns.
        wrapped = f"SELECT row_to_json(t) FROM ({query}) t"
        output = sjzc._ssh_psql(wrapped)
        result["queries"][key] = {
            "sql": query, "rows": [json.loads(line) for line in output.splitlines() if line],
        }
    path = Path(__file__).parent / "evidence" / "platform-metadata.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
