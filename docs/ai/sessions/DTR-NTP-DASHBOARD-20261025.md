# DTR-NTP-DASHBOARD-20261025 交接

- 工具Codex，model unknown，session 20261025T132833Z-14376269；用户明确授权修复、NTP、看板升级。
- [完整结果与剩余事项](../../operations/drug-trace-ntp-dashboard-20261025/README.md)，脚本/安全证据均在同目录。没有源HIS访问/写入、没有本轮新增手动上传。
- 已升级生产ui-20261025T140000Z，012日报、六容器healthy，统一40/min传输节奏和429冷却；新驱动处理UNKNOWN/超时/业务失败并压缩API错误样例保留总数；原回执不截断。日报开关1及timer已恢复，下次10-26 06:10。
- 原件与一致性备份见报告。首次升级因shell执行位退出126（备份前），已保留失败日志；第二次正常backup/install/migrate/health退出0。旧候选包未激活，不修改已冻结包。
- 10-24采集DONE：2414 seen，1470 VALID，944 WAITING；全库409CONFIRMED/41ACCEPTED/206UNKNOWN、used656；其中10-24 408确认/41受理/206未知，另815缺两药房企业映射。205上传429+1上一轮错误core handler执行导致未知全部保留，不重发/清额度。
- 10-24人工重建日报source=manual/outcome=PARTIAL_FAILURE，API200。原driver计数不可恢复，654是实库窗口attempt数；UI1021错误=815准备失败+206UNKNOWN，取50样例，不能作为原timer完整成功证据。
- DMZ NTP Normal/选中<relay-net-host>，开机启用；<prod-host>配置minpoll6/maxpoll8持续轮询但UDP无响应。测试DMZ中继也不通，已撤回allow并恢复原DMZ配置SHA。未手动date或强制makestep；等待网络ACL/回程/服务器白名单处理。
- 四个GET及manual日报POST200；服务端用CN+显式信任锚验证页面与资产200。现UI证书没有IP SAN，本机TCP443可建连但TLS EOF，Chromium同样断开；无页面渲染截图。院方CA及管理TLS策略仍待处理。
- 测试：最终局部38pass，传输45pass1skip；全量467pass1旧断言fail7skip，当前该项与局部均已复验通过（XML仍保留原失败），不称单次全绿。离线镜像freeze未变，无新依赖。
- 最终22:01：247个非终态均用完12轮预算，查询队列已排空，六容器18分钟healthy；不能说仍在自动处理。后续先处理NTP/TLS/两药房批准字典和平台核对/受控查询（不能删job清计数），再观察下一完整日批、原07:30日报SLA、长任务心跳/租约和跨日补偿。不要再次执行contain/upgrade/report_snapshot等一次性脚本；inspect/health可复用，NTP旧apply脚本有过期SHA不可重跑。
