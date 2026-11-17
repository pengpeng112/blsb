from pathlib import Path
import json,subprocess
folder=Path('/var/log/blyb/recovery-stability-20261027-resumed')
out={}
for name in ['baseline.json','progress.json','final.json','error.json']:
    if (folder/name).exists():out[name]=json.loads((folder/name).read_text())
p=subprocess.run(['systemctl','show','blyb-recovery-stability-20261027-resumed.service','-p','ActiveState','-p','SubState','-p','ExecMainStatus'],capture_output=True,text=True,timeout=10)
out['unit']=p.stdout.strip()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
