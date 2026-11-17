# 当前剩余任务

## 最新处置：DTR-QUEUE-REPAIR-20261026（2026-10-26 09:09）

- 已部署验证：排队/执行分开、send/query优先级+每5槽后台保障、过滤耗尽/在途query、看板查询错误列。局部47/全量482通过7跳过，原90秒保护导致的拥堵中止已修。
- 进行中：10-25恢复（原567 PREPARED），09:09新增71受理/65确认；观察/var/log/blyb/queue-recovery-20261026/final.json，不新增第二个消费者。旧247状态与断点不变、配额不重置。
- 待用户信息：050121/050116是否均属本院同一平台企业身份，确认后新建/批准映射revision；旧815和10-25另782未擅自处理。
- 待平台核对：旧41受理/206未知，12轮预算未绕过；当前只避免无效再POST查询，不等于已确认或可重发。
- P1新发现：submission-outcomes先limit200再筛问题，较旧206 UNKNOWN可能不在列表；应SQL先筛后限+租户/LEFT JOIN/旧异常反例验证，不提高limit造绿。
- 仍需：NTP UDP123/回包、正式证书与管理TLS；长collect心跳/租约、原07:30 SLA及跨日补偿后续治理。
- 证据：[本轮实录](../operations/drug-trace-queue-repair-20261026/README.md)，[交接](sessions/DTR-QUEUE-REPAIR-20261026.md)。

## P0新增：10-26日批只发3张后退出（2026-10-26）

- [核查与修复要点](../operations/drug-trace-daily-health-20261026/README.md)：真实第三笔send排队164秒超过90秒驱动等待；实际已受理，但后续发送中止。应区分排队/执行等待并治理单worker prepare积压，保持不二次投递。
- 过滤12轮耗尽/已有在途查询，避免每日重复247个query POST返回HTTP412；不删历史job/清查询预算。昨日说明409已按实际contracts纠正。
- 050121/050116批准企业映射仍缺，今天截至08:31已有537 prepare FAILED；由权威确认后批准新revision。
- 修复并验证后恢复10-25窗口；当时407 PREPARED未发、404 prepare待处理+1执行中；3受理中2确认1待确认，quota659不可重置。下次定时处理新日不补旧窗口。
- 日报/看板需覆盖后台prepare失败与query错误，不把当前errors_count=1当全部异常数。NTP/正式证书/管理TLS仍按前述外部事项处理。
- 本次仅核查，无生产修复/重启/补跑；[交接](sessions/DTR-DAILY-HEALTH-20261026.md)。

## 最新处置：DTR-NTP-DASHBOARD-20261025（2026-10-25）

- 已部署：TUI-08/012日报/同源看板/驱动日报开关1；请求共享40/min限速、429冷却、发送UNKNOWN/超时中止、错误样例保留总数。六容器健康、timer已恢复。旧部署待办由本条更新。
- P0 外部：<prod-host>→<relay-net-host> UDP123及有状态回包/白名单；当前chronyd持续重试但未同步。DMZ已Normal。管理HTTPS本机TLS EOF，现有UI证书缺IP SAN，需院方CA/访问策略处理。
- P0 业务：050121/050116的批准ref_ent_id映射缺失，815张VALID准备失败；由有权人员确认和批准新revision，不能猜值。
- P0 核对：全库206 UNKNOWN（205上传429+1上一轮本地凭据上下文错误）和41 ACCEPTED；最终均已到12轮查询上限，查询队列排空，需平台核对/有审计的受控人工复查。不得删job/重置上限或盲重发。
- P1 实测：10-26 06:10完整日批、报告入库/退出码/终态；10-24已补manual/PARTIAL_FAILURE，不冒充timer成功。长collect的主循环心跳/租约与跨日补偿仍需独立治理。
- 证据：[本轮实录](../operations/drug-trace-ntp-dashboard-20261025/README.md)，交接[本任务](sessions/DTR-NTP-DASHBOARD-20261025.md)。

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


## 最新：TUI 复核修复已完成，仅余生产部署待窗口（2026-10-24）

- **完成：DTR-TIMED-UI-FIX-20261024**（ZCode/GLM-5.3）：FIX-00—09 一次完成（G1—G10 全修），
  全量 **448/7/0**（var/tui-fix-regression.xml）、npm build ✓、浏览器四项复验过、
  禁改清单零改动。见[RESULT 复核修复节](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)。
- **待执行，P0：TUI-08 生产部署**（须用户在场确认窗口，避开每日 06:00–08:30）：build_release →
  <prod-host> upgrade.sh（迁移012自动执行）→ 替换宿主 `/opt/blyb/tools/blyb-daily-upload.py` ← 仓库 v2
  （`docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`）→ 只读验证（daily-reports 200/
  手动幂等跑一次入库/list-timers）→ 留证。清单见[RESULT §五](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)。
  开发与本地验收已完成（FIX 后全量 448/7/0）；回滚=rollback.sh+旧驱动+012 downgrade。
- **观察，P0**：2026-10-25 06:10 首个全量日批（仍是 v1 驱动、文件回执）；部署后首个 v2 日批查
  `/var/log/blyb/daily-upload/` 回执含 `report_push` 且界面「定时上报」面板可见。
- 小项（可选）：既有「提交与回执」卡「查询状态」按钮统一 operator 显隐（本轮仅面板，见 RESULT 遗留说明）。
- 其余同前：E05'/E06'/E07'/E08'业务确认、<prod-host> NTP、A02/A03/A05产品完善。

## 历史：TUI 复核修复待执行，先于生产部署（2026-10-24，已完成见上）

- **待执行，P0：DTR-TIMED-UI-FIX-20261024**（其他AI一次性）：按
  [FIX_TASK.md](../development/drug-trace-timed-ui-plan-20261024/FIX_TASK.md)（FIX-00—09，F-A01—08 验收）修复复核发现的
  G1—G10：upload-stats IN 参数规模缺陷（实测 SQLite 32766/PG 65535 上限，days=90 约33天数据后必炸）、
  四类实施文档欠账、每日实际「其他」列+口径脚注、streak 日期连续性、面板吞错+按钮显隐+死代码+横幅加载态、
  窗口序校验、outcomes 批量化、测试补充（35k 规模/clamp/PG 端到端）。禁改清单见 FIX_TASK §四。
- **待执行，P0（修复完成后）：TUI-08 生产部署**（须用户在场确认窗口，避开每日 06:00–08:30）：build_release →
  <prod-host> upgrade.sh（迁移012自动执行）→ 替换宿主 `/opt/blyb/tools/blyb-daily-upload.py` ← 仓库 v2
  （`docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`）→ 只读验证（daily-reports 200/
  手动幂等跑一次入库/list-timers）→ 留证。清单见[RESULT §五](../development/drug-trace-timed-ui-plan-20261024/RESULT.md)。
  开发与本地验收已完成（全量 444/7/0，A01—A04/A06—A08 过，A05 属部署项）；回滚=rollback.sh+旧驱动+012 downgrade。
- **观察，P0**：2026-10-25 06:10 首个全量日批（仍是 v1 驱动、文件回执）；部署后首个 v2 日批查
  `/var/log/blyb/daily-upload/` 回执含 `report_push` 且界面「定时上报」面板可见。
- 其余同前：E05'/E06'/E07'/E08'业务确认、<prod-host> NTP、A02/A03/A05产品完善。

## 最新：定时上报可视化待执行（2026-10-24，长任务已编好待接手）

- **待执行：DTR-TIMED-UI-20261024**（其他AI）：按[执行书](../development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md)实施TUI-00—09（日报入库012+四API+驱动v2+UI面板+横幅）；入口[NEXT_AI_PROMPT](../development/drug-trace-timed-ui-plan-20261024/NEXT_AI_PROMPT.md)。
  约束：零门禁改动；生产部署避开06:00–08:30且须用户在场确认；TUI-08在无窗口确认时标BLOCKED交付。
- **观察，P0**：2026-10-25 06:10 首个全量日批；查 /var/log/blyb/daily-upload/ 回执与配额消耗。
- 其余同前：E05'/E06'/E07'/E08'业务确认、<prod-host> NTP、A02/A03/A05产品完善。

## 最新：定时上报已上线，剩余为业务确认与首跑观察（2026-10-24）

- **完成：DTR-TIMED-REPORT-20261024**（ZCode/GLM-5.3，用户授权）：定时器active（每日06:10，首跑10-25）、
  standing send scope（10-24→11-23、5药房、80000、可revoke）、采集/发送/查询全链路实测（冒烟单CONFIRMED，
  真实成功累计2单）。见[结果](../development/drug-trace-timed-report-20261024/RESULT.md)。
- **观察，P0**：2026-10-25 06:10 首个全量日批（预计~1,700-2,000单）；检查 /var/log/blyb/daily-upload/ 回执、
  配额消耗、平台限流表现。
- **待院方确认**：E05'拆零流是否上报；E06'双扫11单处理规则；E07'退款口径；**E08'缺口10-12..23是否补报**
  （确认后扩scope窗口+独立backfill分区采集）。
- **运维遗留**：<prod-host> NTP（UDP123）未同步——错钟导致发送显性失败，仍应修复；A02/A03/A05产品完善另立任务。
- 定时上报运行期间不重置配额/不重发CONFIRMED单；暂停=stop timer，停发=revoke证据。

## 最新：源表核查完毕，5077根因闭环；剩余为业务确认与产品完善（2026-10-23晚）

- **完成：DTR-HISTORY-SOURCE-AUDIT-20261023**（ZCode/GLM-5.3，用户授权）：约181条只读SELECT，
  5077条全部U02＝050121拆零流（SPLIT_FLAG=1、20位单号不在主单编号空间）→r10不改不并入；
  31组重复＝<1小时双扫，11整单保持INVALID；81天主单覆盖与视图交叉一致。
  见[RESULT源表核查节](../development/drug-trace-history-rootcause-r2/RESULT.md)。
- **待院方确认，P0**：E05'拆零流是否需上报（业务名/主单语义/完成标准）→确认后另立来源revision开发。
- **待药学/HIS，P1**：E06'双扫11张整单处理规则；E07'退款/退药口径。
- **待DBA，P2**：E04'T侧全量JOIN_MISS计数/CREATE_TIME反向（无索引，需夜间窗口或临时索引评估）。
- **待开发，P1（另立任务）**：A03 ignore_part_success_flag；A02独立完整性来源（窗口内103714张门诊主单无OUT码的应报码数问题）；A05两层展示。
- 平台历史终态未查（无授权即不查）；既有1单4码成功不撤销不重发；自动批量/持续推送不启用。

## 最新：rootcause-r2 已执行，余外部回传与产品完善（2026-10-23）

- **完成：DTR-HISTORY-ROOTCAUSE-R2**（ZCode/GLM-5.3）：Q00—Q06 全部可执行项完成，
  [RESULT](../development/drug-trace-history-rootcause-r2/RESULT.md)。r10 保留；5077 全 U05 待源端；
  1396 零迁移拦截保留；31 组=11 主单全 INVALID/QD-DUP-CODE；37 SELECT/0.876s 仅两视图，源表 0 写 0 上传 0。
- **待外部，P0**：DBA 执行 [SQL01—04](../development/drug-trace-history-rootcause-r2/EXTERNAL_INPUTS.md)
  （S01—S03 小样→531 码分批）回传后 5077 条 U01—U05 归位；或用户批准专用受限审计账号后本机直查。
- **待外部，P1**：SQL06 确认索引→SQL05 逐日 JOIN 覆盖或 CREATE_TIME 反向方案（E04）；
  20 位旧明细号业务来源（E05）；退款/退药口径（E06）；31 组 SPLIT_FLAG 成因（E03）。
- **待开发，P1（另立任务，不在核查内）**：A03 ignore_part_success_flag 配置/冻结映射（全仓未实现）；
  A02 独立完整性来源；A05 整单确认 vs 码级核验两层展示。
- 既有 1 单 4 码成功不撤销不重发；自动批量/持续推送不启用。

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

- **完成：DTR-ONE-BILL-TRIAL-20261022**：1张4码真实上传并平台3/51、本地CONFIRMED；用户指定不撤销配置，单笔额度1已用尽。
- **新增P1：本地明细状态展示**：SubmissionItem四行仍PENDING，整单CONFIRMED；完善整单确认与独立码级核验显示，不能手改明细假装已独立对账。
- **待用户后续启用范围**：自动批量调度/额度和去重；本次不扩大范围或重发同单。正式CA/NTP仍后补。


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

## 最新：接入、采集、冻结已完成（2026-10-22）

- **完成：DTR-SOURCE-DELIVERY-20261022**：src新发行已部署，专用reader已绑定，来源PUBLISHED/ENABLED；三字典新revision已正常批准，原始状态映射问题已消除；真实1张4码DONE/VALID/PREPARED，五必填DRY_RUN通过，上传0。见[本轮实施](../development/drug-trace-source-deploy-20261022/README.md)。
- **完成：DTR-RESTORE-PATH-GUARD-20261022**：修复现网恢复脚本glob误展开后误通过，16运维测试及现场独立路径validate-only通过，未执行真实恢复。
- **外部待落实，P0**：正式CA/两个服务SAN+clientAuth；可达NTP/UDP123并验证同步；uploadretail权限和321/323消歧；具体未报测试单/药房/时间窗/max_bills=1。
- **待启用，依赖上项**：send证据/额度、目的地activation、DMZ仅增加uploadretail；随后首张发送→终态→逐码核验。不得把当前冻结候选当作已确认未报。
- **后续范围，P1**：连续变更源、住院/退药/拆零；未知分支仍等待。恢复演练始终显式传生产根以外的data-root；旧默认子目录会被正确阻断。

以下为前轮记录，其“DSN空/修复未部署/映射待建”已由本轮取代。

## 最新：r10已执行并核验，剩余服务部署配置（2026-10-22）

- **已明确**：uploadretail五必填字段来源；不再把quantity/unit/药名/患者证件/供应商列为本期最小接口必填缺项。
  原SQL按RECORD_ID主键分组，不应继续声称MAX必吞其他行码；单据/时间粒度需按主单发药事实修订。
- **DTR-CONTENT-R10，视图层已实测完成**：用户已执行，实际两视图与r10草案token一致；
  20头中15正常已完成/5住院UNKNOWN，一张1头4码质量VALID、0阻断。
  无须再次改视图或重建账号。见[当前实际核查](../development/drug-trace-r10-readiness-20261022/README.md)。
- **DTR-RAW-STATUS-DICT，已完成（本轮新revision）**：现场字典仅PATIENT_DISPENSE，payload却优先用
  `PRESC_SOURCE=0;FLAG=1`查映射；须补对应bill_type=321/query_type=A和药房医院映射，保留旧批准项。
  该差异已实机只读证实，不通过改来源原始值或手填approved_by绕过。
- 当前可接续材料：[上传内容确定单](../development/drug-trace-content-contract-20261022/README.md)。
  r10视图已生效；一张确认未报测试单、新发行部署/新来源启用、字典映射/目的地启用及真实发送闭环仍待落实；上传0。


## 最新：用户建视图后核验（2026-10-22）

- **已实测完成**：新账号BLYB_DRUGTRACE、PHARMACY两视图/私有同义词/最小直接权限；本机DPAPI记录。
  不再列为“账号/视图尚未建立”，不重跑旧BLYB_RO建库包；未向<prod-host>注入或更新源登记。
- **P0本地已修复、现场待升级：DTR-ORACLE-FIRST-PAGE**。本次真实单据4码，旧谓词0/NULL安全4；
  现场worker仍缺修复。本地110项通过及真实Oracle分页已通过，待新发行升级，不静默读成空单。
- **视图已生效/接入待完成**：r10正常门诊已分类并标完成，住院仍UNKNOWN；一单4码质量VALID。
  来源DRAFT/DSN空、药房ref_ent_id字典空、目的地DRAFT、DMZ仅query；修复部署/采集发送证据/未报范围待落实。
- **首张真实上传仍0**：用户已授权核查无问题后测1张，但当前发现问题；未绕过质量或编造业务批准。
  已询问状态口径与具体未报测试单。详见[本轮核验](../development/drug-trace-view-verify-20261022/README.md)。

详细要求：[第4轮任务书](../../一次性执行任务-本地剩余与内网部署-2026-10-01.md)。
本表按既有实施证据维护；2026-10-16已追加基础部署复核与药品追溯开发任务。2026-10-02的旧索引不覆盖后续测试/部署事实。

| ID | 优先级 | 任务 | 当前状态/证据 |
|---|---|---|---|
| DTR-HIS-DDL-DMZ-CLOCK-20261022 | P0 | HIS有限核查、给用户执行DDL包、按授权校正DMZ时钟 | 本次要求已完成：HIS实际只读字典/零行解析成功，旧r8 owner方案未采用，用户已执行PHARMACY r10并实测通过；DMZ实际校时1946.3秒，校后<1秒，服务保持。长期NTP未同步、上传0。见[执行包与证据](../deployment/his-ddl-clock-20261022/README.md) |
| DTR-ORACLE-FIRST-PAGE | P0 | 本地修复已完成；构建新发行并升级<prod-host>后复核 | NULL首屏分支已落地，新增12回归/相关110通过，真实Oracle4码2/2/0读取通过；现场仍旧模板、未构建/部署，见[修复报告](../development/drug-trace-oracle-first-page-20261022/README.md)。保留完整复合键/上限/后续字符串顺序 |
| DTR-INPUT-RECHECK-20261022 | P0 | 核实已有资料并实际测试可行部分 | 已完成复核：正式worker查询再成功；基础版服务截止2028-12-31，消除服务期待提供项。HIS登记仍medical_dict_push、来源/映射未启用；目的地对象DRAFT但配置PUBLISHED。上传0。见[最新核对](../development/drug-trace-input-recheck-20261022/README.md) |
| DTR-FIRSTBILL | P0 | 按完整长任务完成剩余开发、真实配置/现场验证及首张完整未报单据上传 | **开发部署阶段完成，首张未完成**：FB-00—05/07已执行、FB-06 BLOCKED（0张）；fb-20261021T164800Z、全量405/7/0，worker正式查询成功。10-22复核基础版服务期至2028-12-31；仍缺来源/药房/对象启用、正式CA/NTP、上传权限/321-323确认及未报scope。见[原执行报告](../development/drug-trace-firstbill-exec-20261021/EXECUTION-REPORT.md)+[最新核对](../development/drug-trace-input-recheck-20261022/README.md) |
| DTR-PUSH-REVIEW-20261021 | P0 | 部署后现场核查是否可测试推送 | 复核完成：服务/迁移/mTLS/出口通过；first-hospital单据/采集/证据/配额/submission均0，来源DRAFT、目的地Mock DRAFT无配置、DSN空、真实签名被拒、DMZ仅查询。上传0。见[复核及接续六步](../development/drug-trace-push-review-20261021/README.md)，剩余继续下列三任务 |
| DTR-READINESS-DYNAMIC | P1 | init_drug_trace.readiness_report按实际配置、有效证据计算来源/平台/发送状态 | **已完成（FB-01）**：domains.drug_trace.readiness 单一实现供 CLI/API/UI；层状态动态计算+三态统一；多目的地歧义拒绝；NETWORK 恒 UNKNOWN；配置发布 API/CLI 幂等工具落地；14 项新测试+现场实测 |
| DTR-CERT-ROTATE | P1 | 正式链轮换；院内可用NTP源落实长期同步 | TEST-ONLY仍运行；10-22用户授权DMZ系统时间已校正且稳定复查<1秒，mTLS verify0、进程不变。长期NTP Reach0未恢复，不能重写为仍慢32分，也不能声称已同步 |
| DTR-L2-SOURCE | P0 | 账号/PHARMACY两视图已实测 → 来源绑定/发布启用 → 药房映射与整单采集 | 用户已执行简化SQL，新账号BLYB_DRUGTRACE实际登录/最小权限/17+18列VALID及真实样本通过，密码本机DPAPI保存；不要重建旧r8 owner。首屏本地已修，现场待升级；r10正常门诊已完成且一单质量VALID，DSN/来源/药房映射仍待落实，见[当前核验](../development/drug-trace-r10-readiness-20261022/README.md) |
| DTR-L3-FIRSTBILL | P0 | 目的地生命周期启用/正式证书校时/上传权限与字典确认 → 未报scope → 首张max_bills=1 | PUBLISHED rev1/is_mock=False、签名资格及query/reconcile已具备；10-22只读再成功、基础版服务期至2028-12-31。目的地status仍DRAFT，采集事件仅路由ENABLED目标，需按既有流程启用。其余EXT-01/02/04/05/06权限/08消歧/10/12未齐，真实上传0 |
| DTR-DEPLOY-20261021 | P0 | 按最新安装执行单补齐部署缺口、两台安装/启动/真实路径验证 | **L1 完成（DEPLOYED_TEST_ONLY）**：P01—P05 修复+新发行（app dpl-20261021T111000Z、DMZ 20261021T113750Z）；DMZ 443/mTLS/非root/8001保持/重启保持/上游OK；<prod-host> 备份+升级+迁移001—011+6容器healthy+worker Thick/证书/Secret隔离+<prod-host>→DMZ mTLS health SUCCESS；禁发（仅query.billstatus、DSN空、上传0）。见[执行报告](../deployment/drug-trace-install-20261021/EXECUTION-REPORT.md)。L2/L3 转上方条目 |
| DTR-NETWORK-RETEST-20261021 | P0 | 网络调整后核查正式链路和推送条件 | DMZ公网HTTPS/真实签名只读SUCCESS，<prod-host>→DMZ443/HIS1521 TCP通；**转发服务已由 DTR-DEPLOY-20261021 安装**，院内 mTLS 已通。见[实测](../development/drug-trace-network-20261021/README.md) |
| DTR-FINALIZE-20261020 | P0 | 按一轮收尾任务完成剩余开发、发行、安装工具与具备条件的禁发部署 | **软件交付完成，现场未部署**：FIN-00—09 离线项全落地（DMZ v2+socket+运行时+安装器、r7 草案、分支适配、双窗口查询、session、init 工具、readiness 分层、证书/诊断工具）；全量 373/7/0；两发行包已构建。剩余=窗口/证书/出口/视图/规则/签名资格/字典/scope（见 [session](sessions/DTR-FINALIZE-20261020.md) 唯一剩余清单；**不是只剩网络**）。FIN-10/11 待窗口与材料 |
| PLATFORM-LIVE-TEST-20261020 | P0 | 真实平台验证、正式链路复测、首张真实上传准备 | 两项只读SUCCESS、医院目录吻合；签名空值已修复、80通过1跳过。DMZ转发未监听/HTTPS出口仍超时，<prod-host>仍旧发行；321/323与既往上报待确认，真实上传0。见[证据与接续步骤](../development/platform-live-test-20261020/README.md)，未宣称完整成功 |
| PLATFORM-CREDENTIALS-20261020 | P0 | 平台资料安全落盘、供后续AI定位、核对缺项 | 本机DPAPI/ACL/Git忽略/SecretRef解析与环境恢复已验证；[登记说明](../development/platform-credentials-20261020/README.md)。retail基础身份已收，待平台权限/服务有效期/医院绑定/模式及其他EXT验证；未发真实请求，不自动批准或启用业务 |
| HIS-LIVE-VERIFY-20261019 | P0 | HIS活库登录、确认上传取数关联与字段 | 已按当次追加授权完成有限只读核验：服务his、三表键/索引、8/8复合明细关联；来源r2与[字段/接续计划](../development/his-live-20261019/README.md)已交付，局部32通过1跳过。待：r7来源契约批准、生产受限只读绑定/视图、拆零/退药/住院状态、晚到变更发现及平台材料；本次成功不代表允许上报 |
| HIS-SJZC-BIND-20261019 | P0 | sjzc只读核验HIS、来源事实入程序、统一启动入口 | 首轮平台元数据/r1/启动文档已交付，31 passed/1 skipped。当时live阻断保留[原证据](../development/his-sjzc-20261019/README.md)；后续已按用户追加当次授权完成HIS-LIVE-VERIFY并新建r2，剩余以该任务为准，不再重复索取数据库密码 |
| R4-T1 | P0 | mapping simulate，逐字段结果/字典/异常/血缘 | 本地验收通过（2026-10-02，test_T1 与 field-lineage；终验 216/6） |
| R4-T2 | P1 | ingress事件、持久Inbox、幂等和路由 | 本地验收通过（2026-10-02，test_T2） |
| R4-T3 | P0 | 固定package revision读取 | 本地验收通过（2026-10-02，test_T3） |
| R4-T4 | P0 | publication撤回及结构化/文件可见性 | 本地验收通过（2026-10-02，test_T4/T4b/T4c） |
| R4-T5 | P1 | storage迁移预览/执行/恢复 | 本地验收通过（2026-10-02，test_T5） |
| R4-T6 | P0 | Agent只读配置、owner权限 | 本地验收通过（2026-10-02，test_T6/T6b） |
| R4-T7 | P1 | 手动submission/publication，复用持久执行链 | 本地验收通过（2026-10-02，test_T7/T7b） |
| R4-T8 | P1 | 各列表keyset cursor统一 | 本地验收通过（2026-10-02，test_T8） |
| R4-T9 | P1 | 租户复合约束及保数据迁移 | 本地验收通过（2026-10-02，迁移007，test_T9） |
| R4-T10 | P2 | PII读取边界/授权/脱敏与盘点 | 局部通过；明文证件号不出 API。关联候选摘要比较仍 deferred |
| R4-T11 | P2 | 连接器→映射→批准字典/Pack/Release向导 | 本地验收通过（2026-10-02 构建版浏览器闭环） |
| R4-T12 | P3 | 有限DAG拖拽位置 | 未执行。现有受限表单编辑保留 |
| R4-T13 | P2 | runbook新根恢复与实际命令纠偏 | 2026-10-02 已回填。2026-10-05 修正离线安装文档里的 Python 版本和 wheel 路径 |
| R4-T14 | P0 | 内网离线部署与演示凭据加固 | 脚本已有。2026-10-05 补上卷根隔离、令牌强制和端口占用退出。本会话未完整拉起：本机 8000 被其他程序占用 |
| REPORTING-LINUX-DEPLOY-20261016 | P0 | Linux生产部署修订+隔离验证+<prod-host>基础平台 | §5缺口全落地；隔离23/23、全量229/6/0；<prod-host>全栈healthy（见 sessions/REPORTING-LINUX-DEPLOY-20261016.md）。余：P4完整验收、P5码上放心模块、院方证书/异机备份 |
| REPORTING-DRUGTRACE-PLAN-20261016 | 规划 | 部署复核、源码边界、开发与验收计划、历史SQL审查 | 已交付[开发包](../development/drug-trace-20261016/README.md)；现场只读复核，业务代码未改 |
| DTR-W01 / OPS-01—08 | P0/P1 | TLS、恢复/备份脚本、按能力生产门禁及运维验收 | 有实现与13项旧测试；10-17复核仍有多存储写入静止点/自定义路径/恢复项目保护缺口。完整真实PG恢复不是fake docker验证，待FIX2-06 |
| DTR-W02—W09 | P0/P1 | 两profile药品来源/质量/提交/查询/对账/DMZ/UI | 有离线实现/旧277/6；10-17发现R2-01—16，正式适配器及三方对账尚不完整，改由FIX2工作包整改，不再标整体本地完成 |
| DTR-REVIEW2-20261017 | 复核 | 独立代码复核与详细完善计划 | 已交[第二次复核包](../development/drug-trace-20261016/review-2-20261017/README.md)；47旧局部测试通过，16反例复现；无现场/业务源码修改 |
| DTR-FIX2-20261017 | P0 | R2-01—16 离线整改（FIX2-00—08） | 可离线项全部完成：协议/门禁/状态机/对账/DMZ/备份/UI+迁移010；26反例转换测试+浏览器闭环通过（见[交付](../development/drug-trace-20261016/fix2-20261017/README.md)）。剩余：SITE2/LIVE2 现场项与 EXT-01—12；真实PG全链路备份恢复演练、Anolis实机安装、SDK向量仍待外部材料 |
| DTR-REVIEW3-20261017 | 复核 | FIX2工作区第三次独立复核 | 33局部通过但14反例复现（R3-01—07）；[复核包](../development/drug-trace-20261016/review-3-20261017/README.md)；无业务源码/现场修改 |
| DTR-FIX3-20261017 | P0 | R3-01—07 第三轮整改（FIX3-01—06，G3-01—19） | 全部完成：双ID/查询选择器、逐字段keyset+页级原子+独立会话安全点、受理自动查询+错误分类、完整scope/配额台账/配置换代闭包、对账全分页复用、迁移011+复合tenant FK+真实PG backup→restore演练+慢流硬顶（transport/DMZ）+浏览器禁发闭环；全量334/6/0。见[交付](../development/drug-trace-20261016/fix3-20261017/README.md)。剩余：EXT-01—12外部材料、Anolis实机、<prod-host>升级窗口、SDK官方向量；SQLite存量009旧约束重建明确不在范围（无SQLite生产部署） |
| FIX2-00—02 | P0 | 官方参数/响应/部分成功、生产证据/配置/目标固定、Mock隔离/mTLS | 待开发，见R2-01—05/11/15 |
| FIX2-03—05 | P0 | Oracle与分页、worker停发/状态CAS/轮询回执、平台三方对账 | 待开发，见R2-06—10 |
| FIX2-06—08 | P0/P1 | DMZ运行/备份恢复、权限/质量/UI、独立测试与发行 | 待开发，见R2-12—16；010及后续迁移，保留009原文 |
| DTR-W10—W11 / EXT-01—12 | 外部 | 现场升级、HIS只读、平台权限/字典、首次真实联调签认 | 待材料/维护窗口/相应授权；不能以离线Mock替代 |
| DOC-20261002 | 维护 | 文档归档、统一入口、跨工具交接和检查 | 已完成。交接正文曾把第4轮写成未执行，2026-10-05 已按证据改正 |
| DEPLOY-20261005 | 维护 | 部署前修订与环境核对 | 本会话。见 sessions/DEPLOY-20261005.md |

第4轮除 T12 和外部环境项外，本地实现已有证据；<prod-host>基础部署已完成。下一步按药品开发包增量开发并补运维验收，不重做T1—T11或重装基础栈。
开工先登记自己的范围；完成后把状态改为“实现未测/局部通过/本地验收通过”之一，附真实证据。

## 任务书不能代替架构判断

- 撤回要处理当前结构化视图、文件路由、目标策略和重复/崩溃恢复；不能只写REVOKED字符串。
- 配置读取、上传和设备命令除tenant外还要验证owner；scope参数不替代授权。
- 复合约束遇历史坏数据不能“跳过约束并标完整验收”；隔离/告警/未完成必须可见，不能默删。
- 存储迁移计划与执行进度持久化；hash校验和事务切换后保留旧位置安全窗口。
- API构建通过不替代真实浏览器；当前环境缺指定验证时如实保留未执行。

## 小项（2026-10-16 登记）

- 登录页（打包版）首次登录时组件捕获默认后端地址会覆盖 localStorage：同源发行（VITE_CORE_DEFAULT 注入）不受影响；分离部署需在登录页“后端地址”填对再登录，或后续改为登录时以输入框为准不回写默认值。
- drug_trace 对账 run_reconciliation 未按 profile 过滤单据范围（按目的地全量核对，scope 记录了 profile）；W10 前按 profile×business_kind 收紧。
- A15 专项：跨租户直查 /api/v1/drug-trace/* 的越权用例补强（当前依赖各路由 tenant 过滤+掩码测试间接覆盖）。

## 环境/外部待办

PostgreSQL与Linux容器已有部署/隔离证据；SCM/Win7、50GiB窗口、B01—B09真实接入仍按既有外部清单。新增HIS readonly/服务名、DMZ443出口、证书/NTP、平台SDK权限、异机恢复等见EXT-01—12，不把某一缺项推广为所有本地任务阻断。


## FIX2后第三次复核（当前优先）

FIX2的305/6历史回归保留；第三次复核局部33通过，14反例表明“全部可离线项完成”仍不成立。

| 任务 | 优先级 | 状态/依赖 |
|---|---|---|
| DTR-REVIEW3-20261017 | 复核 | 已交[复核包](../development/drug-trace-20261016/review-3-20261017/README.md)，未修业务源码 |
| FIX3-01/02 | P0 | 企业双ID/查询参数、完整tuple游标、跨session暂停/事务、来源绑定；待实现 |
| FIX3-03 | P0 | 受理自动查询、权限错误/晚页失败不可判无记录、无total满页推进；待实现 |
| FIX3-04 | P0 | 审批API完整范围/配额、精确配置绑定、跨tenant草稿拒绝、未发送配置安全换代、真实字典revision；待实现 |
| FIX3-05 | P0 | 对账完整日期/分页/错误证明，额外单据能力边界；待实现 |
| FIX3-06 | P0/P1 | 真PG完整恢复、旧库迁移/复合FK、硬deadline、浏览器/回归/发行；待实现 |

下一执行提示词与G3-01—19验收见最新包。SITE/LIVE仍等相应EXT资料/窗口/授权；本轮未刷新现场网络状态。
