"""Send history keys to hospital controlled connector; save raw differences privately."""
from pathlib import Path
from datetime import datetime,timezone
import base64
import gzip
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    if '--approved-daytime' not in sys.argv:raise SystemExit('Current daytime approval required')
    phase='diagnose' if '--diagnose' in sys.argv else 'full-compare'
    existing=Path(os.environ['LOCALAPPDATA'])/'BLYB/history-compare-20261023'
    private=existing/('live-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    private.mkdir(exist_ok=False)
    db=sqlite3.connect((existing/'history.sqlite3').as_uri()+'?mode=ro',uri=True)
    pairs=list(db.execute('SELECT bill,code,COUNT(*) FROM history GROUP BY bill,code'))
    samples=[]
    for label,sql in [('standard_key',"SELECT bill,code FROM history WHERE SUBSTR(bill,15,1)='_' ORDER BY file,row_no LIMIT 1"),('nonstandard_key',"SELECT bill,code FROM history WHERE SUBSTR(bill,15,1)<>'_' ORDER BY file,row_no LIMIT 1"),('repeated_pair',"SELECT bill,code FROM history GROUP BY bill,code HAVING COUNT(*)>1 ORDER BY bill,code LIMIT 1")]:
        bill,code=db.execute(sql).fetchone();samples.append({'label':label,'bill':bill,'code':code})
    db.close()
    if phase=='diagnose':
        previous=json.loads((HERE/'evidence/full-compare-r1.json').read_text('utf8'))
        detail=json.loads(Path(previous['private_details_path']).read_text('utf8'))
        pairs={'missing':detail['missing_pairs'],'excluded':detail['currently_excluded_pairs']}
    compressed=base64.b64encode(gzip.compress(json.dumps(pairs,ensure_ascii=False,separators=(',',':')).encode())).decode()
    pairs=None
    base=load('live_base',ROOT/'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    prefix=base.REMOTE.split('try:\n    auth=query')[0].replace("'timeout_ms':45000","'timeout_ms':30000")
    remote=prefix+'\nHISTORY_GZ='+repr(compressed)+'\nSAMPLES='+repr(samples)+'\n'+(HERE/('diagnose_remote.py' if phase=='diagnose' else 'compare_remote.py')).read_text('utf8')
    credential=ROOT/'.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'
    ps="$c=Import-Clixml -LiteralPath '"+str(credential).replace("'","''")+"';@{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',ps],capture_output=True,text=True,timeout=15,check=True)
    credentials=json.loads(result.stdout)
    sjzc=load('live_sjzc',Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    for line in remote.splitlines():
        if line.startswith('from app.core.database ') or line.startswith('from app.models.asset '):remote=remote.replace(line,'')
    imports='\n'.join(line for line in sjzc.LIVE_CODE.splitlines() if line.startswith('from app.'))
    remote=imports+'\nCREDS='+repr(credentials)+'\n'+remote
    credentials.clear()
    out=HERE/'evidence'/(phase+'-r1.json');out.parent.mkdir(exist_ok=True)
    with out.open('x',encoding='utf8') as f:json.dump({'status':'RESERVED'},f)
    proc=subprocess.run(['ssh','-i',sjzc.SSH_KEY,*sjzc.SSH_OPTS,sjzc.SSH_TARGET,
        'docker exec -i -e PYTHONPATH=/app -e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
        input=remote.encode(),capture_output=True,timeout=720)
    try:
        data=json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data={'status':'REMOTE_ERROR','error_type':type(exc).__name__,'ssh_exit_code':proc.returncode,
              'oracle_codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\d+',proc.stderr.decode(errors='replace'))))}
    blob=data.pop('private_detail_gzip_base64',None)
    if blob is not None:
        (private/'comparison-details.json').write_bytes(gzip.decompress(base64.b64decode(blob)))
        data['private_details_path']=str(private/'comparison-details.json')
    data['captured_at']=datetime.now(timezone.utc).isoformat()
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({key:value for key,value in data.items() if key not in ('queries','batches','registration')},ensure_ascii=False))
    return 0 if data.get('diagnosis_completed') or data.get('summary',{}).get('all_historical_pairs_queried') else 1


if __name__=='__main__':raise SystemExit(main())
