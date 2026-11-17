"""Resume only the existing DONE10-26 window, under standing production gates."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,os,subprocess,sys,time

FOLDER=Path('/var/log/blyb/recovery-stability-20261027')
DRIVER=Path('/opt/blyb/tools/blyb-daily-upload.py')
EXPECTED='cf967102e68c4978cc17ba4739af6860de60344404727f6cc8eedf1b4a851c39'


def run(args,timeout=25,data=None):
    p=subprocess.run(args,input=data,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.decode().strip()


def snapshot():
    sql="""SELECT json_build_object(
    'states',(SELECT json_object_agg(state,n) FROM(SELECT state,count(*) n FROM submission GROUP BY state)x),
    'window_states',(SELECT json_object_agg(state,n) FROM(SELECT s.state,count(*) n FROM submission s JOIN package_revision pr ON pr.id=s.package_revision_id JOIN drug_trace_bill_revision r ON r.id=pr.provenance->>'bill_revision_id' JOIN drug_trace_bill b ON b.id=r.bill_id WHERE b.bill_time>='2026-10-26' AND b.bill_time<'2026-10-27' GROUP BY 1)x),
    'protected_old_hash',(SELECT md5(string_agg(id||':'||state||':'||submission_revision::text,',' ORDER BY id)) FROM submission WHERE created_at<'2026-10-25 16:00:00' AND state IN('UNKNOWN','ACCEPTED')),
    'quota',(SELECT json_agg(row_to_json(x)) FROM(SELECT used,max_bills FROM drug_trace_send_quota)x),
    'checkpoint',(SELECT json_agg(row_to_json(x)) FROM(SELECT row_version,cursor_json->>'since_time' since_time FROM source_checkpoint WHERE partition='r10-batch-daily')x),
    'inflight',(SELECT count(*) FROM delivery_attempt WHERE ended_at IS NULL),
    'pending_sends',(SELECT count(*) FROM job WHERE job_type='drug_trace.send' AND state IN('PENDING','LEASED','RETRY_WAIT')),
    'pending_prepares',(SELECT count(*) FROM job WHERE job_type='drug_trace.prepare' AND state IN('PENDING','LEASED','RETRY_WAIT')),
    'window_attempts',(SELECT json_agg(row_to_json(x)) FROM(SELECT http_status,outcome,count(*) n FROM delivery_attempt WHERE started_at>='2026-10-26 16:00:00' GROUP BY 1,2)x))"""
    return json.loads(run(['docker','exec','-e','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=15000','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-v','ON_ERROR_STOP=1','-c',sql]))


def save(name,value):
    p=FOLDER/(name+'.next');p.write_text(json.dumps(value,indent=2)+'\n');p.chmod(0o600);os.replace(p,FOLDER/name)


def recovery_source(source):
    assert source.count("REPORT_SOURCE = 'timer'")==1
    source=source.replace("REPORT_SOURCE = 'timer'","REPORT_SOURCE = 'manual'")
    marker='def call(method, path, body=None, timeout=90):\n'
    assert source.count(marker)==1
    source=source.replace(marker,marker+"    if method == 'POST' and path == '/drug-trace/collection-runs':\n        return 412, {'error_code': 'RECOVERY_NEW_COLLECTION_FORBIDDEN'}\n")
    compile(source,'recovery-driver','exec');return source


def main():
    import fcntl
    os.umask(0o077)
    assert datetime.now(timezone.utc).date().isoformat()=='2026-10-27','DATE_GUARD'
    assert hashlib.sha256(DRIVER.read_bytes()).hexdigest()==EXPECTED,'DRIVER_CHANGED'
    assert Path('/opt/blyb/current').resolve().name=='flow-20261026T132000Z','RELEASE_CHANGED'
    FOLDER.mkdir(mode=0o700)
    lock=open('/run/lock/blyb-recovery-stability-20261027.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    active=run(['systemctl','show','blyb-daily-upload.service','-p','ActiveState','--value'])
    assert active not in ('active','activating','deactivating'),'NORMAL_DRIVER_ACTIVE'
    before=snapshot();assert before['inflight']==before['pending_sends']==0,'SEND_INFLIGHT'
    save('baseline.json',before)
    original=Path('/var/log/blyb/daily-upload/20261027-061029.json')
    save('original-failed-timer.json',json.loads(original.read_text()))
    token=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='))
    preflight='''import json,sys,urllib.request
t=sys.stdin.readline().strip()
def get(path):
 r=urllib.request.Request('http://localhost:8000/api/v1'+path,headers={'Authorization':'Bearer '+t})
 with urllib.request.urlopen(r,timeout=20) as s:return json.load(s)['data']
rows=get('/drug-trace/collection-runs?limit=200')
matches=[x for x in rows if x.get('state')=='DONE' and x.get('window')==['2026-10-26T00:00:00','2026-10-27T00:00:00']]
assert len(matches)==1 and matches[0]['summary']['valid']==1995
assert get('/drug-trace/send-scope')['evidence']['status']=='APPROVED'
print('APPROVED_DONE_WINDOW')
'''
    run(['docker','exec','-i','blyb-core-1','python','-c',preflight],data=(token+'\n').encode())
    source=recovery_source(DRIVER.read_text())
    local=FOLDER/'recovery-driver.py';local.write_text(source);local.chmod(0o600)
    run(['docker','cp',str(local),'blyb-core-1:/tmp/recovery-stability-20261027.py'])
    state={'started_utc':datetime.now(timezone.utc).isoformat(),'outcome':'RUNNING','business_date':'2026-10-26','source':'manual',
           'expected_valid':1995,'new_collection_allowed':False,'driver_sha256':hashlib.sha256(source.encode()).hexdigest()}
    save('progress.json',state)
    with (FOLDER/'2026-10-26.json').open('xb') as output,(FOLDER/'stderr.log').open('xb') as error:
        child=subprocess.Popen(['docker','exec','-i','-u','0','-e','BLYB_DAILY_REPORT_ENABLED=1','blyb-core-1','python','/tmp/recovery-stability-20261027.py','--date','2026-10-26'],stdin=subprocess.PIPE,stdout=output,stderr=error)
        child.stdin.write((token+'\n').encode());child.stdin.close();del token
        start=time.monotonic()
        while child.poll() is None:
            after=snapshot();state.update(updated_utc=datetime.now(timezone.utc).isoformat(),snapshot=after,
                old_246_unchanged=after['protected_old_hash']==before['protected_old_hash'],checkpoint_unchanged=after['checkpoint']==before['checkpoint'])
            save('progress.json',state)
            if time.monotonic()-start>6*3600:raise RuntimeError('RECOVERY_MONITOR_TIMEOUT')
            time.sleep(30)
    receipt=json.loads((FOLDER/'2026-10-26.json').read_text())
    after=snapshot()
    state.update(finished_utc=datetime.now(timezone.utc).isoformat(),exit_code=child.returncode,outcome=receipt.get('outcome'),
        send={k:v for k,v in receipt.get('send',{}).items() if isinstance(v,(int,bool,str))},
        send_error_count=len(receipt.get('send',{}).get('errors') or []),query_error_count=len(receipt.get('query',{}).get('errors') or []),
        snapshot=after,old_246_unchanged=after['protected_old_hash']==before['protected_old_hash'],checkpoint_unchanged=after['checkpoint']==before['checkpoint'])
    save('final.json',state)
    return 0 if child.returncode==0 and state['old_246_unchanged'] and state['checkpoint_unchanged'] else 1


if __name__=='__main__':
    try:sys.exit(main())
    except BaseException as e:
        if FOLDER.exists():save('error.json',{'error_type':type(e).__name__,'utc':datetime.now(timezone.utc).isoformat()})
        raise
