"""Second read-only snapshot plus heartbeat explanation; no HIS/platform calls."""
from datetime import datetime,timezone,timedelta
import json,subprocess
out={'captured_gmt8':datetime.now(timezone(timedelta(hours=8))).isoformat()}
sql="""SELECT json_build_object(
 'progress',(SELECT json_build_object('row_version',row_version,'since_time',cursor_json->>'since_time','completed_window',cursor_json->>'completed_window') FROM source_checkpoint WHERE partition='r10-batch-daily'),
 'quality',(SELECT json_agg(row_to_json(x)) FROM(SELECT b.business_kind,r.quality_state,count(*) n,max(r.created_at)::text latest_revision_utc FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' GROUP BY 1,2)x),
 'waiting_rules',(SELECT json_agg(row_to_json(x)) FROM(SELECT q->>'rule' rule,count(*) n FROM drug_trace_bill b JOIN drug_trace_bill_revision r ON r.id=b.current_revision_id CROSS JOIN LATERAL jsonb_array_elements(r.quality_issues::jsonb) q WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' AND r.quality_state='WAITING' AND q->>'severity'='BLOCKING' GROUP BY 1)x),
 'today_attempts',(SELECT count(*) FROM delivery_attempt WHERE started_at>='2026-10-26 16:00:00'),
 'collection',(SELECT json_build_object('state',state,'created_utc',created_at,'finished_utc',finished_at) FROM drug_trace_collection_run WHERE window_begin='2026-10-26' AND window_end='2026-10-27' ORDER BY created_at DESC LIMIT 1),
 'reports',(SELECT json_agg(row_to_json(x)) FROM(SELECT report_date::text,source,outcome,candidate_count,updated_at::text FROM drug_trace_daily_report WHERE report_date='2026-10-26')x))"""
p=subprocess.run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql],capture_output=True,text=True,timeout=25)
assert p.returncode==0,'READONLY_PROGRESS_QUERY_FAILED'
out['database']=json.loads(p.stdout)
p=subprocess.run(['docker','inspect','--format','{{json .State}}','<worker-container>'],capture_output=True,text=True,timeout=10)
assert p.returncode==0
d=json.loads(p.stdout);h=d.get('Health') or {}
out['worker']={'running':d.get('Running'),'status':d.get('Status'),'started_at':d.get('StartedAt'),'health_status':h.get('Status'),'failing_streak':h.get('FailingStreak'),
 'recent_health_checks':[{'end':r['End'],'exit_code':r['ExitCode']} for r in h.get('Log',[])]}
code="import json,os,time; p=os.environ['BLYB_WORKER_HEARTBEAT'];print(json.dumps({'heartbeat_age_seconds':round(time.time()-os.stat(p).st_mtime,1)}))"
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=10)
assert p.returncode==0;out['worker'].update(json.loads(p.stdout))
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
