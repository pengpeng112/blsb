"""Deploy the tested presentation/budget successor only after business drain.

This orchestrates reviewed existing upgrade helpers. It cannot start collection,
upload, reset query budgets, or bypass the recovery/old41 completion gates.
"""
from pathlib import Path
from datetime import datetime,timezone
import json,os,subprocess,sys,time,xml.etree.ElementTree as ET

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
STATE=HERE/'evidence/phase-completion.json'

def save(data):
    temp=STATE.with_suffix('.next');temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8');os.replace(temp,STATE)

def full_result():
    try:
        suites=list(ET.parse(ROOT/'implementation/var/stability-regression-r3.xml').getroot().iter('testsuite'))
        counts={k:sum(int(s.attrib.get(k,0)) for s in suites) for k in ('tests','failures','errors','skipped')}
    except (FileNotFoundError,ET.ParseError):return None
    assert counts['failures']==counts['errors']==0,'FULL_REGRESSION_FAILED'
    assert counts['tests']-counts['skipped']>=595,'FULL_REGRESSION_INCOMPLETE'
    return counts

def local(script):
    p=subprocess.run([sys.executable,str(HERE/script)],capture_output=True,text=True,encoding='utf8',timeout=90)
    assert p.returncode==0,'LOCAL_'+script+'_FAILED'

def remote(phase):
    p=subprocess.run([sys.executable,str(HERE/'check.py'),'app',phase],capture_output=True,text=True,encoding='utf8',timeout=60)
    assert p.returncode==0,'REMOTE_'+phase+'_FAILED'
    obj=json.loads(p.stdout);assert obj['exit_code']==0,'REMOTE_'+phase+'_FAILED'
    return obj['result']

def main():
    os.umask(0o077)
    with (HERE/'evidence/phase-completion.claimed').open('x') as f:f.write(datetime.now(timezone.utc).isoformat())
    state={'outcome':'WAITING_FOR_RECOVERY_AND_FULL_TESTS','started_utc':datetime.now(timezone.utc).isoformat()};save(state)
    deadline=time.monotonic()+2*3600
    while time.monotonic()<deadline:
        first=json.loads((HERE/'evidence/completion-watch.json').read_text(encoding='utf8'))
        assert first['outcome']!='STOPPED_FOR_REVIEW','RECOVERY_REQUIRES_REVIEW'
        tests=full_result()
        print(json.dumps({'waiting_for':state['outcome'],'window_states':first.get('window_states'),'full_tests':tests},ensure_ascii=False),flush=True)
        if first['outcome']=='RECOVERY_AND_OLD41_COMPLETE' and tests:break
        time.sleep(50)
    else:raise RuntimeError('PREREQUISITES_WAIT_TIMEOUT')
    local('verify_artifacts.py')
    state.update(outcome='UPGRADING_AFTER_FULL_DRAIN',tests=tests,paused=remote('phase_pause'));save(state)
    state['upgrade']=remote('phase_upgrade');save(state)
    deadline=time.monotonic()+900
    while time.monotonic()<deadline:
        status=remote('phase_upgrade_status');result=status.get('result')
        print(json.dumps({'upgrade_release':status.get('release'),'result':result},ensure_ascii=False),flush=True)
        if result:
            assert result['exit_code']==result['timer_restart_exit']==0,'UPGRADE_FAILED_REVIEW_PRIVATE_LOG'
            state['upgrade_result']=result;save(state);break
        time.sleep(15)
    else:raise RuntimeError('UPGRADE_OBSERVATION_TIMEOUT')
    state['verification']=remote('phase_verify')
    state['runtime']=remote('phase_runtime')
    state['content']=remote('content')
    assert state['content']['consistent'] and state['content']['checked_bills']==1995
    assert state['content']['submission_states']=={'CONFIRMED':1995}
    state['final']=remote('fresh');state['system']=remote('system')
    assert state['final']['states']=={'CONFIRMED':4613,'UNKNOWN':205}
    assert state['final']['old_205']=={'n':205,'hash':'7a691905901917167cf999c3d8f4c202'}
    assert state['verification']['all_4818_submissions_unchanged'] and state['verification']['quota_unchanged']
    local('sync_documents.py');local('verify_artifacts.py')
    state.update(outcome='VERIFIED_SUCCESS',finished_utc=datetime.now(timezone.utc).isoformat());save(state)
    print(json.dumps({'outcome':state['outcome'],'release':state['runtime']['release'],'states':state['final']['states'],
                      'codes':state['content']['codes'],'full_tests':tests},ensure_ascii=False),flush=True)

if __name__=='__main__':
    try:main()
    except BaseException as exc:
        state=json.loads(STATE.read_text(encoding='utf8')) if STATE.exists() else {}
        state.update(outcome='STOPPED_FOR_REVIEW',error_type=type(exc).__name__,stopped_utc=datetime.now(timezone.utc).isoformat())
        if isinstance(exc,(AssertionError,RuntimeError)):state['error_code']=str(exc)
        save(state);print(json.dumps({k:state[k] for k in ('outcome','error_type','error_code') if k in state}),flush=True);sys.exit(1)
