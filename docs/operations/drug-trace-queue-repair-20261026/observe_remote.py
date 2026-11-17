"""Read-only capture for the resumed batch; also usable as a bounded watcher."""
from pathlib import Path
from datetime import datetime,timezone
import collections
import json
import subprocess
import sys
import time

FOLDER=Path('/var/log/blyb/queue-recovery-20261026')
BASELINE=Path('/root/blyb-maintenance-backups/queue-recovery-20261026/baseline.json')


def command(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=25)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()


def snapshot():
    baseline=json.loads(BASELINE.read_text())['baseline']
    since=datetime.fromtimestamp(BASELINE.stat().st_mtime,timezone.utc).replace(tzinfo=None).isoformat()
    sql="""SELECT json_build_object(
      'states',(SELECT json_object_agg(state,n) FROM (SELECT state,count(*) n FROM submission GROUP BY state) x),
      'new_attempts',(SELECT json_agg(row_to_json(x)) FROM (SELECT http_status,outcome,count(*) n FROM delivery_attempt WHERE started_at>='%s' AND ended_at IS NOT NULL GROUP BY 1,2) x),
      'inflight_attempts',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
      'confirmed_recovered',(SELECT count(DISTINCT s.id) FROM submission s JOIN delivery_attempt a ON a.submission_id=s.id WHERE a.started_at>='%s' AND s.state='CONFIRMED'),
      'quota',(SELECT json_agg(row_to_json(x)) FROM (SELECT used,max_bills FROM drug_trace_send_quota) x),
      'protected_old_rows',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN ('UNKNOWN','ACCEPTED')),
      'pending_jobs',(SELECT json_agg(row_to_json(x)) FROM (SELECT job_type,state,count(*) n FROM job WHERE state IN ('PENDING','LEASED','RETRY_WAIT') GROUP BY 1,2) x),
      'checkpoint',(SELECT json_agg(row_to_json(x)) FROM (SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily') x))"""%(since,since)
    data=json.loads(command(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000',
        'blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))
    data['old_nonterminal_unchanged']=data.pop('protected_old_rows')==baseline['protected_old_rows']
    data['checkpoint_unchanged']=data['checkpoint']==baseline['checkpoint']
    raw=command(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','-p','SubState','-p','ExecMainStatus','-p','ExecMainStartTimestamp','-p','ExecMainExitTimestamp'])
    out={'captured_utc':datetime.now(timezone.utc).isoformat(),'unit':dict(x.split('=',1) for x in raw.splitlines() if '=' in x),'data':data}
    files=sorted(Path('/var/log/blyb/daily-upload').glob('20261026-*.json'))
    if files:
        path=files[-1];out['receipt_file']=str(path);out['receipt_bytes']=path.stat().st_size
        if path.stat().st_size:
            receipt=json.loads(path.read_text())
            out['receipt']={k:receipt.get(k) for k in ['window','candidate_count','outcome','fatal','elapsed_seconds','report_push'] if k in receipt}
            out['receipt']['send']={k:v for k,v in receipt.get('send',{}).items() if isinstance(v,(int,bool,str))}
            out['receipt']['send_errors']=dict(collections.Counter((e.get('res') or {}).get('error_code','UNKNOWN') for e in receipt.get('send',{}).get('errors',[])))
            out['receipt']['query']={k:v for k,v in receipt.get('query',{}).items() if isinstance(v,(int,bool,str))}
            out['receipt']['query_error_count']=len(receipt.get('query',{}).get('errors') or [])
    return out


if '--watch' in sys.argv:
    FOLDER.mkdir(parents=True,mode=0o700,exist_ok=True)
    deadline=time.monotonic()+4*3600
    while time.monotonic()<deadline:
        try:out=snapshot()
        except Exception as exc:out={'capture_error':type(exc).__name__,'captured_utc':datetime.now(timezone.utc).isoformat()}
        temp=FOLDER/'latest.tmp';temp.write_text(json.dumps(out)+'\n');temp.replace(FOLDER/'latest.json')
        with (FOLDER/'progress.jsonl').open('a') as f:f.write(json.dumps(out)+'\n')
        if out.get('unit',{}).get('ActiveState') not in (None,'active','activating','deactivating'):
            (FOLDER/'final.json').write_text(json.dumps(out,indent=2)+'\n')
            break
        time.sleep(60)
    else:(FOLDER/'observer-timeout.json').write_text(json.dumps(out)+'\n')
else:
    print('__MAINTENANCE__'+json.dumps(snapshot())+'__END__')
