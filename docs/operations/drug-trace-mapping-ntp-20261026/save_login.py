"""Export the user-requested console credential to an ACL-restricted local file.

Never prints the credential or raw SSH transcript. Does not read hosted source credentials.
"""
from pathlib import Path
import csv,importlib.util,io,re,subprocess

ROOT=Path(__file__).resolve().parents[3]
folder=ROOT/'.ai/private/drug-trace-console-20261026'
folder.mkdir(parents=True,exist_ok=True)
rows=csv.reader(io.StringIO(subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True)))
sid=next(rows)[1]
p=subprocess.run(['icacls',str(folder),'/inheritance:r','/grant:r','*'+sid+':(OI)(CI)F','*S-1-5-18:(OI)(CI)F'],capture_output=True)
if p.returncode:raise RuntimeError('PRIVATE_DIRECTORY_ACL_FAILED')
spec=importlib.util.spec_from_file_location('login_pinned_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
conn=fb.deploy.connect_jump(fb.creds()['jump'])
try:
    # The outer tool never receives the transcript: it is parsed only in local memory.
    code="from pathlib import Path; t=next(x.split('=',1)[1].strip() for x in Path('/etc/blyb/runtime.env').read_text().splitlines() if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN=')); print('__LOGIN__'+t+'__END__')"
    import shlex
    rc,raw=fb.nested(conn,'python3 -c '+shlex.quote(code),timeout=30)
finally:conn.close()
if rc:raise RuntimeError('LOGIN_EXPORT_SSH_FAILED')
matches=re.findall(r'__LOGIN__([^\r\n]+?)__END__',raw)
token=next((s for s in reversed(matches) if len(s)>20 and not any(c in s for c in "'\" ")),None)
if not token:raise RuntimeError('LOGIN_EXPORT_MARKER_MISSING')
target=folder/'看板登录信息_仅本机.md'
target.write_text('# 看板登录信息（仅本机受限保存）\n\n'
                  '地址：https://<prod-host>/ui/#/drugtrace\n\n'
                  '登录方式：访问令牌；没有独立用户名和密码。管理身份为bootstrap-admin。\n\n'
                  '在登录页“访问令牌”输入框粘贴下列一整行：\n\n```text\n'+token+'\n```\n\n'
                  '此令牌具有管理权限，不是SSH的root密码。不要放入共享文档或截图。\n'
                  '如果浏览器曾保存旧后端地址，清空“后端地址”配置，使用同源入口；Storage默认/storage。\n',encoding='utf8')
del token,raw,matches
print('Saved local console login file: '+str(target))
print('Restricted directory ACL: verified command exit 0; no token printed.')
