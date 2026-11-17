"""Strict TLS health GET from <prod-host>; no business request or verification bypass."""
import importlib.util
from pathlib import Path
import json
import shlex
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).with_name('evidence') / 'intranet-dmz-tls.json'


def load(n, f):
    s = importlib.util.spec_from_file_location(n, ROOT / 'docs/deployment/reporting-20261015' / f)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


def main():
    with OUT.open('x', encoding='utf-8') as f:
        f.write('{"status":"RESERVED_OUTCOME_UNCONFIRMED"}')
    profiles = load('saved_ssh', 'probe_ssh.py').credentials()
    deploy = load('pinned_ssh', 'deploy_remote.py')
    jump = deploy.connect_jump(profiles['jump'])
    # No -k, no business POST, no client credential copied to target.
    command = ("curl -4 --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 10 "
               "--output /dev/null --write-out "
               + shlex.quote('http_code=%{http_code} remote_ip=%{remote_ip} remote_port=%{remote_port} ssl_verify_result=%{ssl_verify_result} time_total=%{time_total}\n')
               + " https://<relay-net-host>/health")
    script = "printf '__TLS_BEGIN__\\n'\n" + command + "\nr=$?\nprintf '__CURL_EXIT__%s\\n' \"$r\"\nprintf '__TLS_END__\\n'"
    result = {'captured_at': datetime.now(timezone.utc).isoformat(), 'source': '<prod-host>',
              'destination': '<relay-net-host>:443', 'url': 'https://<relay-net-host>/health',
              'read_only': True, 'real_uploads': 0, 'tls_verification_bypassed': False,
              'client_certificate_presented': False, 'remote_changes': 0}
    try:
        rc, transcript = deploy.nested_exec(jump, profiles, script, timeout=25)
        result['ssh_exit_code'] = rc
        output = transcript.split('__TLS_BEGIN__', 1)[-1].split('__TLS_END__', 1)[0]
        for p in profiles.values():
            output = output.replace(p['password'], '[REDACTED]')
        result['output'] = output.replace('\r', '')
        result['probe_complete'] = '__TLS_END__' in transcript
    finally:
        jump.close()
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
