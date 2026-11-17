"""Derive read-only verification from the prior accepted deployment checks."""
from pathlib import Path
import ast
HERE=Path(__file__).resolve().parent
RID='ui-clarity-20261026T063000Z'
prior=HERE.parent/'username-password-login-20261026'
status=(prior/'upgrade_status_remote.py').read_text('utf8').replace('auth-20261026T043500Z',RID)
(HERE/'upgrade_status_remote.py').write_text(status,encoding='utf8')
source=(prior/'verify_remote.py').read_text('utf8').replace('auth-20261026T043500Z',RID)
source=source.replace("Path('/root/blyb-maintenance-backups/auth-20261026/pause.json').read_text())['after']", "Path('/root/blyb-maintenance-backups/upgrade-"+RID+"/baseline.json').read_text())")
source=source.replace("Path('/root/blyb-maintenance-backups/auth-20261026/login-verified.json')", "Path('/root/blyb-maintenance-backups/upgrade-"+RID+"/ui-verified.json')")
source=source.replace("out['utc']=", "out['timer']=run(['systemctl','show','blyb-daily-upload.timer','-p','ActiveState','-p','SubState'])\nassert 'ActiveState=active' in out['timer']\nout['utc']=")
# Verify the served files against the exact frozen frontend, not only HTTP200.
source=source.replace("assert status==200 and all(x==200 for x in out['ui']['asset_statuses'])", """assert status==200 and all(x==200 for x in out['ui']['asset_statuses'])
container_dist='/app/apps/admin_web/dist'
out['ui']['index_matches_release']=hashlib.sha256(html).hexdigest()==run(['docker','exec','blyb-core-1','sha256sum',container_dist+'/index.html']).split()[0]
out['ui']['asset_hashes_match']=all(hashlib.sha256(get(path)[1]).hexdigest()==run(['docker','exec','blyb-core-1','sha256sum',container_dist+'/'+path.split('/ui/',1)[-1]]).split()[0] for path in assets)
out['ui']['new_overview_present']=any('上报总览'.encode() in get(path)[1] for path in assets if path.endswith('.js'))
assert out['ui']['index_matches_release'] and out['ui']['asset_hashes_match'] and out['ui']['new_overview_present']""")
(HERE/'verify_remote.py').write_text(source,encoding='utf8')
for name in ['upgrade_remote.py','upgrade_status_remote.py','verify_remote.py']:
    compile((HERE/name).read_text('utf8'),name,'exec')
tree=ast.parse((HERE/'upgrade_remote.py').read_text('utf8'))
node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='wrapper' for t in n.targets))
wrapper=eval(compile(ast.Expression(node.value),'wrapper-expression','eval'),{'folder':Path('/tmp/upgrade-ui'),'release':Path('/tmp/release')})
compile(wrapper,'upgrade-wrapper','exec')
print('Remote scripts and generated detached wrapper compile; no remote mutation')
