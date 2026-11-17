"""Exercise actual pusher main with synthetic data and intercepted HTTP only.

Findings describe the current implementation, not passing acceptance tests for a fix.
"""
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import io
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.dont_write_bytecode = True
sys.path.insert(0,str(ROOT/'implementation/deploy/inf_pusher'))
import pusher


def row(table, rid, parent=None):
    t=pusher.TABLES[table]
    values={key:'SYNTHETIC' for key in t['required']}
    values.update(id=rid,operationTime='2026-11-15 10:00:00')
    if parent:values['exLabId']=parent
    return {col:values[key] for col,key in t['map'].items() if key in values}


def scenario(name, selection='ex_lab,ex_lab_item', parent_failure=None,
             parent_key='SYN-P1', child_parent='SYN-P1', prior_parent=False, limit=None):
    events=[]
    def fetch(user,pwd,dsn,view,since,row_limit):
        if view==pusher.TABLES['ex_lab']['view']:
            if parent_failure=='fetch':raise RuntimeError('SYNTHETIC_FETCH_ERROR')
            return [row('ex_lab',parent_key)]
        if view==pusher.TABLES['ex_lab_item']['view']:
            return [row('ex_lab_item','SYN-C1',child_parent)]
        return []
    def post(url,payload,timeout=15):
        kind='child' if url.endswith('/emrExLabItem') else 'parent'
        ok=not(kind=='parent' and parent_failure=='04')
        events.append({'kind':kind,'id':payload['id'],'parent':payload.get('exLabId'),'accepted':ok})
        return {'result':ok,'errorCode':None if ok else '04'}
    with tempfile.TemporaryDirectory(prefix='inf-linkage-probe-') as tmp:
        config=Path(tmp)/'config.json'
        config.write_text(json.dumps({'base_url':'http://<frontend-host>','enabled':True,'workdir':tmp}),encoding='utf-8')
        if prior_parent:
            pusher.ReceiptLog(tmp).mark_sent('ex_lab',parent_key,'2026-11-15 10:00:00')
        argv=['pusher.py','--config',str(config),'--table',selection,'--mode','real']
        if limit:argv+=['--limit',str(limit)]
        with patch.object(pusher,'fetch_rows',fetch),patch.object(pusher,'post_json',post),patch.object(pusher.urllib.request,'urlopen',side_effect=AssertionError('NETWORK_FORBIDDEN')),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
            pusher.main()
        return {'scenario':name,'events':events}


def main():
    cases=[
        scenario('normal_parent_then_child'),
        scenario('reversed_cli_list',selection='ex_lab_item,ex_lab'),
        scenario('parent_rejected_04',parent_failure='04'),
        scenario('parent_fetch_exception',parent_failure='fetch'),
        scenario('child_only_cli',selection='ex_lab_item'),
        scenario('limit_does_not_pair_parent',parent_key='SYN-P1',child_parent='SYN-P2',limit=1),
        scenario('accepted_parent_from_history',prior_parent=True),
    ]
    assert [e['kind'] for e in cases[0]['events']]==['parent','child']
    assert [e['kind'] for e in cases[1]['events']]==['parent','child']
    for index in (2,3,4,5):
        assert any(e['kind']=='child' for e in cases[index]['events'])
    assert [e['kind'] for e in cases[6]['events']]==['child']
    evidence={'mode':'SYNTHETIC_HTTP_INTERCEPTED_NO_NETWORK','scenarios':cases,'result':'CURRENT_BEHAVIOR_REPRODUCED_NOT_FIX_ACCEPTANCE'}
    encoded=json.dumps(evidence,ensure_ascii=False,indent=2)
    Path(__file__).with_name('synthetic-order-findings.json').write_text(encoded+'\n',encoding='utf-8')
    print(encoded)


if __name__=='__main__':main()
