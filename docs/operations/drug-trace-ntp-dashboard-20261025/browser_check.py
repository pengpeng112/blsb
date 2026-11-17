"""Read-only UI verification. Trust one SSH-authenticated certificate SPKI."""
from pathlib import Path
import base64
import hashlib
import importlib.util
import json
import re
import socket
import ssl
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from playwright.sync_api import sync_playwright

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]


def main():
    try:
        with socket.create_connection(('<prod-host>',443),timeout=3):pass
    except OSError as e:
        return {'rendered':False,'reason':'LOCAL_TO_APP_443_UNREACHABLE','error_type':type(e).__name__}
    spec=importlib.util.spec_from_file_location('ui_verifier',ROOT/'docs/development/drug-trace-firstbill-exec-20261021/deploy_fb05.py')
    fb=importlib.util.module_from_spec(spec);spec.loader.exec_module(fb)
    conn=fb.deploy.connect_jump(fb.creds()['jump'])
    code="from pathlib import Path;import json,base64;env=Path('/etc/blyb/runtime.env').read_text().splitlines();t=next(x.split('=',1)[1].strip() for x in env if x.startswith('BLYB_BOOTSTRAP_ADMIN_TOKEN='));print('__UI_MEMORY__'+json.dumps({'token':t,'cert':base64.b64encode(Path('/etc/blyb/certs/tls.crt').read_bytes()).decode()})+'__END__')"
    import shlex
    try:rc,raw=fb.nested(conn,'python3 -c '+shlex.quote(code),timeout=30)
    finally:conn.close()
    match=re.search(r'__UI_MEMORY__(.*?)__END__',raw,re.S)
    if rc or not match:return {'rendered':False,'reason':'PINNED_SSH_READ_FAILED'}
    data=json.loads(match.group(1));del raw,match
    cert=x509.load_pem_x509_certificate(base64.b64decode(data['cert']))
    try:
        tls=ssl.create_default_context(cadata=base64.b64decode(data['cert']).decode())
        with socket.create_connection(('<prod-host>',443),timeout=5) as tcp:
            with tls.wrap_socket(tcp,server_hostname='blyb-<prod-host>') as channel:
                channel.sendall(b'GET /ui/ HTTP/1.1\r\nHost: <prod-host>\r\nConnection: close\r\n\r\n')
                status=channel.recv(4096).split(b'\r\n',1)[0].decode('ascii','replace')
        direct_tls={'status_line':status}
    except Exception as exc:
        direct_tls={'error_type':type(exc).__name__,'errno':getattr(exc,'errno',None)}
    spki=base64.b64encode(hashlib.sha256(cert.public_key().public_bytes(Encoding.DER,PublicFormat.SubjectPublicKeyInfo)).digest()).decode()
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,args=['--ignore-certificate-errors-spki-list='+spki,'--no-proxy-server',
            '--host-resolver-rules=MAP blyb-<prod-host> <prod-host>'])
        context=browser.new_context(viewport={'width':1500,'height':1000})
        context.add_init_script('localStorage.setItem("blyb.token",'+json.dumps(data['token'])+');localStorage.setItem("blyb.roles",JSON.stringify(["platform_admin","operator"]));')
        data.clear()
        page=context.new_page();errors=[];bad_responses=[]
        page.on('pageerror',lambda e:errors.append(type(e).__name__))
        page.on('response',lambda r:bad_responses.append(r.status) if r.status>=400 else None)
        try:
            page.goto('https://blyb-<prod-host>/ui/#/drugtrace',wait_until='networkidle',timeout=45000)
        except Exception as exc:
            context.close();browser.close()
            return {'rendered':False,'reason':'BROWSER_TLS_OR_NETWORK_FAILURE','error_type':type(exc).__name__,'direct_tls':direct_tls}
        card=page.locator('.card').filter(has=page.get_by_role('heading',name='定时上报（自动化）',exact=True))
        card.wait_for(timeout=15000)
        # No business-row table or patient fields are included in this image.
        card.screenshot(path=str(HERE/'evidence/dashboard-production.png'))
        result={'rendered':True,'errors':errors,'http_errors':bad_responses,
                'timed_panel_visible':card.is_visible(),'screenshot':'evidence/dashboard-production.png',
                'certificate_trust':'single SPKI authenticated over pinned SSH; no global TLS bypass',
                'clicked_send_or_approve':False}
        context.close();browser.close()
        return result


if __name__=='__main__':
    out=main();(HERE/'evidence/browser.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf8');print(json.dumps(out))
