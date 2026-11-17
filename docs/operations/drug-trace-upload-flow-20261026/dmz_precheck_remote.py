from pathlib import Path
import hashlib,json,subprocess
p=Path('/opt/blyb-dmz/current/forwarder.py')
config=json.loads(Path('/etc/blyb-dmz/forwarder.json').read_text())
out={'release':p.parent.resolve().name,'forwarder_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
 'normalized_sha256':hashlib.sha256(p.read_text().encode()).hexdigest(),
 'rate_per_minute':config['rate_per_minute'],'rate_burst':config['rate_burst']}
s=subprocess.run(['systemctl','show','blyb-dmz-forwarder.service','-p','ActiveState','-p','SubState'],capture_output=True,text=True,timeout=10)
out['service']=s.stdout.strip();assert s.returncode==0
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
