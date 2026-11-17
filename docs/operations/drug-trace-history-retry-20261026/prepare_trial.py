"""Compile and bind one-off task scripts. No credentials or remote actions."""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent
code=(HERE/'trial_container.py').read_text(encoding='utf8')
compile(code,'trial_container','exec')
folder='/root/blyb-maintenance-backups/history-retry-20261026-one-r2'
wrapper='''from pathlib import Path
import json,subprocess,re,os
os.umask(0o077)
folder=Path(FOLDER)
try:
 with (folder/'stdout.log').open('xb') as output,(folder/'stderr.log').open('xb') as error:
  p=subprocess.run(['docker','exec','-i','<worker-container>','python','-','send'],input=(folder/'trial.py').read_bytes(),stdout=output,stderr=error,timeout=300)
 raw=(folder/'stdout.log').read_text()
 m=re.search(r'__TRIAL__(.*?)__END__',raw,re.S)
 out={'exit_code':p.returncode,'result':json.loads(m.group(1)) if m else {'marker_missing':True}}
except BaseException as exc:out={'exit_code':99,'error_type':type(exc).__name__}
(folder/'final.json').write_text(json.dumps(out,indent=2)+'\\n')
'''.replace('FOLDER',repr(folder))
source='''from pathlib import Path
import os,subprocess,json
assert Path('/opt/blyb/current').resolve().name=='flow-20261026T132000Z','RELEASE_DRIFT'
folder=Path(FOLDER);folder.mkdir(mode=0o700)
(folder/'trial.py').write_text(SOURCE)
(folder/'run.py').write_text(WRAPPER)
with (folder/'launcher.log').open('xb') as f:
 child=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':child.pid,'folder':str(folder),'max_bills':1,'max_uploads':1})+'__END__')
'''.replace('FOLDER',repr(folder)).replace('SOURCE',repr(code)).replace('WRAPPER',repr(wrapper))
compile(wrapper,'trial_wrapper','exec');compile(source,'trial_launcher','exec')
(HERE/'send_remote.py').write_text(source,encoding='utf8')
for phase in ['status','query']:
    s='''from pathlib import Path
import json,subprocess,re
folder=Path(FOLDER)
if MODE=='status' and (folder/'final.json').exists():
 out={'runner':json.loads((folder/'final.json').read_text())}
else:out={}
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-',MODE],input=(folder/'trial.py').read_bytes(),capture_output=True,timeout=150)
raw=p.stdout.decode();m=re.search(r'__TRIAL__(.*?)__END__',raw,re.S)
out.update(container_exit=p.returncode,progress=json.loads(m.group(1)) if m else {'marker_missing':True})
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
'''.replace('FOLDER',repr(folder)).replace('MODE',repr(phase))
    compile(s,phase,'exec');(HERE/(phase+'_remote.py')).write_text(s,encoding='utf8')
(HERE/'evidence').mkdir(exist_ok=True)
(HERE/'evidence/trial-binding.json').write_text(json.dumps({'task':'DTR-HISTORY-RETRY-20261026','target':'9b19170e-5ceb-4da4-99bb-7478c77d9695','source_sha256_lf':hashlib.sha256(code.encode()).hexdigest(),'max_uploads':1},indent=2),encoding='utf8')
print('Compiled one-bill, one-POST trial; no remote action performed')
