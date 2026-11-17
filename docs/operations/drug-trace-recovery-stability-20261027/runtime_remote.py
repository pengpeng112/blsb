"""Verify installed runtime files, image identity and driver without business IO."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib, json, subprocess
release=Path('/opt/blyb/current').resolve()
assert release.name=='stability-20261027T030000Z-r2'
manifest=json.loads((release/'manifest.json').read_text())
files=manifest['delta']['files']
assert len(files)==7 and not manifest['delta']['dependencies_changed']
expected={name:hashlib.sha256((release/'implementation'/name).read_bytes()).hexdigest() for name in files}
def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=25)
    assert p.returncode==0,args[0]+' failed'
    return p.stdout.strip()
code="from pathlib import Path;import json,hashlib,sys;print(json.dumps({n:hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest() for n in json.loads(sys.argv[1])}))"
out={'release':release.name,'runtime_files':{}}
for name in ('blyb-core-1','<worker-container>'):
    actual=json.loads(run(['docker','exec',name,'python','-c',code,json.dumps(files)]))
    assert actual==expected,'RUNTIME_SOURCE_DRIFT'
    image=run(['docker','inspect','--format','{{.Image}}',name])
    assert image==manifest['images']['backend'],'RUNTIME_IMAGE_DRIFT'
    out['runtime_files'][name]={'seven_files_match':True,'image':image}
driver=hashlib.sha256(Path('/opt/blyb/tools/blyb-daily-upload.py').read_bytes()).hexdigest()
assert driver=='470b4bcca8dc28fee9d990a0d77a28e070baf8f156d5fae38b65a2014863213d'
out.update(driver_sha256=driver,python=run(['docker','exec','<worker-container>','python','--version']),ntp=run(['chronyc','tracking']),utc=datetime.now(timezone.utc).isoformat())
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
