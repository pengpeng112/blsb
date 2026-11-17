"""Production UI test over pinned SSH, trusting only the server certificate SPKI.

The local listener is loopback-only and disappears at exit. No HAR, trace, video,
password screenshot, business-row screenshot, or business action is recorded.
"""
from pathlib import Path
import base64,hashlib,importlib.util,json,re,select,socketserver,threading
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
account=json.loads((ROOT/'.ai/private/console-password-20261026/console-login.json').read_text('utf8'))
spec=importlib.util.spec_from_file_location('password_browser_channel',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
conn=fb.deploy.connect_jump(fb.creds()['jump'])
result={'production':True,'transport':'pinned SSH jump TCP forwarding + server-specific SPKI trust','direct_management_tls_accepted':False,'business_actions':0}
stage='certificate_read';forward_errors=[]
server=None
try:
    rc,raw=fb.nested(conn,"python3 -c 'from pathlib import Path;import base64;print(\"__CERT__\"+base64.b64encode(Path(\"/etc/blyb/certs/tls.crt\").read_bytes()).decode()+\"__END__\")'",timeout=30)
    match=re.search(r'__CERT__([A-Za-z0-9+/=]+)__END__',raw)
    if rc or not match:raise RuntimeError('PUBLIC_CERTIFICATE_READ_FAILED')
    cert=x509.load_pem_x509_certificate(base64.b64decode(match.group(1)))
    pin=base64.b64encode(hashlib.sha256(cert.public_key().public_bytes(Encoding.DER,PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
    del raw,match
    class Forward(socketserver.BaseRequestHandler):
        def handle(self):
            channel=None
            try:
                channel=conn.get_transport().open_channel('direct-tcpip',('<prod-host>',443),self.request.getpeername())
                while True:
                    ready,_,_=select.select([self.request,channel],[],[],15)
                    if self.request in ready:
                        data=self.request.recv(65536)
                        if not data:break
                        channel.sendall(data)
                    if channel in ready:
                        data=channel.recv(65536)
                        if not data:break
                        self.request.sendall(data)
            except Exception as e:forward_errors.append(type(e).__name__)
            finally:
                if channel is not None:channel.close()
    class Loopback(socketserver.ThreadingTCPServer):
        daemon_threads=True
        allow_reuse_address=True
    server=Loopback(('127.0.0.1',0),Forward)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    base='https://blyb-console.internal:'+str(server.server_address[1])
    with sync_playwright() as p:
        stage='browser_launch'
        browser=p.chromium.launch(headless=True,args=['--no-proxy-server','--host-resolver-rules=MAP blyb-console.internal 127.0.0.1','--ignore-certificate-errors-spki-list='+pin])
        context=browser.new_context(viewport={'width':1200,'height':850})
        page=context.new_page();errors=[];server_errors=[]
        page.on('pageerror',lambda e:errors.append(type(e).__name__))
        page.on('response',lambda r:server_errors.append(r.status) if r.status>=500 else None)
        stage='browser_navigation'
        page.goto(base+'/ui/#/overview',wait_until='networkidle',timeout=45000)
        page.get_by_label('用户名',exact=True).wait_for()
        assert page.get_by_placeholder('访问令牌').count()==0
        page.screenshot(path=str(HERE/'evidence/production-login-before-fill.png'))
        stage='password_login'
        page.get_by_label('用户名',exact=True).fill(account['username'])
        page.get_by_label('密码',exact=True).fill(account['password'])
        page.get_by_role('button',name='登录',exact=True).click()
        page.locator('aside').wait_for(timeout=20000)
        page.get_by_role('button',name='修改密码',exact=True).wait_for()
        token=page.evaluate('sessionStorage.getItem("blyb.token")')
        assert token and page.evaluate('localStorage.getItem("blyb.token")') is None
        page.reload(wait_until='networkidle');page.locator('aside').wait_for(timeout=20000)
        page.get_by_role('button',name='退出',exact=True).click()
        page.get_by_label('用户名',exact=True).wait_for()
        status=page.evaluate("async t => (await fetch('/api/v1/auth/me',{headers:{Authorization:'Bearer '+t}})).status",token)
        assert status==401
        del token
        assert not errors and not server_errors
        result.update(rendered=True,password_login=True,reload_session=True,password_change_button=True,logout_revoked=True,console_errors=errors,server_errors=server_errors)
        context.close();browser.close()
except Exception as e:
    result.update(rendered=False,error_type=type(e).__name__,stage=stage,
                  browser_error_codes=re.findall(r'ERR_[A-Z_]+',str(e)),forward_error_types=sorted(set(forward_errors)))
finally:
    account.clear()
    if server:server.shutdown();server.server_close()
    conn.close()
    (HERE/'evidence/browser-production.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result))
raise SystemExit(0 if result.get('rendered') else 1)
