"""Stop only the one-off driver at a drained send boundary, retaining evidence."""
from pathlib import Path
from datetime import datetime,timezone
import json,os,signal,subprocess,time

def run(args,data=None,timeout=25):
    p=subprocess.run(args,input=data,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode)+': '+p.stderr[:400])
    return p.stdout.strip()

def snapshot():
    sql="""SELECT json_build_object(
    'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
    'sending',(SELECT count(*) FROM submission WHERE state='SENDING'),
    'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
    'pending_sends',(SELECT count(*) FROM job WHERE job_type='drug_trace.send' AND state IN('PENDING','LEASED','RETRY_WAIT')),
    'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
    'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
    'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x))"""
    return json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))

if Path('/opt/blyb/current').resolve().name!='queue-20261026T010000Z-r2':raise RuntimeError('RELEASE_DRIFT')
folder=Path('/root/blyb-maintenance-backups/auth-20261026')
if folder.exists() and any(folder.iterdir()):raise RuntimeError('PAUSE_BACKUP_ALREADY_POPULATED')
folder.mkdir(mode=0o700,exist_ok=True)
if run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value']) in ('active','activating','deactivating'):
    raise RuntimeError('NORMAL_DRIVER_RUNNING')
before=snapshot();parent=None;stopped=False
unit='blyb-mapping-recovery-20261026.service'
original=Path('/var/log/blyb/mapping-recovery-20261026')
try:
    run(['systemctl','stop','blyb-daily-upload.timer'])
    parent=int(run(['systemctl','show',unit,'-p','MainPID','--value']) or '0')
    if parent:
        if b'/opt/blyb/tools/blyb-mapping-recovery-20261026.py' not in Path('/proc/'+str(parent)+'/cmdline').read_bytes().split(b'\0'):
            raise RuntimeError('RUNNER_IDENTITY_CHANGED')
        os.kill(parent,signal.SIGSTOP)
    code='''from pathlib import Path
import json,os,signal
matches=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:args=(p/'cmdline').read_bytes().split(b'\\0')
 except OSError:continue
 if b'/tmp/mapping-recovery-driver.py' in args:matches.append(int(p.name))
assert len(matches)<=1
for pid in matches:os.kill(pid,signal.SIGSTOP)
print(json.dumps(matches))
'''
    children=json.loads(run(['docker','exec','-u','0','blyb-core-1','python','-c',code]))
    stable=0
    for _ in range(40):
        drained=snapshot()
        if not any(drained[k] for k in ['sending','inflight','pending_sends']):stable+=1
        else:stable=0
        if stable>=5:break
        time.sleep(1)
    else:raise RuntimeError('SEND_DRAIN_TIMEOUT')
    stop='''from pathlib import Path
import json,os,signal,sys
for pid in json.loads(sys.argv[1]):
 p=Path('/proc')/str(pid)
 if not p.exists():continue
 assert b'/tmp/mapping-recovery-driver.py' in (p/'cmdline').read_bytes().split(b'\\0')
 os.kill(pid,signal.SIGTERM);os.kill(pid,signal.SIGCONT)
print('stopped')
'''
    run(['docker','exec','-u','0','blyb-core-1','python','-c',stop,json.dumps(children)])
    if parent:
        run(['systemctl','kill','--kill-who=all','--signal=KILL',unit])
    stopped=True
    after=snapshot()
    if any(after[k] for k in ['sending','inflight','pending_sends']):raise RuntimeError('SEND_AFTER_DRAIN')
    out={'outcome':'INTERRUPTED_FOR_LOGIN_UPGRADE','utc':datetime.now(timezone.utc).isoformat(),
         'before':before,'drained':drained,'after':after,'driver_core_pids':children,
         'old_247_unchanged':before['protected_old_hash']==after['protected_old_hash'],
         'checkpoint_unchanged':before['checkpoint']==after['checkpoint'],
         'reason':'Planned upgrade; completed sends retained, resume with normal guarded driver. Original incomplete receipts are preserved, not claimed successful.'}
    if (original/'progress.json').exists():out['previous_progress']=json.loads((original/'progress.json').read_text())
    (folder/'pause.json').write_text(json.dumps(out,indent=2)+'\n')
    (original/'maintenance-interruption.json').write_text(json.dumps(out,indent=2)+'\n')
    print('__MAINTENANCE__'+json.dumps(out)+'__END__')
except Exception:
    if not stopped:
        resume="from pathlib import Path;import os,signal;[(os.kill(int(p.name),signal.SIGCONT)) for p in Path('/proc').iterdir() if p.name.isdigit() and (p/'cmdline').exists() and b'/tmp/mapping-recovery-driver.py' in (p/'cmdline').read_bytes().split(b'\\0')]"
        subprocess.run(['docker','exec','-u','0','blyb-core-1','python','-c',resume],capture_output=True)
        if parent:
            try:os.kill(parent,signal.SIGCONT)
            except ProcessLookupError:pass
        subprocess.run(['systemctl','start','blyb-daily-upload.timer'],capture_output=True)
    raise
