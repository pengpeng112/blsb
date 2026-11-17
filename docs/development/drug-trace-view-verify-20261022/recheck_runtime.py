"""Read-only deployed application/DMZ checks; never enables upload."""
from pathlib import Path
from datetime import datetime,timezone
import importlib.util
import json
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent

def main():
    mode=sys.argv[1]
    if mode not in ('application','dmz'):raise SystemExit('application or dmz')
    revision=sys.argv[2] if len(sys.argv)>2 else 'r1'
    if revision not in ('r1','r2'):raise SystemExit('r1 or r2')
    path=HERE/'evidence'/(mode+'-'+revision+'.json')
    with path.open('x',encoding='utf-8') as stream:json.dump({'status':'RESERVED'},stream)
    spec=importlib.util.spec_from_file_location('view_task_runtime',ROOT/'docs/development/drug-trace-input-recheck-20261022/recheck.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    diagnostics={}
    original_load=module.load
    def traced_load(name,path):
        loaded=original_load(name,path)
        if name=='recheck_jump':
            original_nested=loaded.nested_exec
            def nested(*args,**kwargs):
                rc,transcript=original_nested(*args,**kwargs)
                if rc:
                    diagnostics['ssh_exit_code']=rc
                    diagnostics['markers']=[x for x in (
                        'Permission denied','Connection refused','Connection timed out',
                        'No route to host','Host key verification failed',
                        'Connection closed','Connection reset','Too many authentication failures',
                        'REMOTE HOST IDENTIFICATION HAS CHANGED') if x.lower() in transcript.lower()]
                return rc,transcript
            loaded.nested_exec=nested
        return loaded
    module.load=traced_load
    module.WORKER=module.WORKER.replace('with sf() as db:',
        "from sources.db_view.drug_trace import TEMPLATES\n"
        "out['oracle_first_page_null_safe']=':after_record_id IS NULL' in TEMPLATES['drugtrace.bill_codes']['oracle']\n"
        'with sf() as db:')
    out={'captured_at':datetime.now(timezone.utc).isoformat(),'upload_requests':0,
         'remote_config_changes':0,'source_writes':0}
    try:
        out['result']=getattr(module,mode)()
        result=out['result']
        out['status']='CAPTURED' if result.get('ssh_exit_code')==0 and result.get('inventory') else 'FAILED'
        if diagnostics:out['ssh_diagnostic']=diagnostics
    except Exception as exc:
        out.update(status='ERROR',error_type=type(exc).__name__)
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if out['status']=='CAPTURED' else 1

if __name__=='__main__':raise SystemExit(main())
