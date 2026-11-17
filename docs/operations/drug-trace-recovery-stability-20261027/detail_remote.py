"""Read-only cache/ledger diagnosis. No HIS or platform requests."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,json,subprocess
out={'captured_gmt8':datetime.now(timezone(timedelta(hours=8))).isoformat()}
sql="""WITH w AS (
 SELECT b.id,b.business_kind,b.pharmacy_code,r.quality_state,r.quality_issues,r.header,r.lines
 FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id
 WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'
)
SELECT json_build_object(
 'window_quality',(SELECT json_agg(row_to_json(x)) FROM(SELECT business_kind,quality_state,count(*) n FROM w GROUP BY 1,2)x),
 'waiting_rules',(SELECT json_agg(row_to_json(x)) FROM(SELECT q->>'rule' rule,q->>'severity' severity,count(*) n FROM w CROSS JOIN LATERAL jsonb_array_elements(quality_issues::jsonb) q WHERE quality_state='WAITING' GROUP BY 1,2)x),
 'raw_status_groups',(SELECT json_agg(row_to_json(x)) FROM(SELECT business_kind,quality_state,substring(header->>'source_bill_kind_raw' from 'PRESC_SOURCE=([0-9]+|NULL)') presc_source,substring(header->>'source_bill_kind_raw' from 'FLAG=([0-9]+|NULL)') flag,count(*) n FROM w GROUP BY 1,2,3,4)x),
 'pharmacies',(SELECT json_agg(row_to_json(x)) FROM(SELECT pharmacy_code,quality_state,count(*) n FROM w GROUP BY 1,2)x),
 'completeness',(SELECT json_build_object('bills',count(*),'expected_count_null',count(*) FILTER(WHERE header->>'expected_code_count' IS NULL),'empty_code_rows',count(*) FILTER(WHERE jsonb_array_length(lines::jsonb)=0)) FROM w),
 'submission_states_by_business_day',(SELECT json_agg(row_to_json(x)) FROM(SELECT b.bill_time::date::text business_date,s.state,count(*) n FROM submission s JOIN package_revision pr ON pr.id=s.package_revision_id JOIN drug_trace_bill_revision r ON r.id=(pr.provenance->>'bill_revision_id') JOIN drug_trace_bill b ON b.id=r.bill_id GROUP BY 1,2 ORDER BY 1,2)x),
 'window_submissions',(SELECT count(*) FROM submission s JOIN package_revision pr ON pr.id=s.package_revision_id JOIN drug_trace_bill_revision r ON r.id=(pr.provenance->>'bill_revision_id') JOIN drug_trace_bill b ON b.id=r.bill_id WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27'),
 'unpublished_today_events',(SELECT json_agg(row_to_json(x)) FROM(SELECT event_type,count(*) n FROM outbox_event WHERE created_at>='2026-10-26 16:00:00' AND published_at IS NULL GROUP BY 1)x),
 'reports',(SELECT json_agg(row_to_json(x)) FROM(SELECT report_date::text,source,outcome,candidate_count,collection,send->>'sent' sent,send->>'aborted_reason' aborted_reason,send->>'errors_total' send_errors_total,query_stats->>'queried' queried,elapsed_seconds,updated_at::text FROM drug_trace_daily_report WHERE report_date='2026-10-26')x),
 'item_states_under_confirmed',(SELECT json_agg(row_to_json(x)) FROM(SELECT i.state,count(*) n FROM submission_item i JOIN submission s ON s.id=i.submission_id WHERE s.state='CONFIRMED' GROUP BY 1)x),
 'query_budget_exhausted',(SELECT json_agg(row_to_json(x)) FROM(SELECT s.state,count(*) n FROM submission s WHERE s.state IN('ACCEPTED','UNKNOWN') AND (SELECT count(*) FROM job j WHERE j.job_type='drug_trace.query_status' AND j.dedup_key LIKE 'q:'||s.id||':%')>=12 GROUP BY 1)x),
 'active_jobs',(SELECT json_agg(row_to_json(x)) FROM(SELECT job_type,state,count(*) n,min(lease_until)::text oldest_lease_utc FROM job WHERE state IN('PENDING','LEASED','RETRY_WAIT') GROUP BY 1,2)x),
 'checkpoint',(SELECT json_build_object('row_version',row_version,'since_time',cursor_json->>'since_time','completed_window',cursor_json->>'completed_window') FROM source_checkpoint WHERE partition='r10-batch-daily'))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=20000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=30)
if p.returncode:raise RuntimeError('READONLY_DETAIL_FAILED')
out['database']=json.loads(p.stdout)
code="""from pathlib import Path
import hashlib,json
files=['plugins/sources/db_view/drug_trace.py','plugins/domains/drug_trace/ingest.py','apps/worker/engine.py']
print(json.dumps({f:hashlib.sha256((Path('/app')/f).read_bytes()).hexdigest() for f in files}))
"""
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=10)
assert p.returncode==0;out['runtime_source_sha256']=json.loads(p.stdout)
out['host_driver_sha256']=hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
