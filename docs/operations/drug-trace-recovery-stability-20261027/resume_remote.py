from pathlib import Path
import hashlib,json,subprocess
folder=Path('/root/blyb-maintenance-backups/upgrade-stability-20261027T030000Z-r2')
verified=json.loads((folder/'ui-verified.json').read_text())
assert all(verified[k] for k in ('old_246_unchanged','checkpoint_unchanged','quota_unchanged'))
assert Path('/opt/blyb/current').resolve().name=='stability-20261027T030000Z-r2'
runner=Path('/opt/blyb/tools/blyb-recovery-stability-20261027-resumed.py')
assert hashlib.sha256(runner.read_bytes()).hexdigest()=='64615c6d7de04d59eca4545b5fe972638ea095fd56d51388b6dca0530926b4b8'
assert not Path('/var/log/blyb/recovery-stability-20261027-resumed').exists(),'ONE_OFF_ALREADY_USED'
p=subprocess.run(['systemd-run','--unit=blyb-recovery-stability-20261027-resumed','--property=RuntimeMaxSec=28800','--property=UMask=0077','/usr/bin/python3',str(runner)],capture_output=True)
assert p.returncode==0
print('__MAINTENANCE__'+json.dumps({'launched':True,'release':'stability-20261027T030000Z-r2','verified_before_resume':True})+'__END__')
