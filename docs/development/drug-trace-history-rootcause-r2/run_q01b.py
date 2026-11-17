"""Q01b: bounded two-view fetch of bill_time for the 531 missing codes' view rows.

Purpose: compare historical scan time (from the Excel, = old manual SQL
CREATE_TIME export) with the current view dispense time for the same code
under its 14-digit line, to distinguish same-event-different-id from
different-event records. Two batches, same restricted reader and budget.
"""
from pathlib import Path
from datetime import datetime, timezone
import base64
import gzip
import importlib.util
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
R1 = ROOT / 'docs/development/drug-trace-history-live-compare-20261023'

REMOTE_BODY = '''
try:
    auth=query('identity',"SELECT USER AS LOGIN_USER,SYS_CONTEXT('USERENV','SERVICE_NAME') AS SERVICE_NAME FROM DUAL",limit=1)
    if auth is None or auth[0]['LOGIN_USER']!='BLYB_DRUGTRACE':raise RuntimeError('READER_MISMATCH')
    import time as _t
    started=_t.monotonic()
    codes=list(json.loads(gzip.decompress(base64.b64decode(CODES_GZ))))
    out['batches']=[]
    collected=[]
    for start in range(0,len(codes),500):
        batch=codes[start:start+500]
        binds={'c'+str(i):code for i,code in enumerate(batch)}
        sql='SELECT source_line_id,trace_code,source_code_record_id,business_kind,is_deleted,bill_time FROM PHARMACY.V_DRUG_TRACE_CODE WHERE trace_code IN ('+','.join(':'+k for k in binds)+') AND ROWNUM<=10000'
        begin=_t.monotonic()
        rows=c.execute_readonly(sql,binds,max_rows=10000)
        out['batches'].append({'codes':len(batch),'rows':len(rows),'seconds':round(_t.monotonic()-begin,4),'cap_reached':len(rows)>=10000})
        for r in rows:
            collected.append({'bill':str(r['SOURCE_LINE_ID']),'code':str(r['TRACE_CODE']),'rid':str(r['SOURCE_CODE_RECORD_ID']),'kind':str(r['BUSINESS_KIND']),'deleted':str(r['IS_DELETED']),'bt':str(r['BILL_TIME'])})
    blob={'view_rows':collected}
    out['private_detail_gzip_base64']=base64.b64encode(gzip.compress(json.dumps(blob,ensure_ascii=False).encode())).decode()
    out['run_completed']=True
    out['source_writes']=0;out['uploads']=0
    out['total_elapsed_seconds']=round(_t.monotonic()-started,3)
finally:
    c.close()
print(json.dumps(out,ensure_ascii=False,default=str))
'''


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if '--approved-daytime' not in sys.argv:
        raise SystemExit('Current daytime approval required')
    r2_live = json.loads(sorted((HERE / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    private_dir = Path(r2_live['private_dir'])
    r1_diag = json.loads((R1 / 'evidence/diagnose-r1.json').read_text('utf8'))
    r1_states = json.loads(Path(r1_diag['private_details_path']).read_text('utf8'))
    codes = sorted({a['code'] for a in r1_states['missing_annotated']})
    codes_gz = base64.b64encode(gzip.compress(json.dumps(codes).encode())).decode()

    base = load('live_base', ROOT / 'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    prefix = base.REMOTE.split('try:\n    auth=query')[0].replace("'timeout_ms':45000", "'timeout_ms':30000")
    remote = prefix + 'import base64,gzip\nCODES_GZ=' + repr(codes_gz) + '\n' + REMOTE_BODY
    for line in list(remote.splitlines()):
        if line.startswith('from app.core.database ') or line.startswith('from app.models.asset '):
            remote = remote.replace(line, '')

    credential = ROOT / '.ai/private/drug-trace-view-verify-20261022/his-reader.credential.clixml'
    ps = ("$c=Import-Clixml -LiteralPath '" + str(credential).replace("'", "''") +
          "';@{user=$c.UserName;password=$c.GetNetworkCredential().Password}|ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', ps],
                            capture_output=True, text=True, timeout=15, check=True)
    credentials = json.loads(result.stdout)
    sjzc = load('live_sjzc', Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    imports = '\n'.join(line for line in sjzc.LIVE_CODE.splitlines() if line.startswith('from app.'))
    remote = imports + '\nCREDS=' + repr(credentials) + '\n' + remote
    credentials.clear()

    out = HERE / 'evidence' / ('r2-q01b-times-' + r2_live['run_id'] + '.json')
    with out.open('x', encoding='utf8') as f:
        json.dump({'status': 'RESERVED'}, f)
    proc = subprocess.run(['ssh', '-i', sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
                           'docker exec -i -e PYTHONPATH=/app '
                           '-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
                          input=remote.encode(), capture_output=True, timeout=300)
    try:
        data = json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data = {'run_completed': False, 'error_type': type(exc).__name__,
                'ssh_exit_code': proc.returncode,
                'oracle_codes': sorted(set(re.findall(
                    r'(?:ORA|DPY|DPI)-\d+', proc.stderr.decode(errors='replace'))))}
    blob = data.pop('private_detail_gzip_base64', None)
    if blob is not None:
        target = private_dir / 'q01b-view-times.json'
        target.write_bytes(gzip.decompress(base64.b64decode(blob)))
        data['private_details_path'] = str(target)
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    data['run_id'] = r2_live['run_id']
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    printable = {k: v for k, v in data.items() if k not in ('queries', 'batches', 'registration')}
    print(json.dumps(printable, ensure_ascii=False))
    return 0 if data.get('run_completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
