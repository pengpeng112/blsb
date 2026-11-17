"""Evaluate collected synthetic observations against safety requirements.

Exit 1 means the reviewed implementation does not meet these requirements.
This evaluator does not run the sender or access any network.
"""
from pathlib import Path
import json

HERE=Path(__file__).resolve().parent


def main():
    observations=json.loads((HERE/'implementation-probes.json').read_text(encoding='utf-8'))['observations']
    by_name={x['case']:x for x in observations}
    def requests(name,table=None):
        return sum(table is None or x['table']==table for x in by_name[name].get('events',[]))
    rows=[]
    def add(name,requirement,expected,observed):
        rows.append({'case':name,'requirement':requirement,'expected':expected,'observed':observed,'status':'PASS' if expected==observed else 'FAIL'})
    add('normal_pair','正常父子流程发送1条子项',1,requests('normal_pair','ex_lab_item'))
    add('parent_rejected_without_history','父04失败且无旧成功时子HTTP为0',0,requests('parent_rejected_without_history','ex_lab_item'))
    add('prior_day_accepted_parent_new_child','同版本历史父跨日可复用，新子应发送',1,requests('prior_day_accepted_parent_new_child','ex_lab_item'))
    for name in ('same_day_old_parent_current_rejected','other_table_id_bypasses_parent','reconcile_stale_parent_current_rejected'):
        add(name,'当前父未成功，旧成功或其他表同ID不得放行子项',0,requests(name,'ex_lab_item'))
    add('manifest_expired_wrong_target_bad_digest','错误目标/过期/摘要错清单HTTP为0',0,requests('manifest_expired_wrong_target_bad_digest'))
    add('same_manifest_applied_twice','同清单两次应用只执行一个成功项',1,requests('same_manifest_applied_twice'))
    add('manifest_missing_target_not_reported','清单目标不存在不得静默成功',False,by_name['manifest_missing_target_not_reported']['exit_codes']==[0])
    add('reconcile_manifest_without_apply','对账+重放无apply时HTTP为0',0,requests('reconcile_manifest_without_apply'))
    add('waiting_row_lost_outside_overlap','等待项越水位重启且父恢复后可补回',True,by_name['waiting_row_lost_outside_overlap']['waiting_child_sent_later'])
    add('order_historical_parent_new_child','医嘱历史父同版本可复用，新条目发送',1,requests('order_historical_parent_new_child','order_item'))
    failed=sum(x['status']=='FAIL' for x in rows)
    result={'mode':'SYNTHETIC_NO_NETWORK','passed':len(rows)-failed,'failed':failed,'total':len(rows),'release_ready':failed==0,'note':'12个行为场景，非原R2-A编号逐项替代；与Oracle视图核验分开。','rows':rows}
    (HERE/'acceptance-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'failed':failed,'total':len(rows),'release_ready':result['release_ready']},ensure_ascii=False))
    return 1 if failed else 0


if __name__=='__main__':raise SystemExit(main())
