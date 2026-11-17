"""Generate a restricted local password file, deliver via pinned SSH + stdin."""
from pathlib import Path
import csv,importlib.util,io,json,re,secrets,subprocess

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
folder=ROOT/'.ai/private/console-password-20261026';folder.mkdir(parents=True,exist_ok=True)
sid=next(csv.reader(io.StringIO(subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True))))[1]
acl=subprocess.run(['icacls',str(folder),'/inheritance:r','/grant:r','*'+sid+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True)
if acl.returncode:raise RuntimeError('PRIVATE_DIRECTORY_ACL_FAILED')
credential=folder/'console-login.json'
if credential.exists():data=json.loads(credential.read_text('utf8'))
else:
    data={'username':'admin','password':secrets.token_urlsafe(18),'tenant':'first-hospital'}
    credential.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
spec=importlib.util.spec_from_file_location('password_provision_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
conn=fb.deploy.connect_jump(fb.creds()['jump'])
try:
    fb.feed_file(conn,'/run/blyb-console-login-input.json',json.dumps(data).encode(),mode='600')
    # Secrets are only in the protected input file and child stdin; output is sanitized.
    code=(HERE/'provision_remote.py').read_bytes()
    import base64,shlex
    rc,raw=fb.nested(conn,'python3 -c '+shlex.quote('import base64;exec(compile(base64.b64decode('+repr(base64.b64encode(code).decode())+'),"console-provision","exec"))'),timeout=120)
finally:conn.close()
match=re.search(r'__MAINTENANCE__(.*?)__END__',raw,re.S)
out={'exit_code':rc,'result':json.loads(match.group(1)) if match else {'marker_missing':True}}
(HERE/'evidence/provision-login.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
if rc or not match:raise RuntimeError('CONSOLE_PROVISION_OR_SMOKE_FAILED')
target=folder/'登录账号_仅本机.md'
target.write_text('# 系统网页登录账号（仅本机保存）\n\n'
 '地址：https://<prod-host>/ui/#/drugtrace\n\n用户名：'+data['username']+'\n\n密码：'+data['password']+'\n\n'
 '登录后可在左下角“修改密码”自行修改；修改后所有网页会话失效，需要重新登录。\n'
 '网页密码与SSH root密码、HIS密码、定时上报令牌互相独立。\n'
 '当前电脑到443的TLS握手问题属于访问链路问题，并未由更换登录方式解决；HTTP尚未启用。\n'
 'JSON文件供同机受控维护脚本读取，修改网页密码后请同步更新本机留存，脚本不会自动覆盖已存在账户密码。\n',encoding='utf8')
data.clear();del raw,match
print(json.dumps({'saved_to':str(target),'username':'admin','password_printed':False,'provision':out}))
