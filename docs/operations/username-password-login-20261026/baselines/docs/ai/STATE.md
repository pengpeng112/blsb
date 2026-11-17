# 当前项目状态

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


## 最新：TUI 复核修复完成，仅余生产部署待窗口（2026-10-24，DTR-TIMED-UI-FIX-20261024，ZCode/GLM-5.3）

按[修复执行书](../development/drug-trace-timed-ui-plan-20261024/FIX_TASK.md)一次完成 FIX-00—09（G1—G10 全修），
[RESULT 复核修复节](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)：
**upload-stats IN 规模缺陷已除**（两条分组 SQL+ROW_NUMBER 窗口，端点内无 .in_()；35k 规模回归锁过）、
outcomes 批量化（窗口子查询 LEFT JOIN+单号一次批量）、日报窗口序 422、每日实际「其他」列+UTC/GMT+8 脚注、
streak 按日期连续性、面板错误提示/查询按钮 operator 显隐/删死代码/横幅加载态；四类实施文档补齐。
实测：新增 4 集成测试（35k/clamp/窗口序/PG 端到端）；局部五文件 56/0；**全量回归 448/7/0**
（上轮 444+4，JUnit var/tui-fix-regression.xml）；npm build ✓（173.06 kB）；浏览器四项复验过
（fix-01/03/04.png）。**F-A07 禁改清单 30 文件哈希比对零改动**。**TUI-08 生产部署仍 BLOCKED**
（须用户在场+避开每日 06:00–08:30，就绪清单 RESULT §五）；本轮未触生产/<prod-host>/HIS/平台。
交接：[session](sessions/DTR-TIMED-UI-FIX-20261024.md)。

## 最新：TUI 复核完成，修复执行书待执行（2026-10-24，DTR-TIMED-UI-FIXPLAN-20261024，ZCode/GLM-5.3）

对 DTR-TIMED-UI 交付逐项复核（实验证实，非印象）：契约/幂等/门禁零改动/全量 444/7/0 均成立；发现 G1—G10：
**upload-stats IN 参数规模缺陷**（SQLite 32766/PG 65535 实测上限，生产 ~2000 单/日 days=90 约33天数据后必 500）、
四类实施文档欠账、每日实际「其他」列与 UTC/GMT+8 口径脚注、streak 日期连续性、面板吞错/按钮显隐/死代码/横幅加载态、
窗口序校验、outcomes 批量化、测试缺口。已交[修复执行书](../development/drug-trace-timed-ui-plan-20261024/FIX_TASK.md)
（FIX-00—09、F-A01—08、禁改清单、35k 规模+PG 端到端测试规格）；**顺序：先 FIX 修复再 TUI-08 部署**。
本轮纯复核+执行书零代码改动。交接：[session](sessions/DTR-TIMED-UI-FIXPLAN-20261024.md)。

## 最新：定时上报可视化已本地交付，生产部署待窗口（2026-10-24，DTR-TIMED-UI-20261024，ZCode/GLM-5.3）

按[执行书](../development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md)完成 TUI-00—07+09，[RESULT](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)：
**迁移012日报表**（SQLite+隔离PG up/down/up 幂等）、**4组6个新API**（daily-reports upsert/列表/单日、upload-stats、send-scope、submission-outcomes；均 tenant 隔离、只读或仅新增日报数据）、
**驱动v2**（跑完POST日报，失败仅记report_push，退出码/文件回执语义与v1一致；fatal路径best-effort推FAILED日报）、
**前端「定时上报」面板**（授权/健康§5.4/日报下钻/平台终态/异常提交）+**页顶横幅动态化**（MOCK/正式链路/未启用）。
实测：新增9集成测试（含Docker隔离PG迁移环与驱动端到端）；浏览器合成栈截图3张（面板四区/下钻/横幅切换/空态）；
**全量回归 444/7/0**（首轮2失败=旧迁移名单断言，补012后复跑全绿）；**A07门禁零改动**（service/quality/ingest等文件mtime均早于本会话，仅models/plugin/routes三文件追加）。
**TUI-08 生产部署 BLOCKED**（须用户在场且避开每日06:00–08:30；就绪清单在RESULT §五）；生产<prod-host>未动、定时器仍v1驱动照跑。交接：[session](sessions/DTR-TIMED-UI-20261024.md)。

## 最新：定时上报可视化长任务计划已交付（2026-10-24，DTR-TIMED-UI-PLAN-20261024，ZCode/GLM-5.3）

应用户要求编制[完整开发执行书](../development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md)（TUI-00—09、API契约§5、验收A01—A08）与[可复制提示词](../development/drug-trace-timed-ui-plan-20261024/NEXT_AI_PROMPT.md)，交其他AI执行：管理界面新增"定时上报"面板（授权/配额、健康、日报下钻、平台终态统计、异常提交）+日报入库（迁移012）+三个新查询API+驱动v2末尾上报+页顶横幅动态化。
差距分析与设计决策已定（日报入库不读文件、健康纯数据推导、零门禁改动、生产部署避开06:00–08:30须用户在场）。本轮纯计划零代码改动；未执行任何生产操作。交接：[session](sessions/DTR-TIMED-UI-PLAN-20261024.md)。

## 最新：定时上报已上线（2026-10-24，DTR-TIMED-REPORT-20261024，ZCode/GLM-5.3）

用户授权后完成剩余待办核查+定时上报落地，[结果](../development/drug-trace-timed-report-20261024/RESULT.md)。
**<prod-host> systemd 定时器 `blyb-daily-upload.timer` 已 active**（每日06:10 Asia/Shanghai，首跑2026-10-25处理10-24全天）：幂等采集昨日窗口→VALID门诊单串行发送（≤40/min遵DMZ限流）→查询补进→回执落/var/log/blyb/daily-upload/。
**发送授权standing scope**（证据0eb0b906，可revoke即停）：窗口2026-10-24→11-23、5药房白名单、max 80000、used保留计数；**10-12..23缺口不发送**（E08'待院方确认）；CONFIRMED永不重发。
**实测**：监督采集51单幂等0重复；冒烟发送1单HTTP200→**CONFIRMED（真实成功累计2单）**；配额2/80000；四类证据APPROVED。E06'/E07'三时点零迁移拦截维持。
风险如实：<prod-host> NTP未同步（错钟=显性失败可查）；全量日批明晨首跑未测；拆零流/缺口补报待院方。运维/回滚见[任务README](../development/drug-trace-timed-report-20261024/README.md)。交接：[session](sessions/DTR-TIMED-REPORT-20261024.md)。

## 最新：源表核查执行完毕，5077条根因闭环（2026-10-23 晚）

用户授权后任务 DTR-HISTORY-SOURCE-AUDIT-20261023（ZCode/GLM-5.3）经<relay-jump>受控连接器以平台只读账号READY_HIS执行源表SELECT约181条（约23秒，走既有索引、限量分批）：源写0、上传0、生产改动0。
**根因闭环**：5077条未匹配全部U02＝药房050121的**拆零（SPLIT_FLAG=1）OUT扫码流**，其20位单号不在处方主单编号空间（主单PRESC_NO仅1—5位；050121是最大药房排除药房失配；S01-S03按号+日期查主单0行）——r10三键JOIN按设计不可命中，**非正常门诊漏报，r10不改不并入**；是否覆盖待院方确认后另立来源revision。**31组重复＝1小时内真实双扫**（SPLIT_FLAG=0、每组恰2条、JOIN命中），11张整单保持INVALID/QD-DUP-CODE。逐日覆盖81天：主单245794张、142080张有OUT码484567行≈r1视图484539行（交叉一致，视图未丢已JOIN记录）；住院主单窗口内零OUT码。T侧全量JOIN_MISS/CREATE_TIME反向未做（无索引，SQL06实证）。
r2b工作簿与全部原始行在本机受限目录（ACL仅当前用户+SYSTEM）；共享证据无原码泄漏（扫描通过）。剩余外部项收敛为E05'—E08'。交接：[DTR-HISTORY-SOURCE-AUDIT-20261023](sessions/DTR-HISTORY-SOURCE-AUDIT-20261023.md)、[结果](../development/drug-trace-history-rootcause-r2/RESULT.md)。

## 最新接力：rootcause-r2 历史核查已执行（2026-10-23）

按用户 NEXT_AI_PROMPT 执行完 Q00—Q06：[结果](../development/drug-trace-history-rootcause-r2/RESULT.md)。
37 条受控 SELECT（仅两视图、BLYB_DRUGTRACE 再证不变、0.876 秒零错误）；源表查询 0、写 0、上传 0、生产改动 0。
**要点**：r10 定义/身份与 r1 完全一致可继续；5077 未匹配视图侧两时点零变化、全部暂记 U05（源端待 DBA SQL01—04；2330 行"同码同日异标识"为最强提示非证明）；1396 UNKNOWN 三态零迁移拦截保留；31 组重复实为 **11 张主单、全部 INVALID/QD-DUP-CODE（整单双扫模式）**；同 CREATE_TIME 反向审计如实标未完成（SQL05/06 已备）；持续自动推送不具备条件（A03 未实现、A02 恒空、A05 部分）。
S01—S03 样本、531 码/4974 旧单绑定包与新 r2 工作簿在本机受限目录（ACL 仅当前用户+SYSTEM）；六条 DBA SQL 静态校验通过均未执行，最小外部清单 [E01—E07](../development/drug-trace-history-rootcause-r2/EXTERNAL_INPUTS.md)。交接：[DTR-HISTORY-ROOTCAUSE-R2](sessions/DTR-HISTORY-ROOTCAUSE-R2.md)。原表根因未闭环、同窗口反向未做，均如实标注。

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

核对日期：2026-10-22。基线：**v0.4**。业务源码：`implementation/`。
这是共享的当前摘要。详细证据仍在实施文档和 `implementation/var/` 的 log/XML。

## 已执行到哪里

| 工作 | 当前证据状态 |
|---|---|
| 2026-10-22 来源接入与真实整单建包（DTR-SOURCE-DELIVERY-20261022，Codex/unknown） | **已实际部署src-20261022T103000Z/六healthy**；专用reader经worker登录、仅两视图SELECT；两类Oracle空游标修复已生效。新r10独立来源PUBLISHED/ENABLED（batch），三字典新revision正常API批准并保留旧项；collect证据按实证登记1天。**真实采集1张4码，DONE/VALID，冻结PREPARED，五必填DRY_RUN通过，attempt/quota/send_job均0**；新worker经mTLS平台查询成功。132相关测试通过；全量首轮424/8/7，UTF8后受影响两文件25/0复验通过。恢复路径保护修复，16运维测试与现场validate-only过；备份20261022T095541Z。**真实上传0，send仍MISSING/目的地DRAFT/DMZ仅query**；正式链/NTP/平台upload权限与321口径/未报首单仍待落实。见[本轮实施与下一步](../development/drug-trace-source-deploy-20261022/README.md) |
| 2026-10-22 r10执行后复核（DTR-R10-UPLOAD-READINESS-20261022，Codex/unknown） | **用户执行的r10已实际生效**：两视图VALID，定义与草案token一致；20头样本15正常已完成/5住院UNKNOWN，一单1头4码按实际质量函数VALID、0阻断（层级警告）。**视图无需重改**。现网仍fb-20261021T164800Z、首屏修复未部署、DSN空/来源DRAFT、医院药房映射空、目的地DRAFT；DMZ仅query，collect/send缺、采集/submission/配额0。正式worker查询与DMZ官方HEAD成功，首轮SSH拒绝后r2成功。上传0/HIS写0/现场改0，见[当前卡点与接续](../development/drug-trace-r10-readiness-20261022/README.md) |
| 2026-10-22 上传内容契约收口（DTR-UPLOAD-CONTENT-CONTRACT-20261022，Codex/unknown） | **零售5必填来源已明确**：稳定bill_code、M发药时间、沿用拟321、已存医院ref_ent_id、T小码列表；数量/药名/患者证件非本期必填。现库注释DUMP解码确认SOURCE0门诊/1住院、FLAG1正常2全退3部分退、REFUND1退费0/null未退；形成门诊正常且有发药时间/未退费r10草案，未批准/未执行DDL。5条HIS SELECT成功、两草案零行解析过、12候选反例检查过。现场只读字典查实：当前仅PATIENT_DISPENSE源code，需新revision补原始状态→321/A及药房→医院映射。见[上传内容确定单/修订SQL](../development/drug-trace-content-contract-20261022/README.md)。程序部署/来源启用/未报首张仍待落实，上传0、HIS写0 |
| 2026-10-22 Oracle首屏修复（DTR-ORACLE-FIRST-PAGE-20261022，Codex/unknown） | **本地已修复，110通过/0失败/0跳过；真实Oracle单据4码分页2/2/0全部读出**。新增12回归修复前10失败/2通过，保留反例；局部首轮WSL环境失败已通过进程PATH使用既有Git工具复验。业务质量门禁未改。**未构建/部署新发行，<prod-host>仍旧模板**；账号/PHARMACY视图此前已实测，业务UNKNOWN/finalized空、来源DSN/药房映射/启用及首张范围尚待落实，上传0。见[本轮修复与接续](../development/drug-trace-oracle-first-page-20261022/README.md) |
| 2026-10-22 用户建视图后实测（DTR-VIEW-VERIFY-FIRSTBILL-20261022，Codex/unknown） | **新账号BLYB_DRUGTRACE实际登录成功；实际owner为PHARMACY，两个视图VALID、17+18列、仅两视图SELECT及登录权限**；密码已本机DPAPI保存。当天20头/100码样本正常形状，一张1头4码关联一致；但kind=UNKNOWN/finalized空，真实4码在旧首屏SQL读0、NULL安全读4，部署worker也未修复。来源仍DRAFT/DSN空、目的地DRAFT、药房映射空，DMZ仅query；正式worker只读查询成功，时钟仍接近官方Date。**实际上传0、HIS写0、远端配置改0**。见[本轮核验/接续](../development/drug-trace-view-verify-20261022/README.md)，无需重建用户/视图，旧r8 owner拟定以本轮实测为准 |
| 2026-10-22 HIS执行包与DMZ实际校时（DTR-HIS-DDL-DMZ-CLOCK-20261022，Codex/unknown） | 用户知晓登记差异后明确追加本次核查授权，**实际HIS连接成功**：服务his，5条字典SELECT成功，目标账号未见；r8两视图SELECT零行解析通过。交付[建用户/视图/授权/验证执行包](../deployment/his-ddl-clock-20261022/README.md)，DDL由用户执行，尚未创建。另发现Oracle首屏空游标必漏读码，DUAL证明，源码未改，见NEXT_AI_SOURCE_FIX。**DMZ已按授权向前校正1946.3秒，校后差0.57秒、14分钟后仍<1秒；mTLS verify0、转发/Java进程不变**。仅一次校时，NTP长期同步仍未恢复。HIS写0、上传0 |
| 2026-10-22 资料重核与实际只读测试（DTR-INPUT-RECHECK-20261022，Codex/unknown） | **正式worker链路查询再成功，基础版服务期实测至2028-12-31**，服务有效期待提供项已消除；AppKey/Secret/HIS/SSH账号已提供不重索。当前fb-20261021T164800Z/6healthy、签名True、真实配置PUBLISHED；目的地对象仍DRAFT、来源DRAFT/无active config、DSN空、医院药房映射空，单据/采集/submission/配额0；query/reconcile APPROVED、collect/send MISSING。平台HIS登记仍medical_dict_push，视图元数据0（不等于活库不存在），本轮HIS活库0。DMZ TEST-ONLY/NTP未同步慢32分/仅query；上传0、现场配置零改动。见[实测与剩余说明](../development/drug-trace-input-recheck-20261022/README.md) |
| 2026-10-22 FB轮执行（DTR-FIRSTBILL-EXEC-20261021，ZCode/GLM-5.3） | **开发/部署/真实配置/签名/链路查询完成；首张上传0（外部输入阻断）**。FB-00—07 执行：动态就绪统一（CLI/API/UI 同源+配置发布API/CLI，14测试）；EXT-07 签名资格按批准修订闭合（官方C#向量+官方PyPI SDK移植对照+**平台受理本实现签名**实测；SIGNATURE_VERIFIED=True 随发行）；官方2026.01指南取证落码（321=使用出库、0/1/3/4+51/52、空结果解析修复）；新发行 **fb-20261021T164800Z** 升级（备份20261021T165233Z+restore演练过）；经API发布**真实目的地配置 PUBLISHED rev1**（is_mock=False）+官方字典（321/A）+EXT-09 query/reconcile 证据 APPROVED；**正式链路只读查询 SUCCESS**（worker→mTLS→DMZ→平台 found=true/total_num=0；DMZ allow_ips 加跨区NAT出口<relay-net-host> 实证）；<prod-host> 时钟与平台差≤10s。全量回归 **405/7/0**。真实上传0：HIS视图/账号（DBA）、正式CA、NTP源、uploadretail权限、321/323消歧、首张scope 均待外部。见[执行报告](../development/drug-trace-firstbill-exec-20261021/EXECUTION-REPORT.md) |
| 2026-10-21 首张上传长任务交付（DTR-FIRSTBILL-LONGTASK-20261021） | 已编制[下一AI执行提示词](../development/drug-trace-push-review-20261021/NEXT_AI_PROMPT.md)：FB-00—07、29项验收、外部输入回执及交付物，涵盖动态readiness/签名/生产只读来源/校时证书/配置与证据发布/正式链路查询/首张max_bills=1。仅文档交付，未新增现场操作或真实上传；当前运行事实仍以下一行现场复核为准 |
| 2026-10-21 23:29 部署后推送复核（DTR-PUSH-REVIEW-20261021，Codex/unknown） | **现场确认服务与网络就绪，真实上传仍阻断**。两机实际发行与上棒一致；6容器healthy、迁移001—011；worker→DMZ严格mTLS health200、DMZ→平台HEAD200/TLS0、<prod-host>→HIS1521 TCP成功。直接只读查询first-hospital：来源DRAFT/无active config、目的地Mock DRAFT/无revision、destination配置0、字典draft-1、单据/采集/证据/配额/submission全0；worker DSN空、真实签名断言CapabilityError、DMZ仅query.billstatus。时钟约慢32分钟未同步。现场零配置修改/HIS查询0/上传0。见[复核与接续步骤](../development/drug-trace-push-review-20261021/README.md) |
| 2026-10-21 现场部署执行（DTR-DEPLOY-20261021，ZCode/GLM-5.3） | **L1 安装启动完成（DEPLOYED_TEST_ONLY，禁发）**。安装前修复 P01—P05 全落地（oracledb 3.4.2+Thick 一次初始化+IC19.32 入镜像并容器内实测；lib.sh BLYB_DRUGTRACE_ENABLED 统一 compose 闭包；certs.sh 根 CA 扩展修复；DMZ 安装器 ss 解析+systemd219 兼容；init --tenant）。新发行：app **dpl-20261021T111000Z**（148 文件）、DMZ **20261021T113750Z**（首装 110326Z 后升级）。现场：DMZ 443 socket 持有/非 root blyb-dmz/真实 mTLS health 200 UP/无证书拒绝/8001=java 不变/重启保持/上游 OK；<prod-host> 备份 20261021T111644Z（含对象）→升级→迁移 001—011→6 容器 healthy→worker UID10001 证书可读不可写/Thick 19.32 实测/AppSecret 仅 worker→**<prod-host>→DMZ 真实 worker mTLS health SUCCESS**→init 草稿（--tenant first-hospital）→UI 200/路由 401→restore --validate-only 通过。禁发：DMZ 仅 query.billstatus、DSN 空、上传 0。全量回归 380/2/7（2 失败=R22 旧 fake，补齐后 56/0）。见[执行报告](../deployment/drug-trace-install-20261021/EXECUTION-REPORT.md) |
| 2026-10-21 安装与启用执行单（DTR-INSTALL-RUNBOOK-20261021） | 已交[部署执行单/下一AI提示词](../deployment/drug-trace-install-20261021/README.md)，L1服务/L2采集/L3推送分开。材料147/14条SHA匹配；核实旧包缺Oracle驱动/Thick初始化、统一compose未纳药品覆盖、DMZ证书SAN不含真实IP等安装前置，要求下一AI补修重打包后安装。仅编制与材料审计，现场未改；执行任务DTR-DEPLOY-20261021**已完成（见上行）** |
| 2026-10-21 网络调整复测（DTR-NETWORK-RETEST-20261021） | **DMZ→官方HTTPS443已通，且从DMZ真实签名只读查询SUCCESS**。<prod-host>→DMZ443/HIS1521 TCP均通；院内HTTPS health仍exit35、DMZ转发unit/socket未安装、<prod-host>仍旧发行。真实上传0，现场零改动。见[本轮实测](../development/drug-trace-network-20261021/README.md)；旧“DMZ出口443仍超时”已被更新，不整体关闭部署/来源/字典/首批范围缺项 |
| 2026-10-20 收尾执行（DTR-FINALIZE-20261020，ZCode/GLM-5.3） | FIN-00—09 可离线项完成：DMZ 转发器 v2（socket 激活/全阶段预算/连接限额/日志脱敏/诊断 CLI/严格配置校验）、219 兼容 unit+socket、安装器 v2（manifest 哈希/原子切换/严格 verify/完整回退）、私有运行时 cpython-3.11.9（官方 sha256 核对+centos:7 实测）、r7 视图契约+SQL 草案、业务分支适配层（未批准→等待）、批次/增量两模式、查询日期默认双窗口、session 可选端到端、init_drug_trace 幂等工具、compose worker 密钥隔离、readiness 分层（API+UI）、clientAuth 证书工具、旧系统 DNS/NTP 诊断。**全量 373/7/0**（380 cases，740.61s，var/dtr-finalize-full.xml）。两发行包已构建（<prod-host> fin-20261020T130011Z；DMZ 20261020T130019Z）。**现场未部署**（无维护窗口委托）：两台零改动、真实上传 0。systemd239 PID1 容器实测全生命周期+DMZ 只读版本探测（Anolis7.9/219/glibc2.17/无 python3/8001=java）。见[session](sessions/DTR-FINALIZE-20261020.md) |
| 2026-10-20 剩余开发与部署收尾任务书（DTR-FINALIZATION-PLAN-20261020） | 已编制[完整任务包](../development/drug-trace-finalization-20261020/README.md)：FIN-00—11、FIN-A01—38、网络/资料回执及下一AI提示词。覆盖旧systemd/socket兼容、严格安装/回退、HIS r7、签名/历史查询日期、配置注入与禁发部署。仅计划交付，未执行这些开发/部署；非网络输入仍需落实，不能宣称只剩网络 |
| 2026-10-20 真实平台联调（PLATFORM-LIVE-TEST-20261020） | 两项真实只读调用SUCCESS：状态查询＋医院企业目录（名称/标识一致）。正式链路实测仍阻断：<prod-host>仍旧发行，DMZ443/8443无监听、出口HTTPS超时；真实上传0。修复TOP空值签名，局部80 passed/1 skipped，正式签名门禁未关闭。见[实测与接续计划](../development/platform-live-test-20261020/README.md) |
| 2026-10-20 平台凭证登记（PLATFORM-CREDENTIALS-20261020） | 用户已提供AppKey/AppSecret/ref_ent_id/医院名称，DPAPI加密＋受限ACL保存在本机私有目录，后续AI按[登记说明](../development/platform-credentials-20261020/README.md)获取。登记阶段仅本地解密/SecretRef/环境恢复等验证；后续真实只读联调见上一行；EXT-06部分资料已收，权限/有效期/医院绑定/授权模式待确认。医院ent_id已由后续企业查询保存，库存场景仍需批准映射；当前retail不因此阻断 |
| 2026-10-19 HIS活库核查（HIS-LIVE-VERIFY-20261019） | 用户追加当次只读授权后，sjzc实际连接成功，服务名his；真实主键/索引/字段/注释已核实，8条OUT记录与处方主单和复合明细键唯一关联，6条拆零、一明细可多码。已建立来源r2、上传字段清单与后续计划；生产采集/发送仍关闭。见[最新核查](../development/his-live-20261019/README.md)。局部32 passed/1 skipped；无源库写入或真实发送 |
| 2026-10-19 首轮sjzc/HIS来源接入（HIS-SJZC-BIND-20261019，历史阶段） | 当时仅平台元数据：40条源登记、追溯表30列；因medical_dict_push登记未执行live。r1为PLATFORM_METADATA_ONLY，局部31 passed/1 skipped、5条SQL静态通过。随后用户追加当次授权完成活库续接，见上一行；[首轮证据](../development/his-sjzc-20261019/README.md)保留 |
| 第1—3轮与真实栈功能测试 | 既有记录。功能测试后回归 191/6，43 项复核通过 |
| 第4轮本地收尾（2026-10-02） | 216 passed / 6 skipped（var/round4-final-regression2.log）；T12 拖拽未做 |
| 2026-10-05 部署修订 | 内网卷隔离+令牌强制+端口退出（T14 两项 2 passed） |
| 2026-10-16 Linux 生产部署修订（REPORTING-LINUX-DEPLOY-20261016） | Git基线b7fda30起；§5部署修订落地；既有隔离23/23、全量229 passed / 6 skipped / 0 failed；<prod-host>最终发行 **20261016T114735Z**，部署任务已handed_off |
| 2026-10-16 部署复核与药品开发规划（REPORTING-DRUGTRACE-PLAN-20261016） | 22:29现场复核：发行哈希通过、6服务healthy、宿主22/443、迁移001—008；业务connector/destination/job等均0。发现TLS名称不符、备份/恢复静态缺口；新开发计划与用户SQL审查已交付，业务代码未改。见[本轮交接](sessions/REPORTING-DRUGTRACE-PLAN-20261016.md) |
| 2026-10-16 药品追溯离线开发（DTR-IMPLEMENT-20261016，ZCode/GLM-5.3） | 已交领域/009迁移/合成采集/Mock适配器/DMZ代码/API/UI及历史回归 **277 passed/6 skipped/0 failed**（283 cases）。旧“W01—W09全部完成、签名硬阻断、三方对账”结论已被10-17代码反例限制，须先整改；原交接保留 |
| 2026-10-17 二次代码复核（DTR-REVIEW2-20261017） | Git0ca47c6；原4组局部测试重跑 **47 passed/0 failed/0 skipped**，另16个离线反例均复现问题：TOP字段/响应、部分误确认、签名豁免、配置未固定、Oracle类型/游标、UNKNOWN重发/暂停失效、查询不再执行、无远端的对账。无远程登录/业务源码改动，正式上报不可启用。见[复核与完善计划](../development/drug-trace-20261016/review-2-20261017/README.md) |
| 2026-10-17 药品追溯第二轮整改（DTR-FIX2-20261017，ZCode/GLM-5.3） | FIX2-00—08全部可离线项完成：官方协议两profile/证据精确绑定+EXT-05/10/12门禁/Mock类型隔离/Oracle规范化+持久重取/停发CAS+查询代次+回执历史/平台快照对账/DMZ白名单与材料口径/备份全写入口+恢复路径保护/父子码+tenant隔离/UI修复；迁移010（真实隔离PG验证）。新增反例转换测试26项+转发器2项；全量回归见fix2-20261017/evidence/full-regression.log。EXT-01—12未齐，正式上报仍阻断。见[整改交付](../development/drug-trace-20261016/fix2-20261017/README.md) |

| 2026-10-17 第三次复核（DTR-REVIEW3-20261017） | FIX2工作区按实际字节复核：局部33/0/0，14个独立反例复现；已确认若干R2修复，但R3-01—07仍影响正式上报。详见[最新复核包](../development/drug-trace-20261016/review-3-20261017/README.md)，无业务源码/远程改动 |
| 2026-10-18 第三轮整改（DTR-FIX3-20261017，ZCode/GLM-5.3） | R3-01—07全部修复（FIX3-01—06）：企业双ID/查询选择器、逐字段keyset+页级原子采集+独立会话安全点、受理自动查询+QUERY_ERROR分类+满页推进、完整EXT-12 scope/配额台账/配置换代闭包、对账全分页复用UNVERIFIED、迁移011复合tenant FK+真实隔离PG backup→restore演练+transport/DMZ慢流硬顶+浏览器禁发闭环。新增G3测试29项；全量回归 **334/6/0**（340 cases）。EXT-01—12未变，正式上报仍阻断。见[整改交付](../development/drug-trace-20261016/fix3-20261017/README.md) |

**最近全量自动回归为 DTR-FIRSTBILL-EXEC-20261021 的 405 passed / 7 skipped / 0 failed**（412 cases，565.59s，2026-10-21；JUnit XML implementation/var/fb-final-regression.xml；7 skipped=真PG/live栈/Windows fd/B01·B05真实DSN×2/50GB 环境依赖项，理由在测试执行记录）。覆盖 DPL 轮 380/7/2（其 2 个 R22 fake 失败已不复现）。历史通过不能抵消新缺陷，也不等于真实上报通过。
期间修复四个真实缺陷：PG迁移建表拓扑序、compose 未把注册令牌传给 core（默认密钥漏洞）、
postgres initdb 健康竞态、bootstrap 测试进程污染（详见 docs/…/测试执行记录.md）。

## 可以部署的形态

- Windows 单机（SQLite）：`implementation/deploy/intranet/start.ps1`（旧路径，仍有效）。
- **Linux Compose 生产形态（新增，已隔离验证）**：发行包构建
  `implementation/deploy/release/build_release.py`；目标机安装
  `implementation/deploy/linux/install.sh`（含离线 Docker 材料 docker-offline/）；
  唯一入口 nginx TLS 443，后端 8000/8001/8002 不发布宿主机端口。
- <prod-host>基础栈已部署并升级至 `/opt/blyb/releases/fb-20261021T164800Z`（2026-10-22 DTR-FIRSTBILL-EXEC：动态就绪/签名资格/配置发布API；**真实目的地配置已发布 PUBLISHED rev1、EXT-09 已批、正式链路只读查询实测通过**；send 仍 fail-closed：send 证据未登记、配额0、DMZ 仅 query、DSN 空）。DMZ <relay-net-host> 转发器 20261021T113750Z 运行（TEST-ONLY 链；allow_ips 含跨区NAT出口 <relay-net-host>）。

## 尚未满足的条件

- **截至2026-10-22 15:44的真实剩余**：用户执行r8视图/账号脚本后验收、新受限只读登记和业务规则、Oracle码明细首屏NULL游标兼容修复、医院药房映射及来源/目的地启用、正式证书、长期NTP时间源、上传权限/321-323及未报scope。DMZ系统时间已纠正，长期NTP仍未同步。基础版服务截止日已实测2028-12-31，不再索要；签名/真实配置已完成。

- HIS于10-19及10-22用户追加即时授权后分别做过有限活库只读核验，服务名his。10-22仅字典/零行解析/DUAL，原登记仍medical_dict_push；不作为生产readonly验收或未来任意探查批准。r1/r2及r7原件保留，新r8脚本尚待用户执行；EXT-01/02、业务规则/晚到变更仍未关闭。入口见[sjzc](../startup/DATABASE_ACCESS.md)，已有密码不重索。
- 真实HIS/PIS/扫描仪/医保（B01—B09）及药品各项生产证据不得整体标绿。FIX3验收保留；<prod-host>/DMZ升级、真实目的地配置发布和签名资格已实测。query/reconcile按现行租户/目的地规则APPROVED，本轮collect凭实证APPROVED有效1天，send仍MISSING；不得伪造来源/发送证据或扩大首批范围。
- 药品新发行/DMZ443/mTLS及PHARMACY r10两视图、BLYB_DRUGTRACE受限账号均已现场验证；10-22本轮已完成worker绑定、正常来源发布及单张真实采集冻结。旧r8“用户/视图未见”和“仅零行解析”是历史结果，不再作为当前阻断。
- mTLS仍TEST-ONLY，待正式院方链；管理UI证书需另验。DMZ于15:29:49实际校正系统时间，校后<1秒、verify通过，NTP仍无可达源，不能把当前准时说成自动同步已恢复。
- 本机Docker隔离PG的backup→restore全链路演练已于FIX3完成；DTR-DEPLOY已完成生产升级前含对象备份和restore --validate-only、Anolis实机安装验证。生产实际恢复/异机备份尚未演练，不把只读校验当成实际恢复成功。
- 2026-10-16 22:34历史复测：DMZ可登录、无443/8443转发监听，强制IPv4平台HTTPS443超时、HTTP80通。10-20复测仍无监听/HTTPS超时，未重测HTTP80；时钟约慢32分钟，NTP状态因旧命令不兼容未取得。HIS当时仅1521 TCP通；现已按10-19新增证据确认实际服务his和原表只读查询成功，生产受限绑定仍待落实。
- 用户MED_TRACE_RECORD原SQL的单据粒度/时间/变更发现问题仍在；活库已确认RECORD_ID为主键，故更正旧“MAX必然吞不同记录码”的判断。已进行限量活库核验，未执行原始全期SQL；见[最新来源与上传字段](../development/his-live-20261019/README.md)。
- T12 受限画布拖拽未做。

- FB轮遗留回执见[原报告](../development/drug-trace-firstbill-exec-20261021/EXTERNAL-RECEIPT.md)，其“服务期待提供”由本轮接口证据更新；不能把“软件已交付”当成来源/目的地启用及真实整单编排已现场验收。目的地status仍DRAFT，plugin采集事件只为ENABLED目的地排PREPARE。后续通过既有受控流程补齐，不因有密码而绕过HIS策略或生成单据/证据。

## 维护方法

新的已验证结果写到本文件，并链到 session 和 log/XML。Git 已建立（main 分支）；
凭据/运行库/患者文件不入库（.gitignore/.gitattributes 已约束）。
