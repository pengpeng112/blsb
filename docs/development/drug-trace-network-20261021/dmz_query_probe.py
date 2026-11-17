"""One signed read-only query from DMZ via SSH stdin; no remote files or secrets.

The official reference signs on Windows. This diagnoses the DMZ outbound path;
it is not the application's <prod-host> -> mTLS forwarder production acceptance.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone
from urllib.parse import parse_qs

import paramiko

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / 'evidence/dmz-signed-query.json'
PRIVATE = ROOT / '.ai/private/drug-trace-retest-20261021'


def main():
    receipt = {'captured_at': datetime.now(timezone.utc).isoformat(),
               'route': 'Windows_official_CSharp_signer_SSH_stdin_to_DMZ_curl_to_official_HTTPS',
               'source_server': '<relay-net-host>', 'endpoint': 'https://eco.taobao.com/router/rest',
               'method': 'alibaba.alihealth.drugtrace.top.yljg.query.billstatus',
               'request_count': 0, 'real_uploads': 0, 'patient_data_sent': False,
               'app_secret_copied_to_DMZ': False, 'remote_files_written': 0,
               'redirects_allowed': False, 'retries': 0, 'tls_verification': True,
               'app_signing_gate_changed': False, 'status': 'RESERVED_OUTCOME_UNCONFIRMED'}
    if not os.environ.get('BLYB_ALIHEALTH_APP_SECRET') or not PRIVATE.is_dir():
        raise SystemExit('Use the private wrapper and protected task directory.')
    with OUT.open('x', encoding='utf-8') as f:
        json.dump(receipt, f)
    client = paramiko.SSHClient()
    try:
        signer = subprocess.run(['pwsh', '-NoProfile', '-File', str(HERE / 'build_readonly_form.ps1')],
                                capture_output=True, timeout=15, check=True)
        form = signer.stdout.strip()
        values = parse_qs(form.decode('utf-8'))
        if values.get('method') != [receipt['method']]:
            raise ValueError('Fixed readonly method mismatch')
        receipt['probe_bill_code'] = values['bill_code'][0]
        ps = """$ErrorActionPreference='Stop'
$c=Import-Clixml -LiteralPath '.ai/private/reporting-20261015/dmz-credential.clixml'
@{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress
"""
        cp = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps],
                            cwd=ROOT, capture_output=True, text=True, timeout=12, check=True)
        credential = json.loads(cp.stdout)
        observed = json.loads((ROOT / 'docs/deployment/reporting-20261015/revision-6/followup-evidence-20261016T085216Z.json').read_text('utf-8'))[
            'local_to_dmz_ssh_handshake']['fingerprint']

        class MatchPin(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, ssh, hostname, key):
                actual = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                if actual != observed:
                    raise paramiko.SSHException('DMZ pinned key changed')

        client.set_missing_host_key_policy(MatchPin())
        client.connect('<relay-net-host>', port=22, username=credential['user'], password=credential['password'],
                       allow_agent=False, look_for_keys=False, timeout=7, banner_timeout=10, auth_timeout=12)
        # No signed parameters in argv, environment, shell text, or remote files.
        command = ("curl -4 --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 20 "
                   "--request POST --header 'Content-Type: application/x-www-form-urlencoded;charset=utf-8' "
                   "--data-binary @- --write-out '\\n__BLYB_HTTP__%{http_code}|%{remote_ip}|%{ssl_verify_result}' "
                   "https://eco.taobao.com/router/rest")
        receipt['request_count'] = 1
        stdin, stdout, stderr = client.exec_command(command, timeout=25)
        stdin.write(form)
        stdin.flush()
        stdin.channel.shutdown_write()
        raw = stdout.read(1048577)
        err = stderr.read(8192)  # Captured; never print reflected request details.
        code = stdout.channel.recv_exit_status()
        receipt['curl_exit_code'] = code
        if len(raw) > 1048576:
            raise ValueError('Probe response limit exceeded')
        body, marker, meta = raw.rpartition(b'\n__BLYB_HTTP__')
        if marker:
            status, ip, tls = meta.decode('ascii').split('|')
            receipt.update(http_status=int(status), destination_ip=ip, ssl_verify_result=int(tls))
        else:
            body = raw
        # Protect raw response with Windows DPAPI. It never enters AI output/logs.
        protect = """$ErrorActionPreference='Stop'
$s=ConvertTo-SecureString ([Console]::In.ReadToEnd()) -AsPlainText -Force
try {[Console]::Out.Write((ConvertFrom-SecureString $s))}finally{$s.Dispose()}
"""
        encrypted = subprocess.run(['pwsh', '-NoProfile', '-Command', protect], input=body,
                                   capture_output=True, timeout=10, check=True).stdout
        private_path = PRIVATE / 'dmz-query-response.dpapi'
        with private_path.open('xb') as f:
            f.write(encrypted)
        receipt['encrypted_response'] = private_path.relative_to(ROOT).as_posix()
        if code != 0:
            receipt['status'] = 'DMZ_TRANSPORT_ERROR'
        else:
            parsed = json.loads(body)
            if isinstance(parsed.get('error_response'), dict):
                error = parsed['error_response']
                receipt.update(status='TOP_ERROR', error_code=str(error.get('code', '')),
                               sub_code=str(error.get('sub_code', '')))
            else:
                result = parsed['alibaba_alihealth_drugtrace_top_yljg_query_billstatus_response']['result']
                receipt.update(status='BUSINESS_RESPONSE', response_success=result.get('response_success'),
                               msg_code=str(result.get('msg_code', '')), total_num=(result.get('model') or {}).get('total_num'))
        receipt['upload_permission_verified'] = False
        receipt['production_forwarder_verified'] = False
    except Exception as exc:
        receipt.update(status='LOCAL_OR_TRANSPORT_ERROR', error_type=type(exc).__name__)
    finally:
        client.close()
        OUT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt.get('response_success') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
