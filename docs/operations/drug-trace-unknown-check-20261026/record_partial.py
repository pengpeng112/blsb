"""Publish measured partial results and exact external block; no new requests."""
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone,timedelta
import json,csv
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
def read(phase):
 p=sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))[-1]
 d=json.loads(p.read_text('utf8'));assert d['exit_code']==0
 return p,d['result']
ip,inventory=read('inventory');rows={r['submission_id']:{**r,'platform_state':'NOT_CHECKED','checked_utc':''} for r in inventory['rows']}
paths=[ip]
for phase in ['sample','progress','progress2','progress3']:
 p,data=read(phase);paths.append(p)
 for r in data['rows']:
  if r['submission_id'] in rows:rows[r['submission_id']].update(r)
dp,diagnosis=read('diagnose');paths.append(dp)
assert diagnosis['summary']['classification']=={'state':'CONFIRMED','result_type':'3','sub_process_flag':'51'}
sid='5aaa9241-82f6-4bdb-b47a-608e55fd43ef'
rows[sid].update(platform_state='CONFIRMED',result_type='3',sub_process_flag='51',query_status='OK',error_code=None,reason=None,total_num=1,
 checked_utc=json.loads(dp.read_text('utf8'))['captured_at'],verified_by=dp.name)
pp,last=read('progress3')
error=next(r['top_error'] for r in last['rows'] if r.get('top_error'))
assert error=={'code':7,'msg':'App Call Limited','sub_code':'accesscontrol.limited-by-api-access-count'}
assert last['final']['exit_code']==1
ap,after=read('inspect');paths.append(ap)
pairs={a+'->'+b:n for (a,b),n in Counter((r['local_state'],r['platform_state']) for r in rows.values()).items()}
assert pairs=={'ACCEPTED->CONFIRMED':41,'UNKNOWN->NOT_FOUND':143,'UNKNOWN->QUERY_ERROR':1,'UNKNOWN->NOT_CHECKED':62},pairs
assert after['states']=={'CONFIRMED':2576,'ACCEPTED':41,'UNKNOWN':206}
assert after['quota']==[{'used':2823,'max_bills':80000}]
assert all(r['minimum']==12 and r['maximum']==12 for r in after['poll_counts'])
assert after['checkpoint']==[{'row_version':46,'since_time':'2026-10-26T00:00:00'}]
summary={'unique_targets':247,'conclusively_checked':184,'pairs':pairs,'not_yet_verified':63,
 'external_block':error,'uploads':0,'his_calls':0,'business_writes':0,'poll_budget_reset':False,
 'production_states_unchanged':after['states'],'quota_unchanged':after['quota'],'checkpoint_unchanged':after['checkpoint'],
 'ui_states_not_rewritten':True,'complete_platform_review':False,'evidence':[p.name for p in paths]}
(HERE/'evidence/review-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
with (HERE/'核查明细_无患者信息.csv').open('w',encoding='utf-8-sig',newline='') as f:
 writer=csv.DictWriter(f,fieldnames=['submission_id','local_state','platform_state','result_type','sub_process_flag','bill_code_sha256','bill_code_md5','checked_utc','verified_by'],extrasaction='ignore')
 writer.writeheader();writer.writerows(sorted(rows.values(),key=lambda r:(r['platform_state'],r['submission_id'])))
now=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
section=f'''## 最新：平台核查184笔有结论，63笔受API额度限制（{now}）

[实测核查与证据](../operations/drug-trace-unknown-check-20261026/README.md)：旧41 ACCEPTED全部在平台查到result_type=3/sub_process_flag=51；旧206 UNKNOWN中143完整查询NOT_FOUND，剩余63未核实（1笔平台拒查、62笔未查）。不能称206全未收到或247全核清。
独立只读查询首批及r2/r3三次遇平台HTTP200内TOP7，最后留存App Call Limited/accesscontrol.limited-by-api-access-count；冷却且降至4.5秒仍拒绝，已停止。正对照仍成功，精确配额周期/恢复时间须平台确认，不猜“每日额度”。
本次没有上报/重发、没有读HIS、没有业务写入或清12轮预算。看板本地仍2576确认/41受理/206未知；配额2823、断点46不变。平台已确认的41笔尚未回写；NOT_FOUND限定同企业/类型/单据号和日期覆盖，不能排除历史换单号/人工上传。
下一步：平台方查当前AppKey该查询接口调用次数额度及恢复时间；恢复后按CSV中QUERY_ERROR/NOT_CHECKED的63笔继续，不查已核清184笔；受控回写41成功，再核对143未找到与后续63笔的人工历史，不能盲重发。精确清单与平台错误证据已存，本轮无遗留活动探针。[交接](sessions/DTR-UNKNOWN-CHECK-20261026.md)。

'''
def insert(path,block):
 p=ROOT/path;old=p.read_text('utf8');head,tail=old.split('\n',1)
 p.write_text(head+'\n\n'+block+tail.lstrip('\n'),encoding='utf8')
for name in ['STATE.md','HANDOFF.md','BACKLOG.md']:insert('docs/ai/'+name,section)
insert('数据上报/1、码上放心平台/README.md',section.replace('../operations/','../../docs/operations/').replace('(sessions/','(../../docs/ai/sessions/'))
append=f'''\n\n## 最终交付：部分核查，外部限制未解决（{now}）

| 原状态 | 平台实测 | 数量 |
|---|---|---:|
| ACCEPTED | 已确认成功3/51 | 41 |
| UNKNOWN | 完整查询未找到NOT_FOUND | 143 |
| UNKNOWN | 平台调用次数限制，未核实 | 1 |
| UNKNOWN | 本轮尚未查到 | 62 |

**已核实184笔，剩余63笔。不是全量成功。** r3已退出1，最后一次平台错误code7/msg App Call Limited/sub_code accesscontrol.limited-by-api-access-count，进一步降速仍被拒，未开第四轮、未换身份或绕过。任务交接待平台方核实/恢复该AppKey查询接口调用次数额度，额度周期未确认。

[核查汇总](evidence/review-summary.json)、[逐笔内部UUID/结果清单（无患者与原始追溯码）](核查明细_无患者信息.csv)。清单包含全部247笔，NOT_CHECKED明确表示未查，不能当未收到；QUERY_ERROR也不能当NOT_FOUND。平台查询成功的41笔尚未回写，所以看板仍保留41受理，属本次只读边界。

原业务状态2576/41/206、配额2823、源断点46、查询job12轮均不变。原有定时任务、部署和发送范围未修改。后续需要一个可审计人工状态同步入口，保留平台证据并防止误写；不直接UPDATE，不使用perform_query绕开入队预算。未找到单据按历史人工上报核对后再审批恢复方案。

交平台支持的现成说明：当前AppKey的alibaba.alihealth.drugtrace.top.yljg.query.billstatus在2026-10-26 15:37:22及15:39:09（北京时间）返回HTTP200/error_response code7 App Call Limited，sub_code accesscontrol.limited-by-api-access-count；已降至4.5秒间隔仍拒绝。请核实该AppKey/该API的调用次数额度、计数周期、剩余额度及恢复时间，必要时扩容；并确认已受理后的推荐查询节奏。HTTP连通不代表查询成功，不需要据此新增端口。
'''
with (HERE/'README.md').open('a',encoding='utf8') as f:f.write(append)
handoff=ROOT/'docs/ai/sessions/DTR-UNKNOWN-CHECK-20261026.md'
handoff.write_text('# DTR-UNKNOWN-CHECK-20261026 交接\n\nCodex/model unknown；会话20261026T070804Z-b37f25ba。仅运维核查工具和文档，无应用源码/镜像/数据库/视图/服务配置改动。\n\n'+section.replace('../operations/','../../operations/').replace('(sessions/','(')+'\n实际命令及失败证据见[核查实录](../../operations/drug-trace-unknown-check-20261026/README.md)。sample exit0且ledger_unchanged；首轮/r2/r3业务核查均因平台TOP7退出1，通道读取exit0不是业务核查通过。实际结论41成功/143未找到/63未核实；未测应用回归，无应用变更不宣称全量测试。所有诊断进程已自然退出，无后台继续请求。\n\n下次只读progress3/inspect核对，禁止重跑all/resume/finalpass。平台额度恢复后按CSV待核63逐笔有限核查，已核184保留。需人工回写功能和历史人工对账后的恢复批准；不要直接UPDATE/清预算或重发UNKNOWN。finish_review.py为全量成功守卫脚本，本次没有执行，实际交付由record_partial.py生成。\n',encoding='utf8')
catalog=ROOT/'docs/catalog.json';data=json.loads(catalog.read_text('utf8'))
for path,category,title in [('docs/operations/drug-trace-unknown-check-20261026/README.md','operations','追溯平台是否收到的人工只读核查'),('docs/ai/sessions/DTR-UNKNOWN-CHECK-20261026.md','shared_ai_state','追溯未知与受理单据核查交接')]:
 if not any(x['path']==path for x in data['documents']):data['documents'].append({'path':path,'category':category,'title':title})
catalog.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(summary,ensure_ascii=False))
