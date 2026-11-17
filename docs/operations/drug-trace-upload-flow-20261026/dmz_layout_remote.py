from pathlib import Path
import json,hashlib,subprocess
current=Path('/opt/blyb-dmz/current').resolve()
folder=Path('/root/blyb-maintenance-backups/dmz-flow-20261026T132000Z')
out={'current':str(current),'parent':str(current.parent),'sha':hashlib.sha256((current/'forwarder.py').read_bytes()).hexdigest(),
 'candidate_exists':(current.parent/'flow-20261026T132000Z').exists(),'maintenance_folder_exists':folder.exists()}
p=subprocess.run(['systemctl','is-active','blyb-dmz-forwarder.service'],capture_output=True,text=True,timeout=10)
out['maintenance_parent_exists']=folder.parent.is_dir();out['active']=p.stdout.strip();out['probe']=subprocess.run(['/opt/blyb-dmz/runtime/bin/python3.11',str(current/'forwarder.py'),'--config','/etc/blyb-dmz/forwarder.json','--check-config'],capture_output=True,text=True,timeout=15).returncode
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
