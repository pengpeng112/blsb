"""Wait for the authorized recovery, then execute the fixed query-only 41 gate.

No sends, source queries, state resets, or recovery restarts are available here.
Each remote mutation is the already-reviewed one-off old41 launcher, once only.
"""
from pathlib import Path
from datetime import datetime,timezone
import json,os,subprocess,sys,time

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
STATE=HERE/'evidence/completion-watch.json'
EXPECTED_205='7a691905901917167cf999c3d8f4c202'

def save(state):
    temp=STATE.with_suffix('.next');temp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,STATE)

def remote(phase):
    p=subprocess.run([sys.executable,str(HERE/'check.py'),'app',phase],capture_output=True,text=True,encoding='utf8',timeout=60)
    if p.returncode:raise RuntimeError('REMOTE_'+phase.upper()+'_FAILED')
    data=json.loads(p.stdout)
    if data['exit_code']:raise RuntimeError('REMOTE_'+phase.upper()+'_FAILED')
    return data['result']

def sync():
    subprocess.run([sys.executable,str(HERE/'sync_documents.py')],capture_output=True,check=True,timeout=30)

def main():
    os.umask(0o077)
    with (HERE/'evidence/completion-watch.claimed').open('x') as claimed:claimed.write(datetime.now(timezone.utc).isoformat())
    state={'started_utc':datetime.now(timezone.utc).isoformat(),'outcome':'WATCHING_RECOVERY','old41_launched':False}
    save(state);deadline=time.monotonic()+3*3600;last_sync=0
    while time.monotonic()<deadline:
        data=remote('resumed_status');progress=data.get('final.json') or data.get('progress.json') or {}
        snapshot=progress.get('snapshot') or {};counts=snapshot.get('window_states') or {}
        state.update(updated_utc=datetime.now(timezone.utc).isoformat(),window_states=counts,
            protected_old_246=progress.get('old_246_unchanged'),checkpoint_unchanged=progress.get('checkpoint_unchanged'))
        save(state)
        print(json.dumps({'utc':state['updated_utc'],'window_states':counts,'recovery_outcome':progress.get('outcome')},ensure_ascii=False),flush=True)
        if time.monotonic()-last_sync>600:sync();last_sync=time.monotonic()
        if 'final.json' in data:
            if progress.get('outcome')!='ALL_CONFIRMED':raise RuntimeError('RECOVERY_STOPPED_FOR_REVIEW')
            assert state['protected_old_246'] and state['checkpoint_unchanged']
            before=remote('fresh');assert before['old_205']=={'n':205,'hash':EXPECTED_205}
            assert before['window_states']=={'CONFIRMED':1995}
            state['old41_launch']=remote('old41_launch');state['old41_launched']=True;state['outcome']='CONFIRMING_OLD41';save(state)
            break
        time.sleep(45)
    else:raise RuntimeError('WATCH_DEADLINE_EXCEEDED')
    deadline=time.monotonic()+1500
    while time.monotonic()<deadline:
        data=remote('old41_status');result=(data.get('result.json') or {}).get('result') or {}
        print(json.dumps({'old41':result.get('outcome') or data.get('progress.json',{}).get('outcome','RUNNING'),
            'rows':result.get('rows_count') or data.get('progress.json',{}).get('rows_count',0)},ensure_ascii=False),flush=True)
        if data.get('result.json'):
            assert result.get('outcome')=='ALL_41_CONFIRMED' and result['rows_count']==41
            assert result['row_states']=={'CONFIRMED':41} and result['query_generations_unchanged']
            assert result['before']==result['after']
            break
        time.sleep(30)
    else:raise RuntimeError('OLD41_DEADLINE_EXCEEDED')
    final=remote('fresh');remote('detail');remote('runtime')
    assert final['old_205']=={'n':205,'hash':EXPECTED_205}
    assert final['window_states']=={'CONFIRMED':1995}
    assert final['states']=={'CONFIRMED':4613,'UNKNOWN':205}
    assert final['inflight']==0
    state.update(outcome='RECOVERY_AND_OLD41_COMPLETE',finished_utc=datetime.now(timezone.utc).isoformat(),final=final)
    save(state);sync();print(json.dumps({'outcome':state['outcome'],'states':final['states'],'quota':final['quota']},ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        state=json.loads(STATE.read_text(encoding='utf8')) if STATE.exists() else {}
        state.update(outcome='STOPPED_FOR_REVIEW',error_type=type(exc).__name__,stopped_utc=datetime.now(timezone.utc).isoformat())
        if isinstance(exc,RuntimeError):state['error_code']=str(exc)
        save(state);print(json.dumps({k:state[k] for k in ('outcome','error_type','error_code') if k in state}),flush=True);sys.exit(1)
