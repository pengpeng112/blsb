"""Prepared live probe. Execution requires the current daytime permission.

No passwords, legacy identifiers or trace codes are written to shared output.
"""
from pathlib import Path
import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    # This flag records a supplied authorization; it does not itself grant one.
    if '--approved-daytime' not in sys.argv:
        print(json.dumps({'status':'NOT_EXECUTED','reason':'Current daytime HIS window has not been recorded','live_queries':0}))
        return 2
    dbpath=Path(os.environ['LOCALAPPDATA'])/'BLYB/history-compare-20261023/history.sqlite3'
    db=sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True)
    selection=[('standard_key',"SELECT bill,code FROM history WHERE SUBSTR(bill,15,1)='_' ORDER BY file,row_no LIMIT 1"),
               ('nonstandard_key',"SELECT bill,code FROM history WHERE SUBSTR(bill,15,1)<>'_' ORDER BY file,row_no LIMIT 1"),
               ('repeated_pair',"SELECT bill,code FROM history GROUP BY bill,code HAVING COUNT(*)>1 ORDER BY bill,code LIMIT 1")]
    samples=[]
    for label,sql in selection:
        row=db.execute(sql).fetchone()
        if row is None:raise RuntimeError('SAMPLE_CATEGORY_MISSING')
        bill,code=row
        times=[r[0] for r in db.execute('SELECT bill_time FROM history WHERE bill=? AND code=? ORDER BY bill_time',(bill,code))]
        samples.append({'label':label,'bill':bill,'code':code,'times':times})
    db.close()
    sql='\n'.join(line for line in (HERE/'probe_by_code_r1.sql').read_text('utf8').splitlines() if not line.lstrip().startswith('--'))
    validator=load('history_sql_validator',Path('F:/python/数据资产/.agents/skills/hisuser-readonly-sql/scripts/validate_his_sql.py'))
    errors=validator.validate(sql)
    if errors:raise RuntimeError('STATIC_SQL_REJECTED')
    base=load('history_live_reader',ROOT/'docs/development/drug-trace-view-verify-20261022/verify_reader.py')
    base.HERE=HERE
    prefix=base.REMOTE.split('try:\n    auth=query')[0].replace("'timeout_ms':45000","'timeout_ms':30000")
    base.REMOTE=prefix+'\nSAMPLES='+repr(samples)+'\nPROBE_SQL='+repr(sql)+'\n'+(HERE/'probe_history.remote.py').read_text('utf8')
    sys.argv=['history-probe','r1']
    with contextlib.redirect_stdout(io.StringIO()):result=base.main()
    receipt=json.loads((HERE/'evidence/reader-r1.json').read_text('utf8'))
    print(json.dumps({key:value for key,value in receipt.items() if key not in ('queries','registration')},ensure_ascii=False))
    failures=[key for key,value in receipt.get('queries',{}).items() if not value['ok']]
    return result or int(bool(failures))


if __name__=='__main__':raise SystemExit(main())
