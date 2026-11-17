"""Stage E probes (aggregates only, PRESC_DATE PK range over the 81-day window):

E1: PRESC_NO length distribution among window masters (do 20-digit PRESC_NO exist?)
E2: DISPENSARY distribution among window masters (does 050121 appear at all?)
E3: for S01-S03 exact records, master by (PRESC_NO, PRESC_DATE) WITHOUT store —
    distinguishes store-key mismatch from date/no-master.
Same authorization basis as the source audit; SELECT-only, indexed range.
"""
from pathlib import Path
from datetime import datetime, timezone
import importlib.util
import json
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent

REMOTE_BODY = '''
import time
from datetime import date
started=time.monotonic()
out={'authorization':"User explicit authorization 2026-10-23; stage-E aggregates",
     'budget_seconds':180,'per_query_timeout_seconds':30,'source_writes':0,'uploads':0}
def run(key,sql,binds,limit):
    begin=time.monotonic()
    try:
        rows=c.execute_readonly(sql,binds,max_rows=limit)
        return {'ok':True,'key':key,'rows':len(rows),'seconds':round(time.monotonic()-begin,4),'groups':[dict(x) for x in rows]}
    except Exception as exc:
        return {'ok':False,'key':key,'error_type':type(exc).__name__,'oracle_codes':sorted(set(re.findall(r'(?:ORA|DPY|DPI)-\\d+',str(exc))))}
binds={'b':date(2026,7,23),'e':date(2026,10,12)}
out['e1_prescno_length']=run('e1',"SELECT LENGTH(TO_CHAR(M.PRESC_NO)) AS LEN,COUNT(*) AS N FROM PHARMACY.DRUG_PRESC_MASTER M WHERE M.PRESC_DATE>=:b AND M.PRESC_DATE<:e GROUP BY LENGTH(TO_CHAR(M.PRESC_NO)) ORDER BY 1",binds,30)
out['e2_dispensary']=run('e2',"SELECT M.DISPENSARY AS DISPENSARY,COUNT(*) AS N FROM PHARMACY.DRUG_PRESC_MASTER M WHERE M.PRESC_DATE>=:b AND M.PRESC_DATE<:e GROUP BY M.DISPENSARY ORDER BY 2 DESC",binds,40)
# E3 assembled from injected sample triples (no identifiers in shared receipt)
probes=json.loads(PROBES)
if probes:
    conds=[];binds3={}
    for i,(bn,pd,st) in enumerate(probes):
        conds.append("(M.PRESC_NO=TO_NUMBER(:bn%d) AND M.PRESC_DATE=TO_DATE(:pd%d,'YYYY-MM-DD'))"%(i,i))
        binds3['bn%d'%i]=bn;binds3['pd%d'%i]=pd
    sql3="SELECT TO_CHAR(M.PRESC_NO) AS PRESC_NO,TO_CHAR(M.PRESC_DATE,'YYYY-MM-DD') AS PRESC_DATE,M.DISPENSARY FROM PHARMACY.DRUG_PRESC_MASTER M WHERE "+" OR ".join(conds)+" AND ROWNUM<=10"
    r=run('e3',sql3,binds3,10)
    out['e3_master_by_no_date']={'ok':r['ok'],'rows':r.get('rows'),'oracle_codes':r.get('oracle_codes'),'matches_privately_retained':r.get('groups') if r['ok'] else None}
out['run_completed']=out['e1_prescno_length']['ok'] and out['e2_dispensary']['ok']
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
    # build E3 probes from private S01-S03 exact records (code+line -> row)
    audit = json.loads(sorted((HERE / 'evidence').glob('source-audit-20261023T14*.json'))[-1].read_text('utf8'))
    rows = json.loads(Path(audit['private_rows_path']).read_text('utf8'))
    prev = json.loads(sorted((HERE / 'evidence').glob('r2-two-views-*.json'))[-1].read_text('utf8'))
    pkg = json.loads((Path(prev['private_dir']) / 'dba-sample-package.json').read_text('utf8'))
    probes = []
    for s in pkg['samples']:
        for r in rows['sql04_missing']:
            if str(r['T_TRACE_CODE']) == s['code'] and str(r['PRESC_DETAILID']) == s['bill']:
                probes.append([str(r['TRACE_BILLNO']), str(r['PRESC_DATE'])[:10], str(r['STORE_CODE'])])
                break
    assert len(probes) == 3, probes

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
    remote = imports + '\n' + setup + 'PROBES=' + repr(json.dumps(probes)) + '\n' + REMOTE_BODY
    compile(remote, 'remote', 'exec')
    out = HERE / 'evidence' / ('source-audit-stageE-' + run_id + '.json')
    with out.open('x', encoding='utf8') as f:
        json.dump({'status': 'RESERVED'}, f)
    proc = subprocess.run(['ssh', '-i', sjzc.SSH_KEY, *sjzc.SSH_OPTS, sjzc.SSH_TARGET,
                           'docker exec -i -e PYTHONPATH=/app '
                           '-e APP_IDENTITY_SYNC_DIRECT_CONNECTION=true data-asset-api python -'],
                          input=remote.encode(), capture_output=True, timeout=300)
    try:
        data = json.loads(proc.stdout.decode().splitlines()[-1])
    except Exception as exc:
        data = {'status': 'REMOTE_ERROR', 'error_type': type(exc).__name__,
                'ssh_exit_code': proc.returncode,
                'oracle_codes': sorted(set(re.findall(
                    r'(?:ORA|DPY|DPI)-\d+', proc.stderr.decode(errors='replace'))))}
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    data['run_id'] = run_id
    if isinstance(data.get('e3_master_by_no_date', {}).get('matches_privately_retained'), list):
        priv = Path(audit['private_dir']) / ('stageE-' + run_id + '-e3.json')
        priv.write_text(json.dumps(data['e3_master_by_no_date']['matches_privately_retained'],
                                   ensure_ascii=False, indent=2), encoding='utf8')
        data['e3_master_by_no_date']['matches_privately_retained'] = 'saved to ' + str(priv)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    printable = dict(data)
    printable.pop('e2_dispensary', None)
    print(json.dumps({'e1': data.get('e1_prescno_length', {}).get('groups'),
                      'e2_ok': data.get('e2_dispensary', {}).get('ok'),
                      'e2_rows': data.get('e2_dispensary', {}).get('rows'),
                      'e3': data.get('e3_master_by_no_date'),
                      'run_completed': data.get('run_completed')}, ensure_ascii=False))
    return 0 if data.get('run_completed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
