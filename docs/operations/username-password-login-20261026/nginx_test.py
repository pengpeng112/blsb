"""Production nginx config + real isolated request throttling, synthetic only."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import ipaddress,json,ssl,subprocess,tempfile,time,urllib.request,urllib.error,uuid
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
RID='auth-20261026T043500Z';tag='blyb/nginx:'+RID
suffix=uuid.uuid4().hex[:8];network='blyb-auth-net-'+suffix;core='blyb-auth-core-'+suffix;nginx='blyb-auth-nginx-'+suffix
def run(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=60)
    if p.returncode:raise RuntimeError(args[0]+'_EXIT_'+str(p.returncode))
    return p.stdout.strip()
tmp=tempfile.TemporaryDirectory(prefix='blyb-auth-nginx-');folder=Path(tmp.name)
key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic loopback test')])
cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
      .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=1))
      .not_valid_after(datetime.now(timezone.utc)+timedelta(hours=1))
      .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
      .sign(key,hashes.SHA256()))
(folder/'tls.crt').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(folder/'tls.key').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
result={'synthetic':True,'production_connection':False}
try:
    run(['docker','network','create',network])
    server='''from http.server import BaseHTTPRequestHandler,HTTPServer
class H(BaseHTTPRequestHandler):
 def do_POST(self):
  self.rfile.read(int(self.headers.get('Content-Length',0)))
  self.send_response(401);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"error":{"code":"UNAUTHORIZED"}}')
 def log_message(self,*args):pass
HTTPServer(('0.0.0.0',8000),H).serve_forever()
'''
    run(['docker','run','-d','--name',core,'--network',network,'--network-alias','core','--network-alias','storage','--network-alias','exchange','blyb/backend:'+RID,'python','-c',server])
    config=ROOT/'implementation/deploy/compose/nginx/nginx.conf'
    run(['docker','run','-d','--name',nginx,'--network',network,'-p','127.0.0.1::443',
         '--mount','type=bind,source='+str(config)+',target=/etc/nginx/conf.d/default.conf,readonly',
         '--mount','type=bind,source='+str(folder)+',target=/etc/blyb/certs,readonly',tag])
    run(['docker','exec',nginx,'nginx','-t']);result['nginx_config_exit']=0
    port=run(['docker','port',nginx,'443/tcp']).rsplit(':',1)[1]
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(folder/'tls.crt'))))
    time.sleep(1)
    statuses=[]
    for _ in range(10):
        req=urllib.request.Request('https://127.0.0.1:'+port+'/api/v1/auth/login',data=b'{"token":"synthetic-incorrect"}',headers={'Content-Type':'application/json'})
        try:
            with opener.open(req,timeout=10) as r:statuses.append(r.status)
        except urllib.error.HTTPError as e:statuses.append(e.code)
    assert 401 in statuses and 429 in statuses and set(statuses)<={401,429}
    result.update(statuses=statuses,rate_limit_observed=True,tls_verified=True)
finally:
    for name in [nginx,core]:subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
    subprocess.run(['docker','network','rm',network],capture_output=True,timeout=30)
    tmp.cleanup()
    (HERE/'evidence/nginx-synthetic.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf8')
print(json.dumps(result))
