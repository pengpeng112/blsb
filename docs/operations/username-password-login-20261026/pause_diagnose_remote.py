from pathlib import Path
import json,subprocess
out={}
for name,args in {'units':['systemctl','show','blyb-mapping-recovery-20261026.service','blyb-daily-upload.timer','-p','Id','-p','ActiveState','-p','SubState','-p','MainPID'],
                  'children':['docker','exec','blyb-core-1','python','-c',"from pathlib import Path;import json;out=[]\nfor p in Path('/proc').iterdir():\n if not p.name.isdigit():continue\n try:a=(p/'cmdline').read_bytes().split(bytes([0]));st=(p/'status').read_text()\n except OSError:continue\n if b'/tmp/mapping-recovery-driver.py' in a:out.append({'pid':int(p.name),'state':next(x for x in st.splitlines() if x.startswith('State:'))})\nprint(json.dumps(out))"]}.items():
    p=subprocess.run(args,capture_output=True,text=True,timeout=25);out[name]={'exit_code':p.returncode,'summary':p.stdout[:5000]}
folder=Path('/root/blyb-maintenance-backups/auth-20261026')
out['folder_exists']=folder.exists();out['files']=[p.name for p in folder.iterdir()] if folder.exists() else []
out['interruption_exists']=Path('/var/log/blyb/mapping-recovery-20261026/maintenance-interruption.json').exists()
out['runner_states']=[]
for p in Path('/proc').iterdir():
    if not p.name.isdigit():continue
    try:a=(p/'cmdline').read_bytes().split(bytes([0]));st=(p/'status').read_text()
    except OSError:continue
    if b'/opt/blyb/tools/blyb-mapping-recovery-20261026.py' in a:out['runner_states'].append({'pid':int(p.name),'state':next(x for x in st.splitlines() if x.startswith('State:'))})
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
