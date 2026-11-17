from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess
release=Path('/opt/blyb/current').resolve();assert release.name=='stability-20261027T064000Z-r3'
base=Path('/opt/blyb/releases/stability-20261027T030000Z-r2')
manifest=json.loads((release/'manifest.json').read_text())
assert manifest['delta']['base_release']=='stability-20261027T030000Z-r2' and not manifest['delta']['dependencies_changed']
core=['apps/worker/engine.py','packages/workflow_core/jobs.py','packages/workflow_core/lease_guard.py','plugins/domains/drug_trace/ingest.py','plugins/domains/drug_trace/plugin.py','plugins/domains/drug_trace/service.py','plugins/sources/db_view/drug_trace.py']
front=['apps/admin_web/src/views/DrugTrace.vue','apps/admin_web/src/views/drugTraceDisplay.ts','apps/admin_web/tests/drugTraceDisplay.test.mjs']
assert set(manifest['delta']['files'])==set(front)
for name in core:assert (base/'implementation'/name).read_bytes()==(release/'implementation'/name).read_bytes()
expected={n:hashlib.sha256((release/'implementation'/n).read_bytes()).hexdigest() for n in core+front}
def run(args):
 p=subprocess.run(args,capture_output=True,text=True,timeout=25);assert p.returncode==0;return p.stdout.strip()
code="from pathlib import Path;import hashlib,json,sys;print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in json.loads(sys.argv[1])}))"
out={'release':'stability-20261027T064000Z-r3','core_runtime_unchanged':True,'containers':{}}
for name in ('blyb-core-1','<worker-container>'):
 assert json.loads(run(['docker','exec',name,'python','-c',code,json.dumps(core+front)]))==expected
 assert run(['docker','inspect','--format','{{.Image}}',name])==manifest['images']['backend']
 out['containers'][name]={'ten_files_match':True,'image':manifest['images']['backend']}
out['driver_sha256']=hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()
assert out['driver_sha256']=='adbc32b1cec4de368884c97df77285825ee4cfc6b5a50a97d4d03a3d80432314'
out['utc']=datetime.now(timezone.utc).isoformat()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
