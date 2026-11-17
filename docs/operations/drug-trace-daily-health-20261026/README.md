# 2026-10-26 早间日批只读核查

执行：Codex / model unknown，session `20261026T002857Z-b30a80fd`。核查时间08:29—08:32 GMT+8。仅查询系统状态、应用库聚合、脱敏日志和看板GET；没有重启、补跑、改配置、HIS访问/写入或额外触发平台上传/查询。

## 结论：自动触发和采集正常，整批推送部分失败

06:10:29按时启动，处理**10-25 00:00→10-26 00:00**业务窗口。08:19:16采集DONE；08:31:05驱动exit1 / PARTIAL_FAILURE。看板source=timer日报已成功入库，不是昨日manual重建记录。六容器healthy不代表日批成功。

| 项目 | 实测 |
|---|---|
| 采集 | 2059单，1352 VALID、707 WAITING、0 INVALID、0冲突；耗时约2小时9分 |
| 已上传 | 3次真实attempt，均HTTP200/ACCEPTED；其中2单已CONFIRMED，1单仍ACCEPTED |
| 新增限流/未知 | DMZ当日7次200转发，0拒绝；上传0个429，0个新增UNKNOWN |
| 驱动失败 | SEND_WORKER_TIMEOUT，第三个send job排队/执行总历时163.93秒，超过90秒等待上限 |
| 准备失败 | 截至08:31:37已有537个FAILED：050121缺企业映射530个、050116缺映射7个；worker仍在处理后续prepare，数字非最终 |
| 尚未发送 | 当时407个PREPARED，另404 prepare PENDING+1 LEASED；driver已退出，这些准备任务继续不等于会自动发送 |
| 历史查询 | 247次query API失败，错误VERSION_CONFLICT / **HTTP412**（达到12轮上限）；耗费约9分钟驱动补查时间，没有新增对应平台请求 |
| 看板 | list/detail/stats/scope均200；10-25 timer日报PARTIAL_FAILURE，sent3，send错误1，query错误247 |
| 校时 | DMZ仍选中<relay-net-host> / Normal；<prod-host> chronyd active/enabled但Reach0、Not synchronised |
| 后续定时器 | active/waiting，下一次10-27 06:10；不会自动补齐今天剩余PREPARED |

全库08:31:37：CONFIRMED411、ACCEPTED42、UNKNOWN206、PREPARED407；quota659/80000。历史409/41/206到今日新增2/1/0，不能把累计411说成今天确认411单。WAITING707=UNKNOWN业务650+PATIENT_DISPENSE质量等待57；没有擅自解释未知业务。

## 新故障的证据链

第三个发送job：

- 入队：08:20:26.281861；之前有大量drug_trace.prepare任务占用同一个worker队列。
- 真正投递attempt：08:23:09.385474。
- 返回HTTP200并结束job：08:23:10.212051。
- 驱动只等90秒，于约08:21:56把这笔记为SEND_WORKER_TIMEOUT并停止后续发送；已入队job仍执行，故3张真实受理而不是只发2张。**这不是第三笔平台网络超时或平台拒绝。**
- 驱动随后对历史247个已经12轮耗尽的单据逐个POST query，均返回412，直到08:31:05收尾。最后文件回执25223字节、stderr0，report_push.ok=true。

昨天新增的“超时即停止后续新发送”保护避免继续积压，但90秒忽略了批量prepare的排队时间；与真实单worker负载组合后，今天只发3张。需要修调度/等待策略，不能简单去掉保护或补发已受理单据。

另需明确：VERSION_CONFLICT在本项目contracts映射为HTTP412；昨天个别说明写409是口径错误，本次以实际代码和日志纠正。12轮保护未重置，UNKNOWN206未重发。

看板errors_count=1仅统计驱动send.errors，**没有包括自动prepare的537次失败**；详情query_errors_total=247可由API读取，但界面详情主要显示send错误。看板本轮成功反映PARTIAL_FAILURE，不代表错误计数已覆盖全部后台失败。

## 修复优先级

1. **P0：发送排队和90秒等待策略。** 将排队等待与已开始投递的超时分开；单worker的prepare不能长时间挤占send/query。可采用按窗口的准备收敛阶段+有限等待，或有公平性和租户隔离的任务优先级；不能只增大一个常数掩盖排队。原send job未结束时不得二次投递。
2. **P0：补查过滤。** 调用query之前排除已耗尽12轮、已有待处理query的提交，统计“轮次耗尽待人工”而非每日产生247次412；保留历史审计，不删除job/清次数绕过。
3. **P0：两药房批准字典。** 050121/050116缺drugtrace.ref_ent_id仍阻断，需要权威确认后批准新revision。来源VALID和send范围白名单不代替批准字典。不能猜测映射或篡改冻结包。
4. **P1：日报覆盖后台任务失败。** 分开记录采集等待、自动准备失败、驱动错误、平台终态及待发；按业务revision去重，避免同一错误重复加总；界面同时显示query错误及轮次耗尽，不把API受理3当CONFIRMED3。
5. **修复验证后恢复10-25窗口。** 使用原稳定单号和幂等键复用PREPARED，已CONFIRMED/ACCEPTED/UNKNOWN仍不重发；保留used659。避免仅等10-27处理10-26而遗漏今天窗口。
6. **外部仍需处理**：<prod-host>→<relay-net-host> UDP123/有状态回包/白名单，以及两个药房映射；看板证书IP SAN/管理机TLS通路仍为既有未验收项，不因服务器healthy自动完成。

建议回归用例：单worker且≥1352 prepare积压、发送任务排队>90秒但最终HTTP200，应只产生一次attempt并持续正确推进；12轮耗尽247单应0个重复query POST；缺映射单独报告、合格单继续；UNKNOWN/已受理/确认禁止重发；40/min共用预算不变；恢复同一窗口不重复采集/发送；日报与平台终态分别验证。

本次为核查，**尚未实施上述修复或补跑**。

## 命令与证据

```powershell
python docs/operations/drug-trace-daily-health-20261026/check.py app status
python docs/operations/drug-trace-daily-health-20261026/check.py app detail
python docs/operations/drug-trace-daily-health-20261026/check.py app dashboard
python docs/operations/drug-trace-daily-health-20261026/check.py dmz dmz
```

均exit0，结果见[evidence](evidence/)。应用库SELECT强制default_transaction_read_only=on，statement_timeout=15—20秒；只输出计数/时间/状态/固定药房编码，不输出患者、追溯码或凭据。源码仅读取核对，未改业务代码。本次未运行业务测试，未声称测试通过。
