"""Register the discovered driver dependency using the existing guarded protocol."""
from pathlib import Path
import sys,shutil
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from scripts import ai_session as a
sid='20261026T125856Z-42c112e3'
scope='docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py'
with a.guard(ROOT):
    path,entry=a.get_record(ROOT,sid)
    assert entry['status']=='running'
    normalized=a.scope_path(ROOT,scope)
    assert all(x['session_id']==sid or x['status']!='running' or not any(a.overlaps(normalized,s) for s in x['scopes']) for x in a.records(ROOT))
    entry['scopes']=sorted(set(entry['scopes']+[normalized]));a.save(path,entry)
destination=Path(__file__).resolve().parent/'baselines'/scope
destination.parent.mkdir(parents=True,exist_ok=True)
assert not destination.exists()
shutil.copy2(ROOT/scope,destination)
print('Registered nonoverlapping driver scope; saved baseline')
