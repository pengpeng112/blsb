"""Source-table audit executor (user-authorized 2026-10-23 '授权你执行').

Runs SQL01/02 (S01-S03 samples), SQL04 (531 missing + 31 dup codes, batched),
SQL06 (index metadata) and SQL05 (per-day coverage) as SELECT-only queries
through the <relay-jump> controlled connector using the platform-hosted credential
(never read locally). Raw rows go to a private gzip blob only; the shared
receipt carries aggregates. No r1/r2 evidence file is overwritten.
"""
from pathlib import Path
from datetime import datetime, timezone
import base64
import gzip
import importlib.util
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if '--authorized' not in sys.argv:
        raise SystemExit('Explicit user authorization required')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    prev = json.loads(sorted((HERE / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    private = Path(prev['private_dir']) / ('source-audit-' + run_id)
    private.mkdir(parents=True, exist_ok=False)

    pkg = json.loads((Path(prev['private_dir']) / 'dba-sample-package.json').read_text('utf8'))
    missing_codes = json.loads((Path(prev['private_dir']) / 'all-missing-codes-r2.json').read_text('utf8'))['codes']
    detail = json.loads((Path(prev['private_details_path'])).read_text('utf8'))
    dup_codes = sorted({r['code'] for r in detail['dup_pair_view_rows']})

    payload = {
        'samples': [{'id': s['id'], 'code': s['code'], 'line': s['bill']} for s in pkg['samples']],
        'missing_codes': missing_codes,
        'dup_codes': dup_codes,
    }
    compressed = base64.b64encode(gzip.compress(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())).decode()

    remote = 'AUDIT_GZ=' + repr(compressed) + '\n' + (HERE / 'source_audit_remote.py').read_text('utf8')
    sjzc = load('live_sjzc', Path('C:/Users/Administrator/.zcode/skills/sjzc/scripts/sjzc_query.py'))
    imports = '\n'.join(line for line in sjzc.LIVE_CODE.splitlines()
                        if line.startswith('from app.') or line.startswith('import sys')
                        or line.startswith('from sqlalchemy import'))
    setup = (
        "import base64,gzip,re,json,time\n"
        "code='his_source_<his_host_id>'\n"
        "db=SessionLocal()\n"
        "src=db.scalar(select(AssetDataSource).where(AssetDataSource.source_code==code))\n"
        "db.close()\n"
        "if src is None or not src.enabled or (src.target_host,src.port,src.service_name)!=('<his-host>',1521,'his'):\n"
        "    raise RuntimeError('Registered source endpoint mismatch')\n"
        "_u,_p=resolve(src.credential_ref)\n"
        "c=DB_CONNECTOR_MAP['oracle'](host=src.target_host,port=src.port,database=src.service_name,\n"
        "    user=_u or '',password=_p or '',connection_mode=src.connection_mode or 'direct',\n"
        "    **{**transport_kwargs(src.db_type,src.connection_options),'timeout_ms':30000})\n"
        "del _u,_p\n"
    )
    remote = imports + '\n' + setup + remote
    compile(remote, 'remote', 'exec')

    out = HERE / 'evidence' / ('source-audit-' + run_id + '.json')
    with out.open('x', encoding='utf8') as f:
        json.dump({'status': 'RESERVED'}, f)
    proc = subprocess.run(['ssh', '-i', sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
                           'docker exec -i -e PYTHONPATH=/app '
                           '-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
                          input=remote.encode(), capture_output=True, timeout=900)
    try:
        data = json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data = {'status': 'REMOTE_ERROR', 'error_type': type(exc).__name__,
                'ssh_exit_code': proc.returncode,
                'oracle_codes': sorted(set(re.findall(
                    r'(?:ORA|DPY|DPI)-\d+', proc.stderr.decode(errors='replace'))))}
    blob = data.pop('private_detail_gzip_base64', None)
    if blob is not None:
        target = private / 'source-rows.json'
        target.write_bytes(gzip.decompress(base64.b64decode(blob)))
        data['private_rows_path'] = str(target)
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    data['run_id'] = run_id
    data['private_dir'] = str(private)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    printable = {k: v for k, v in data.items()
                 if k not in ('indexes', 'daily_coverage', 'sql04_batches')}
    print(json.dumps(printable, ensure_ascii=False))
    return 0 if data.get('run_completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
