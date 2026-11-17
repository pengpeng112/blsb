"""Aggregate immutable raw evidence; publish only after all247 have a result."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone,timedelta
import json,csv,hashlib
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def evidence(phase):
    p=sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))[-1]
    data=json.loads(p.read_text('utf8'));assert data['exit_code']==0
    return p,data['result']
sample_path,sample=evidence('sample')
first_path,first=evidence('progress')
diagnose_path,diagnose=evidence('diagnose')
second_path,second=evidence('progress2')
third_path,third=evidence('progress3')
recheck_path,recheck=evidence('recheck_limit')
final_path,final=evidence('progress4')
inspect_path,after=evidence('inspect')
assert final['final']['exit_code']==0 and final['final']['summary']['ok']
assert final['final']['summary']['ledger_unchanged']
rows={}
for r in sample['rows']+first['rows']+second['rows']+third['rows']:
    if r['local_state']!='CONFIRMED':rows[r['submission_id']]=dict(r)
diagnose_cls=diagnose['summary']['classification']
assert diagnose_cls=={'state':'CONFIRMED','result_type':'3','sub_process_flag':'51'}
sid='5aaa9241-82f6-4bdb-b47a-608e55fd43ef'
rows[sid].update(platform_state='CONFIRMED',result_type='3',sub_process_flag='51',query_status='OK',
                 verified_by='separate_post_cooldown_diagnosis:'+diagnose_path.name,reason=None,error_code=None,total_num=1,
                 checked_utc=json.loads(diagnose_path.read_text('utf8'))['captured_at'])
assert recheck['summary']['classification']['state']=='NOT_FOUND' and not recheck['summary']['top_errors']
rows['60291343-4a03-4a35-a1ad-2ece7a38b3be'].update(platform_state='NOT_FOUND',result_type=None,sub_process_flag=None,query_status='OK',
 reason='complete platform query has no entry for bill_code',error_code=None,top_error=None,total_num=0,
 verified_by=recheck_path.name,checked_utc=json.loads(recheck_path.read_text('utf8'))['captured_at'])
for r in final['rows']:
    if r['local_state']!='CONFIRMED':rows[r['submission_id']]=dict(r)
assert len(rows)==247
assert Counter(r['local_state'] for r in rows.values())==Counter({'UNKNOWN':206,'ACCEPTED':41})
assert all(r['platform_state'] in ('CONFIRMED','NOT_FOUND','PARTIAL','REJECTED','ACCEPTED','PROCESSING') for r in rows.values())
pairs={a+'->'+b:n for (a,b),n in Counter((r['local_state'],r['platform_state']) for r in rows.values()).items()}
assert pairs=={'UNKNOWN->NOT_FOUND':206,'ACCEPTED->CONFIRMED':41},'FINAL_WORDING_REQUIRES_REVIEW_OF_UNEXPECTED_RESULTS'
summary={'unique_targets':247,'pairs':pairs,'started_utc':sample['summary']['started_utc'],
 'finished_utc':final['final']['summary']['finished_utc'],'all_final_requests_http200':final['http'],
 'uploads':0,'his_calls':0,'business_writes':0,'poll_budget_reset':False,
 'ledger_unchanged':True,'ui_states_not_rewritten':True,
 'production_states':after['states'],'quota':after['quota'],'checkpoint':after['checkpoint'],
 'evidence':[p.name for p in [sample_path,first_path,diagnose_path,second_path,third_path,recheck_path,final_path,inspect_path]]}
assert after['states']=={'CONFIRMED':2576,'ACCEPTED':41,'UNKNOWN':206}
assert after['quota']==[{'used':2823,'max_bills':80000}]
assert after['checkpoint']==[{'row_version':46,'since_time':'2026-10-26T00:00:00'}]
(HERE/'evidence/review-summary-final.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
columns=['submission_id','local_state','platform_state','result_type','sub_process_flag','bill_code_sha256','checked_utc','verified_by']
with (HERE/'核查明细_最终版_无患者信息.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=columns,extrasaction='ignore');writer.writeheader();writer.writerows(sorted(rows.values(),key=lambda r:(r['local_state'],r['submission_id'])))
now=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
detail=f'''\n\n## 全量实测结论（{now}）

247笔已全部完成一次有效人工核查：**{pairs}**。所有结果均来自真实平台查询；不是合成数据或从本地旧状态推算。

当前平台已确认成功的41笔，需后续通过受控人工回写入口更新本地状态。206笔UNKNOWN按冻结企业/单据号/类型及覆盖业务与上传日期窗口查询NOT_FOUND（若结果分类变化以核查汇总为准）；不能把NOT_FOUND扩大为所有企业身份/所有历史单号从未上报的结论。先核对人工上报名单/相同追溯码的既往记录，再形成恢复清单，不能直接重发。

本地业务状态仍2576 CONFIRMED/41 ACCEPTED/206 UNKNOWN；quota2823、源断点46不变。查看页面仍有41/206是本次未回写，不代表这次查询没有执行。没有发任何新药品数据、没有清零查询代次或配额。

证据：[最终核查汇总](evidence/review-summary-final.json)、[最终逐笔内部UUID及平台结果（不含患者/原始追溯码）](核查明细_最终版_无患者信息.csv)。原部分核查汇总及CSV原样保留；不能用最终成功覆盖下午TOP7错误。
'''
p=HERE/'README.md';old=p.read_text('utf8');heading,rest=old.split('\n',1)
p.write_text(heading+f'\n\n**最新结论（{now}）：247笔全部查清，41笔平台确认成功，206笔按原身份/单据号查询未找到。** 原下午143/63部分报告保留在下方；18:12用户要求继续后实测额度已恢复，有限核查完成。仅查询，本地状态未回写、未重发。\n'+rest+detail,encoding='utf8')
section=f'''## 最新：平台人工查询完成，41受理已在平台成功（{now}）

[实际核查与边界](../operations/drug-trace-unknown-check-20261026/README.md)：用户要求看平台是否收到，按冻结身份/类型/业务和上传日期覆盖查询旧247笔。去重结果{pairs}，已知成功单作正对照正常；不是mock。
旧206 UNKNOWN原发送205次HTTP429、1次FileNotFoundError；历史查询126仍429/80未找到，41受理历史查询也429且均12轮耗尽。下午r1—r3遇TOP7/调用次数限制已停并保留143未找到/63未核实的部分报告；用户随后要求继续，18:12单笔实测额度恢复且NOT_FOUND，r4以4.5秒间隔完成剩余62笔，未重复核已查清记录。未猜平台精确额度或恢复周期。
本次只读独立证据，不改业务状态/轮询预算、不发新单、不读HIS。生产本地仍2576确认/41受理/206未知，额度2823/80000、断点46不变；**41笔平台确认不等于看板已经回写**。逐笔UUID及结果CSV在任务目录，无患者/原始码/凭据。
下一步：受控人工核查回写41笔并保留平台证据；206按NOT_FOUND名单比对历史人工上报及身份后制定恢复清单，禁止盲重发；平台TOP7退避/子错误保存、轮询耗尽人工入口、明细分页仍待完善。[交接](sessions/DTR-UNKNOWN-CHECK-20261026.md)。

'''
def insert(path,block):
    p=ROOT/path;old=p.read_text('utf8');head,tail=old.split('\n',1)
    p.write_text(head+'\n\n'+block+tail.lstrip('\n'),encoding='utf8')
for name in ['STATE.md','HANDOFF.md','BACKLOG.md']:insert('docs/ai/'+name,section)
insert('数据上报/1、码上放心平台/README.md',section.replace('../operations/','../../docs/operations/').replace('(sessions/','(../../docs/ai/sessions/'))
handoff=ROOT/'docs/ai/sessions/DTR-UNKNOWN-CHECK-20261026.md'
handoff.write_text('# DTR-UNKNOWN-CHECK-20261026 交接\n\nCodex/model unknown，会话20261026T070804Z-b37f25ba。仅本次运维核验文档、工具与共享状态；未改应用源码/镜像/服务/配置/视图。\n\n'+section.replace('../operations/','../../operations/').replace('(sessions/','(')+'\n实际命令、初始失败与恢复见[实录](../../operations/drug-trace-unknown-check-20261026/README.md)。r1—r3因平台TOP7退出1，15:42部分报告未核完；18:12用户继续后单笔查明已恢复，r4实际查询exit0且ledger_unchanged=true。inspect/sample/diagnose/recheck_limit/evening/progress4通道exit0。本轮无应用代码改动，未跑应用回归也不冒充通过。\n\n复核工具all/resume/finalpass/evening均一次性，禁止重跑；progress4/inspect只读。原始平台与诊断证据私有服务器目录见实录。最终CSV247行：41已在平台成功、206平台未找到；全部仅单据级结果，不排除其他身份/单号人工上报。尚未实现状态回写与未找到后的补报流程，不直接UPDATE或重发UNKNOWN。原部分报告JSON/CSV保留，最终另存-final及最终版CSV。\n',encoding='utf8')
catalog=ROOT/'docs/catalog.json';data=json.loads(catalog.read_text('utf8'))
for path,category,title in [('docs/operations/drug-trace-unknown-check-20261026/README.md','operations','追溯平台是否收到的人工只读核查'),('docs/ai/sessions/DTR-UNKNOWN-CHECK-20261026.md','shared_ai_state','追溯未知与受理单据核查交接')]:
    if not any(x['path']==path for x in data['documents']):data['documents'].append({'path':path,'category':category,'title':title})
catalog.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(summary,ensure_ascii=False))
