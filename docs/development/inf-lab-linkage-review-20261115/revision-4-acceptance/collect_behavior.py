"""Audit the frozen implementation using synthetic data and intercepted I/O.

Exit zero means observations were collected, not that the implementation is safe.
"""
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import datetime as dt
import io
import json
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.dont_write_bytecode=True
sys.path.insert(0,str(HERE/'baseline'))
import pusher as p


def row(table,rid,parent=None,op='2026-11-15 10:00:00'):
    t=p.TABLES[table]
    values={key:'SYNTHETIC' for key in t['required']}
    values.update(id=rid,operationTime=op)
    if parent:values[t.get('parent_fk','exLabId')]=parent
    return {col:values[key] for col,key in t['map'].items() if key in values}


def run_case(name,selection='ex_lab,ex_lab_item',history=None,parent_reject=False,
             manifest=None,apply=False,repeat=1,empty_source=False):
    events=[]
    def fetch(user,pwd,dsn,view,since,limit):
        if empty_source:return []
        if view==p.TABLES['ex_lab']['view']:return [row('ex_lab','SYN-P')]
        if view==p.TABLES['ex_lab_item']['view']:return [row('ex_lab_item','SYN-C','SYN-P')]
        if view==p.TABLES['order']['view'] and 'order' in selection:return [row('order','SYN-P')]
        if view==p.TABLES['order_item']['view'] and 'order_item' in selection:return [row('order_item','SYN-C','SYN-P')]
        if view==p.TABLES['death']['view'] and 'death' in selection:return [row('death','SYN-P')]
        return []
    def post(url,payload,timeout=15):
        table=next(n for n,t in p.TABLES.items() if url.endswith('/'+t['endpoint']))
        ok=not(table=='ex_lab' and parent_reject)
        events.append({'table':table,'id':payload['id'],'accepted':ok})
        return {'result':ok,'errorCode':None if ok else '04'}
    with tempfile.TemporaryDirectory(prefix='inf-audit-r2-') as tmp:
        cfg=Path(tmp)/'config.json'
        cfg.write_text(json.dumps({'base_url':'http://<frontend-host>','enabled':True,'workdir':tmp}),encoding='utf-8')
        receipts=p.ReceiptLog(tmp)
        if history=='prior_day':
            receipts.mark_sent('ex_lab','SYN-P','2026-11-15 10:00:00')
            (Path(tmp)/'receipts-20000101.jsonl').write_text(json.dumps({'table':'ex_lab','id':'SYN-P','result':True,'mode':''})+'\n',encoding='utf-8')
        if history=='same_day_old':
            receipts.mark_sent('ex_lab','SYN-P','2026-11-15 09:00:00')
            receipts.receipt('ex_lab',{'id':'SYN-P','result':True})
        if history=='legacy_old':receipts.mark_sent('ex_lab','SYN-P','2026-11-15 09:00:00')
        if history=='prior_day_order':receipts.mark_sent('order','SYN-P','2026-11-15 10:00:00')
        argv=['pusher.py','--config',str(cfg),'--table',selection,'--mode','real']
        if manifest is not None:
            mf=Path(tmp)/'manifest.json';mf.write_text(json.dumps(manifest),encoding='utf-8')
            argv+=['--replay-manifest',str(mf)]
            if apply:argv+=['--apply']
        exit_codes=[]
        for _ in range(repeat):
            with patch.object(p,'fetch_rows',fetch),patch.object(p,'post_json',post),patch.object(p.urllib.request,'urlopen',side_effect=AssertionError('NETWORK_FORBIDDEN')),patch.object(p.time,'sleep',lambda _:None),patch.object(sys,'argv',argv),redirect_stdout(io.StringIO()):
                try:p.main();exit_codes.append(0)
                except SystemExit as exc:exit_codes.append(str(exc.code))
        output={'case':name,'events':events,'exit_codes':exit_codes}
        last=Path(tmp)/'last-run-real.json'
        if last.exists():output['stats']=json.loads(last.read_text(encoding='utf-8'))
        return output


def lost_waiting():
    events=[]
    all_rows=[row('ex_lab_item','SYN-OLD','SYN-P','2026-11-01 10:00:00'),row('ex_lab_item','SYN-NEW','SYN-Q','2026-11-15 10:00:00')]
    def fetch(u,pwd,d,view,since,limit):
        return [r for r in all_rows if r['OPERATION_TIME']>=since]
    def post(url,payload,timeout=15):
        events.append(payload['id']);return {'result':True}
    with tempfile.TemporaryDirectory(prefix='inf-wait-r2-') as tmp:
        cfg={'base_url':'http://<frontend-host>','enabled':True,'workdir':tmp}
        receipt=p.ReceiptLog(tmp);wm=p.Watermark(str(Path(tmp)/'watermark.json'))
        with patch.object(p,'fetch_rows',fetch),patch.object(p,'post_json',post),patch.object(p.urllib.request,'urlopen',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            first=p.run_table('ex_lab_item',cfg,'real',receipt,wm,since_override='2026-11-01 00:00:00',parent_accept={'SYN-Q':True})
            second=p.run_table('ex_lab_item',cfg,'real',p.ReceiptLog(tmp),p.Watermark(wm.path),parent_accept={'SYN-P':True,'SYN-Q':True})
        return {'case':'waiting_row_lost_outside_overlap','first_parent_wait':first['parent_wait'],'second_source_rows':second['rows'],'waiting_child_sent_later':'SYN-OLD' in events,'watermark':wm.state.get('ex_lab_item')}


def main():
    basic={'table':'ex_lab','ids':['SYN-P'],'force':True}
    observations=[
        run_case('normal_pair'),
        run_case('parent_rejected_without_history',parent_reject=True),
        run_case('prior_day_accepted_parent_new_child',history='prior_day'),
        run_case('same_day_old_parent_current_rejected',history='same_day_old',parent_reject=True),
        run_case('other_table_id_bypasses_parent',selection='death,ex_lab,ex_lab_item',parent_reject=True),
        run_case('reconcile_stale_parent_current_rejected',selection='reconcile',history='legacy_old',parent_reject=True),
        run_case('manifest_expired_wrong_target_bad_digest',selection='ex_lab',apply=True,manifest={**basic,'target':'WRONG','source_instance':'WRONG','expected_source_time':'1900-01-01','expires_at':'1900-01-01T00:00:00Z','revision':'WRONG','payload_digest':'WRONG','max_count':0}),
        run_case('same_manifest_applied_twice',selection='ex_lab',apply=True,manifest=basic,repeat=2),
        run_case('manifest_missing_target_not_reported',selection='ex_lab',apply=True,manifest=basic,empty_source=True),
        run_case('reconcile_manifest_without_apply',selection='reconcile',manifest=basic,apply=False),
        lost_waiting(),
        run_case('order_historical_parent_new_child',selection='order,order_item',history='prior_day_order'),
    ]
    child_count=lambda x:sum(e['table']=='ex_lab_item' for e in x.get('events',[]))
    evidence={'mode':'SYNTHETIC_FROZEN_CODE_NO_NETWORK','note':'Observation assertions only; defects were reproduced, not fixed.','observations':observations}
    encoded=json.dumps(evidence,ensure_ascii=False,indent=2)
    (HERE/'implementation-probes.json').write_text(encoded+'\n',encoding='utf-8')
    print(json.dumps({'observations':len(observations),'child_http_counts':{x['case']:child_count(x) for x in observations if 'events' in x},'waiting_loss':observations[10]},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
