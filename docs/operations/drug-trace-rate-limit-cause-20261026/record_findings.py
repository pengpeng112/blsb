from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,importlib.util
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
dmz=json.loads(sorted((HERE/'evidence').glob('dmz-dmz-*.json'))[-1].read_text('utf8'))
app=json.loads(sorted((HERE/'evidence').glob('app-app-*.json'))[-1].read_text('utf8'))
assert dmz['exit_code']==0 and app['exit_code']==0
d=dmz['result'];a=app['result']
assert d['config']['rate_per_minute']==60 and d['config']['rate_burst']==10
assert d['periods']['old_failure']['counts']['reject:429:rate_limited']==3679
assert d['periods']['old_failure']['peak_per_utc_minute'][0][1]==194
assert a['query_jobs_for_old247']==2964
assert not a['running_transport']['transport_inspects_top_code7'] and not a['running_transport']['query_parser_keeps_subcode']
now=datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
reference={'url':'https://developer.alibaba.com/docs/doc.htm?articleId=108869&docType=1','title':'淘宝开放平台：App Call Limited问题','published_updated':'2021-11-26',
 'verified_at':now,'interpretation':'官方通用定义：limited-by-api-access-count为API全部应用共享的总调用频率限制；不同于单AppKey的app-access-count或未上线应用的app-api-access-count。医疗API特定阈值仍须平台确认。','copied_full_article':False}
(HERE/'evidence/official-reference.json').write_text(json.dumps(reference,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
block=f'''## 最新：限流原因已分层定位（{now}）

[原因实证与后续整改](../operations/drug-trace-rate-limit-cause-20261026/README.md)：10-25的HTTP429来自DMZ本地per-IP 60/min、burst10；固定失败窗6903次请求/3679本地拒绝，峰194/min、14/s。旧247共2964查询job放大请求量，旧transport无pacing；本地限速已于10-25修复，当前1.5s/40min并HTTP429冷却，修复后固定窗0本地429。
10-26的HTTP200/TOP7为平台返回accesscontrol.limited-by-api-access-count。官方通用解释是该API所有应用共享的秒/分钟总频率限制，**不能据此断言本院AppKey日额度耗尽**；前一棒建议查应用额度只是待确认方向，现按子码应优先确认平台接口总流控。具体阈值/禁用秒数/医药业务覆盖策略未获确认。
当前缺口实测源码确认：transport只因HTTP429立即冷却，TOP7未建立跨单据共享冷却；query解析未留sub_code/sub_msg；QUERY_ERROR统一120s退避/12轮、进程内预算不跨维护进程共享。建议独立整改，**本轮未修改或部署**。DMZ来源均<relay-net-host>，NAT是否还共享其他系统未核实，不把全量请求都归于<prod-host>。
本轮仅SSH日志聚合/应用库只读SELECT及官方文档检索，平台请求/HIS/发送/业务写入0。既有41平台成功/206 NOT_FOUND仍未本地回写或补报。[交接](sessions/DTR-RATE-LIMIT-CAUSE-20261026.md)。

'''
def insert(path,text):
 p=ROOT/path;old=p.read_text('utf8');head,tail=old.split('\n',1);p.write_text(head+'\n\n'+text+tail.lstrip('\n'),encoding='utf8')
for name in ['STATE.md','HANDOFF.md','BACKLOG.md']:insert('docs/ai/'+name,block)
insert('数据上报/1、码上放心平台/README.md',block.replace('../operations/','../../docs/operations/').replace('(sessions/','(../../docs/ai/sessions/'))
handoff=ROOT/'docs/ai/sessions/DTR-RATE-LIMIT-CAUSE-20261026.md'
handoff.write_text('# DTR-RATE-LIMIT-CAUSE-20261026 交接\n\nCodex/model unknown；session20261026T122448Z-7d63f104。只读分析，无应用源码/配置/镜像/视图变更。\n\n'+block.replace('../operations/','../../operations/').replace('(sessions/','(')+'\n实际命令两条check.py dmz dmz、check.py app app均exit0；内部journalctl/psql也exit0，结果与代码核对。官方原文访问及精确子码含义已登记。未测试应用回归，无应用变更不冒充新增功能通过。后续整改优先保留TOP子码、共享冷却与限流错峰/低频查询；不要增大DMZ限额或换AppKey绕过，不重跑未知补发。\n',encoding='utf8')
catalog=ROOT/'docs/catalog.json';data=json.loads(catalog.read_text('utf8'))
for path,category,title in [('docs/operations/drug-trace-rate-limit-cause-20261026/README.md','operations','追溯上报限流原因实证分析'),('docs/ai/sessions/DTR-RATE-LIMIT-CAUSE-20261026.md','shared_ai_state','追溯限流原因分析交接')]:
 if not any(r['path']==path for r in data['documents']):data['documents'].append({'path':path,'category':category,'title':title})
catalog.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
spec=importlib.util.spec_from_file_location('rate_cause_links',ROOT/'scripts/project_docs.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
for p in [HERE/'README.md',handoff]:
 for link in m.link_targets(p.read_text('utf8')):
  if '://' in link or link.startswith('#'):continue
  assert (p.parent/link.split('#',1)[0]).exists(),(p.name,link)
print(json.dumps({'documented':True,'evidence_checked':True,'links_valid':True,'source_changes':0,'new_platform_calls':0,'his_calls':0}))
