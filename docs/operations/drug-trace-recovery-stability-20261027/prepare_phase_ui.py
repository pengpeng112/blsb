"""Preserve the two report presentation files before the P2 correction."""
from pathlib import Path
import hashlib,json,shutil
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
files=['implementation/apps/admin_web/src/views/DrugTrace.vue','implementation/apps/admin_web/src/views/drugTraceDisplay.ts']
saved={}
for name in files:
    src=ROOT/name;dst=HERE/'baselines/phase-ui'/name
    assert not dst.exists(),'UI_BASELINE_ALREADY_SAVED'
    dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    saved[name]=hashlib.sha256(src.read_bytes()).hexdigest()
(HERE/'evidence/phase-ui-baselines.json').write_text(json.dumps(saved,indent=2),encoding='utf8')
print('Saved two presentation baselines; no live change')
