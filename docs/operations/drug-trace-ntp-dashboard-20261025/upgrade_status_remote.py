from pathlib import Path
import json
import subprocess

folder=Path('/root/blyb-maintenance-backups/upgrade-ui-20261025T140000Z-r2')
out={'current':str(Path('/opt/blyb/current').resolve()),
     'result':json.loads((folder/'result.json').read_text()) if (folder/'result.json').exists() else None}
for key,args in {'containers':['docker','ps','-a','--format','{{.Names}} {{.Status}}'],
                 'migrations':['docker','exec','blyb-db-1','psql','-X','-U','blyb','-d','blyb','-At','-c','SELECT step,name FROM schema_migrations ORDER BY step'],
                 'backup_dirs':['ls','-t','<prod-path>/backups']}.items():
    p=subprocess.run(args,capture_output=True,text=True,timeout=20);out[key]={'rc':p.returncode,'out':p.stdout[:4000]}
if (folder/'upgrade.log').exists():
    text=(folder/'upgrade.log').read_text(errors='replace')
    out['log_bytes']=len(text)
    out['markers']={k:text.count(k) for k in ['healthy','备份完成','安装完成','升级完成','Traceback','ERROR','错误','permission denied','Permission denied']}
    out['error_types']=__import__('re').findall(r'\b[A-Za-z]+(?:Error|Exception)\b',text)[-10:]
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
