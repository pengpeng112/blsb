"""Record actual trial results and shared handoff; no remote operations."""
from pathlib import Path
import csv,json,re,xml.etree.ElementTree as ET
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
TASK='DTR-HISTORY-RETRY-20261026'
TARGET='9b19170e-5ceb-4da4-99bb-7478c77d9695'


def latest(phase):
    p=sorted((HERE/'evidence').glob('app-'+phase+'-*.json'))[-1]
    d=json.loads(p.read_text(encoding='utf8'));assert d['exit_code']==0
    return p,d['result']


qpath,q=latest('query');vpath,v=latest('verify')
progress=q['progress']
assert q['container_exit']==0 and progress['local_state']=='CONFIRMED'
assert progress['upload_calls']==1 and progress['query']=={'state':'CONFIRMED','result_type':'3','sub_process_flag':'51'}
assert v['target_state']=='CONFIRMED' and v['other_246_unchanged'] and v['checkpoint_unchanged']
assert v['states']=={'CONFIRMED':2577,'ACCEPTED':41,'UNKNOWN':205}
suite=next(ET.parse(ROOT/'implementation/var/history-retry-guards-r2.xml').getroot().iter('testsuite'))
assert suite.attrib['tests']=='24' and suite.attrib['failures']=='0' and suite.attrib['errors']=='0'

readme=HERE/'README.md';s=readme.read_text(encoding='utf8')
s=s.replace('受理不等于最终成功；等待至少120秒后查询。最终实测结果将在下方追加，未取得前不称测试成功。',f'''**本次真实历史重发测试成功。** 22:05:49受理，等待约128秒后，22:07:57平台返回result_type=3/sub_process_flag=51，全部处理成功；原业务服务将本地状态更新CONFIRMED并保存正式ReceiptEvent。22:08:32只读查库确认该单CONFIRMED、正式3/51回执1条。

| 项目 | 结果 |
| --- | --- |
| 范围 | 1张历史单、1条追溯码 |
| 新上传次数 | 1次，无自动重发 |
| 平台结果 | HTTP200受理→3/51全部处理成功 |
| 本地状态 | UNKNOWN→人工审计恢复→ACCEPTED→CONFIRMED |
| 全库 | 2577确认、41受理、205未知 |
| 历史其他246张 | 状态/版本不变 |
| 额度/源断点 | 2824/80000；断点46不变 |
| 运行服务 | 原flow发行、六healthy、timer active/waiting |

实际证据：[正式查询](evidence/{qpath.name})、[最终查库与保护核验](evidence/{vpath.name})、[本机保护专项XML](../../../implementation/var/history-retry-guards-r2.xml)。这次闭环证明所选历史单可恢复并成功上传；未测试其他205张，不保证不同历史内容均能通过。
''')
readme.write_text(s,encoding='utf8')

old=ROOT/'docs/operations/drug-trace-unknown-check-20261026/核查明细_最终版_无患者信息.csv'
with old.open(encoding='utf-8-sig',newline='') as f:
    reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
remain=[r for r in rows if r['local_state']=='UNKNOWN' and r['submission_id']!=TARGET]
assert len(remain)==205
with (HERE/'仍未重发_205张_原核查时间保留.csv').open('w',encoding='utf-8-sig',newline='') as f:
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(remain)

summary='''## 最新：历史一张重发测试成功（2026-10-26 22:08）

[真实闭环与维护证据](LINK)：用户明确授权历史重发测试，选择原HTTP429且仅1条码的历史单；新鲜完整查询NOT_FOUND、配置/源/授权/租户/范围/空在途复核后，ManualDecision+审计+精确CAS仅恢复该张，再走原perform_send。22:05:49唯一一次POST返回HTTP200 ACCEPTED；22:07:57正式查3/51 CONFIRMED并由原服务落账，22:08最终查库确认。
本地现2577确认/41受理/205未知，配额2824/80000，断点46不变；原attempt1的429保留，新attempt2受理，12代查询job未删除/重置。其他246张状态/版本摘要、目标冻结digest均未变。六容器healthy、原timer active/waiting，没有改应用镜像/HIS/视图/字典/定时范围；维护保护专项24通过，未重跑应用全量。
这是一张固定目标的维护例外，不是批量恢复：剩205张未知未发送；旧41平台已成功应核查回写，勿重发。剩余清单保留原核查时间，不称刚查询过。首个新流程完整日批与07:30SLA仍待观察。[交接](HANDOFF)。
'''


def prepend(path,link,handoff):
    p=ROOT/path;s=p.read_text(encoding='utf-8-sig');assert '## 最新：历史一张重发测试成功（2026-10-26 22:08）' not in s
    first,rest=s.split('\n',1)
    p.write_text(first+'\n\n'+summary.replace('LINK',link).replace('HANDOFF',handoff)+'\n'+rest,encoding='utf8')


for p in ['docs/ai/STATE.md','docs/ai/HANDOFF.md','docs/ai/BACKLOG.md']:
    prepend(p,'../operations/drug-trace-history-retry-20261026/README.md','sessions/'+TASK+'.md')
prepend('数据上报/1、码上放心平台/README.md','../../docs/operations/drug-trace-history-retry-20261026/README.md','../../docs/ai/sessions/'+TASK+'.md')
handoff=ROOT/'docs/ai/sessions'/('DTR-HISTORY-RETRY-20261026.md')
assert not handoff.exists()
handoff.write_text('''# DTR-HISTORY-RETRY-20261026 · 历史单重发测试交接

Codex/model unknown，session20261026T135855Z-86d40348。用户明确要求历史重发测试；先读START_HERE/STATE/HANDOFF并登记，无并行代理。实际仅维护脚本、专项测试、共享记录和业务导航，没有修改登记范围内业务服务源码，也没有部署新镜像或访问HIS。

[任务说明和真实证据](../../operations/drug-trace-history-retry-20261026/README.md)。选择9b19170e-5ceb-4da4-99bb-7478c77d9695，原HTTP429/UNKNOWN/1条码；使用同企业/查询A/原单号/日期覆盖先完整查询NOT_FOUND，复核原冻结配置、digest、源版本、授权scope、租户、空在途和共享冷却。

人工一次决策94184837-4fc8-50e8-b9c6-c499c3415e33与审计/预查回执及UNKNOWN+版本3 CAS恢复PREPARED在同一事务提交；然后正常perform_send再次校验并发送一次。22:05:49 HTTP200 ACCEPTED，等待至少120秒，22:07:57正常perform_query查3/51 CONFIRMED并落真实回执/状态。不是手写成功状态。未新建payload/单据号，未重置原12代查询job，未覆盖原attempt1。

实际命令：

```powershell
# implementation目录，本机合成；最终24通过，退出0
python -m pytest tests/integration/test_drug_trace_history_retry.py -q -ra --junitxml=var/history-retry-guards-r2.xml
# 根目录，以下现场命令已执行，send不得重跑
python docs/operations/drug-trace-history-retry-20261026/prepare_trial.py
python docs/operations/drug-trace-history-retry-20261026/check.py app inspect
python docs/operations/drug-trace-history-retry-20261026/check.py app send
python docs/operations/drug-trace-history-retry-20261026/check.py app status
python docs/operations/drug-trace-history-retry-20261026/check.py app query
python docs/operations/drug-trace-history-retry-20261026/check.py app verify
```

本机最初23保护测试通过，但未覆盖实际查询job类型；首次现场临时脚本写错drug_trace.query，断言在授权CAS之前拦截（发送0/决策0）。保存旧源码和绑定，修正为注册常量drug_trace.query_status并增加真实SQLite计数测试，24通过后在新r2目录执行一次成功；原失败证据保留。未把首轮失败隐去，未重复真实发送。

最后只读查库：2577确认/41受理/205未知，2823提交/2824尝试，额度2824/80000，源断点46；投影恢复目标旧状态/版本后，历史247摘要与升级前一致，证明其他246未变。目标digest未变，原429尝试保留，新的200受理尝试及1条正式3/51查询回执存在。六容器healthy、timer active/waiting；普通daily-upload.service上次失败历史仍保留，不能用timer状态声称昨日任务全正常。

下一步：其余205张未知按保留原时间的清单逐单新查、核历史和批准恢复，不批量UPDATE UNKNOWN；41已在平台成功的走受控回写，不能重传。本轮只固定一张一次POST，send/prepare_trial不能拿来做通用恢复工具，正式批量恢复需要独立范围和逐单审计设计。原定时授权与共享冷却保持，首次完整新日批/SLA未观察。真实上传无法用恢复数据库撤销，不倒灌旧备份。
''',encoding='utf8')

cp=ROOT/'docs/catalog.json';catalog=json.loads(cp.read_text(encoding='utf8'))
for path,category,title in [
 ('docs/operations/drug-trace-history-retry-20261026/README.md','operations','历史追溯单一次重发测试实录'),
 ('docs/ai/sessions/'+TASK+'.md','shared_ai_state','历史追溯重发测试交接')]:
    assert not any(d['path']==path for d in catalog['documents'])
    catalog['documents'].append(dict(path=path,category=category,title=title))
cp.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
files=[readme,handoff]+[ROOT/p for p in ['docs/ai/STATE.md','docs/ai/HANDOFF.md','docs/ai/BACKLOG.md','数据上报/1、码上放心平台/README.md']]
broken=[];count=0
for p in files:
    s=p.read_text(encoding='utf8');s=s if p in files[:2] else s.split('## ',2)[1]
    for link in re.findall(r'\]\(([^)]+)\)',s):
        if '://' in link or link.startswith('#'):continue
        count+=1
        if not (p.parent/link.split('#')[0]).exists():broken.append([str(p),link])
assert not broken,broken
out={'links':count,'broken':broken,'guards_tests':24,'real_uploads':1,'final_platform_state':'3/51 CONFIRMED','remaining_unknown_not_retried':205,'final_ledger':v}
(HERE/'evidence/completion.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps({k:out[k] for k in ['links','broken','guards_tests','real_uploads','final_platform_state','remaining_unknown_not_retried']},ensure_ascii=False))
