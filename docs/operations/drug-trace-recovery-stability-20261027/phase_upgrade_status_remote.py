from pathlib import Path
import json,re,subprocess
folder=Path('/root/blyb-maintenance-backups/upgrade-stability-20261027T064000Z-r3')
out={'release':Path('/opt/blyb/current').resolve().name,'result':json.loads((folder/'result.json').read_text()) if (folder/'result.json').exists() else None}
for name,args in {'containers':['docker','ps','-a','--format','{{.Names}} {{.Status}}'],
                  'migrations':['docker','exec','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c','SELECT step,name FROM schema_migrations ORDER BY step'],
                  'backups':['ls','-t','<prod-path>/backups']}.items():
    p=subprocess.run(args,capture_output=True,text=True,timeout=25);out[name]={'exit_code':p.returncode,'summary':p.stdout[:4000]}
if (folder/'upgrade.log').exists():
    log=(folder/'upgrade.log').read_text(errors='replace')
    out['markers']={k:log.count(k) for k in ['healthy','备份完成','安装完成','升级完成','Traceback','ERROR']}
    out['error_types']=re.findall(r'\b[A-Za-z]+(?:Error|Exception)\b',log)[-10:]
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
