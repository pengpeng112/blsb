# 药品追溯上传与限流处理改进

任务DTR-UPLOAD-FLOW-20261026，Codex/model unknown。用户要求按接口要求改进上传。2026-10-26 21:32复验完成：内网服务和DMZ均已上线flow-20261026T132000Z，原定时任务已恢复；新流程首个完整日批仍待实际运行观察。

## 接口口径

本次重新尝试读取语雀指南，网页读取失败；使用工作区已有官方2026.01指南快照逐项对照，并成功读取[开放平台限流官方说明](https://developer.alibaba.com/docs/doc.htm?articleId=108869&docType=1)。原始快照：[医疗机构接口对接指南](../../development/drug-trace-firstbill-exec-20261021/reference/official-guide-202601.md)。不声称取得语雀新的修订版本。

指南明确要求状态查询控制频率，失败隔几分钟再查；上传返回受理后须查处理状态，3/51全部成功、3/52部分成功不能混同。平台没有在该指南中提供当前医院可用的固定QPS，本次1.5秒/4.5秒是本地保守策略，不冒充平台允许上限。

现场只读聚合核验：2823个冻结提交均零售profile、bill_type=321、query_bill_type=A；因此使用uploadretail匹配医院患者发药业务，未改成uploadinoutbill，未将上传bill_type当查询选择器。已批准字典、历史payload、view r10、ref_ent_id和权限/scope不改。

## 实际改进

1. **两层限流统一识别**：HTTP429，以及HTTP200内TOP error_response code7；保留脱敏code/msg/sub_code/sub_msg/request_id和建议等待时间，不把HTTP200当业务成功。
2. **尊重等待值**：读取Retry-After秒数或HTTP日期，以及平台“This ban will last for … more seconds”；平台明确等待值不被本地退避上限缩短。缺省TOP7从180秒指数退避、最高1800秒，加正向随机错峰；HTTP429缺省60秒。
3. **多进程共享**：生产worker显式启用/data/exchange/drugtrace-rate（沿用现有exchange挂载）。文件原子替换+短文件锁；只存身份哈希、时间戳和计数，无payload/签名/Secret。所有网络调用共享出口1.5秒节奏，查询另设4.5秒节奏；API/应用/应用×API按子码安装共享冷却。别的维护进程加载同一生产env时也共享，服务重启后保留。
4. **先等待，再发送**：已知冷却在发送额度预留及DeliveryAttempt前预检。不会让每笔待发单在冷却中各占一次额度。若其他进程恰在预检后设置冷却，明确未发出请求的尝试保留为BLOCKED_LOCAL并回PREPARED，已保守计入的额度不抹除；未知网络结果仍UNKNOWN，不重传。
5. **任务队列延后**：RequestDeferred只表示本次没有发出请求；原租约经epoch/state条件更新后转RETRY_WAIT，退还本次领取增加的attempt，不创建新查询代次，不删除原12轮账本。1天没有进展显式失败人工处理，避免无限挂起。
6. **减少查询**：真实受理后首查延迟120秒；真实UNKNOWN首查300秒。后续查询按状态基础退避指数增加并错峰，平台等待值优先。相交/相同的业务与上传日期窗取并集，覆盖不变；不相交的历史与上传窗口仍分别查。终态查询job跳过，不联系平台。
7. **定时驱动适配**：原send job明确PREPARED+RETRY_WAIT+PLATFORM_COOLDOWN时，重置执行阶段180秒计时、保留最初30分钟排队总上限；原job复用，不再POST发送。其他SENDING/普通重试不重置执行时限，避免用“冷却”掩盖真的超时。
8. **前置机提示**：本地429增加Retry-After:60；保持per-IP 60/min、burst10及HTTPS/mTLS/白名单。上游API总限流不能靠增大本地限额消除。

保持规则：真实上传一次POST一次结果，不自动重放；UNKNOWN先查，确认/受理不重发；授权/租户/owner/配置冻结闭包/配额及CAS不放松。Mock仍明确隔离。未开启HTTP80或后端800x宿主直映。

## 实现位置与恢复基线

基线位于baselines/，覆盖本轮修改前10个既有源码/配置文件及宿主driver。仅新增rate_control.py及测试，复用原架构和依赖。

- implementation/plugins/destinations/alihealth_drugtrace/：共享限速、HTTP传输、协议解析、SDK客户端回传rate_limit。
- implementation/plugins/domains/drug_trace/service.py、plugin.py：发送前预检、延迟查询/退避/窗口合并、终态跳过。
- implementation/packages/connector_sdk/__init__.py：RequestDeferred信号。
- implementation/packages/workflow_core/jobs.py、apps/worker/engine.py：带fencing的同job延后，其他业务正常执行。
- implementation/deploy/compose/drug-trace.override.yml：显式共享目录env；现有volume提供持久化。
- implementation/deploy/dmz/forwarder.py：仅增加本地429等待响应头。
- docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py：宿主驱动等待状态适配，真实通道仍原unit/stdin。

本轮没有业务库DDL和编号迁移；新增JSON为版本1的运维节流状态，不是业务账本。文件损坏/不可用时失败关闭并将job延后，不能自动删文件绕过冷却。跨主机/不同挂载目录不共享；当前生产仍只有一个worker，未授权扩容。共享目录随现有exchange备份保留；回退旧版本忽略新运维文件，不恢复旧业务数据库覆盖回执。

## 本地验证

使用已有依赖、Git Bash/openssl与本机隔离PostgreSQL。零医院/平台真实数据，TLS测试仅本机合成上游。

```powershell
$env:PYTHONUTF8='1'
$env:PATH='D:\Program Files\Git\usr\bin;D:\Program Files\Git\bin;'+$env:PATH
# implementation目录
python -m pytest tests/integration/test_drug_trace_upload_flow.py tests/integration/test_drug_trace_transport_pacing.py tests/integration/test_drug_trace_fix3.py tests/integration/test_dmz_forwarder.py -q -ra
python -m pytest tests/integration/test_drug_trace_daily_driver_repair.py tests/integration/test_drug_trace_api.py tests/integration/test_drug_trace_upload_flow.py -q -ra
python -m pytest tests/integration/test_drug_trace_flow_pg.py -q -ra
python -m pytest tests -q -ra --junitxml=var/upload-flow-regression.xml
```

最终局部证据：upload-flow-local-2.xml **69通过/1跳过**（Windows FD布局），upload-flow-api-driver.xml **54通过**，upload-flow-pg.xml **1通过**。新专项包括真实双进程锁/节奏、跨实例冷却、等待值不缩短、存储失败后不重放已经发生的上传、发送预检不花额度、SQLite与实际PG过期租约fencing及同job延后、真实TLS转发、终态不查询、重叠/分离日期范围。

早期测试纠正：子进程缺_bootstrap导入、SUPERSEDED是既有字符串而非SubmissionState属性；均修正后保留断言复验。初次Git路径填错导致TLS测试跳过，找到既有D盘Git后完整执行；失败/跳过XML保留。部署生成器换行转义在本机compile阶段被阻断，修正后才可远程执行；没有用失败脚本部署。

全量实际结果：**522通过/7跳过/0失败/0错误，退出0**，529项，耗时约674秒。证据：[XML](../../../implementation/var/upload-flow-regression.xml)、[日志](../../../implementation/var/upload-flow-regression.log)。7跳过为：>2GB PG专用环境、live-stack整栈、Windows FD布局、SQLServer/Oracle真实DSN、pyodbc已安装的反向分支、50GB专门压力窗口。隔离PG租约测试另有1通过，不把它等同>2GB压测。

## 发布计划及验收边界

离线发行flow-20261026T132000Z，基于ui-clarity-20261026T063000Z，manifest严格限定本轮11个源码/部署文件、pip freeze一致；不联网安装。源码与冻结包逐文件核对相同，已有白名单排除var/SQLite/Secret。宿主driver作为受SHA保护的独立运维文件，绑定信息在evidence/deployment-bindings.json。

上线前再次确认原driver停止、SENDING/在途/待执行job均0，暂停timer→一致性备份→标准upgrade.sh→安装已校验driver→恢复timer。DMZ以旧发行复制成新的不可变发行，仅替换已审阅forwarder，check-config通过后原子切current/restart，失败切回旧发行。证书、白名单、上游地址不变。

生产验收：六容器健康、timer恢复、共享目录可写且权限受限；临时独立目录内合成TOP7只发一次且第二次不触网；已确认历史单仅做一次真实只读状态查询验证正式链路，平台若仍限流则保留外部结果，不能造成功。升级前后旧247摘要、额度2823、断点46应不变。本轮不直接调用HIS采集，不自动回写41或重发206。

完整次日日批的耗时、退避后的实际收尾及07:30日报SLA尚须真实运行观察。平台总频率/禁用阈值由平台决定，本地改进不能保证不再出现任何限流；遇限流会保留状态并排队等待，不记作成功。

## 实际部署与核验

内网升级21:25:41开始、21:27:36完成，标准upgrade.sh退出0；一致性备份`<prod-path>/backups/20261026T132541Z/`。当前`/opt/blyb/current`指向flow发行，六容器healthy，timer为active/waiting，迁移仍1—13，无本轮DDL。宿主driver SHA绑定见[deployment-bindings.json](evidence/deployment-bindings.json)，旧driver保存在`/root/blyb-maintenance-backups/upgrade-flow-20261026T132000Z/daily-upload.before.py`。

DMZ初次保护检查后因维护父目录不存在而退出，未创建候选发行、未切换服务；只读检查证实后创建指定维护目录，21:31:41升级成功。新current为`/opt/blyb-dmz/releases/flow-20261026T132000Z`；原发行20261021T113750Z保留，check-config退出0、service active。没有改证书/时钟/配置或上游限额。

| 核验 | 真实结果 | 证据 |
| --- | --- | --- |
| 内网升级 | exit0，driver已更新，timer恢复 | [升级状态](evidence/app-upgrade_status-20261026T132807133238Z.json) |
| 生产临时合成TOP7 | 一次假网络调用后第二次被阻挡；不触真实平台 | [运行时验收](evidence/app-smoke-20261026T132831416036Z.json) |
| 正式已成功单只读查询 | CONFIRMED，result_type3/sub_process_flag51，1页1窗 | 同上 |
| 共享目录 | 实际创建，mode0700，限流文件持久化 | 同上 |
| DMZ上线 | 退出0，实际源码SHA一致，active | [升级结果](evidence/dmz-dmz_upgrade-20261026T133141960850Z.json) |
| 内网→新DMZ | 实际mTLS GET /health=200；上游调用0 | [健康检查](evidence/app-health-20261026T133210584588Z.json) |
| 最后内网复验 | 六healthy、原授权APPROVED、UI/资产200、SHA一致、timer等待；旧247/配额/断点一致 | [最终复验](evidence/app-verify-20261026T133227730603Z.json) |

证据链接以本目录实际文件名为准；自动整理时逐项匹配真实采集时间，不重写证据内容。本轮真实新上传0、HIS查询0、业务账本写入0（升级运维记录/共享限速文件除外）。正式只读查询验证当前客户端、mTLS转发、签名、平台状态响应链路；不等同已经实测新版本上传一张新单。

21:36最终逐文件复核：[生产源码与driver哈希](evidence/app-final-20261026T133631443631Z.json)全部一致；真实本地账本仍CONFIRMED2576、ACCEPTED41、UNKNOWN206。[文档校验](evidence/document-validation.json)核对23个本轮链接零缺失、catalog登记唯一、全量XML计数一致，退出0。

## 后续执行与恢复

驱动发布器用read_text读取后在Linux写入，因此绑定SHA对应LF版本；本机原文件有CRLF，直接Get-FileHash不相同，规范化读取后SHA为cf967102e68c4978cc17ba4739af6860de60344404727f6cc8eedf1b4a851c39，与线上一致。首次收尾校验误用了本地原始字节而断言失败，确认换行差异后修正核验，未再部署或改业务逻辑。

原timer继续按既有批准范围采集并发送，无需再启用一个定时任务。下一轮先看`systemctl status blyb-daily-upload.timer blyb-daily-upload.service`、`/var/log/blyb/daily-upload/`日报和看板；核对候选数、已发送/受理/确认、QUERY_ERROR、平台等待秒数、待处理job和耗时。遇TOP7应等待，不能启动另一个人工补发脚本绕过共享状态。每日超过30min排队或1天无进展会明确失败，应按证据核查，而非放任无限等待。

已核实的旧41平台成功仍需带原查询证据受控回写；旧206 NOT_FOUND先核人工历史和冻结身份，制定批准恢复单再发，不自动清UNKNOWN/重置12轮。管理电脑正式IP SAN证书、真实浏览器验收、明细分页等原待办仍按原记录处理。

回退须先暂停timer、等在途请求结束并保存新回执。内网用现有发行回退流程恢复ui-clarity-20261026T063000Z，且**同时**把上述daily-upload.before.py恢复到`/opt/blyb/tools/blyb-daily-upload.py`，核验旧SHA，再恢复timer；DMZ原子切回20261021T113750Z、restart该service并验证mTLS健康。采用现有维护规范操作，不直接在有在途请求时切换。业务库不恢复旧备份覆盖新回执；本轮只新增运维JSON，旧版本忽略，不手工删除冷却文件冒充恢复。
