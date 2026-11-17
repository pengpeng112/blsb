from pathlib import Path
import json,subprocess,re
folder=Path('/root/blyb-maintenance-backups/history-retry-20261026-one-r2')
if 'status'=='status' and (folder/'final.json').exists():
 out={'runner':json.loads((folder/'final.json').read_text())}
else:out={}
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-','status'],input=(folder/'trial.py').read_bytes(),capture_output=True,timeout=150)
raw=p.stdout.decode();m=re.search(r'__TRIAL__(.*?)__END__',raw,re.S)
out.update(container_exit=p.returncode,progress=json.loads(m.group(1)) if m else {'marker_missing':True})
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
