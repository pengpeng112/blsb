# 当前交接

## 最新：10-27日批已超时失败，后台仍采集（2026-10-27 09:19）

[详细现状、问题和修复顺序](../operations/drug-trace-report-review-20261027/README.md)：06:10启动的10-26窗09:10:31 collect_timeout/exit3，FAILED日报已入库；09:19后台RUNNING，3000张=1773 VALID+1227 WAITING，新增提交/发送0，断点76/16:39。1227无有效码：979住院原始1/0、3退药0/2尚未批准分支，另245正常门诊0/1缺码，未查HIS不能断言源库没扫码。
当前驱动已退出，采集完成只会自动prepare、不自动send；明天默认只处理10-27，需后续受控恢复失败10-26窗。同步长collect阻塞outbox3000条，heartbeat约11355s/worker unhealthy、租约06:12过期；进程和采集仍推进，勿贸然重启或加worker。现场源码SHA一致，Oracle读取逐单明细+头复核、每次fetch新建/关闭连接，是需计时优化的明确点，未拿到Oracle慢SQL不能断言具体耗时占比。
累计2577确认/41受理/205未知、配额2824不变；旧41平台已成功待回写、205未补发，246均耗尽12轮。FAILED日报collection空/candidate0缺采集原因；已确认单下8077码项仍PENDING，不等同平台未收到，缺码级闭环。3000张应有码数均NULL，VALID不能证明无码遗漏。
内外NTP正常、DMZ active，今日转发/拒绝0；本轮只读应用库和系统日志，HIS/平台请求、业务写入、补发、重启0。修复尚未执行，详见优先级表。[交接](sessions/DTR-REPORT-REVIEW-20261027.md)。


## 最新：10-27日批尚未完成，仍在采集（2026-10-27 08:31）

[只读核查与证据](../operations/drug-trace-daily-health-20261027/README.md)：06:10:29启动，处理10-26业务窗；08:31采集RUNNING，快照2200张（1273 VALID/927 WAITING），较08:29增加100，断点67→68、游标14:03→14:14。今天新增提交/尝试/查询0，日报文件暂0字节/数据库日报未生成；原07:30日报目标尚未达到，不能称正常完成。
全库仍2577确认/41受理/205未知、额度2824，均为旧结果。927 WAITING均缺有效码，716另有分类未知/未完成/批准分支问题，规则重叠不相加。5容器healthy；worker进程running但heartbeat约8433s未刷新而unhealthy，源码为同步tick结束后才刷新；断点持续推进，不判停机。collect租约未续期也须后续评估，勿现场贸然重启。
驱动采集等待上限3h，约09:10仍未完成会超时；尚未进入发送，不能归因新平台限流。下一棒看采集终态/summary、日批退出码和正式平台终态；本轮没有访问HIS/平台、补发、改配置或业务写入。[交接](sessions/DTR-DAILY-HEALTH-20261027.md)。


## 最新：历史一张重发测试成功（2026-10-26 22:08）

[真实闭环与维护证据](../operations/drug-trace-history-retry-20261026/README.md)：用户明确授权历史重发测试，选择原HTTP429且仅1条码的历史单；新鲜完整查询NOT_FOUND、配置/源/授权/租户/范围/空在途复核后，ManualDecision+审计+精确CAS仅恢复该张，再走原perform_send。22:05:49唯一一次POST返回HTTP200 ACCEPTED；22:07:57正式查3/51 CONFIRMED并由原服务落账，22:08最终查库确认。
本地现2577确认/41受理/205未知，配额2824/80000，断点46不变；原attempt1的429保留，新attempt2受理，12代查询job未删除/重置。其他246张状态/版本摘要、目标冻结digest均未变。六容器healthy、原timer active/waiting，没有改应用镜像/HIS/视图/字典/定时范围；维护保护专项24通过，未重跑应用全量。
这是一张固定目标的维护例外，不是批量恢复：剩205张未知未发送；旧41平台已成功应核查回写，勿重发。剩余清单保留原核查时间，不称刚查询过。首个新流程完整日批与07:30SLA仍待观察。[交接](sessions/DTR-HISTORY-RETRY-20261026.md)。


## 最新：上传与限流处理已改进上线（2026-10-26 21:32）

[实现、证据及恢复说明](../operations/drug-trace-upload-flow-20261026/README.md)：<prod-host>与DMZ均已切换flow-20261026T132000Z；六容器healthy、前置机active/config校验0、正式mTLS健康200、原timer active/waiting。新增跨进程持久节奏和TOP7/HTTP429共享冷却，保留脱敏子错误/等待秒数；真实受理首查120s、未知300s、后续指数错峰、重叠日期窗去重。只在明确本次未触网时复用原job延后，不增加查询代次；宿主driver同时适配等待。没有改视图r10、冻结payload/批准字典或发送授权。
全量522通过/7跳过/0失败，局部69通过1跳过、API/driver54通过、实际隔离PG1通过；一次正式已成功单只读查询返回3/51 CONFIRMED。没有新增真实上传或HIS访问，维护间旧247摘要、配额2823与断点46均不变；旧41仍待受控回写，206不得盲重发。
下一步观察首个完整日批的积压、冷却收尾与07:30日报；平台实际阈值/共享NAT仍待确认。旧247轮询预算没有重置，管理电脑TLS/IP SAN和真实浏览器验收也不因本轮自动完成。[交接](sessions/DTR-UPLOAD-FLOW-20261026.md)。

## 最新：限流原因已分层定位（2026-10-26 20:30:23）

[原因实证与后续整改](../operations/drug-trace-rate-limit-cause-20261026/README.md)：10-25的HTTP429来自DMZ本地per-IP 60/min、burst10；固定失败窗6903次请求/3679本地拒绝，峰194/min、14/s。旧247共2964查询job放大请求量，旧transport无pacing；本地限速已于10-25修复，当前1.5s/40min并HTTP429冷却，修复后固定窗0本地429。
10-26的HTTP200/TOP7为平台返回accesscontrol.limited-by-api-access-count。官方通用解释是该API所有应用共享的秒/分钟总频率限制，**不能据此断言本院AppKey日额度耗尽**；前一棒建议查应用额度只是待确认方向，现按子码应优先确认平台接口总流控。具体阈值/禁用秒数/医药业务覆盖策略未获确认。
当前缺口实测源码确认：transport只因HTTP429立即冷却，TOP7未建立跨单据共享冷却；query解析未留sub_code/sub_msg；QUERY_ERROR统一120s退避/12轮、进程内预算不跨维护进程共享。建议独立整改，**本轮未修改或部署**。DMZ来源均<relay-net-host>，NAT是否还共享其他系统未核实，不把全量请求都归于<prod-host>。
本轮仅SSH日志聚合/应用库只读SELECT及官方文档检索，平台请求/HIS/发送/业务写入0。既有41平台成功/206 NOT_FOUND仍未本地回写或补报。[交接](sessions/DTR-RATE-LIMIT-CAUSE-20261026.md)。

## 最新：平台人工查询完成，41受理已在平台成功（2026-10-26 18:18:41）

[实际核查与边界](../operations/drug-trace-unknown-check-20261026/README.md)：用户要求看平台是否收到，按冻结身份/类型/业务和上传日期覆盖查询旧247笔。去重结果{'UNKNOWN->NOT_FOUND': 206, 'ACCEPTED->CONFIRMED': 41}，已知成功单作正对照正常；不是mock。
旧206 UNKNOWN原发送205次HTTP429、1次FileNotFoundError；历史查询126仍429/80未找到，41受理历史查询也429且均12轮耗尽。下午r1—r3遇TOP7/调用次数限制已停并保留143未找到/63未核实的部分报告；用户随后要求继续，18:12单笔实测额度恢复且NOT_FOUND，r4以4.5秒间隔完成剩余62笔，未重复核已查清记录。未猜平台精确额度或恢复周期。
本次只读独立证据，不改业务状态/轮询预算、不发新单、不读HIS。生产本地仍2576确认/41受理/206未知，额度2823/80000、断点46不变；**41笔平台确认不等于看板已经回写**。逐笔UUID及结果CSV在任务目录，无患者/原始码/凭据。
下一步：受控人工核查回写41笔并保留平台证据；206按NOT_FOUND名单比对历史人工上报及身份后制定恢复清单，禁止盲重发；平台TOP7退避/子错误保存、轮询耗尽人工入口、明细分页仍待完善。[交接](sessions/DTR-UNKNOWN-CHECK-20261026.md)。

## 最新：平台核查184笔有结论，63笔受API额度限制（2026-10-26 15:49:14）

[实测核查与证据](../operations/drug-trace-unknown-check-20261026/README.md)：旧41 ACCEPTED全部在平台查到result_type=3/sub_process_flag=51；旧206 UNKNOWN中143完整查询NOT_FOUND，剩余63未核实（1笔平台拒查、62笔未查）。不能称206全未收到或247全核清。
独立只读查询首批及r2/r3三次遇平台HTTP200内TOP7，最后留存App Call Limited/accesscontrol.limited-by-api-access-count；冷却且降至4.5秒仍拒绝，已停止。正对照仍成功，精确配额周期/恢复时间须平台确认，不猜“每日额度”。
本次没有上报/重发、没有读HIS、没有业务写入或清12轮预算。看板本地仍2576确认/41受理/206未知；配额2823、断点46不变。平台已确认的41笔尚未回写；NOT_FOUND限定同企业/类型/单据号和日期覆盖，不能排除历史换单号/人工上传。
下一步：平台方查当前AppKey该查询接口调用次数额度及恢复时间；恢复后按CSV中QUERY_ERROR/NOT_CHECKED的63笔继续，不查已核清184笔；受控回写41成功，再核对143未找到与后续63笔的人工历史，不能盲重发。精确清单与平台错误证据已存，本轮无遗留活动探针。[交接](sessions/DTR-UNKNOWN-CHECK-20261026.md)。

## 最新：药品追溯界面已优化上线（2026-10-26 14:50:17）

[使用说明与证据](../operations/drug-trace-ui-clarity-20261026/README.md)：生产ui-clarity-20261026T063000Z，默认四状态卡、进度、趋势和需关注事项；分总览/上报记录/单据与追溯码/对账核查/接入与授权五入口。英文状态改中文，技术信息折叠；受理与最终成功分开，失败统计不显示虚假零，最多200笔明细边界明确提示。
前端4文件变更，后端/依赖/数据库迁移不变；构建exit0、辅助测试5通过、合成浏览器13项通过且零业务POST。生产六容器healthy、UI与资产200且SHA一致、原授权scope200、timer已恢复active/waiting；服务器验证成功，当前管理电脑TLS/正式IP SAN和生产浏览器验收仍未解决，未启用HTTP或绕过SSH转发策略。
本轮等原批准补发自然结束后才升级，未中断/重跑driver。两个窗口均OK，结束汇总以final.json为准；当前{'CONFIRMED': 2576, 'ACCEPTED': 41, 'UNKNOWN': 206}，used=2823/80000。旧41受理/206未知摘要、源断点46与升级间额度均不变；旧247仍须平台核对，未因界面上线变成已处理。
一致性备份<prod-path>/backups/20261026T064714Z/；私有维护日志/root/blyb-maintenance-backups/upgrade-ui-clarity-20261026T063000Z/。回退上一auth发行，不恢复旧业务库。访问仍https://<prod-host>/ui/#/drugtrace，admin及原独立密码不变，Ctrl+F5刷新。[交接](sessions/DTR-UI-CLARITY-20261026.md)。

## 最新：网页登录已改用户名/密码并上线（2026-10-26 12:50:36）

[实现与部署实录](../operations/username-password-login-20261026/README.md)：生产auth-20261026T043500Z，迁移013仅新增账号/会话表，六容器healthy。生产登录页改为用户名/密码；账号admin对应console-admin，实际密码仅存本机受限文件，位置见实录，不使用root/HIS密码。
密码加盐PBKDF2，8h会话/退出撤销/改密撤销全部会话/5次失败暂锁15min；原自动化Bearer不轮换。专项17通过，PG并发补验1通过，全量499通过/7跳过/0失败；本机合成浏览器登录、刷新、改密、退出通过，nginx真实限流401→429通过。
生产实际密码登录200、错误401、跨storage200、退出200后旧会话401，原令牌scope200；旧247摘要/配额/源断点在维护期间不变。服务器UI与JS/CSS均200；当前电脑443 TLS仍未验收，跳板明确禁止direct-tcpip，未绕过；生产浏览器验证受阻，本轮未启用HTTP或替换证书。
12:44在发送排空后暂停旧补发runner并留维护中断证据，完成一致性备份/升级；12:49已按原driver/相同两日窗口恢复到blyb-mapping-recovery-auth-20261026，日志/var/log/blyb/mapping-recovery-auth-20261026/。当前仍RUNNING，旧41受理/206未知未重发；旧runner的failed/SIGKILL是计划维护历史，不是新增业务发送失败。每日timer已恢复enabled/waiting；NTP仍选中<intranet-host>。
下一棒只读check.py app status或上述final.json核对补发终态，勿重跑旧launcher/另开driver。正式看板访问、旧247平台核查、outcomes筛选边界仍待办。[交接](sessions/AUTH-PASSWORD-20261026.md)。

## 最新：两药房映射与内网NTP已修复，补发运行中（2026-10-26 12:11:52）

[本轮运维实录](../operations/drug-trace-mapping-ntp-20261026/README.md)：用户确认050121、050116属于本院，新增批准ref_ent_id字典hospital-pharmacies-20261026-r1，旧字典/历史payload未改。未来日批可使用两药房映射。
<prod-host>已改为<intranet-host> UDP123，选中^*、Reach377、Leap Normal、chronyd enabled/active；DMZ仍用正常的<relay-net-host>。旧“待确认药房归属”和“<prod-host>校时未通”已由本轮证据解决。
上午567 PREPARED恢复已结束：567全部HTTP200受理，原回执因782缺映射为PARTIAL_FAILURE。现单独blyb-mapping-recovery-20261026按10-25（782张）→10-24（815张）顺序恢复，只复用DONE采集；当前22受理/16确认，仍RUNNING，不称1597张完成。旧41受理/206未知及断点46均不变，配额不重置，普通每日timer保持enabled。
观察<prod-host> /var/log/blyb/mapping-recovery-20261026/progress.json、final.json、error.json；用本轮check.py app status实时核验。窗口driver完成不等于所有平台终态确认；日报send为本次调用计数。
看板https://<prod-host>/ui/#/drugtrace使用访问令牌，无独立用户名/密码；用户所需令牌已存本机ACL受限文件，路径见运维实录。当前电脑443仍TLS握手失败、80未开，本轮未启用HTTP；HTTP可另行作为院内同源入口，但明文传输令牌，平台/DMZ HTTPS/mTLS仍保留。
本轮无应用源码/镜像/表结构更改，语法检查与API登录/字典实际查找/NTP/补发实测；未重跑全量回归。旧247平台核对、看板证书与访问、outcomes先limit后筛问题仍待办。[交接](sessions/DTR-MAPPING-NTP-20261026.md)。

## 最新：排队超时已修复部署，10-25恢复运行中（2026-10-26 09:09）

[修复与三类缺项说明](../operations/drug-trace-queue-repair-20261026/README.md)：生产queue-20261026T010000Z-r2已上线，六容器healthy；发送/查询priority50/60、每5槽保留1个后台槽，排队1800s/执行180s分开等待原job，不重投递。
driver过滤已12轮耗尽/在途查询，看板分开展示发送/查询错误和耗尽；未改证据、scope、配额或批准字典。最终局部47通过，**全量482通过/7跳过/0失败**，同源构建与离线依赖一致。
09:01:52按原unit恢复10-25窗口，复用DONE采集和567 PREPARED；09:09:14已新增71次HTTP200受理、65确认，余496PREPARED，**仍RUNNING，不能称整批完成**。全库477确认/47受理/206未知，used730；旧41+206状态版本摘要及源断点46均未变。
4h只读观察器blyb-queue-observe-20261026.service每分钟写/var/log/blyb/queue-recovery-20261026/latest.json，结束写final.json，超时写observer-timeout.json；无平台/HIS请求或业务写入。回执20261026-090152.json在活动期间0字节属预期，原061029失败回执保留。
旧815缺映射是10-24快照（050121=810/050116=5），10-25最终另782（775/7）；两药房是否共用已登记医院ref_ent_id等待用户答复，未猜值批准。旧41/206预算耗尽仍待平台核对，过滤无效查询不等于业务已核清。
新增未修明细边界：submission-outcomes先limit200再筛问题，可能漏掉较旧UNKNOWN；聚合统计为准，问题列表0不等于已清零。NTP/正式证书/TLS仍为原外部事项。
下一棒只读看恢复final/日报/终态，再按确认处理药房字典及旧单核对；不要另开driver/worker或重跑一次性部署脚本。交接：[本任务](sessions/DTR-QUEUE-REPAIR-20261026.md)。下方日批失败是本次恢复前的真实历史。

## 最新：10-26日批部分失败，只发3张；发送排队触发90秒保护（2026-10-26 08:32）

[只读实测与修复重点](../operations/drug-trace-daily-health-20261026/README.md)：06:10:29触发，处理10-25窗口；08:19采集DONE2059单（VALID1352/WAITING707），08:31:05驱动exit1/PARTIAL_FAILURE，timer日报已入库。
新增3次HTTP200/ACCEPTED，其中2CONFIRMED、1ACCEPTED；无新429/UNKNOWN。第三个send排队近164秒才结束，超过驱动90秒等待，触发SEND_WORKER_TIMEOUT停止后续新发送；不是平台拒绝。之后247次历史query因12轮耗尽返回HTTP412，约9分钟后结束。
截至08:31:37，407 PREPARED未发；另404 prepare待处理+1执行中；537次prepare FAILED（050121缺企业映射530、050116缺映射7），仍在增长。全库411CONFIRMED/42ACCEPTED/206UNKNOWN，quota659。worker继续准备不等于driver继续发送。
六容器healthy，日报GET/详情/stats/scope均200；DMZ NTP Normal，<prod-host>仍未同步。下次10-27 06:10只处理10-26，不自动补今日失败窗口。旧文“VERSION_CONFLICT=409”纠正为项目实际HTTP412。
P0：修发送排队/等待与积压公平性、过滤查询轮次耗尽及在途job、确认批准两药房企业字典；验证后幂等恢复10-25，不能重发已受理/UNKNOWN或重置配额。日报还需覆盖后台prepare和query失败。
本轮未重启、未改远程/业务代码、未补跑或新增平台调用，只读证据已存；待修复不能用六容器healthy或日报入库成功替代业务验收。交接：[本任务](sessions/DTR-DAILY-HEALTH-20261026.md)。

## 最新：看板已升级，限流已修复；<prod-host> NTP仍待UDP放通（2026-10-25）

用户授权后完成生产`ui-20261025T140000Z`升级、迁移012、宿主驱动和日报开关1，六容器healthy；timer下一次10-26 06:10。
[现场实录/网络要求/回退](../operations/drug-trace-ntp-dashboard-20261025/README.md)：上传与每次查询统一40/min，429后仅冷却后续请求、不重放。
DMZ修复后226次200转发、0次拒绝；修复前3679次429。采集DONE2414单，VALID1470；全库409 CONFIRMED/41 ACCEPTED/206 UNKNOWN、used656。
10-24窗口408确认+41受理+206未知，另815张缺050121/050116批准ref_ent_id映射；没有盲补字典、重发UNKNOWN或重置额度。
已补入10-24 **manual/PARTIAL_FAILURE**重建日报，654是实际尝试数不是成功数；原timer中断回执仍保留，不能说全天正常。
DMZ chronyd选中<relay-net-host>且Normal；<prod-host>已开机启用并64—256秒持续轮询，**UDP123仍无响应、未同步**（动态源端口与固定123均测）。试验中继无效已撤回。
服务器页面/资产和看板API均200；现有UI证书缺IP SAN，本机到443 TLS EOF，**浏览器未验收**。
最终局部38通过、传输45通过/1跳过；全量467通过/1旧断言失败/7跳过，该旧断言已在当前局部及单项复验通过，未称单次全绿。
最终22:01查询队列已排空；41 ACCEPTED和206 UNKNOWN均已用完12轮预算，须人工核对/受控复查，不称仍在自动处理。下一步：NTP ACL/回程及白名单、管理入口证书/TLS策略、2药房映射、247个非终态核对、10-26完整日批及原07:30日报SLA观察。
交接：[DTR-NTP-DASHBOARD-20261025](sessions/DTR-NTP-DASHBOARD-20261025.md)。下方为旧时点，TUI-08“未部署”及“采集仍RUNNING”等不能覆盖本轮证据。

## 最新：定时故障已修复部署，全天恢复仍在运行（2026-10-25）

用户明确要求修复。[本轮修复/回执/其他问题](../operations/drug-trace-timer-repair-20261025/README.md)：unit %%s、GET形状/失败JSON、UNKNOWN只查询、PREPARED续发、--date补跑均落实；等待3h/unit6h/节奏2.2s，原件备份在<prod-host> /root/blyb-timer-backups/。六容器未重启，r10/审批/冻结包/配额未改。
**真实worker新增1单HTTP200→平台3/51→CONFIRMED，累计3单成功。** 本轮最初临时验证误用core执行handler，因worker专属凭据不可见形成1单UNKNOWN；已改worker预检并查得NOT_FOUND，保留查询/人工复核，未重发或清额度。used=4/80000（含该失败保守计1），不能说4单成功。
10-24全天同一collect job和断点已恢复，仍RUNNING；18:29已持久化500单，不是全部发送完成。新只读观察器active，每分钟记录/var/log/blyb/repair-observe-20261025/latest.json，业务退出后写final.json，最长6h，无重试/发送。
最终局部30/0；全量450/11/7后修本机UTF8环境，对全部失败项复验11/0，未宣称单次全量全绿。其他待跟踪：<prod-host> NTP未同步、TUI-08/日报表未部署（当前日报开关0）、全天性能/最终收尾与1条UNKNOWN人工核查。交接：[本任务](sessions/DTR-TIMER-REPAIR-20261025.md)。下方首跑失败为修复前历史，现不能继续要求重复修%s，但也不能提前标全天成功。


## 最新：首个自动日批失败，尚未正常推送（2026-10-25，只读实测）

用户要求核查已开启的定时任务。[现场报告与最小修订候选](../operations/drug-trace-timer-health-20261025/README.md)：timer于10-25 06:10:29触发，但service同秒failed/exit1，回执0字节；今日新增采集/提交/尝试/job均0，仍仅此前2单CONFIRMED+1单PREPARED，used=2/80000，六容器healthy。
**根因已证实：unit的printf %s未转义为%%s，systemd有效格式变成/bin/sh，容器stdin收到错误令牌；驱动又未检查GET错误响应类型，在第65行str.get异常，未落有效JSON。** 正确令牌的两个只读GET均200/list，非HIS或阿里平台网络超时证据。
已准备仅修转义的service候选，源文件SHA与现场unit一致；尚未部署/补跑。下一棒优先修unit及驱动错误回执，再监督恢复10-24日批，不能只等10-26自动处理10-25而漏过失败日。现timer仍enabled/waiting，原故障未修。
生产仍src-20261022T103000Z/v1驱动，日报表不存在，TUI-08仍未部署；本地界面测试不等于生产看板可用。整单CONFIRMED下5个明细PENDING仍为既有A05边界，不冒充码级确认。
本次HIS/平台业务调用0、手动发送0、远程修改0。交接：[DTR-TIMER-HEALTH-20261025](sessions/DTR-TIMER-HEALTH-20261025.md)。下列“已上线”仅说明定时器安装，不能覆盖本次首跑失败实测。


## 最新：TUI 复核修复完成，下一棒=TUI-08 生产部署（2026-10-24，DTR-TIMED-UI-FIX-20261024，ZCode/GLM-5.3）

按 FIX_TASK.md 一次完成 FIX-00—09（[RESULT 复核修复节](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)）：
G1 upload-stats IN 规模缺陷改两条分组 SQL+窗口函数（无 .in_()，35k 回归锁）、G8 outcomes 批量化、
G7 窗口序 422、G3/G4/G5/G6/G9 前端六项、G2 四类实施文档、G10 测试（新增 4：35k/clamp/窗口序/PG 端到端）。
全量 **448/7/0**（XML var/tui-fix-regression.xml）；npm build ✓；浏览器四项复验+截图；
F-A07 禁改 30 文件哈希零改动。改动仅 routes.py 三处、DrugTrace.vue、timed_ui 测试、
serve 种子与文档/状态文件。**下一棒：TUI-08 生产部署**（须用户在场确认窗口且避开每日 06:00–08:30；
就绪清单 RESULT §五；完成后观察首个 v2 日批回执含 report_push 且界面可见）。
既有观察项不变：10-25 06:10 首个全量日批（仍 v1 驱动）、E05'—E08'、<prod-host> NTP。
交接：[session](sessions/DTR-TIMED-UI-FIX-20261024.md)。

## 最新：TUI 复核完毕，修复执行书已交（2026-10-24，DTR-TIMED-UI-FIXPLAN-20261024，ZCode/GLM-5.3）

对 DTR-TIMED-UI 交付逐项复核（实验证实）：功能/契约/门禁零改动均成立，但发现 G1—G10——最重两项为
**upload-stats IN 参数规模缺陷**（SQLite 32766/PG 65535 上限实测，生产 ~2000 单/日 days=90 约33天后必 500）
与**四类实施文档欠账**；另有每日实际列口径/streak 断档/吞错/死代码等展示问题。
已编制[修复执行书 FIX_TASK.md](../development/drug-trace-timed-ui-plan-20261024/FIX_TASK.md)
（FIX-00—09、验收 F-A01—08、禁改清单、35k 规模与 PG 端到端测试规格），**下一棒先执行
DTR-TIMED-UI-FIX-20261024 一次性修复，再进入 TUI-08 生产部署**（后者仍须用户在场且避开 06:00–08:30）。
本轮纯复核+执行书，零代码改动。交接：[session](sessions/DTR-TIMED-UI-FIXPLAN-20261024.md)。

## 最新：定时上报可视化本地交付完毕，待用户窗口部署（2026-10-24，DTR-TIMED-UI-20261024，ZCode/GLM-5.3）

按执行书完成 TUI-00—07+09（[RESULT](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)）：
迁移012日报表+daily-reports/upload-stats/send-scope/submission-outcomes 六端点+驱动v2（POST日报，失败不改退出码/回执）+
前端「定时上报」面板（授权/健康/日报下钻/平台终态/异常提交）+横幅动态化。全量回归 **444/7/0**；
A07 门禁文件零改动（mtime 证据）；A01—A04/A06/A08 本地验收过，浏览器截图3张。
**TUI-08 BLOCKED**：生产部署须用户在场且避开每日 06:00–08:30（timer 06:10 真实发送）；就绪清单
RESULT §五（build_release→upgrade.sh→替换 /opt/blyb/tools/blyb-daily-upload.py→只读验证→回滚）。
下一棒：用户确认窗口后执行 TUI-08 并观察首个用 v2 驱动的日批（回执应含 report_push 且界面可见）。
既有观察项不变：10-25 06:10 首个全量日批、E05'—E08'、<prod-host> NTP。交接：[session](sessions/DTR-TIMED-UI-20261024.md)。

## 最新：定时上报可视化计划已交付，待执行AI实施（2026-10-24，DTR-TIMED-UI-PLAN-20261024）

用户要求做前端可视化并交其他AI执行长任务。已交付[执行书](../development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md)（TUI-00—09含API契约/健康规则/验收A01—A08/边界）与[提示词](../development/drug-trace-timed-ui-plan-20261024/NEXT_AI_PROMPT.md)。核心：新表012日报入库+daily-reports/upload-stats/send-scope/submission-outcomes四API+驱动v2上报+UI"定时上报"面板+横幅动态化；**零门禁改动**；生产部署避开06:00–08:30且须用户在场。本轮纯计划零代码。执行者从NEXT_AI_PROMPT进入；部署前须用户确认窗口。交接：[session](sessions/DTR-TIMED-UI-PLAN-20261024.md)。

## 最新：定时上报已上线（2026-10-24，DTR-TIMED-REPORT-20261024，ZCode/GLM-5.3）

用户授权"核查剩余待办+定时上报"后：E06'/E07'零迁移复核、范围测算（日均~1,700-2,400 VALID单）；
**standing send scope 登记**（10-24→11-23、5药房、80000、可revoke）；监督采集51单幂等验证+冒烟发送**CONFIRMED**（真实成功2单）；
**blyb-daily-upload.timer active于<prod-host>**（每日06:10 CST，首跑10-25处理10-24全天，串行≤40/min）。
缺口10-12..23未发送（E08'确认后扩窗口+backfill分区即可）；拆零流5077待院方；NTP/全量日批为已知风险（显性失败可查）。
运维/暂停/回滚命令见[任务README](../development/drug-trace-timed-report-20261024/README.md)。交接：[session](sessions/DTR-TIMED-REPORT-20261024.md)。

## 最新：源表核查执行完毕，5077条根因闭环（2026-10-23晚，DTR-HISTORY-SOURCE-AUDIT-20261023，ZCode/GLM-5.3）

用户授权"授权你执行"后，经<relay-jump>受控连接器以平台只读账号READY_HIS执行SQL01/02/04/06+逐日覆盖+阶段E判别（约181条SELECT/23秒，源写0上传0生产改动0）。
**结论**：5077条未匹配全部U02＝药房050121拆零流（SPLIT_FLAG=1，20位单号不在主单PRESC_NO空间，主单仅1—5位；050121为最大药房；S01-S03不带药房查主单0行）→r10不改、不并入、不自动补报，是否覆盖待院方确认另立revision；31组重复＝<1小时真实双扫（SPLIT_FLAG=0），11张整单保持INVALID；1396退款拦截不变；81天主单覆盖与r1视图交叉一致（484567≈484539行，视图未丢已JOIN记录）；住院主单零OUT码。
剩余外部E05'—E08'：拆零流业务确认、T侧全量计数（无索引）、双扫处理规则、退款口径、平台终态。产品完善A02/A03/A05另立任务。1单4码成功不撤销不重发。交接：[session](sessions/DTR-HISTORY-SOURCE-AUDIT-20261023.md)、[RESULT源表核查节](../development/drug-trace-history-rootcause-r2/RESULT.md)。

## 最新：rootcause-r2 历史核查已执行（2026-10-23，DTR-HISTORY-ROOTCAUSE-R2，ZCode/GLM-5.3）

Q00—Q06 一次完成，[RESULT](../development/drug-trace-history-rootcause-r2/RESULT.md)、
[候选修订 C01—C07](../development/drug-trace-history-rootcause-r2/CANDIDATE_CHANGES.md)、
[外部缺项 E01—E07](../development/drug-trace-history-rootcause-r2/EXTERNAL_INPUTS.md)。
37 条 SELECT/0.876 秒仅两视图（身份授权再证不变、视图定义=r10=r1）；源表 0、写 0、上传 0。
5077 条两时点零变化、全部 U05 待 DBA 源端小样（S01—S03 绑定包+531 码清单已入受限目录）；
1396 三态零迁移拦截保留；**31 组重复=11 张主单全部 INVALID/QD-DUP-CODE，整单双扫模式成因待源表**；
反向同窗口审计未完成（SQL05/06 备好未执行）；A01—A05 检查：A03 未实现/A02 恒空/A05 部分，自动推送不启用。
既有 1 单 4 码不撤销不重发。下一步=DBA 回传 E01—E03 后 U 分类归位、E04 定反向方案；
A02/A03/A05 产品完善另立任务。交接：[session](sessions/DTR-HISTORY-ROOTCAUSE-R2.md)。

## 最新接力任务：详细核查执行包已交付（2026-10-23）

按用户要求已编写[完整说明与Q00—Q06执行任务书](../development/drug-trace-history-live-compare-20261023/EXECUTION_TASK.md)和[可复制给下一AI的提示词](../development/drug-trace-history-live-compare-20261023/NEXT_AI_PROMPT.md)，细化5077条查源/主单JOIN、1396退款状态、31组整单、同CREATE_TIME反向核对、候选修订及A01—A05检查，含CHK-01—10验收与受限交付格式。
现有SQL01/02精确旧键查不到时，新增SQL03按原码查源上下文；三条SQL均未执行，需已批准原表审计身份或DBA。原reader仍只两视图，不扩大权限；旧r1证据/工作簿不覆盖，下一AI应使用rootcause-r2新任务目录。
本次只是任务书与静态校验，HIS/平台访问0、生产改动0、新上传0；不改变下列972条SELECT实测结论，不宣布根因已全部闭环。交接：[本任务记录](sessions/DTR-HISTORY-CHECK-TASK-20261023.md)。


## 最新：历史清单已与HIS两视图实际核查（2026-10-23）

用户已明确批准当前白天只读核查；本次授权解决此前待窗口问题。全部489613行对应473614个码、489582个旧明细号＋码组合已查询，实际972条受控SELECT，无触顶/查询错误。
[实测报告](../development/drug-trace-history-live-compare-20261023/README.md)：484536行同键且次数一致；5077行另一格式历史单号未匹配，不能直接判漏报。1396条当前UNKNOWN已查到全退/退费原始状态；31组同单同码为真实多源记录，整单样本触发QD-DUP-CODE。
**正常门诊r10及退费拦截保留；先由DBA小样核查5077条的原记录和三键主单JOIN，再按证据决定是否新增业务来源revision。** 两条限量SELECT已写，未执行，无需重建当前用户。完整差异Excel在仓库外受限目录，6535行标识回读/原25文件摘要/ACL验证通过。
[下一步L01—L06](../development/drug-trace-history-live-compare-20261023/MODIFICATION_PLAN.md)：查未匹配来源、确认业务覆盖、退款/实物退药分离、重复码原因、CREATE_TIME同口径审计/齐套晚到、历史防重。DBA源表权限及真实业务解释尚待落实；现reader仅两视图，不绕过权限。
HIS写入0、生产改动0、新上传0。尚无同CREATE_TIME区间全视图反向核查或每码平台成功终态。既有1单4码成功保留不重发。
本次交接：[DTR-HISTORY-LIVE-COMPARE-20261023](sessions/DTR-HISTORY-LIVE-COMPARE-20261023.md)。下列旧窗口/离线/待时段记录仅是历史，不能覆盖本次实测。

## 用户纠正：任务误发窗口，已移交其他窗口（2026-10-23）

用户明确说明“历史上报与视图比对”误发到qywx窗口，改由另一个窗口核查。**本窗口已停止此任务，从qywx后续工作范围排除；此前异步白天查询问题不再由本窗口等待或执行，也不构成任何实时查询批准。**
已做内容仅为25份Excel离线统计、仓库外受限核对表和SQL计划。原文件未改，HIS连接0、平台访问0、上传0、生产/产品代码修改0；尚无真实视图一致性结论。已生成资料保留供接手，不删除原始文件或撤销其他人的改动。
接手入口：[离线结果与范围](../development/drug-trace-history-compare-20261023/README.md)、[原任务记录](sessions/DTR-HISTORY-COMPARE-20261023.md)。下方“待白天窗口”是原分析阶段记录；后续执行和授权由用户指定的另一窗口处理。

## 历史上报与两视图比对（2026-10-23，实时核查待窗口）

[历史清单离线检查与比对计划](../development/drug-trace-history-compare-20261023/README.md)：25份Excel已全读，489613行、205334个历史单号、473614个不同码，覆盖7月23日—10月11日；追溯码均为完整20位文本。完全重复行0；同单同码再次出现31次（62行），14个单号有不同时间，3562个码跨历史单号，均待结合业务核对，不先判错报。
已输出仓库外受限Excel，按原文件/行号定位。原历史SQL单号/时间与现主单/发药时间口径不同，明细号探针SQL已静态PASS，尚未执行。
**HIS实时视图访问0、真实平台访问0、上传0、生产改动0；当前不能判定两边一致。** 仅白天live窗口待用户确认；目标与比对请求已有授权，不再索要已登记凭据。后续先3键小样确认映射，再限量分批，保留次数及未覆盖范围。

## 各上报业务目录导航与强制维护约定（2026-10-23）

[数据上报总索引](../../数据上报/README.md)已建立；码上放心目录README串联原始文档、官方快照、代码/两视图、部署/真实回执/复核/NTP及受限凭据位置。医保材料、既有病理插件和历史资料均补就近README；未将材料存在当成业务实现或真实验收。
根AGENTS、AI_START、项目README、START_HERE和统一启动文档已约定：**每个上报项目在数据上报/<编号、业务名>/README.md维护项目目录说明，接口/代码/SQL/部署/证据路径变化同步更新**，并维护总索引；状态仍统一共享记录。
[本轮维护与验证](../maintenance/reporting-directory-guides-20261023/README.md)。本轮纯文档，无远程连接/数据库访问/上传；既有NTP与单笔试报事实不变。

## 最新：院内NTP配置已执行（2026-10-23）

用户明确授权修改同步时间，[两机配置/备份/实测](../deployment/drug-trace-ntp-20261023/README.md)。
两台/etc/chrony.conf均已持久使用`server <relay-net-host> iburst`，原公网pool注释保留，各只重启一次chronyd、active/enabled。
**DMZ <relay-net-host>已同步：^*该源/Leap Normal/stratum2，约两分钟复查仍正常、Reach77，直接偏差约0.01毫秒。**
**<prod-host>已配置但仍未同步：^?/Reach0/Not synchronised，实际UDP123继续超时。** 余项为<prod-host>→<relay-net-host> UDP123及会话回包/服务端ACL，网络恢复后复测；不能笼统宣称双机NTP全通过。
DMZ转发PID5174/Java14438未变，<prod-host>六容器healthy；业务服务重启0、HIS访问0、上传0。
原配置分别保存在两机/root/blyb-ntp-backups/20261023-…/chrony.conf，精确路径见本轮报告。上轮“仅探测/未配置”的描述为历史状态。

## 院内NTP候选源实测（2026-10-22 23:08）

用户纠正时钟服务器为**<relay-net-host>**，未测试误填的<relay-net-host>。
[双机实际UDP123探测](../deployment/drug-trace-ntp-20261022/README.md)：DMZ <relay-net-host>三次有效NTP v4/stratum1响应，较该源慢约2.144秒；<prod-host>三次均超时。
两台已有chronyd但当前均未同步；本轮仅探测，无时间/配置修改或服务重启、上传0。NTP源地址已提供，剩余是<prod-host>跨区UDP123/回包或服务ACL核通，以及后续持久配置及持续同步验收；不得再把“待提供NTP IP”列为缺项。

## 最新：官方指南与HIS视图只读复核（2026-10-22）

[核查报告与A01—A05完善任务](../development/drug-trace-official-view-audit-20261022/README.md)：重新获取语雀正文HTTP200，规范标题后与旧快照一致；正常门诊零售五必填/method/321/3+51匹配。
HIS专用reader27条SELECT成功，视图VALID/17+18列、定义与r10一致；30头/100码样本、8张整单11条有效码未见主从版本/时间/重复异常。
**现存需完善：100条明细修改晚于发药20～251秒；主单updated_at=发药时间、expected_code_count恒空，连续自动齐套/变更尚未认证。**
现表单未传ignore_part_success_flag（缺省允许部分解析）；新revision完善整单解析策略及失败码流程。包装父子/人工历史完整性与4条明细PENDING显示仍待解决。
正常完成门诊batch可用，不能扩为全历史/退药/住院/拆零全面验收。本轮HIS写0、线上改0、上传0；既有一笔真实3/51成功不变、不撤销不重发。

## 追溯码链路及人工记录核查资料（2026-10-22）

[清晰流程说明](../operations/drug-trace-flow-20261022/README.md)已整理：视图/取数服务器/前置机/官方接口及人工记录比对方法；附空白Excel文本模板。
完整密码与AppSecret仅在本机受限`.ai/private/drug-trace-flow-20261022/推送流程及账号密码_仅本机.md`，只允许当前用户和SYSTEM，Git忽略；未输出到共享文档或聊天。
此资料整理无远程连接/业务变更/上传；既有真实试报1单4码CONFIRMED不变。后续人工文件只读核查，不自动补报或重发。

## 最新：首张真实上传已成功（2026-10-22）

**真实上传1张/4码，平台result_type=3、sub_process_flag=51（处理成功/全部成功），本地CONFIRMED。**
发送attempt仅1次/HTTP200/ACCEPTED，两次查询由处理中推进终态；quota used=1/max_bills=1。
用户已明确延期前述生产要求，并要求不用撤销。本轮通过原API登记USER_SINGLE_BILL_EXCEPTION，
保留延期事实和UNKNOWN，不伪称正式CA/NTP/旧厂商未报已验证；固定单据窗口/药房/配置/额度1。
**目的地ENABLED，DMZ允许query+uploadretail、库存关闭；配置和审批未主动撤销，未开自动批量**。
审批默认有效至北京时间2026-10-22 23:18:32，已有到期机制保留；后续需新范围/额度时由用户另行安排，不复位已用计数。
原“上传0/send MISSING/DMZ仅query”均为前轮历史事实；该AppKey/医院的uploadretail和321本笔已实测有效。
HIS写0、业务源码无改动。正式链/持续NTP/历史去重/扩展业务留为后续完善，不能阻止承认本次实测成功。
[结果及完整证据](../development/drug-trace-one-bill-trial-20261022/README.md)。

## 单笔测试条件修订（2026-10-22，用户要求非必需先忽略）

正式CA、持续NTP验收和平台书面回执后移，不再把这些材料一律当成单笔测试前提。
现有mTLS严格校验和发前准确时钟保留；uploadretail实际权限必需，可由受控调用响应验证，321沿用已有批准映射。
历史全量对账延期，测试单本身优先作范围内重复核对；未核实仍UNKNOWN，不能伪称已未报。
[分类及下一步边界](../development/drug-trace-trial-prerequisites-20261022/README.md)。**本轮仅改文档，未改线上门禁或发送；线上仍send MISSING/目的地DRAFT/DMZ仅query。**
下一AI如继续单笔试报，需实现/使用明确trial路径记录延期，保留一单额度/固定配置/时钟与TLS实测/幂等，不能伪填生产证据。

最后任务：**DTR-SOURCE-DELIVERY-20261022**（Codex/unknown），并顺序完成DTR-RESTORE-PATH-GUARD-20261022。
用户授权继续开发；**本轮已部署、接入并真实采集冻结**，不要再停留在计划或重复HIS建库。

- <prod-host>当前`src-20261022T103000Z`，六容器healthy，Oracle Thick；两类首屏空游标修复已部署。
- 专用BLYB_DRUGTRACE的DSN仅worker持有；实际HIS服务his，CREATE SESSION+两视图SELECT、无角色。
  新来源`blyb_drugtrace_views@20261022-r10`固定对象/reader/batch，原sjzc广源medical_dict_push不改。
- 经真实API发布来源PUBLISHED/ENABLED及三类字典`r10-normal-20261022-r1`，旧批准项保留；
  collect EXT-01/02根据用户已执行r10和专用reader实测登记1天，未伪造send证据。
- **1张4码真实采集DONE/VALID/0冲突，冻结PREPARED；实际五必填DRY_RUN通过**。
  submission `7ed95aaa-ee66-4510-b80d-630f6c9dc381`，package `93fc0afb-b068-471a-bd3a-4b03f72799a1`。
  source config `10bd581d-5a28-49c9-ab14-668aa9dfc4d6`；destination配置仍原`2e138e50-2be7-4574-8e48-61264be8946d`。
- **真实上传0**；attempt/quota/send_job均0。真实目的地对象仍DRAFT、send MISSING、DMZ仅query。
  本系统新稳定平台单号查得0条不等于旧厂商没报同一码，候选未签认为测试首单。
- 132相关测试通过；全量首轮424/8/7失败记录保留，进程UTF8后受影响两文件25/0，含新路径反例。
  不能称另跑过单轮全量绿灯。恢复脚本case修复已部署；本轮备份`20261022T095541Z`，
  独立data-root现场validate-only通过，未真实恢复生产库；默认演练目录在生产根下须改用显式独立根。

[本轮实施、证据、回退、接口及后续执行顺序](../development/drug-trace-source-deploy-20261022/README.md)。
下一AI只处理实际剩余：正式CA/SAN链、持续NTP、uploadretail授权与321/323口径、药学/原厂商确认未报首单。
已有AppKey/Secret/ref_ent_id、服务期、HIS账号/视图、SSH凭据不重索。
具备事实后正常登记绑定准确配置的send证据和max_bills=1 scope，再启用目的地并只加DMZ uploadretail方法，
确认该单未报后发送、查询终态和逐码核验。未知结果先查，不重编单号、不重置额度重试。
本轮没有开启连续采集或自动发送；未知住院/退药/拆零/晚到变更仍单独待验。

## 前一轮交接（已由本轮实测继续推进）

最后任务：**DTR-R10-UPLOAD-READINESS-20261022**，Codex / unknown。
用户报告已执行r10，**已实际登录HIS确认两视图生效，定义与草案token一致**。
正常门诊分支已是PATIENT_DISPENSE/finalized=1/is_void=0；20头中15正常、5住院仍UNKNOWN。
一张真实整单1头4码，无重复/revision错配，远端运行当前实际质量纯函数结果**VALID/0阻断**，
仅QD-LEVEL-UNCONFIRMED警告；未导出原始码/单据键/患者字段，未生产采集或建包。
**不要再让用户重建账号、重复修改已执行视图，或声称门诊完成字段仍全部为空。**
见[实际核查与接续](../development/drug-trace-r10-readiness-20261022/README.md)、
[视图当前状态](../development/drug-trace-r10-readiness-20261022/verified-view-state.json)、
[session](sessions/DTR-R10-UPLOAD-READINESS-20261022.md)。

仍不能上传的实际位置：<prod-host>仍fb-20261021T164800Z/6healthy，首屏修复未部署、Oracle DSN空、
来源DRAFT无active config、药房→医院映射空、原始状态字典需补、目的地对象DRAFT；DMZ只允许query。
collect/send证据MISSING，采集/业务单据/submission/配额0；真实上传0。
正式worker→mTLS→DMZ→平台状态查询成功，DMZ官方HEAD200/时钟接近官方；
首轮<jump-host>→<prod-host>:22 Connection refused，r2已SSH0/worker0成功，不据此说持续断网。

下一项是**服务部署和接入配置**：构建部署前轮已测首屏修复、安全绑定已存专用只读账号、
正常发布所需三类映射/来源及启用真实目的地，依据已有实际完成材料登记来源证据；
再按真实发送条件/未报首张/DMZ必要方法/max_bills=1采集冻结发送并查终态。
不重复索取已存账号/平台凭证；不把随机4码候选当确认未报或已验证全单未拆零。
用户r10执行和本轮质量实测解决视图层问题，不自动补齐应用证据/额度/正式链及长期时钟控制。
本轮HIS写0、远端配置改0、上传0，未构建/部署新发行。

## 前一阶段：上传内容契约与r10草案（现已由用户执行并实测）

最后任务：**DTR-UPLOAD-CONTENT-CONTRACT-20261022**，Codex / unknown。
用户再次给原SQL与数据资产目录，要求尽量确定上传内容；本轮已交
[上传内容确定单/候选视图SQL](../development/drug-trace-content-contract-20261022/README.md)、
[机器契约r10](../development/drug-trace-content-contract-20261022/upload-content-contract-r10.json)。
**五必填来源已齐**：稳定单号、实际发药时间、当前拟321、已存医院ref_ent_id、T_TRACE_CODE整单列表。
数量/单位/药名/患者证件/供应商不是该零售接口必填，不再据此泛化阻断。
原SQL按主键分组不必然MAX丢码，但PRESC_DETAILID不是主单号、CREATE_TIME不是发药时间。

已通过受控sjzc对现库注释DUMP/GBK解码：SOURCE0门诊1住院；FLAG1正常2全退3部分退；
REFUND_FLAG1已退费、0/null未退费。候选首批门诊规则已明确为SOURCE0/FLAG1/发药时间非空/未退费，
r10只给该分支正常+完成，其余UNKNOWN；**未人工批准、未改HIS视图、未写APPROVED_STATUS_RULES**。
5条HIS SELECT全部成功（含r10两个SELECT零行解析），12候选分支反例校验过；不是业务正式批准。

**新P0配置缺口实际查明**：现payload优先拿原始source_bill_kind_raw查字典，已批准字典源code却只有
PATIENT_DISPENSE。下一AI须新revision保留原项并补`PRESC_SOURCE=0;FLAG=1 -> 321/A`，
以及实际药房→已登记医院ref_ent_id；不能为迁就字典覆盖来源原值或伪造approved_by。
平台当前目标查询/正式关系/配方/值域未命中；本轮不做平台治理写入，不伪造run refs。

下一步：院方确认正常门诊完成/扫码齐套及一张未报单→DBA执行r10并核验→
正常发布来源/字典/连接启用→构建部署前轮已测Oracle首屏修复→单张整单质量VALID/冻结预览→
满足实际发送证据/方法白名单/范围max_bills=1后发送与终态逐码核验。
住院FLAG0、退药/拆零流程和旧厂商已报清单不能从文档直接替院方签认。
新账号BLYB_DRUGTRACE和PHARMACY两视图已实测，密码DPAPI已保存，无需重建或重索。
本轮源DDL/DML0、平台写0、现场配置改0、真实上传0；源码/现网没有新修改。
详见[本轮session](sessions/DTR-UPLOAD-CONTENT-CONTRACT-20261022.md)。

## 前一阶段：Oracle首屏修复（本地已测、现场未升级）

最后任务：**DTR-ORACLE-FIRST-PAGE-20261022**，Codex / unknown，承接用户“继续”。
**Oracle码明细首屏漏读已在本地修复**：NULL或空串首次游标可取数，后续严格大于原始字符串ID；
复合键、排序、行上限及业务/完成状态质量门禁不变。
新增12项回归在旧源码10失败/2通过；相关6文件最终**110通过/0失败/0跳过**。
真实Oracle受控SELECT使用修复本地模板加同日守卫，一张4码按2/2/0分页完整读出，NULL/空串首屏一致。
见[修复报告/精确增量补丁](../development/drug-trace-oracle-first-page-20261022/README.md)、
[session](sessions/DTR-ORACLE-FIRST-PAGE-20261022.md)。

**尚未构建/部署新发行，<prod-host>现场仍是旧模板**，不要把本地绿灯写成升级或真实采集/发送成功。
新账号及PHARMACY两个视图已实测通过，DPAPI本机凭据见下方前轮记录；无需再建用户或向用户重索密码。
业务kind UNKNOWN/finalized空尚待确认，来源DRAFT/DSN空、药房医院映射空、目的地DRAFT，DMZ仅query。
用户已授权符合条件后测1张；具体正常完成/未撤销口径和一张未由其他系统上报的单据已异步询问，尚无答复。
下一步落定口径与首张范围，按现有不可变发行/备份/升级流程部署修复，并正常绑定发布来源、映射/启用目的地，
整单质量VALID后再按证据/配额/方法白名单max_bills=1发送与终态核对。真实上传0、HIS写0、远端配置修改0。

## 前一阶段：用户建视图后的只读核验

最后任务：**DTR-VIEW-VERIFY-FIRSTBILL-20261022**，Codex / unknown。
用户已执行简化SQL；**本轮实际用新账号登录HIS核验成功**，不是仅用户口述。
实际连接：<his-host>:1521/his；账号BLYB_DRUGTRACE；两视图在**PHARMACY**下，
私有同义词位于BLYB_DRUGTRACE。只读权限/VALID/17+18列均通过；不要再按旧r8的BLYB_RO owner重建。
凭据已本机DPAPI和ACL保护，详见[核验说明](../development/drug-trace-view-verify-20261022/README.md)。

当天20张头/100码限量核查；一张1头4码无重复、revision一致，原始码/单号未带出远端。
**真实Oracle首屏缺陷已由实际码表复现：旧谓词0条，NULL安全谓词4条**，部署worker也确认仍未修。
主单kind=UNKNOWN/finalized=NULL导致质量等待；我此前提供的待确认常量视图并非可直接上报版，
不得将此归咎用户执行错误。数量/层级空值不一概判为平台必填。

<prod-host>仍fb-20261021T164800Z/6healthy，来源DRAFT/DSN空、药房医院映射空、目的地DRAFT；
query/reconcile批准，collect/send缺；worker正式链路只读查询成功1次。DMZ仅query，
时钟已接近官方Date，长期NTP和正式链仍未完成。**真实上传0、HIS写0、远端配置改0**。
已异步询问正常完成/未撤销状态口径与未报测试单，不重复索取现有平台/数据库凭据。

下一AI：先修空游标并测试/构建/授权窗口升级，再据院方确认的新revision对齐业务口径、
绑定新只读源/药房映射、启用来源目的地/整单质量VALID；满足真实证据及未报单据范围后，
仅开必要uploadretail方法并max_bills=1发送/查询终态。用户测试意图明确，无须再泛问是否允许测试。
完整证据和接续见[本轮session](sessions/DTR-VIEW-VERIFY-FIRSTBILL-20261022.md)。

## 上一阶段HIS执行包与DMZ校时（历史，视图已由用户后续创建）

最后任务：**DTR-HIS-DDL-DMZ-CLOCK-20261022**，Codex / unknown。
用户要求的[HIS视图/用户执行包](../deployment/his-ddl-clock-20261022/README.md)已交付；
**DMZ已实际一次性校时**，向前1946.3秒，校后与严格TLS HTTPS Date差0.57秒，14分钟复查仍<1秒。
转发service/Java8001进程保持，mTLS verify退出0；NTP长期自动同步仍未恢复，未改chrony/forwarder配置。

本次用户在登记差异说明后明确追加“登录数据库帮我看看”，已按即时限量只读范围经sjzc实际登录HIS：
his服务、5条字典成功；两目标视图当前账号未见，ALL_USERS无BLYB_RO/BLYB_DRUGTRACE。
修订r8两视图SELECT在活库零行解析通过；**用户/视图/授权DDL由用户执行，尚未落库**，
原HIS策略medical_dict_push不改，不作为生产受限只读认证。

新包入口sql/run_install.sql（DBA执行，隐藏输入新口令）与sql/50_verify_reader.sql（新账号单独验证）；
锁定owner+只读reader/角色/私有别名、直接基表WITH GRANT OPTION仅给owner，r7原件未改。
业务kind=UNKNOWN、finalized/数量未批准保持等待，不承诺执行完即可发送。

**新P0取数缺陷**：现模板码明细首次after_record_id=''，Oracle按NULL处理导致首屏恒0行。
已在DUAL证实，源码未改/未部署；精确修复见[NEXT_AI_SOURCE_FIX](../deployment/his-ddl-clock-20261022/NEXT_AI_SOURCE_FIX.md)。
下一AI先登记代码范围修复/回归并新发行，再结合用户执行回执核验新只读源、业务规则与实际整单；
来源/药房/目的地启用和真实发送证据仍保持门禁，上传0。详见[session](sessions/DTR-HIS-DDL-DMZ-CLOCK-20261022.md)。

## 上一阶段资料核对与只读平台实测

最后任务：**DTR-INPUT-RECHECK-20261022**，Codex / unknown；已完成资料重核与具备条件的真实只读测试。
见[核对说明/现场证据](../development/drug-trace-input-recheck-20261022/README.md)、
[session](sessions/DTR-INPUT-RECHECK-20261022.md)。**worker正式链路查询成功；医院药行业基础版服务期截至2028-12-31**，不用再索要服务期或既有账号/Secret。

当前：fb-20261021T164800Z、6healthy、签名True、is_mock=False/配置PUBLISHED；目的地对象仍DRAFT、
来源DRAFT/无active config、DSN空、医院药房映射空；单据/采集/submission/配额0，query/reconcile
APPROVED、collect/send MISSING。DMZ TEST-ONLY、NTP未同步慢32分、仅query。上传0、现场配置零改动。
HIS登记最新仍medical_dict_push，仅平台元数据查询，无活库查询；元数据无视图不等于活库未建。
已异步询问视图schema/执行回执和首张药房/日期/既往未报情况，回复前不假填。

更正留痕：本轮r2探针人为给query加配置版本约束而MISMATCH_CONFIG，不是部署故障；核对
perform_query实际授权语义后r3按现行规则成功，未改生产证据绕过。有效期首轮本地PS5安全模块
加载失败（平台HTTP200），公开样例定位后改pwsh并用新r2回执成功；旧失败均保留。

下一步：落实视图/受限只读登记/业务及药房→按生命周期启用来源和目的地（plugin需ENABLED）→
整单采集/冻结预览；配合正式CA/校时/uploadretail权限和321/323/未报scope后才max_bills=1。
不把旧“系统侧无再开发项”扩成所有配置/业务步骤已验收，不再重复要求已接口查明的服务期。

## 上一阶段FB开发与部署

最后任务：**DTR-FIRSTBILL-EXEC-20261021**，ZCode / GLM-5.3（2026-10-21/22）。
**结论：FB-00—05/07 全部实测完成；FB-06 首张上传 BLOCKED（实际 0 张，外部输入未齐）。**
完整交付见[任务目录](../development/drug-trace-firstbill-exec-20261021/)：
[EXECUTION-REPORT](../development/drug-trace-firstbill-exec-20261021/EXECUTION-REPORT.md)、
[ACCEPTANCE](../development/drug-trace-firstbill-exec-20261021/ACCEPTANCE.md)（FB-A01—29：
PASS 17/BLOCKED 8/NOT_RUN 4/FAIL 0）、
[EXTERNAL-RECEIPT](../development/drug-trace-firstbill-exec-20261021/EXTERNAL-RECEIPT.md)、
[FIRST-BILL-RESULT](../development/drug-trace-firstbill-exec-20261021/FIRST-BILL-RESULT.md)、
[OPERATIONS](../development/drug-trace-firstbill-exec-20261021/OPERATIONS.md)、
[SIGNATURE-QUALIFICATION](../development/drug-trace-firstbill-exec-20261021/SIGNATURE-QUALIFICATION.md)、
[DBA-PACKAGE](../development/drug-trace-firstbill-exec-20261021/DBA-PACKAGE.md)；
[session](sessions/DTR-FIRSTBILL-EXEC-20261021.md)。

本轮实际完成（全部现场实测）：
- 新发行 **fb-20261021T164800Z**（动态就绪统一 CLI/API/UI、目的地配置发布
  API+CLI、SIGNATURE_VERIFIED=True 批准修订、官方枚举 0/4 落码、空结果解析修复）；
  全量回归 **405/7/0**。
- 真实目的地配置 **PUBLISHED rev1**（is_mock=False）+官方字典（321/A）+
  EXT-09 query/reconcile 证据 APPROVED（经 API，审计留痕）。
- **正式链路只读查询 SUCCESS**：真实 worker→mTLS→DMZ→平台 query.billstatus
  （found=true/total_num=0）。过程中实证医院跨区 NAT（DMZ 见 <relay-net-host>），
  allow_ips 已加白（mTLS+方法白名单不变，可回退）。
- 签名 EXT-07 三源闭合：官方C#向量+官方PyPI SDK移植对照+**平台受理本实现签名**。
- 时钟：<prod-host> 与平台权威时间差≤10s（实测）；DMZ 慢32.4分；两机 NTP 无源。

**下一棒（全部为外部输入驱动，系统侧无再开发项）**：
1. DBA：[DBA-PACKAGE](../development/drug-trace-firstbill-exec-20261021/DBA-PACKAGE.md)
   审阅执行 r7 视图+只读账号 → 30-verify 限量核验 → SourceProfile r3 → DSN 注入
   → collect 证据（EXT-01/02）→ 小窗口采集 1 张。
2. 院方：正式证书链轮换+院内 NTP（OPERATIONS §四/§五）。
3. 平台：uploadretail 权限/服务期书面确认+321/323 消歧。
4. 药学部/原厂商：既往上报核对+首张未报 scope（max_bills=1 签认）。
5. 齐备后：send 证据登记 → DMZ 白名单加 uploadretail → 首张发送→终态→对账。
停发/回退命令见 OPERATIONS.md；send 现为 fail-closed（无证据/无配额/DMZ仅query）。

## 上一阶段（保留）

- **DTR-FIRSTBILL-LONGTASK-20261021**（Codex/unknown）：编制任务书（已由本轮执行）。
- **DTR-PUSH-REVIEW-20261021**（Codex/unknown）：23:29 现场复核（结论已被本轮
  实测推进：配置已发布、签名已闭合、链路查询已通；其"确认存在的推送阻断"1/2/3/5
  已解决，4 的 query 部分已解决）。
- **DTR-DEPLOY-20261021**（ZCode/GLM-5.3）：L1 部署（发行已被 fb-…164800Z 升级
  替代；回退路径与备份链保留）。[执行报告](../deployment/drug-trace-install-20261021/EXECUTION-REPORT.md)。
- 更早各轮（FINALIZE/网络复测/平台联调/凭据登记/HIS活库/FIX2/FIX3/REVIEW2/3）
  记录保留在本文件历史与 STATE.md。

病理B01—B09及tenant/owner、0/1/N、前导零、固定revision、dry-run零真实副作用
不放松。离线代码、现场验收、正式业务签认分开报告。
