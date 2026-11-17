"""One-time sequential recovery of already collected, standing-approved windows.

Uses the unchanged production driver and worker; no raw platform calls, source
queries, quota resets, UNKNOWN replays, or schema changes in this wrapper.
"""
from pathlib import Path
from datetime import datetime,timezone
import fcntl,hashlib,json,os,subprocess,sys

FOLDER=Path('/var/log/blyb/mapping-recovery-20261026')
DRIVER=Path('/opt/blyb/tools/blyb-daily-upload.py')
SHA='1d1862d93ca36449fa8658b7295b0b9a857d8b902059057ee25f9a68a1cd8c46'

def run(args,timeout=30,data=None):
    p=subprocess.run(args,input=data,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.decode().strip()

def snapshot():
    sql="""SELECT json_build_object(
    'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
    'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
    'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
    'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
    'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL))"""
    return json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))

def save(name,value):
    temp=FOLDER/(name+'.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');os.chmod(str(temp),0o600);temp.replace(FOLDER/name)

def main():
    if datetime.now(timezone.utc).date().isoformat()!='2026-10-26':raise RuntimeError('DATE_GUARD')
    if hashlib.sha256(DRIVER.read_bytes()).hexdigest()!=SHA:raise RuntimeError('DRIVER_CHANGED')
    if Path('/opt/blyb/current').resolve().name!='queue-20261026T010000Z-r2':raise RuntimeError('RELEASE_CHANGED')
    if FOLDER.exists():raise RuntimeError('ONE_TIME_RECOVERY_ALREADY_EXISTS')
    FOLDER.mkdir(mode=0o700)
    lock=open('/run/lock/blyb-mapping-recovery-20261026.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    before=snapshot()
    if before['inflight']:raise RuntimeError('DELIVERY_INFLIGHT')
    save('baseline.json',before)
    state={'started_utc':datetime.now(timezone.utc).isoformat(),'outcome':'RUNNING','dates':[],'driver_sha256':SHA}
    save('progress.json',state)
    for day in ['2026-10-25','2026-10-24']:
        active=run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value'])
        if active in ('active','activating','deactivating'):raise RuntimeError('NORMAL_DRIVER_ACTIVE')
        token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
        # Prevent the normal driver's collection-create branch: both windows
        # must already be DONE in this tenant, using its authenticated API.
        preflight='''import json,sys,urllib.request
t=sys.stdin.readline().strip();day=sys.argv[1];end=sys.argv[2]
r=urllib.request.Request('http://localhost:8000/api/v1/drug-trace/collection-runs?limit=200',headers={'Authorization':'Bearer '+t})
with urllib.request.urlopen(r,timeout=20) as s:rows=json.load(s)['data']
assert any(x.get('state')=='DONE' and x.get('window')==[day+'T00:00:00',end+'T00:00:00'] for x in rows)
print('DONE')
'''
        end='2026-10-26' if day=='2026-10-25' else '2026-10-25'
        run(['docker','exec','-i','blyb-core-1','python','-c',preflight,day,end],data=(token+'\n').encode())
        run(['docker','cp',str(DRIVER),'blyb-core-1:/tmp/mapping-recovery-driver.py'])
        state['current_date']=day;save('progress.json',state)
        path=FOLDER/(day+'.json')
        with path.open('xb') as output,(FOLDER/(day+'.stderr')).open('xb') as error:
            p=subprocess.run(['docker','exec','-i','-u','0','-e','BLYB_DAILY_REPORT_ENABLED=1','blyb-core-1','python','/tmp/mapping-recovery-driver.py','--date',day],input=(token+'\n').encode(),stdout=output,stderr=error,timeout=4*3600)
        del token
        receipt=json.loads(path.read_text())
        after=snapshot()
        protected=after['protected_old_hash']==before['protected_old_hash']
        checkpoint=after['checkpoint']==before['checkpoint']
        item={'date':day,'exit_code':p.returncode,'outcome':receipt.get('outcome'),'receipt':str(path),
              'send':{k:v for k,v in receipt.get('send',{}).items() if isinstance(v,(int,bool,str))},
              'send_error_count':len(receipt.get('send',{}).get('errors') or []),
              'old_247_unchanged':protected,'checkpoint_unchanged':checkpoint}
        state['dates'].append(item);save('progress.json',state)
        if p.returncode or not protected or not checkpoint:
            state['outcome']='STOPPED_FOR_REVIEW';break
    else:state['outcome']='DRIVER_WINDOWS_COMPLETED'
    state['finished_utc']=datetime.now(timezone.utc).isoformat();state['final_snapshot']=snapshot()
    save('final.json',state)
    return 0 if state['outcome']=='DRIVER_WINDOWS_COMPLETED' else 1

if __name__=='__main__':
    try:sys.exit(main())
    except Exception as e:
        if FOLDER.exists():save('error.json',{'error_type':type(e).__name__,'utc':datetime.now(timezone.utc).isoformat()})
        sys.exit(1)
