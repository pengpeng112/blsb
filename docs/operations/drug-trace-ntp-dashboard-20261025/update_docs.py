"""Update shared handoff and the existing implementation document set."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]


def prepend_after_title(rel, section):
    p=ROOT/rel
    text=p.read_text('utf8')
    title,rest=text.split('\n',1)
    p.write_text(title+'\n\n'+section.strip()+'\n'+rest,encoding='utf8')


summary='''## 最新：看板已升级，限流已修复；<prod-host> NTP仍待UDP放通（2026-10-25）

用户授权后完成生产`ui-20261025T140000Z`升级、迁移012、宿主驱动和日报开关1，六容器healthy；timer下一次10-26 06:10。
[现场实录/网络要求/回退](../operations/drug-trace-ntp-dashboard-20261025/README.md)：上传与每次查询统一40/min，429后仅冷却后续请求、不重放。
DMZ修复后226次200转发、0次拒绝；修复前3679次429。采集DONE2414单，VALID1470；全库409 CONFIRMED/41 ACCEPTED/206 UNKNOWN、used656。
10-24窗口408确认+41受理+206未知，另815张缺050121/050116批准ref_ent_id映射；没有盲补字典、重发UNKNOWN或重置额度。
已补入10-24 **manual/PARTIAL_FAILURE**重建日报，654是实际尝试数不是成功数；原timer中断回执仍保留，不能说全天正常。
DMZ chronyd选中<relay-net-host>且Normal；<prod-host>已开机启用并64—256秒持续轮询，**UDP123仍无响应、未同步**（动态源端口与固定123均测）。试验中继无效已撤回。
服务器页面/资产和看板API均200；现有UI证书缺IP SAN，本机到443 TLS EOF，**浏览器未验收**。
最终局部38通过、传输45通过/1跳过；全量467通过/1旧断言失败/7跳过，该旧断言已在当前局部及单项复验通过，未称单次全绿。
下一步：NTP ACL/回程及白名单、管理入口证书/TLS策略、2药房映射、247个非终态人工核对与受控复查、10-26完整日批观察。
交接：[DTR-NTP-DASHBOARD-20261025](sessions/DTR-NTP-DASHBOARD-20261025.md)。下方为旧时点，TUI-08“未部署”及“采集仍RUNNING”等不能覆盖本轮证据。
'''
for path in ['docs/ai/STATE.md','docs/ai/HANDOFF.md']:
    prepend_after_title(path,summary)
prepend_after_title('docs/ai/BACKLOG.md','''## 最新处置：DTR-NTP-DASHBOARD-20261025（2026-10-25）

- 已部署：TUI-08/012日报/同源看板/驱动日报开关1；请求共享40/min限速、429冷却、发送UNKNOWN/超时中止、错误样例保留总数。六容器健康、timer已恢复。旧部署待办由本条更新。
- P0 外部：<prod-host>→<relay-net-host> UDP123及有状态回包/白名单；当前chronyd持续重试但未同步。DMZ已Normal。管理HTTPS本机TLS EOF，现有UI证书缺IP SAN，需院方CA/访问策略处理。
- P0 业务：050121/050116的批准ref_ent_id映射缺失，815张VALID准备失败；由有权人员确认和批准新revision，不能猜值。
- P0 核对：全库206 UNKNOWN（205上传429+1上一轮本地凭据上下文错误）和41 ACCEPTED；不少已到12轮查询上限，需平台核对/有审计的受控人工复查。不得删job/重置上限或盲重发。
- P1 实测：10-26 06:10完整日批、报告入库/退出码/终态；10-24已补manual/PARTIAL_FAILURE，不冒充timer成功。长collect的主循环心跳/租约与跨日补偿仍需独立治理。
- 证据：[本轮实录](../operations/drug-trace-ntp-dashboard-20261025/README.md)，交接[本任务](sessions/DTR-NTP-DASHBOARD-20261025.md)。
''')

common='''## 2026-10-25 生产看板/限流部署（DTR-NTP-DASHBOARD-20261025）

[现场实录](../../docs/operations/drug-trace-ntp-dashboard-20261025/README.md)：生产ui-20261025T140000Z已上线，迁移012与六容器健康；日报开关1，timer下一次10-26 06:10。
RealTransport按DMZ origin跨实例统一1.5秒间隔（单worker，40次/分），上传/查询分页共用；429冷却后续请求60秒，不自动重试POST。
驱动发现发送结果UNKNOWN/90秒仍在途即中止后续新发送；REJECTED/PARTIAL计失败。日报错误超过50条时保留样例+errors_total/errors_truncated，文件保留完整错误；API列表按准确总数，UI说明截取。
日报单据日期/平台终态原口径不变。补录10-24 manual/PARTIAL_FAILURE明确由数据库重建；654是attempt数，非成功数。当前全库409 CONFIRMED/41 ACCEPTED/206 UNKNOWN，815准备失败缺两药房企业映射，未自动补字典或重发。
DMZ NTP正常；<prod-host>持续轮询但UDP123未通。服务端页面/资源200，本机TLS EOF且证书无IP SAN，浏览器未验收；下一次完整日批亦未验收。
'''
for path in ['implementation/docs/实施状态矩阵.md','implementation/docs/接口与设计差异.md','implementation/docs/04-API目录对照矩阵.md']:
    prepend_after_title(path,common)
prepend_after_title('implementation/docs/测试执行记录.md',common+'''
本轮最终局部38 passed（var/ntp-dashboard-local-verified.xml），传输45 passed/1 skipped（ntp-dashboard-transport.xml）。
全量467 passed/1 failed/7 skipped（ntp-dashboard-regression.xml）：全量进程加载了仍要求日报开关0的旧断言，当前需012部署后的开关1；当前局部及单项ntp-dashboard-full-failure-recheck.xml均通过，原失败证据保留，未声称单次全量全绿。
同源npm build通过、继承镜像依赖freeze一致；现场升级exit0、012台账/六容器、四GET/日报POST、站内TLS-CN验证页面和资产200；DMZ观察226次200/0限流。
''')
prepend_after_title('数据上报/1、码上放心平台/README.md','''## 2026-10-25 最新运行与看板入口

看板已部署到`https://<prod-host>/ui/#/drugtrace`，生产发行ui-20261025T140000Z、日报迁移012及开关1。六容器healthy，timer下一次10-26 06:10，处理10-25。
[本轮修复/网络要求/回退/剩余事项](../../docs/operations/drug-trace-ntp-dashboard-20261025/README.md)：统一40次/分解决上传和查询叠加限流；10-24日报为人工重建PARTIAL_FAILURE，不是全天成功。
当前409张确认、41受理、206未知；另815张缺050121/050116批准企业映射。DMZ已同步<relay-net-host>，<prod-host> UDP123仍不通、已持续轮询；UI原证书缺IP SAN且本机TLS断开，页面服务端200但浏览器访问未验收。下列旧日期材料保留作溯源，以共享STATE最新证据为准。
''')

handoff='''# DTR-NTP-DASHBOARD-20261025 交接

- 工具Codex，model unknown，session 20261025T132833Z-14376269；用户明确授权修复、NTP、看板升级。
- [完整结果与剩余事项](../../operations/drug-trace-ntp-dashboard-20261025/README.md)，脚本/安全证据均在同目录。没有源HIS访问/写入、没有本轮新增手动上传。
- 已升级生产ui-20261025T140000Z，012日报、六容器healthy，统一40/min传输节奏和429冷却；新驱动处理UNKNOWN/超时/业务失败并压缩API错误样例保留总数；原回执不截断。日报开关1及timer已恢复，下次10-26 06:10。
- 原件与一致性备份见报告。首次升级因shell执行位退出126（备份前），已保留失败日志；第二次正常backup/install/migrate/health退出0。旧候选包未激活，不修改已冻结包。
- 10-24采集DONE：2414 seen，1470 VALID，944 WAITING；全库409CONFIRMED/41ACCEPTED/206UNKNOWN、used656；其中10-24 408确认/41受理/206未知，另815缺两药房企业映射。205上传429+1上一轮错误core handler执行导致未知全部保留，不重发/清额度。
- 10-24人工重建日报source=manual/outcome=PARTIAL_FAILURE，API200。原driver计数不可恢复，654是实库窗口attempt数；UI1021错误=815准备失败+206UNKNOWN，取50样例，不能作为原timer完整成功证据。
- DMZ NTP Normal/选中<relay-net-host>，开机启用；<prod-host>配置minpoll6/maxpoll8持续轮询但UDP无响应。测试DMZ中继也不通，已撤回allow并恢复原DMZ配置SHA。未手动date或强制makestep；等待网络ACL/回程/服务器白名单处理。
- 四个GET及manual日报POST200；服务端用CN+显式信任锚验证页面与资产200。现UI证书没有IP SAN，本机TCP443可建连但TLS EOF，Chromium同样断开；无页面渲染截图。院方CA及管理TLS策略仍待处理。
- 测试：最终局部38pass，传输45pass1skip；全量467pass1旧断言fail7skip，当前该项与局部均已复验通过（XML仍保留原失败），不称单次全绿。离线镜像freeze未变，无新依赖。
- 后续先处理NTP/TLS/两药房批准字典、247个非终态平台核对及受控查询（不少到12轮上限，不能删job清计数），再观察下一完整日批、长任务心跳/租约和跨日补偿。不要再次执行contain/upgrade/report_snapshot等一次性脚本；inspect/health可复用，NTP旧apply脚本有过期SHA不可重跑。
'''
(ROOT/'docs/ai/sessions/DTR-NTP-DASHBOARD-20261025.md').write_text(handoff,encoding='utf8')
print('Updated shared state, handoff, backlog, four implementation docs, business README and session.')
