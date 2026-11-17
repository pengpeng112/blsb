from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]


def prepend(rel,section):
    p=ROOT/rel
    first,rest=p.read_text('utf8').split('\n',1)
    p.write_text(first+'\n\n'+section.strip()+'\n'+rest,encoding='utf8')


summary='''## 最新：10-26日批部分失败，只发3张；发送排队触发90秒保护（2026-10-26 08:32）

[只读实测与修复重点](../operations/drug-trace-daily-health-20261026/README.md)：06:10:29触发，处理10-25窗口；08:19采集DONE2059单（VALID1352/WAITING707），08:31:05驱动exit1/PARTIAL_FAILURE，timer日报已入库。
新增3次HTTP200/ACCEPTED，其中2CONFIRMED、1ACCEPTED；无新429/UNKNOWN。第三个send排队近164秒才结束，超过驱动90秒等待，触发SEND_WORKER_TIMEOUT停止后续新发送；不是平台拒绝。之后247次历史query因12轮耗尽返回HTTP412，约9分钟后结束。
截至08:31:37，407 PREPARED未发；另404 prepare待处理+1执行中；537次prepare FAILED（050121缺企业映射530、050116缺映射7），仍在增长。全库411CONFIRMED/42ACCEPTED/206UNKNOWN，quota659。worker继续准备不等于driver继续发送。
六容器healthy，日报GET/详情/stats/scope均200；DMZ NTP Normal，<prod-host>仍未同步。下次10-27 06:10只处理10-26，不自动补今日失败窗口。旧文“VERSION_CONFLICT=409”纠正为项目实际HTTP412。
P0：修发送排队/等待与积压公平性、过滤查询轮次耗尽及在途job、确认批准两药房企业字典；验证后幂等恢复10-25，不能重发已受理/UNKNOWN或重置配额。日报还需覆盖后台prepare和query失败。
本轮未重启、未改远程/业务代码、未补跑或新增平台调用，只读证据已存；待修复不能用六容器healthy或日报入库成功替代业务验收。交接：[本任务](sessions/DTR-DAILY-HEALTH-20261026.md)。
'''
for rel in ['docs/ai/STATE.md','docs/ai/HANDOFF.md']:
    prepend(rel,summary)
prepend('docs/ai/BACKLOG.md','''## P0新增：10-26日批只发3张后退出（2026-10-26）

- [核查与修复要点](../operations/drug-trace-daily-health-20261026/README.md)：真实第三笔send排队164秒超过90秒驱动等待；实际已受理，但后续发送中止。应区分排队/执行等待并治理单worker prepare积压，保持不二次投递。
- 过滤12轮耗尽/已有在途查询，避免每日重复247个query POST返回HTTP412；不删历史job/清查询预算。昨日说明409已按实际contracts纠正。
- 050121/050116批准企业映射仍缺，今天截至08:31已有537 prepare FAILED；由权威确认后批准新revision。
- 修复并验证后恢复10-25窗口；当时407 PREPARED未发、404 prepare待处理+1执行中；3受理中2确认1待确认，quota659不可重置。下次定时处理新日不补旧窗口。
- 日报/看板需覆盖后台prepare失败与query错误，不把当前errors_count=1当全部异常数。NTP/正式证书/管理TLS仍按前述外部事项处理。
- 本次仅核查，无生产修复/重启/补跑；[交接](sessions/DTR-DAILY-HEALTH-20261026.md)。
''')
prepend('数据上报/1、码上放心平台/README.md','''## 2026-10-26 08:32 现场核查

[今日日批报告与修复重点](../../docs/operations/drug-trace-daily-health-20261026/README.md)：06:10自动触发、采集2059单完成，但第三张发送排队超过90秒后中止，08:31以PARTIAL_FAILURE退出；3张真实受理中2张确认、1张待确认，无新429。
两药房映射缺失、发送准备队列积压、247次历史查询上限拒绝仍影响运行；当时407张PREPARED未发。看板日报已入库不代表全批成功。本轮只读未修复/补跑，具体状态以共享STATE及报告为准。
''')
text='''# DTR-DAILY-HEALTH-20261026 交接

Codex / model unknown；session 20261026T002857Z-b30a80fd。用户要求“看看今天是否正常”，本轮仅只读核查。

报告：[今日日批实测与修复优先级](../../operations/drug-trace-daily-health-20261026/README.md)，证据和复用脚本同目录。

06:10触发10-25窗口；08:19采集DONE2059（1352有效、707等待），08:31:05驱动exit1/PARTIAL_FAILURE，源timer日报成功入库。第三个send 08:20:26入队、08:23:09真正发出、08:23:10成功受理，排队超过90秒导致driver先中止；3张均HTTP200、2确认1待确认。旧247查询轮次耗尽仍被驱动逐一POST，实际HTTP412（contracts VERSION_CONFLICT），耗时约9分。

08:31:37：407 PREPARED、537 prepare FAILED（530为050121、7为050116缺批准企业映射）、404 PENDING+1 LEASED prepare；worker继续准备，但driver已退出，不会自动发余单。全库411确认/42受理/206未知，used659。DMZ无新429、NTP Normal；<prod-host>仍未同步。日报API list/detail/stats/scope均200，界面错误1未纳入后台537失败、query错误247需看详情API。

下一步：先修积压发送等待/公平调度、查询预算及在途去重、日报后台错误汇总；权威确认批准两药房映射后，验证并幂等恢复10-25失败窗口，保留配额/历史attempt，不重发已受理/确认/UNKNOWN。详见报告P0及回归场景。次日定时不自动补今日窗口。

本轮没有修改业务源码、服务器或审批，没有触发新的采集/平台业务请求；无业务测试运行。已更新共享STATE/HANDOFF/BACKLOG及业务README，登记与证据随工作区交接。
'''
(ROOT/'docs/ai/sessions/DTR-DAILY-HEALTH-20261026.md').write_text(text,encoding='utf8')
print('Updated current status, backlog, business navigation and session handoff.')
