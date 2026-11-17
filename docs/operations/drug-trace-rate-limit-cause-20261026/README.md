# 追溯上报限流原因核查

2026-10-26，Codex/model unknown。本轮仅分析、只读核验，不改服务、限额或代码，不向追溯平台新增请求，不访问HIS。

## 结论：发生过两种不同层级的限流

| 现象 | 来源 | 已查实原因 |
|---|---|---|
| 10-25上传/查询HTTP429 | <relay-net-host>院内DMZ转发器 | 同一出口IP请求密度超过本地令牌桶：持续60次/分钟、突发容量10 |
| 10-26查询HTTP200，但JSON内code7 | 淘宝开放平台query.billstatus接口 | App Call Limited，sub_code=accesscontrol.limited-by-api-access-count；官方通用定义为接口所有应用调用总频率限制 |

不能把两者合并成“网络不通”，也不能仅凭App Call Limited文案判断“医院AppKey每日额度用完”。

## 一、最初429：证据足以定位到本地前置机

本轮实际读取DMZ外置forwarder.json的限速字段，rate_per_minute=60、rate_burst=10。实现为每来源IP一个令牌桶，按每秒1个令牌补充、容量10；不是严格整分钟计数。请求在_forward()之前因rate_limited被拒，日志是reject status429，而不是上游返回后forward status429。

固定日志窗口：2026-10-25 20:28:00—21:31:11北京时间（12:28—13:31:11 UTC）：

- 转发HTTP200：3224次；本地reject429/rate_limited：3679次；合计6903次。
- 峰值194次/分钟（北京时间21:17），另几分钟192/188/187/186；峰值14次/秒。
- 这些日志均来自DMZ看到的同一来源IP<relay-net-host>。该地址是当前链路的出口标识；本轮没有拿到NAT映射和其他系统流量，**不能认定6903次全是<prod-host>单独产生，也不能认定有其他系统抢额度**。
- 代码按来源IP限流，因此若同一NAT地址确实承载其他调用，它们会共用这个60/min预算；这是需网络科核实的可能因素。

应用账本核验：旧247笔共2964个drug_trace.query_status任务（247×12）。旧查询回执中2462次UNKNOWN/http429、460次NOT_FOUND、1次PROCESSING；job数、回执数与实际HTTP请求数不是同一单位，不能简单相加。

一次任务可能包含业务日期与上传日期两个窗口，每个窗口还可能分页；旧版本transport.post没有请求节奏控制，查询任务和上传共用DMZ入口。因此“655笔单据”不会只产生655次HTTP请求。持续/重复轮询放大流量，再遇429继续查询未知单据，造成更多限流并耗尽查询预算。这是请求调度设计缺口，有账本和代码证据，不能归因于数据库视图或服务器规格。

当时205笔上传HTTP429、1笔维护时执行环境缺文件形成UNKNOWN；后者不是限流。3679是该观察窗全部类型请求的拒绝次数，不能称3679张药品单据失败。

## 二、现在code7：更准确的含义是接口总频率限制

实际保存的平台响应：

```json
{"error_response":{"code":7,"msg":"App Call Limited","sub_code":"accesscontrol.limited-by-api-access-count"}}
```

根据[淘宝开放平台官方《App Call Limited问题》](https://developer.alibaba.com/docs/doc.htm?articleId=108869&docType=1)（页面更新2021-11-26，本次2026-10-26重新读取），需区分三种子码：

| 子码 | 官方通用解释 |
|---|---|
| accesscontrol.limited-by-app-access-count | 单个AppKey的流量限制 |
| accesscontrol.limited-by-api-access-count | 某API全部应用的总调用频率限制，按秒或分钟治理 |
| accesscontrol.limited-by-app-api-access-count | 未上线应用调用单个接口的频率限制 |

本次是第二种。**此前笼统建议核查AppKey调用额度，是待确认方向，不是已经证明医院日配额耗尽；按精确子码应优先让平台确认这个查询API的总流控策略。** 药品业务是否另有覆盖策略、具体阈值、封禁剩余时间仍须业务方确认，不能把通用文档中的其他API示例额度套到医疗接口。

实际接口alibaba.alihealth.drugtrace.top.yljg.query.billstatus；冻结配置元数据确认AppKey35085469。15:37:22及15:39:09北京时间发生该子码；后一次已降至4.5秒请求间隔仍被拒。18:12用户要求继续后，单笔查得NOT_FOUND并恢复有效响应，18:18完成最后62笔。只证明该时点恢复，不证明固定每小时/每日何时重置。

DMZ在人工核查固定观察窗10-26 15:15—18:19（07:15—10:19 UTC）记录266次forward HTTP200、无本地reject429；峰值19次/分钟、1次/秒。code7就在这些HTTP200响应的业务JSON内，因此与本地60/min令牌桶是两层问题。峰值19/min仍受上游限制，也不能凭此推算上游阈值为19，因为官方通用定义是全部应用共享。

## 三、已经解决与仍存在的程序缺口

已上线的实际worker传输源码确认：每DMZ host+port进程内共用1.5秒间隔（40次/分钟）；上传和每次查询/分页共享；HTTP429使后续操作冷却60秒。这是10-25已完成修复，本轮未更改。

修复后固定观察窗10-25 21:43—21:53:41北京时间，本轮读取223次forward200、0本地429，峰值40/min。旧报告226次是其当时不同结束时点的统计，不原地更改历史证据。这个窗口支持本地节奏控制有效，不表示从此平台永远不会限流。

本轮源码与现场哈希核验的缺口：

1. transport只检查HTTP429触发冷却，不识别HTTP200中的TOP7/子码。业务服务会将其列为QUERY_ERROR并退避，但transport没有立即给同进程其他查询建立同等冷却屏障。
2. parse_query_response对error_response只保留code/msg，未保留sub_code/sub_msg/平台请求标识；生产回执不便区分应用限额、接口总限额及禁用剩余秒数。此次人工探针直接捕获子码才明确区分。
3. QUERY_ERROR目前统一120秒后按单据再次入队，最多12轮。许多单据遇到同一上游限流时，单独按单据重试可能形成一批同步恢复请求；缺少按AppKey/API共享的退避状态。
4. 当前40/min预算在进程内共享；另开的维护脚本/另一个worker进程不共享此内存状态。不能把“一台机器”误认为“一个共享预算”；DMZ的per-IP硬限仍是兜底。
5. DMZ本地429未携带Retry-After头，客户端使用固定60秒冷却；后续可明确返回建议等待值。

## 四、建议的后续整改范围（本轮未实施）

- 保留并脱敏记录TOP code/sub_code/sub_msg/request_id，将HTTP429与TOP7都识别为限流；按子码区分本地/应用/接口总限，页面显示明确原因。
- 上传和查询共享本地出口预算；查询另按AppKey+method维护跨进程冷却/去重。收到平台给定禁用秒数时尊重等待值，缺失时采用有上限的指数退避和随机错峰。具体速率需结合官方业务阈值调整，4.5秒仅是人工核查时使用过的节奏，不是永久可用保证。
- 减少无效查询：新受理先延迟；确认终态停止；合并确实相交的日期窗口、缓存同一轮结果；轮询耗尽走有独立证据和上限的人工复核入口，保留原12轮账本。不得删job重置计数或盲重发UNKNOWN。
- 对平台提工单，附AppKey/API/时间/子码/request_id，优先确认接口总频率和禁用恢复策略，再核实是否还有AppKey限制及上线状态。不得仅以“给医院多买日额度”作为已确定解决方案。
- 继续保留DMZ安全限速；仅增大60/min不能解决上游流控，可能放大问题。真实上报恢复需继续遵守原批准范围及幂等。

验收应包含：本地429和HTTP200/TOP7分别注入、跨单据/跨进程共享冷却、Retry-After/平台等待秒数、终态不再查、限流不自动重放上传，以及实际指标不混淆HTTP200与业务成功。只修文案或增加sleep不足以完整验收。

## 证据和实际操作

本轮证据：[DMZ当前配置及日志聚合](evidence/dmz-dmz-20261026T122705150814Z.json)、[应用账本/现场源码](evidence/app-app-20261026T122703898000Z.json)。上游错误原件见[上一轮核查](../drug-trace-unknown-check-20261026/README.md)。

命令（既有依赖、固定主机指纹、凭据只在既有受限通道内存使用）：

```powershell
$env:PYTHONUTF8='1'
python docs/operations/drug-trace-rate-limit-cause-20261026/check.py dmz dmz
python docs/operations/drug-trace-rate-limit-cause-20261026/check.py app app
```

两通道exit0；journalctl内部exit0，PostgreSQL以default_transaction_read_only=on、statement_timeout=15000执行SELECT聚合。没有请求或日志正文导出，不含患者、追溯码或Secret。没有执行新平台请求/HIS查询/发送/应用变更，因此未跑应用测试或声称新功能通过。

目前已核实业务结论仍为41笔平台确认成功、206笔同身份/单号范围NOT_FOUND；本地未回写、未补报。该项与限流原因分析分开处理。
