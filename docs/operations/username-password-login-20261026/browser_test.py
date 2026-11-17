"""Exercise production-built username/password UI against a synthetic local API."""
from pathlib import Path
import json,socket,subprocess,sys,tempfile,time,urllib.request
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
EVID=HERE/'evidence';EVID.mkdir(exist_ok=True)
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
base='http://127.0.0.1:'+str(port)
tmp=tempfile.TemporaryDirectory(prefix='blyb-auth-browser-')
log=(EVID/'synthetic-browser-server.log').open('wb')
child=subprocess.Popen([sys.executable,str(HERE/'serve_synthetic.py'),'--port',str(port),'--db',str(Path(tmp.name)/'synthetic.sqlite3')],stdout=log,stderr=subprocess.STDOUT)
result={'synthetic':True,'production_connection':False}
try:
    for _ in range(100):
        try:
            with urllib.request.urlopen(base+'/health',timeout=1) as r:assert r.status==200
            break
        except Exception:time.sleep(.2)
    else:raise RuntimeError('LOCAL_SERVER_NOT_READY')
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1200,'height':850})
        context.add_init_script('localStorage.setItem("blyb.core",'+json.dumps(base)+');localStorage.setItem("blyb.storage",'+json.dumps(base)+');')
        page=context.new_page();errors=[];server_errors=[]
        page.on('pageerror',lambda e:errors.append(type(e).__name__))
        page.on('response',lambda r:server_errors.append(r.status) if r.status>=500 else None)
        page.goto(base+'/ui/#/audit',wait_until='networkidle')
        page.get_by_label('用户名',exact=True).wait_for()
        assert page.get_by_placeholder('访问令牌').count()==0
        assert page.get_by_text('本地开发工具',exact=True).count()==0
        page.screenshot(path=str(EVID/'synthetic-login.png'))
        page.get_by_label('用户名',exact=True).fill('admin')
        page.get_by_label('密码',exact=True).fill('wrong')
        page.get_by_role('button',name='登录',exact=True).click()
        page.get_by_role('alert').filter(has_text='用户名或密码错误').wait_for()
        result['wrong_password_rejected']=True
        page.get_by_label('密码',exact=True).fill('Synthetic-browser-pass-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.locator('aside').wait_for()
        result['password_login']=True
        token=page.evaluate('sessionStorage.getItem("blyb.token")')
        assert token and page.evaluate('localStorage.getItem("blyb.token")') is None
        page.reload(wait_until='networkidle');page.locator('aside').wait_for()
        result['reload_session']=True
        page.get_by_role('button',name='修改密码',exact=True).click()
        page.get_by_label('原密码',exact=True).fill('Synthetic-browser-pass-123')
        page.get_by_label('新密码',exact=True).fill('Synthetic-changed-pass-123')
        page.get_by_label('确认新密码',exact=True).fill('Synthetic-changed-pass-123')
        page.get_by_role('button',name='保存',exact=True).click()
        page.get_by_label('用户名',exact=True).wait_for()
        result['password_change_requires_login']=True
        assert context.request.get(base+'/api/v1/auth/me',headers={'Authorization':'Bearer '+token}).status==401
        page.get_by_label('用户名',exact=True).fill('admin')
        page.get_by_label('密码',exact=True).fill('Synthetic-changed-pass-123')
        page.get_by_role('button',name='登录',exact=True).click()
        page.locator('aside').wait_for()
        new_token=page.evaluate('sessionStorage.getItem("blyb.token")')
        page.get_by_role('button',name='退出',exact=True).click()
        page.get_by_label('用户名',exact=True).wait_for()
        assert context.request.get(base+'/api/v1/auth/me',headers={'Authorization':'Bearer '+new_token}).status==401
        assert page.evaluate('sessionStorage.getItem("blyb.token")') is None
        result.update(logout_revokes_session=True,console_errors=errors,server_errors=server_errors)
        assert not errors and not server_errors
        context.close();browser.close()
finally:
    child.terminate()
    try:child.wait(timeout=10)
    except subprocess.TimeoutExpired:child.kill();child.wait()
    log.close();tmp.cleanup()
    (EVID/'browser-synthetic.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result))
