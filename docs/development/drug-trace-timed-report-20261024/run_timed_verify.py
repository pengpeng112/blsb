"""Timed-report verification round (user-authorized 2026-10-24).

Part 1 (E06'/E07' re-verify): current raw states of the 1396 refund pairs and
the 31 dup-group pairs - same mechanism as r2 Q02/Q03.
Part 2 (scope analysis): per-day counts of view bills by business_kind /
finalized plus code rows for 2026-10-12 .. 2026-10-25 (the gap after manual
history ended) - how many bills a scheduled daily run would collect/send/hold.
Restricted reader BLYB_DRUGTRACE, two views only, aggregates to shared
evidence, identifiers to private dir only.
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
R2 = ROOT / 'docs/development/drug-trace-history-rootcause-r2'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REMOTE_BODY = (HERE / 'timed_verify_remote.py').read_text('utf8')


def main():
    if '--authorized' not in sys.argv:
        raise SystemExit('Explicit user authorization required')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    prev = json.loads(sorted((R2 / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    priv_base = Path(prev['private_dir']).parent
    private = priv_base / ('timed-' + run_id)
    private.mkdir(parents=True, exist_ok=False)
    diag = json.loads((R2.parent / 'drug-trace-history-live-compare-20261023/'
                       'evidence/diagnose-r1.json').read_text('utf8'))
    r1_states = json.loads(Path(diag['private_details_path']).read_text('utf8'))
    excluded_pairs = [[s['bill'], s['code']] for s in r1_states['excluded_states']]
    detail = json.loads(Path(prev['private_details_path']).read_text('utf8'))
    dup_pairs = [[r['bill'], r['code']] for r in detail['dup_pair_view_rows']]

    payload = {'excluded': excluded_pairs, 'dup': dup_pairs}
    compressed = base64.b64encode(gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())).decode()

    base = load('live_base', ROOT / 'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    prefix = base.REMOTE.split('try:\n    auth=query')[0].replace("'timeout_ms':45000", "'timeout_ms':30000")
    remote = prefix + 'PAYLOAD_GZ=' + repr(compressed) + '\n' + REMOTE_BODY
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
    imports = '\n'.join(line for line in sjzc.LIVE_CODE.splitlines()
                        if line.startswith('from app.') or line.startswith('from sqlalchemy import'))
    remote = imports + '\nCREDS=' + repr(credentials) + '\n' + remote
    credentials.clear()
    compile(remote, 'remote', 'exec')

    out = HERE / 'evidence' / ('timed-verify-' + run_id + '.json')
    with out.open('x', encoding='utf8') as f:
        json.dump({'status': 'RESERVED'}, f)
    proc = subprocess.run(['ssh', '-i', sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
                           'docker exec -i -e PYTHONPATH=/app '
                           '-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
                          input=remote.encode(), capture_output=True, timeout=720)
    try:
        data = json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data = {'status': 'REMOTE_ERROR', 'error_type': type(exc).__name__,
                'ssh_exit_code': proc.returncode,
                'oracle_codes': sorted(set(re.findall(
                    r'(?:ORA|DPY|DPI)-\d+', proc.stderr.decode(errors='replace')))),
                'stderr_tail': proc.stderr.decode(errors='replace')[-1500:],
                'stdout_tail': proc.stdout.decode(errors='replace')[-800:]}
    blob = data.pop('private_detail_gzip_base64', None)
    if blob is not None:
        target = private / 'timed-verify-details.json'
        target.write_bytes(gzip.decompress(base64.b64decode(blob)))
        data['private_details_path'] = str(target)
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    data['run_id'] = run_id
    data['private_dir'] = str(private)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    printable = {k: v for k, v in data.items()
                 if k not in ('queries', 'batches', 'registration', 'view_definitions', 'scope_days')}
    print(json.dumps(printable, ensure_ascii=False, default=str))
    return 0 if data.get('run_completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
