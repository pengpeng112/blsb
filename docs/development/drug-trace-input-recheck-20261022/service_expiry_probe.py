"""One fixed official read-only service expiry request, signed locally.

Windows signer -> SSH stdin -> DMZ outbound curl -> official HTTPS. This is
diagnostic, not a production forwarder method or an upload-permission test.
"""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime,timedelta,timezone

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
PRIVATE=ROOT/'.ai/private/drug-trace-input-recheck-20261022'
METHOD='alibaba.alihealth.drugtrace.top.yljg.service.getenddate'


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    for key in ('BLYB_ALIHEALTH_APP_KEY','BLYB_ALIHEALTH_APP_SECRET','BLYB_ALIHEALTH_REF_ENT_ID'):
        if not os.getenv(key):raise SystemExit('Use the existing protected credential wrapper.')
    if not PRIVATE.is_dir():raise SystemExit('Protected private directory required.')
    suffix='-'+sys.argv[1] if len(sys.argv)>1 else ''
    if suffix and suffix!='-r2':raise SystemExit('Only the documented r2 revision is supported.')
    path=HERE/'evidence'/('service-expiry'+suffix+'.json')
    receipt={'captured_at':datetime.now(timezone.utc).isoformat(),'method':METHOD,
             'official_parameters_source':'https://developer.alibaba.com/docs/api.htm?apiId=66087',
             'route':'Windows_signer->SSH_stdin->DMZ_curl->official_HTTPS',
             'request_count':0,'upload_requests':0,'patient_data_sent':False,
             'app_secret_copied_to_DMZ':False,'remote_config_changes':0,
             'forwarder_allowlist_changed':False,'app_query_evidence_changed':False,
             'tls_verification':True,'retries':0,'redirects_allowed':False,
             'upload_permission_verified':False,'status':'RESERVED'}
    with path.open('x',encoding='utf-8') as stream:json.dump(receipt,stream)
    sys.path[:0]=[str(ROOT/'implementation/packages'),str(ROOT/'implementation/plugins')]
    protocol=load('expiry_protocol',ROOT/'implementation/plugins/destinations/alihealth_drugtrace/protocol.py')
    body,ctype=protocol.build_form(METHOD,os.environ['BLYB_ALIHEALTH_APP_KEY'],os.environ['BLYB_ALIHEALTH_APP_SECRET'],biz_params={'ref_ent_id':os.environ['BLYB_ALIHEALTH_REF_ENT_ID'],'business':'1','service':'11'},timestamp=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S'))
    client=None
    try:
        helper=load('expiry_dmz',ROOT/'docs/deployment/drug-trace-install-20261021/deploy_dmz.py')
        client=helper.connect()
        command=("curl -4 --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 20 "
                 "--request POST --header 'Content-Type: application/x-www-form-urlencoded;charset=utf-8' "
                 "--data-binary @- --write-out '\\n__BLYB_HTTP__%{http_code}|%{remote_ip}|%{ssl_verify_result}' "
                 "https://eco.taobao.com/router/rest")
        receipt['request_count']=1
        stdin,stdout,stderr=client.exec_command(command,timeout=25)
        stdin.write(body);stdin.flush();stdin.channel.shutdown_write()
        raw=stdout.read(1048577);stderr.read(8192)
        receipt['curl_exit_code']=stdout.channel.recv_exit_status()
        if len(raw)>1048576:raise ValueError('Response size limit')
        response,marker,meta=raw.rpartition(b'\n__BLYB_HTTP__')
        if marker:
            status,ip,tls=meta.decode('ascii').split('|')
            receipt.update(http_status=int(status),destination_ip=ip,ssl_verify_result=int(tls))
        else:response=raw
        receipt['response_bytes']=len(response)
        protect="$ErrorActionPreference='Stop'; $s=ConvertTo-SecureString ([Console]::In.ReadToEnd()) -AsPlainText -Force; try {[Console]::Out.Write((ConvertFrom-SecureString $s))} finally {$s.Dispose()}"
        encrypted=subprocess.run(['pwsh','-NoProfile','-NonInteractive','-Command',protect],input=response,capture_output=True,timeout=15,check=True).stdout
        raw_path=PRIVATE/('service-expiry-response'+suffix+'.dpapi')
        with raw_path.open('xb') as stream:stream.write(encrypted)
        receipt['encrypted_response']=raw_path.relative_to(ROOT).as_posix()
        if receipt['curl_exit_code']!=0:receipt['status']='TRANSPORT_ERROR'
        else:
            parsed=json.loads(response)
            if isinstance(parsed.get('error_response'),dict):
                err=parsed['error_response'];receipt.update(status='TOP_ERROR',error_code=str(err.get('code','')),sub_code=str(err.get('sub_code','')))
            else:
                result=parsed.get(protocol.method_response_key(METHOD),{}).get('result',{})
                receipt.update(status='BUSINESS_RESPONSE',success=result.get('success'),end_date=result.get('end_date'),msg_code=str(result.get('msg_code','')))
    except Exception as exc:receipt.update(status='ERROR',error_type=type(exc).__name__)
    finally:
        if client is not None:client.close()
        path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt,ensure_ascii=False,indent=2))
    return 0 if receipt.get('success') is True else 1


if __name__=='__main__':raise SystemExit(main())
