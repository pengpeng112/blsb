"""In-memory remote install. Payload contains code/unit only, never secrets."""
from pathlib import Path
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import time


def run(args, check=True):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=40)
    if check and p.returncode:
        raise RuntimeError('COMMAND_FAILED:' + args[0] + ':' + str(p.returncode))
    return p.stdout.decode('utf8', 'replace').strip()


paths = {'daily_upload_driver.py': Path('/opt/blyb/tools/blyb-daily-upload.py'),
         'blyb-daily-upload.service': Path('/etc/systemd/system/blyb-daily-upload.service')}
expected = {'daily_upload_driver.py': '90fc2814186b0bd9cef862a2a3fa48857818c7afb25cfb365010a079f6ddfaa8',
            'blyb-daily-upload.service': '2b34f1d3804bc8751d6883291490025c5d6d5d93a2c3c052cdb650c7028f9d26'}
if EXPECTED:
    expected = EXPECTED
result = {'installed': False, 'started': False, 'business_containers_restarted': 0}
backup = None
timer_active = run(['systemctl', 'is-active', 'blyb-daily-upload.timer'], check=False) == 'active'
mutated = False
try:
    state = run(['systemctl', 'show', 'blyb-daily-upload.service', '-p', 'ActiveState', '--value'])
    if state in ('active', 'activating', 'deactivating') and not EXTEND_WAIT:
        raise RuntimeError('DAILY_SERVICE_ALREADY_RUNNING')
    for name, path in paths.items():
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected[name]:
            raise RuntimeError('BASELINE_DRIFT:' + name)
    driver = base64.b64decode(PAYLOAD['daily_upload_driver.py'])
    unit = base64.b64decode(PAYLOAD['blyb-daily-upload.service'])
    compile(driver, 'repaired-driver', 'exec')
    if b'printf "%%s' not in unit or b'printf "%s' in unit:
        raise RuntimeError('PRINTF_NOT_ESCAPED')
    backup = Path('/root/blyb-timer-backups') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    os.chmod(backup.parent, 0o700)
    for name, path in paths.items():
        shutil.copy2(path, backup / ('original-' + name))
    old_log = Path('/var/log/blyb/daily-upload/last-error.log')
    if old_log.exists():
        shutil.copy2(old_log, backup / 'first-failure-error.log')
    (backup / 'blyb-daily-upload.service').write_bytes(unit)
    # Validation only; does not start the service or send any data.
    run(['systemd-analyze', 'verify', str(backup / 'blyb-daily-upload.service')])
    if EXTEND_WAIT and state in ('active', 'activating'):
        # This driver is only waiting for the independently running collect job.
        # Refuse replacement if sends have begun; never stop the worker/source job.
        sql = "SELECT json_build_object('attempts',(SELECT count(*) FROM delivery_attempt WHERE started_at>='2026-10-25 09:57:00'),'running_collections',(SELECT count(*) FROM drug_trace_collection_run WHERE state='RUNNING' AND window_begin='2026-10-24' AND window_end='2026-10-25'))"
        checked = json.loads(run(['docker', 'exec', '-e', 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=10000', 'blyb-db-1', 'psql', '-X', '-U', 'blyb', '-d', 'blyb', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql]))
        if checked != {'attempts': 0, 'running_collections': 1}:
            raise RuntimeError('NOT_SAFE_TO_REPLACE_WAITING_DRIVER')
        killer = '''from pathlib import Path
import os,signal,json
found=[]
for path in Path('/proc').glob('[0-9]*/cmdline'):
 try:args=path.read_bytes().split(bytes([0]))
 except OSError:continue
 if b'/tmp/daily-upload.py' in args:found.append(int(path.parent.name))
if len(found)!=1:raise RuntimeError('DRIVER_PROCESS_NOT_UNIQUE')
os.kill(found[0],signal.SIGTERM)
print(json.dumps({'driver_only_terminated':True}))
'''
        result['waiting_driver_replacement'] = json.loads(run(['docker', 'exec', '-u', '0', 'blyb-core-1', 'python', '-c', killer]))
        for _ in range(20):
            state = run(['systemctl', 'show', 'blyb-daily-upload.service', '-p', 'ActiveState', '--value'])
            if state not in ('active', 'activating', 'deactivating'):
                break
            time.sleep(.5)
        if state in ('active', 'activating', 'deactivating'):
            raise RuntimeError('WAITING_DRIVER_DID_NOT_EXIT')
        old_receipt = Path('/var/log/blyb/daily-upload/20261025-175741.json')
        if old_receipt.exists() and old_receipt.stat().st_size == 0:
            old_receipt.write_text(json.dumps({'outcome':'FAILED','fatal':'waiting_driver_replaced_for_long_batch',
                 'window':['2026-10-24T00:00:00','2026-10-25T00:00:00'],
                 'source_collect_job_preserved':True,'send_attempts_at_replacement':0})+'\n')
        result['source_collection_preserved'] = True
    if timer_active:
        run(['systemctl', 'stop', 'blyb-daily-upload.timer'])
    mutated = True
    for name, path in paths.items():
        path.write_bytes(base64.b64decode(PAYLOAD[name]))
        os.chmod(path, 0o700 if name.endswith('.py') else 0o644)
    run(['systemctl', 'daemon-reload'])
    effective = run(['systemctl', 'show', 'blyb-daily-upload.service', '-p', 'ExecStart', '--value'])
    match = re.search(r'printf\s+"([^"\r\n]+)"', effective)
    if not match or not re.fullmatch(r'%s(?:\\+n)', match.group(1)):
        raise RuntimeError('EFFECTIVE_PRINTF_INVALID')
    fake = subprocess.run(['/bin/bash', '-c', 'printf "' + match.group(1) + '" "PUBLIC_DUMMY"'], stdout=subprocess.PIPE)
    if fake.stdout != b'PUBLIC_DUMMY\n':
        raise RuntimeError('EFFECTIVE_PRINTF_DUMMY_MISMATCH')
    run(['systemctl', 'reset-failed', 'blyb-daily-upload.service'], check=False)
    if timer_active:
        run(['systemctl', 'start', 'blyb-daily-upload.timer'])
    result.update({'installed': True, 'backup_dir': str(backup), 'public_dummy_passed': True,
                   'hashes': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}})
    if START:
        # Existing standing evidence/scope/quality gates remain in the API/worker.
        run(['systemctl', 'start', '--no-block', 'blyb-daily-upload.service'])
        result['started'] = True
    result['service_state'] = run(['systemctl', 'show', 'blyb-daily-upload.service', '-p', 'ActiveState', '--value'])
except Exception as exc:
    result['error_kind'] = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
    if mutated and not result['started']:
        for name, path in paths.items():
            shutil.copy2(backup / ('original-' + name), path)
        run(['systemctl', 'daemon-reload'], check=False)
        if timer_active:
            run(['systemctl', 'start', 'blyb-daily-upload.timer'], check=False)
        result['rolled_back'] = True
    result['installed'] = False
print('__REPAIR_JSON__' + json.dumps(result, ensure_ascii=True) + '__REPAIR_END__')
