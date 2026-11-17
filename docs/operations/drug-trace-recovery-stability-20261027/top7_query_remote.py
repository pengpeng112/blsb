from pathlib import Path
import json,subprocess,re
folder=Path('/root/blyb-maintenance-backups/recovery-top7-20261027-one')
out={'runner':json.loads((folder/'final.json').read_text()) if (folder/'final.json').exists() else None}
p=subprocess.run(['docker','exec','-i','<worker-container>','python','-','query'],input=(folder/'trial.py').read_bytes(),capture_output=True,timeout=150)
m=re.search(r'__TRIAL__(.*?)__END__',p.stdout.decode(),re.S)
out.update(container_exit=p.returncode,progress=json.loads(m.group(1)) if m else {'marker_missing':True})
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
