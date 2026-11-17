"""Read only private one-off result/progress and expose sanitized summaries."""
from pathlib import Path
from collections import Counter
import json,subprocess
folder=Path('/root/blyb-maintenance-backups/old41-confirmations-20261027')
out={}
if (folder/'result.json').exists():
    raw=json.loads((folder/'result.json').read_text());result=raw.get('result') or {};rows=result.get('rows') or []
    out['result.json']={'exit_code':raw.get('exit_code'),'result':{k:v for k,v in result.items() if k!='rows'}}
    out['result.json']['result'].update(rows_count=len(rows),row_states=dict(Counter(r.get('state') for r in rows)),
        platform_results=dict(Counter(str(r.get('result_type'))+'/'+str(r.get('sub_process_flag')) for r in rows)),
        max_calls=max((r.get('calls',0) for r in rows),default=0),query_generations_unchanged=all(r.get('query_generation_unchanged') or r.get('already_confirmed') for r in rows))
code="""from pathlib import Path
import json
p=Path('/data/exchange/old41-confirmations-20261027/progress.json')
if p.exists():
 d=json.loads(p.read_text());rows=d.pop('rows',[]);d['rows_count']=len(rows);print(json.dumps(d))
else:print('{}')
"""
p=subprocess.run(['docker','exec','<worker-container>','python','-c',code],capture_output=True,text=True,timeout=15)
out['progress.json']=json.loads(p.stdout) if p.returncode==0 else {'unavailable':True}
p=subprocess.run(['systemctl','show','blyb-old41-confirm-20261027.service','-p','ActiveState','-p','SubState','-p','ExecMainStatus'],capture_output=True,text=True,timeout=10)
out['unit']=p.stdout.strip()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
