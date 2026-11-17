"""Re-run stage D (per-day coverage) with GROUP BY TRUNC(PRESC_DATE).

The first attempt grouped by the raw DATE column (time components) and every
day's result was silently capped at 50 rows, so its totals were invalid.
This runner repeats only the daily aggregation with day-truncated grouping
and explicit master counts; stage A/B/C results are untouched.
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

REMOTE_BODY = '''
import time
started=time.monotonic()
out={'authorization':"User explicit authorization 2026-10-23 ('授权你执行'); stage-D re-run with TRUNC grouping",
     'budget_seconds':300,'per_query_timeout_seconds':30,'source_writes':0,'uploads':0}
SQL=("SELECT TO_CHAR(TRUNC(M.PRESC_DATE),'YYYY-MM-DD') AS PRESC_DAY,M.PRESC_SOURCE AS PRESC_SOURCE,"
     "COUNT(*) AS JOIN_ROWS,"
     "COUNT(DISTINCT M.PRESC_NO||'|'||M.DISPENSARY) AS MASTERS,"
     "COUNT(DISTINCT CASE WHEN T.RECORD_ID IS NOT NULL THEN M.PRESC_NO||'|'||M.DISPENSARY END) AS MASTERS_WITH_OUT,"
     "SUM(CASE WHEN T.RECORD_ID IS NULL THEN 1 ELSE 0 END) AS MASTERS_NO_OUT_ROWS,"
     "SUM(CASE WHEN T.RECORD_ID IS NOT NULL THEN 1 ELSE 0 END) AS OUT_ROWS "
     "FROM PHARMACY.DRUG_PRESC_MASTER M LEFT JOIN DRUG_USER.MED_TRACE_RECORD T "
     "ON T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO) AND T.PRESC_DATE=M.PRESC_DATE AND T.STORE_CODE=M.DISPENSARY AND T.IN_OUT_TYPE='OUT' "
     "WHERE M.PRESC_DATE>=:day_begin AND M.PRESC_DATE<:day_end "
     "GROUP BY TRUNC(M.PRESC_DATE),M.PRESC_SOURCE")
from datetime import date,timedelta
d0=date(2026,7,23);d1=date(2026,10,12)
out['daily_coverage']=[];d=d0;errs=0
while d<d1:
    if time.monotonic()-started>280:out['daily_stop']='BUDGET';break
    binds={'day_begin':d,'day_end':d+timedelta(days=1)}
    begin=time.monotonic()
    try:
        rows=c.execute_readonly(SQL,binds,max_rows=20)
    except Exception as exc:
        errs+=1
        out['daily_coverage'].append({'day':str(d),'ok':False,'oracle_codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\\d+',str(exc))))})
        if errs>=3:out['daily_stop']='QUERY_ERRORS';break
        d+=timedelta(days=1);continue
    out['daily_coverage'].append({'day':str(d),'ok':True,'rows':len(rows),'seconds':round(time.monotonic()-begin,4),'groups':[dict(x) for x in rows],'cap_reached':len(rows)>=20})
    if len(rows)>=20:
        out['daily_stop']='ROW_CAP_REACHED';break
    d+=timedelta(days=1)
if 'daily_stop' not in out:out['daily_stop']='ALL_DAYS_QUERIED'
out['daily_days_queried']=len(out['daily_coverage'])
out['run_completed']=out['daily_stop']=='ALL_DAYS_QUERIED'
out['total_elapsed_seconds']=round(time.monotonic()-started,3)
try:c.close()
except Exception:pass
print(json.dumps(out,ensure_ascii=False,default=str))
'''


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if '--authorized' not in sys.argv:
        raise SystemExit('Explicit user authorization required')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
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
    remote = imports + '\n' + setup + REMOTE_BODY
    compile(remote, 'remote', 'exec')
    out = HERE / 'evidence' / ('source-audit-dailyfix-' + run_id + '.json')
    with out.open('x', encoding='utf8') as f:
        json.dump({'status': 'RESERVED'}, f)
    proc = subprocess.run(['ssh', '-i', sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
                           'docker exec -i -e PYTHONPATH=/app '
                           '-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
                          input=remote.encode(), capture_output=True, timeout=600)
    try:
        data = json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data = {'status': 'REMOTE_ERROR', 'error_type': type(exc).__name__,
                'ssh_exit_code': proc.returncode,
                'oracle_codes': sorted(set(re.findall(
                    r'(?:ORA|DPY|DPI)-\d+', proc.stderr.decode(errors='replace'))))}
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    data['run_id'] = run_id
    data['supersedes'] = 'source-audit-20261023T141852Z daily_coverage (truncated grouping)'
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    days = [d for d in data.get('daily_coverage', []) if d.get('ok')]
    print(json.dumps({'run_completed': data.get('run_completed'), 'daily_stop': data.get('daily_stop'),
                      'days_ok': len(days), 'cap_days': sum(1 for d in days if d.get('cap_reached')),
                      'elapsed': data.get('total_elapsed_seconds'), 'run_id': run_id,
                      'oracle_codes': data.get('oracle_codes')}, ensure_ascii=False))
    return 0 if data.get('run_completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
