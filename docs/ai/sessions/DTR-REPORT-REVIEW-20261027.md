# DTR-REPORT-REVIEW-20261027 · 上报现状详细核查

Codex/model unknown，session20261027T011425Z-5606621a。用户要求核查数据和详细问题；本轮只读分析，不擅自修复/补发。[完整报告与后续执行顺序](../../operations/drug-trace-report-review-20261027/README.md)。登记范围仅任务运维目录、共享AI记录、catalog和业务README，无业务源码变更、无并行代理。

现场命令：任务check.py app status退出0、app detail两次退出0、app progress退出0、dmz dmz退出0。读取应用PG（只读/statement_timeout）、系统/DMZ日志、NTP和源码hash。没有HIS或平台新请求、采集重跑、补发、业务写入、服务重启/配置修改。没有新应用测试，仅本机编译/统计/hash/链接校验，结果见validation.json。

核心证据：今日10-26窗06:10:29启动、09:10:31 collect_timeout/exit3、FAILED日报已入库；09:19后台采集仍RUNNING/3000张、1773VALID、1227WAITING，新增提交/尝试0，断点76游标16:39。979原始1/0住院+3原始0/2退药仍UNKNOWN分支，245正常门诊缺码；1227均无码明细。未读HIS，缺码不等同源库未扫码，业务扩展须先确认原状态/完成/方向。

三条主因已读现场同SHA源码确认：驱动固定等整窗3h后退出且不会自动恢复失败日；单worker同步长collect挡住outbox/其他job、心跳与租约未续，health异常但进程仍动；Oracle每fetch新建/关闭连接、逐单明细与版本复核，性能需逐段计时，不猜Oracle全表扫描。采集结束可能prepare，但PACKAGE_FROZEN不会自动send。明天只处理10-27，不自动补10-26，后续恢复必须显式日期并复用现run/断点。

其余：原41在10-26已查平台3/51但本地未回写；205仍UNKNOWN未发、246均耗尽12代，不直接重传或清预算。CONFIRMED下8077码项PENDING为本地码级同步/对账边界，不能当平台未收。今日3000应有码数全NULL，VALID不代表独立核全量码。FAILED日报collection空/candidate0且没带fatal原因，UI会误导。正式证书/浏览器和分页等旧待办未重新验收。

后续优先：保护现场与原失败回执，采集收尾后按既有scope受控恢复10-26；改分段调度/进度超时/恢复账本；Oracle连接复用和慢SQL证据；独立heartbeat/带epoch续租和失租停止；源快照缺码/住院退药/可靠补码来源；旧41/205处置；日报和码级闭环。具体验收表见README。不要因本次review completed推断日批完成，也不要重跑部署或发送一次性脚本。
