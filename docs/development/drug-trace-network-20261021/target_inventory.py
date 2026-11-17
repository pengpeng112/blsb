"""Read-only intranet inventory via existing, pinned ordinary second-hop SSH."""
from pathlib import Path
import base64
import importlib.util
import json
import shlex
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
OUT = HERE / 'evidence/intranet-inventory.json'


def load(name, file):
    s = importlib.util.spec_from_file_location(name, ROOT / 'docs/deployment/reporting-20261015' / file)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def main():
    with OUT.open('x', encoding='utf-8') as f:
        f.write('{"status":"RESERVED_OUTCOME_UNCONFIRMED"}')
    profiles = load('legacy_ssh', 'probe_ssh.py').credentials()
    deploy = load('pinned_deploy', 'deploy_remote.py')
    script = '''import json,socket,subprocess,time
out={}
def run(args):
 p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=12)
 return {"exit_code":p.returncode,"stdout":p.stdout,"stderr":p.stderr}
out["release"]=run(["readlink","-f","/opt/blyb/current"])
out["services"]=run(["docker","ps","--format","{{.Names}} {{.Image}} {{.Status}} {{.Ports}}"])
out["tcp"]=[]
for host,port in (("<relay-net-host>",443),("<relay-net-host>",8443),("<his-host>",1521)):
 s=socket.socket();s.settimeout(4);s.bind(("<prod-host>",0));t=time.monotonic()
 r={"source":s.getsockname(),"destination":[host,port]}
 try:s.connect((host,port));r["connected"]=True
 except OSError as e:r.update(connected=False,error_type=type(e).__name__)
 finally:s.close()
 r["elapsed_seconds"]=round(time.monotonic()-t,3);out["tcp"].append(r)
probe='import os,json;print(json.dumps({"plugins":os.environ.get("BLYB_PLUGINS",""),"alihealth_secret_present":bool(os.environ.get("BLYB_ALIHEALTH_APP_SECRET"))}))'
out["core_config_presence"]=run(["docker","exec","blyb-core-1","python","-c",probe])
out["worker_config_presence"]=run(["docker","exec","<worker-container>","python","-c",probe])
print("__BLYB_DATA_BEGIN__"+json.dumps(out)+"__BLYB_DATA_END__")
'''
    code = base64.b64encode(script.encode()).decode()
    command = 'python3 -c ' + shlex.quote("import base64;exec(base64.b64decode('" + code + "'))")
    jump = deploy.connect_jump(profiles['jump'])
    result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'read_only': True,
              'remote_changes': 0, 'real_uploads': 0, 'business_source_writes': 0}
    try:
        rc, transcript = deploy.nested_exec(jump, profiles, command, timeout=45)
        result['ssh_exit_code'] = rc
        if '__BLYB_DATA_BEGIN__' in transcript and '__BLYB_DATA_END__' in transcript:
            raw = transcript.split('__BLYB_DATA_BEGIN__', 1)[1].split('__BLYB_DATA_END__', 1)[0]
            result['inventory'] = json.loads(raw)
            result['status'] = 'CAPTURED'
        else:
            result['status'] = 'NO_INVENTORY'
            result['diagnostic'] = transcript[-1500:]
    except Exception as exc:
        result.update(status='LOCAL_OR_SSH_ERROR', error_type=type(exc).__name__)
    finally:
        jump.close()
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        for profile in profiles.values():
            encoded = encoded.replace(profile['password'], '[REDACTED]')
        OUT.write_text(encoded + '\n', encoding='utf-8')
    print(encoded)
    return 0 if result['status'] == 'CAPTURED' else 1


if __name__ == '__main__':
    raise SystemExit(main())
