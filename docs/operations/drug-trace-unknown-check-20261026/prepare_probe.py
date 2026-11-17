from pathlib import Path
HERE=Path(__file__).resolve().parent
code=(HERE/'probe_container.py').read_text('utf8')
compile(code,'manual-review','exec')
source='''import json,subprocess,re
code=CONTAINER_SOURCE
p=subprocess.run(['docker','exec','-i','-u','0','<worker-container>','python','-c',code,'sample'],capture_output=True,text=True,timeout=180)
m=re.search(r'__MANUAL_REVIEW__(.*?)__END__',p.stdout,re.S)
if not m:raise RuntimeError('MANUAL_SAMPLE_NO_RESULT')
rows=[json.loads(line)['data'] for line in p.stdout.splitlines() if line.startswith('{') and json.loads(line).get('event')=='result']
out={'exit_code':p.returncode,'summary':json.loads(m.group(1)),'rows':rows}
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
'''.replace('CONTAINER_SOURCE',repr(code))
(HERE/'sample_remote.py').write_text(source,encoding='utf8')
all_source='''from pathlib import Path
import subprocess,json,os
folder=Path('/root/blyb-maintenance-backups/unknown-review-20261026')
assert not folder.exists(),'ONE_OFF_REVIEW_ALREADY_EXISTS'
assert Path('/opt/blyb/current').resolve().name=='ui-clarity-20261026T063000Z','RELEASE_DRIFT'
folder.mkdir(mode=0o700)
(folder/'probe.py').write_text(CONTAINER_SOURCE)
wrapper=WRAPPER_SOURCE
compile(wrapper,'manual-review-wrapper','exec')
(folder/'run.py').write_text(wrapper)
os.chmod(str(folder/'run.py'),0o700)
with (folder/'launcher.log').open('wb') as f:
 child=subprocess.Popen(['python3',str(folder/'run.py')],stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
print('__MAINTENANCE__'+json.dumps({'launched':True,'pid':child.pid,'folder':str(folder),'mode':'one_off_query_only','uploads':0})+'__END__')
'''
wrapper='''from pathlib import Path
from datetime import datetime,timezone
import subprocess,json,re,os
os.umask(0o077)
folder=Path('/root/blyb-maintenance-backups/unknown-review-20261026')
try:
 with (folder/'results.jsonl').open('xb') as output,(folder/'stderr.log').open('xb') as error:
  p=subprocess.run(['docker','exec','-i','-u','0','<worker-container>','python','-','all'],input=(folder/'probe.py').read_bytes(),stdout=output,stderr=error,timeout=1800)
 raw=(folder/'results.jsonl').read_text()
 m=re.search(r'__MANUAL_REVIEW__(.*?)__END__',raw,re.S)
 out={'exit_code':p.returncode,'summary':json.loads(m.group(1)) if m else {'ok':False,'missing_summary':True},'finished_utc':datetime.now(timezone.utc).isoformat()}
except Exception as exc:out={'exit_code':99,'error_type':type(exc).__name__,'summary':{'ok':False}}
(folder/'final.json').write_text(json.dumps(out,indent=2))
'''
compile(wrapper,'all-query-wrapper','exec')
all_source=all_source.replace('CONTAINER_SOURCE',repr(code)).replace('WRAPPER_SOURCE',repr(wrapper))
compile(all_source,'all-review-launcher','exec')
(HERE/'all_remote.py').write_text(all_source,encoding='utf8')
print('Prepared query-only probe; upload methods rejected before network; database transaction read-only')
