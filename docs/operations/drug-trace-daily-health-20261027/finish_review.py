"""Write evidence-based status/handoff only; never changes production state."""
from pathlib import Path
import json,re
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[2]
TASK='DTR-DAILY-HEALTH-20261027'
sp=sorted((HERE/'evidence').glob('app-status-*.json'))[-1]
pp=sorted((HERE/'evidence').glob('app-progress-*.json'))[-1]
sd=json.loads(sp.read_text(encoding='utf8'));pd=json.loads(pp.read_text(encoding='utf8'))
assert sd['exit_code']==pd['exit_code']==0
s=sd['result'];p=pd['result'];q=p['database']['quality']
total=sum(x['n'] for x in q);valid=sum(x['n'] for x in q if x['quality_state']=='VALID');waiting=sum(x['n'] for x in q if x['quality_state']=='WAITING')
assert (total,valid,waiting)==(2200,1273,927)
assert p['database']['today_attempts']==0 and p['database']['collection']['state']=='RUNNING'
assert p['database']['progress']['row_version']>s['database']['checkpoint'][0]['row_version']
(HERE/'README.md').write_text(f'''# 2026-10-27定时上报只读核查

**截至08:31，今天执行的日批尚未完成，仍在采集，新增真实上传0。** 定时任务06:10:29启动，处理北京时间10月26日00:00至10月27日00:00；10月27日当天业务不在此批范围，按现有调度由次日任务处理。

| 项目 | 现场结果 |
| --- | --- |
| 日批服务 | activating/start，尚无退出时间，不能把ExecMainStatus=0视为已完成 |
| 采集运行 | RUNNING，窗口尚未完成 |
| 08:29已采集快照 | 2100张：1233 VALID、867 WAITING |
| 08:31已采集快照 | 2200张：1273 VALID、927 WAITING；不是全天最终总数 |
| 断点推进 | row_version67→68；业务游标14:03:03→14:14:17 |
| 今天新增提交/发送/查询回执 | 0/0/0；尚未进入发送阶段 |
| 日报 | 20261027-061029.json暂0字节；数据库10月26日业务日报尚无记录 |
| 既有全库状态 | 2577确认、41受理、205未知，均为此前结果 |
| 配额 | 2824/80000，未新增 |
| 内网NTP | 选中<intranet-host>，Reach377，Leap Normal，chronyd active/enabled |
| 容器 | 5 healthy；worker进程running但health unhealthy |

## 待核查数据和运行问题

当前927张WAITING均有QD-NO-CODES（无有效追溯码）；其中716张同时有QD-KIND-UNKNOWN、QD-NOT-FINALIZED、QD-BRANCH-UNAPPROVED，另211张为已识别发药业务但仍缺码。规则数量存在重叠，不能相加为多张失败，也不能把716直接等同原药房映射缺失；实际业务类型/批准分支须以已批准规则核对。本次只看当前采集快照，未查询HIS源表，不能判定原系统全天最终值。

worker最近心跳约8433秒未更新、health失败555次；同时两个快照显示100张增量和断点推进，所以不是“worker已停止”的证据。当前engine.main在同步we.tick()返回后才刷新心跳，而health要求mtime<60s；长采集阻塞心跳刷新的实现缺陷与现场一致。采集job租约仍为06:12:29过期值，长任务心跳/租约续期需一起评估，不能只延长健康阈值掩盖失租风险，也不能在真实采集进行中贸然重启。

原驱动先等整窗采集DONE才选候选并发送；当前尚无新平台尝试，不能归因为今天阿里平台限流或发送失败。驱动采集等待上限3小时，06:10开始意味着约09:10仍未完成会collect_timeout退出（具体受轮询/请求耗时影响）。当前unit总超时6小时不等同采集能等待6小时。08:31日报未生成，原07:30日报目标今日尚未达到。

## 下一步核查

采集完成后核对最终summary及WAITING原因，再确认发送attempt数量、ACCEPTED和正式3/51终态，以及日批退出码/日报；不能只用service或HTTP200判业务完成。若约09:10触发collect_timeout，保留原job和断点，先核运行状态再制定恢复，不重复创建采集或重发UNKNOWN。既有205未知/41受理不属于今天新失败，原历史核查/受控回写待办保留。

本轮没有改应用源码、镜像、unit/定时、业务数据库、HIS、授权/额度或共享限速文件，也没有请求平台、补发或重启服务。只读PG用default_transaction_read_only和statement_timeout保护；SSH沿用固定指纹与原受限凭据，无Secret/患者/原始码输出。

## 真实命令与证据

```powershell
python docs/operations/drug-trace-daily-health-20261027/prepare_check.py
python docs/operations/drug-trace-daily-health-20261027/check.py app status
python docs/operations/drug-trace-daily-health-20261027/check.py app progress
```

上述命令退出0；[首个快照](evidence/{sp.name})、[增量/健康与质量复核](evidence/{pp.name})。旧版本只读probe复制后将原错误文本输出改为错误码分组，防止业务字段进入报告。最初PowerShell内联字符串转义失败仅发生本机解析阶段，无现场操作，改为文件脚本后执行成功。

无应用行为修改，未运行应用回归，不将昨天的测试次数计为本轮通过。最终本机只校验脚本可编译、两个证据成功、统计/时间窗与链接一致。报告为08:31时点，不预判之后批次成功或失败。
''',encoding='utf8')
summary='''## 最新：10-27日批尚未完成，仍在采集（2026-10-27 08:31）

[只读核查与证据](LINK)：06:10:29启动，处理10-26业务窗；08:31采集RUNNING，快照2200张（1273 VALID/927 WAITING），较08:29增加100，断点67→68、游标14:03→14:14。今天新增提交/尝试/查询0，日报文件暂0字节/数据库日报未生成；原07:30日报目标尚未达到，不能称正常完成。
全库仍2577确认/41受理/205未知、额度2824，均为旧结果。927 WAITING均缺有效码，716另有分类未知/未完成/批准分支问题，规则重叠不相加。5容器healthy；worker进程running但heartbeat约8433s未刷新而unhealthy，源码为同步tick结束后才刷新；断点持续推进，不判停机。collect租约未续期也须后续评估，勿现场贸然重启。
驱动采集等待上限3h，约09:10仍未完成会超时；尚未进入发送，不能归因新平台限流。下一棒看采集终态/summary、日批退出码和正式平台终态；本轮没有访问HIS/平台、补发、改配置或业务写入。[交接](HANDOFF)。
'''
files=[]
for f in ['docs/ai/STATE.md','docs/ai/HANDOFF.md','docs/ai/BACKLOG.md','数据上报/1、码上放心平台/README.md']:
    path=ROOT/f;text=path.read_text(encoding='utf-8-sig');assert '## 最新：10-27日批尚未完成，仍在采集（2026-10-27 08:31）' not in text
    prefix='../' if f.startswith('docs/ai/') else '../../docs/'
    section=summary.replace('LINK',prefix+'operations/drug-trace-daily-health-20261027/README.md').replace('HANDOFF',('sessions/' if f.startswith('docs/ai/') else '../../docs/ai/sessions/')+TASK+'.md')
    first,rest=text.split('\n',1);path.write_text(first+'\n\n'+section+'\n'+rest,encoding='utf8');files.append(path)
handoff=ROOT/'docs/ai/sessions'/f'{TASK}.md'
assert not handoff.exists()
handoff.write_text('''# DTR-DAILY-HEALTH-20261027 · 今日上报核查

Codex/model unknown，session20261027T002910Z-738b7d30。用户仅要求核查今天数据是否完成；实际只读，未扩为修复/补跑。已读共享STATE/HANDOFF和既有probe，登记后无并行代理。[现场结果与证据](../../operations/drug-trace-daily-health-20261027/README.md)。

实际命令check.py app status和app progress均exit0；通过<jump-host>普通二跳到<prod-host>，凭据与指纹沿用受限文件，不输出秘密。只读查询应用PG、systemd/docker/chrony状态；没有HIS或平台调用、业务写入、服务重启、定时/授权变更。无应用回归，本轮脚本编译/证据/链接核对另记validation.json。

08:29→08:31采集从2100推进到2200，VALID1273/WAITING927；断点67→68，业务游标14:14，RUNNING。今天上传/新提交/回执0，06:10日批尚无退出时间、日报暂空，不把ExecMainStatus0/Resultsuccess当完成。旧2577/41/205及额度2824不变。10-27任务处理10-26，10-27业务由次日调度，不能混为同一窗口。

现场发现worker unhealthy而进程running；heartbeat8433s、collect租约06:12已过期，但数据继续持久化。engine主循环仅同步tick返回后写心跳，长collect持续占用，造成健康检查异常；不要未经维护安排中断真实任务。后续应同时评估长任务进度心跳/续租与当前单worker阻塞prepare/send，并保留CAS/fencing，不能仅延长阈值或加worker掩盖。927缺有效码，其中716另分类/未完成/批准分支问题，规则有重叠，未查源库不猜业务值域。

下一棒只读复查collection DONE/FAILED、最终质量summary、当天attempts和3/51、日批最终JSON/退出码/日报。驱动采集上限3h，约09:10可能collect_timeout；unit6h不是采集6h。若失败，留原job/断点后制定恢复，不重复采集或盲重发UNKNOWN。本次核查已完成，但日批业务未完成，07:30日报目标未达到；不得把此任务completed当上报完成。
''',encoding='utf8')
catalog=ROOT/'docs/catalog.json';d=json.loads(catalog.read_text(encoding='utf8'))
for path,category,title in [
 ('docs/operations/drug-trace-daily-health-20261027/README.md','operations','10月27日定时上报只读核查'),
 ('docs/ai/sessions/'+TASK+'.md','shared_ai_state','10月27日上报核查交接')]:
    assert not any(x['path']==path for x in d['documents']);d['documents'].append(dict(path=path,category=category,title=title))
catalog.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
broken=[];count=0
for path in files+[HERE/'README.md',handoff]:
    text=path.read_text(encoding='utf8');text=text.split('## ',2)[1] if path in files else text
    for link in re.findall(r'\]\(([^)]+)\)',text):
        if '://' in link or link.startswith('#'):continue
        count+=1
        if not (path.parent/link.split('#')[0]).exists():broken.append([str(path),link])
assert not broken,broken
for path in HERE.glob('*.py'):compile(path.read_text(encoding='utf8'),str(path),'exec')
out={'total':total,'valid':valid,'waiting':waiting,'progressing':True,'new_uploads':0,'daily_batch_complete':False,'links':count,'broken_links':broken,'readonly_remote_commands_exit0':True,'application_tests_run':False}
(HERE/'evidence/validation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
print(json.dumps(out,ensure_ascii=False))
