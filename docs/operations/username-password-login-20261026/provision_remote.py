from pathlib import Path
from datetime import datetime,timezone
import json,subprocess

if Path('/opt/blyb/current').resolve().name!='auth-20261026T043500Z':raise RuntimeError('AUTH_RELEASE_NOT_ACTIVE')
source=Path('/run/blyb-console-login-input.json')
data=json.loads(source.read_text());source.unlink()
if data['username']!='admin' or data['tenant']!='first-hospital':raise RuntimeError('IDENTITY_INPUT_INVALID')
code='''import json,sys,urllib.request,urllib.error
from core_db import make_engine,make_session_factory
from core_db.models import ConsoleAccount
from contracts.passwords import provision_account
data=json.load(sys.stdin)
engine=make_engine();sf=make_session_factory(engine)
with sf() as db:
 exists=db.query(ConsoleAccount).filter_by(username=data['username']).first()
 if exists is None:
  provision_account(db,tenant_code=data['tenant'],username=data['username'],password=data['password'],display_name='系统管理员',roles=['platform_admin'])
def call(method,path,body=None,token=None,port=8000):
 req=urllib.request.Request('http://localhost:'+str(port)+'/api/v1'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})},method=method)
 try:
  with urllib.request.urlopen(req,timeout=20) as r:return r.status,json.load(r).get('data')
 except urllib.error.HTTPError as e:return e.code,None
bad,_=call('POST','/auth/login',{'username':data['username'],'password':'deliberately-incorrect'})
status,user=call('POST','/auth/login',{'username':data['username'],'password':data['password']})
assert status==200 and user['code']=='console-admin' and 'platform_admin' in user['roles']
token=user['access_token']
me,_=call('GET','/auth/me',token=token)
req=urllib.request.Request('http://storage:8001/api/v1/storage/volumes',headers={'Authorization':'Bearer '+token})
with urllib.request.urlopen(req,timeout=20) as r:storage=r.status;r.read()
logout,_=call('POST','/auth/logout',{},token)
revoked,_=call('GET','/auth/me',token=token)
assert (bad,me,storage,logout,revoked)==(401,200,200,200,401)
print(json.dumps({'username':data['username'],'account_existed':exists is not None,'bad_password_status':bad,'login_status':status,'me_status':me,'storage_status':storage,'logout_status':logout,'revoked_status':revoked,'password_or_token_logged':False}))
'''
p=subprocess.run(['docker','exec','-i','blyb-core-1','python','-c',code],input=json.dumps(data).encode(),capture_output=True,timeout=100)
data.clear()
if p.returncode:raise RuntimeError('PASSWORD_PROVISION_SMOKE_EXIT_'+str(p.returncode))
out=json.loads(p.stdout);out['utc']=datetime.now(timezone.utc).isoformat()
print('__MAINTENANCE__'+json.dumps(out)+'__END__')
