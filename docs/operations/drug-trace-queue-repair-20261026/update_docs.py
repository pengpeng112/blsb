from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]


def prepend(rel,section):
    p=ROOT/rel
    first,rest=p.read_text('utf8').split('\n',1)
    p.write_text(first+'\n\n'+section.strip()+'\n'+rest,encoding='utf8')


summary='''## 最新：排队超时已修复部署，10-25恢复运行中（2026-10-26 09:09）

[修复与三类缺项说明](../operations/drug-trace-queue-repair-20261026/README.md)：生产queue-20261026T010000Z-r2已上线，六容器healthy；发送/查询priority50/60、每5槽保留1个后台槽，排队1800s/执行180s分开等待原job，不重投递。
driver过滤已12轮耗尽/在途查询，看板分开展示发送/查询错误和耗尽；未改证据、scope、配额或批准字典。最终局部47通过，**全量482通过/7跳过/0失败**，同源构建与离线依赖一致。
09:01:52按原unit恢复10-25窗口，复用DONE采集和567 PREPARED；09:09:14已新增71次HTTP200受理、65确认，余496PREPARED，**仍RUNNING，不能称整批完成**。全库477确认/47受理/206未知，used730；旧41+206状态版本摘要及源断点46均未变。
4h只读观察器blyb-queue-observe-20261026.service每分钟写/var/log/blyb/queue-recovery-20261026/latest.json，结束写final.json，超时写observer-timeout.json；无平台/HIS请求或业务写入。回执20261026-090152.json在活动期间0字节属预期，原061029失败回执保留。
旧815缺映射是10-24快照（050121=810/050116=5），10-25最终另782（775/7）；两药房是否共用已登记医院ref_ent_id等待用户答复，未猜值批准。旧41/206预算耗尽仍待平台核对，过滤无效查询不等于业务已核清。
新增未修明细边界：submission-outcomes先limit200再筛问题，可能漏掉较旧UNKNOWN；聚合统计为准，问题列表0不等于已清零。NTP/正式证书/TLS仍为原外部事项。
下一棒只读看恢复final/日报/终态，再按确认处理药房字典及旧单核对；不要另开driver/worker或重跑一次性部署脚本。交接：[本任务](sessions/DTR-QUEUE-REPAIR-20261026.md)。下方日批失败是本次恢复前的真实历史。
'''
for rel in ['docs/ai/STATE.md','docs/ai/HANDOFF.md']:
    prepend(rel,summary)
prepend('docs/ai/BACKLOG.md','''## 最新处置：DTR-QUEUE-REPAIR-20261026（2026-10-26 09:09）

- 已部署验证：排队/执行分开、send/query优先级+每5槽后台保障、过滤耗尽/在途query、看板查询错误列。局部47/全量482通过7跳过，原90秒保护导致的拥堵中止已修。
- 进行中：10-25恢复（原567 PREPARED），09:09新增71受理/65确认；观察/var/log/blyb/queue-recovery-20261026/final.json，不新增第二个消费者。旧247状态与断点不变、配额不重置。
- 待用户信息：050121/050116是否均属本院同一平台企业身份，确认后新建/批准映射revision；旧815和10-25另782未擅自处理。
- 待平台核对：旧41受理/206未知，12轮预算未绕过；当前只避免无效再POST查询，不等于已确认或可重发。
- P1新发现：submission-outcomes先limit200再筛问题，较旧206 UNKNOWN可能不在列表；应SQL先筛后限+租户/LEFT JOIN/旧异常反例验证，不提高limit造绿。
- 仍需：NTP UDP123/回包、正式证书与管理TLS；长collect心跳/租约、原07:30 SLA及跨日补偿后续治理。
- 证据：[本轮实录](../operations/drug-trace-queue-repair-20261026/README.md)，[交接](sessions/DTR-QUEUE-REPAIR-20261026.md)。
''')

common='''## 2026-10-26 排队超时与查询预算修复（DTR-QUEUE-REPAIR-20261026）

[现场部署/解释/验证](../../docs/operations/drug-trace-queue-repair-20261026/README.md)：queue-20261026T010000Z-r2上线，现有012不变，依赖不变。
send/query现有队列priority50/60；worker每5槽保障1个后台任务，仍使用原lease/fencing。driver按原job区分排队1800s和执行180s等待，不重复投递；过滤12轮用尽及在途查询，记录skipped_exhausted/inflight。
日报列表追加query_errors_count/query_skipped_exhausted；UI分开展示准备/发送错误、查询错误及轮次用尽，“发送请求”不等于CONFIRMED。
生产复用10-25 DONE与567 PREPARED恢复；09:09新增71受理/65确认，仍在运行。旧41+206状态/版本摘要、源断点46均未变，配额按真实attempt递增；未修改批准字典/范围/未知重发规则。
最终局部47 pass、全量482 pass/7 skip/0 fail（var/queue-repair-regression-final.xml）；同源构建和源码/镜像/driver SHA核对通过。恢复最终收尾及药房映射/旧单核清仍待跟踪。
已发现未修：问题明细先limit200再筛可能漏较旧异常，不能凭列表空认定业务清零；聚合统计不受此展示边界影响。
'''
for rel in ['implementation/docs/实施状态矩阵.md','implementation/docs/接口与设计差异.md','implementation/docs/04-API目录对照矩阵.md','implementation/docs/测试执行记录.md']:
    prepend(rel,common)
prepend('数据上报/1、码上放心平台/README.md','''## 2026-10-26 09:09 排队超时已修复，恢复仍运行

[修复、三类缺项和恢复观察](../../docs/operations/drug-trace-queue-repair-20261026/README.md)：queue-20261026T010000Z-r2已部署，队列优先级+后台公平槽、排队/执行分开等待和查询预算过滤生效；全量482通过/7跳过。
复用10-25已采集数据，原567 PREPARED恢复中；09:09新增71受理/65确认，旧41受理及206未知未重发、状态摘要不变。两药房平台企业归属等待确认，旧815/今782缺映射未盲补。
只读观察器最终结果在<prod-host> /var/log/blyb/queue-recovery-20261026/final.json；活动回执尚未收尾，不称全部成功。最新状态仍看共享STATE/HANDOFF。
''')

session='''# DTR-QUEUE-REPAIR-20261026 交接

Codex / model unknown；session20261026T004340Z-4d9bbc61。用户要求解释41/206/815并一起查看超时，按持续修复/部署/发送授权落实。

[完整实录](../../operations/drug-trace-queue-repair-20261026/README.md)，脚本和脱敏证据同目录。实际运行queue-20261026T010000Z-r2，012和依赖不变。

已实现/部署：send50/query60优先级；每5槽1后台，原lease_epoch不改；driver排队1800s/执行180s分开等原job、未知/超限仍停新发送；12轮耗尽和已有query在途过滤；列表/详情分开展示query错误与耗尽。局部47，全量482pass7skip0fail；构建/离线freeze/源码/安装SHA匹配。原测试fixture错误和过时中止回归保留，最终单次全量为绿。

09:01:52通过原daily-upload.service恢复10-25，只复用原DONE采集和567 PREPARED，前三张已确认跳过；09:09:14新增71次已结束HTTP200受理、65确认，余496PREPARED，全库477确认/47受理/206未知、quota730。旧41+206状态版本hash不变，r10断点46不变，未新采集/HIS访问、未清配额或重发未知。仍RUNNING，不称567全部完成。

观察器blyb-queue-observe-20261026.service，4h只读，每分钟写/var/log/blyb/queue-recovery-20261026/{latest.json,progress.jsonl}，业务退出写final.json；超时写observer-timeout.json。不对外查询/发数据、不排队、不写业务库。当前回执/var/log/blyb/daily-upload/20261026-090152.json可能仍0字节，结束才写；原061029失败文件保留。

部署备份<prod-path>/backups/20261026T005833Z；driver备份/root/blyb-maintenance-backups/20261026T010142Z-driver；升级日志/root/blyb-maintenance-backups/upgrade-queue-20261026T010000Z-r2-r1。

用户仍未回复050121/050116是否均属本院且共用已登记ref_ent_id。批准字典未动；旧10-24缺映射815（810/5），10-25另782（775/7），不混同。旧41/206自动查询预算12已满，过滤无效再查不等于核清；需平台结果或有审计的受控人工复查，不删job清次数。不要重索AppKey/Secret。

新发现未修P1：submission-outcomes先取最近200再筛only=problem，新日大量PREPARED会遮住旧UNKNOWN，列表0不等于206清零；应SQL先筛后limit、保留租户/LEFT JOIN语义并增加旧异常反例。另NTP/CA/TLS、长collect心跳/租约、日报SLA、跨日补偿仍为后续项。

下一棒先check.py app observe并读final/回执/日报终态；当前恢复时不再起driver/worker，不重复deploy/install_driver/recover等一次性脚本。若用户确认药房，使用原API新建/批准新revision再校验，不原地改冻结或批准字典。
'''
(ROOT/'docs/ai/sessions/DTR-QUEUE-REPAIR-20261026.md').write_text(session,encoding='utf8')
print('Updated shared handoff, backlog, four implementation docs and business navigation.')
