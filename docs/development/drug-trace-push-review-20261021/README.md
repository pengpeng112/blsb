# 部署后测试推送条件复核（2026-10-21）

**结论：服务已部署、网络已通；可以继续只读联调，当前尚不能通过应用真实上传。**
Codex / unknown，于北京时间 2026-10-21 23:29—23:31 实际登录两台服务器复核。
本轮未修改远端配置、未连接 HIS 数据库、未发送上传请求；不把健康检查冒称业务成功。
此前安装成果成立，不能再沿用“DMZ 未安装、443 不通”的历史结论。

下一AI直接执行入口：[真实接入与首张上传长任务提示词](NEXT_AI_PROMPT.md)
（FB-00—07、FB-A01—29及外部输入回执；仅为任务交付，不代表已执行）。

## 本轮现场实测

| 检查 | 结果 | 证据 |
|---|---|---|
| <prod-host> 实际发行及容器 | dpl-20261021T111000Z，6 容器 healthy；宿主发布 nginx 443，后端三端口未直映 | [target.json](evidence/target.json) |
| 药品迁移台账 | 001—011 存在，含 009/010/011；只读事务查询 | [followup.json](evidence/followup.json) |
| <prod-host>→<relay-net-host>:443 | 源地址绑定 TCP 成功；真实 worker、UID10001、严格 CA/SAN 校验和客户端证书，GET /health HTTP200 / UP | [target.json](evidence/target.json) |
| <prod-host>→<his-host>:1521 | TCP 成功；仅连接端口，不等于数据库认证或视图验收 | 同上 |
| worker Oracle 客户端 | Thick，19.32.0.0.0；密钥可读不可写；AppSecret 存在，仅记录布尔值 | 同上 |
| DMZ 服务 | 20261021T113750Z；socket/service active，service 用户 blyb-dmz | [dmz.json](evidence/dmz.json) |
| DMZ→eco.taobao.com:443 | HTTPS HEAD200，TLS 校验结果0；本次解析目标59.82.122.127，不固定为防火墙唯一IP | 同上 |
| DMZ 时钟 | NTP enabled=yes，synchronized=no；较同轮<prod-host>约慢1941秒（32分21秒），非精密校时测量 | [followup.json](evidence/followup.json) |
| 应用真实签名门禁 | 真实 RealTransport 调用 assert_signing_allowed，抛 CapabilityError；没有发网络 POST | [target.json](evidence/target.json) |

原始第一次迁移探针误用 `version` 列，退出1；第一次 NTP 探针使用旧 systemd 不支持的
`timedatectl show`，退出1。失败原证据保留；followup 改为实际 `step,name` 列及
`timedatectl status` 后均退出0。脚本已同步正确命令，不覆盖原始证据。

## 确定存在的推送阻断

1. **真实来源没有启用。** worker 的 Oracle DSN 为空；连接器 DRAFT、无 active config。
   本轮数据库元数据里 DrugTraceBill、DrugTraceBillRevision、DrugTraceCollectionRun 均为0。
   r7 两视图和生产只读绑定仍无本轮活库验收，不能仅凭端口通或已装驱动判定可采集。
2. **目的地仍是演示草稿。** `alihealth-drugtrace-retail` 为 DRAFT、`is_mock=true`、
   config_revision=null；destination ConfigRevision 数量0。平台三项字典仍 draft-1。
3. **真实签名资格未通过。** 已部署 worker 实测 `SIGNATURE_VERIFIED=false`，真实调用被拦截。
   之前独立参考签名器的真实只读查询成功，是诊断证据，不能代替应用签名资格验收。
4. **DMZ 当前明确只放行查询。** `allowed_top_methods` 只有
   `alibaba.alihealth.drugtrace.top.yljg.query.billstatus`，没有 `uploadretail`。
   `enable_inout_upload=false` 另外表示库存接口未启用，不应误当成零售专属开关。
5. **真实采集/发送证据尚未登记。** 本租户 production evidence=0、send quota=0，
   全领域 submission 总数0。证书仍为部署记录所述 TEST-ONLY；NTP 未同步。
   业务分类/整单完成/拆零、321/323正式口径、上传权限、既往是否已报、首批范围都不能假填。

因此，当前点击真实发送不能成功；改一个开关或去掉 Mock 并不足以完成联调。
已有用户测试意图授权，不需要重复索要泛化的“是否同意测试”；实际仍需要指定可核对的
单据、药房、时间窗口、既往上报情况，并如实登记系统要求的证据。

## 接续执行顺序（沿用现有任务，不重建系统）

1. **DTR-CERT-ROTATE：处理时钟、证书。** 院方给出可达 NTP 时间源；维护窗口内校时并
   验证两机偏差，替换 TEST-ONLY 链并再次验证 worker mTLS、无证书拒绝和重启保持。
   不以证书回溯签发代替 NTP。期间保持上传禁用。
2. **DTR-L2-SOURCE：完成生产取数。** DBA 审核既有
   [r7建视图](../../deployment/reporting-20261015/sql-r7/10-create-views.sql)、
   [授权](../../deployment/reporting-20261015/sql-r7/20-grants.sql)、
   [核验](../../deployment/reporting-20261015/sql-r7/30-verify.sql)。DDL 不在本次复核执行。
   后续 AI 经 sjzc 规则进行限量只读核验，确认服务名/视图 schema/主子单关联和规则，发布新
   SourceProfile/连接配置，经受限渠道绑定 worker；小窗口完整采集1张，零真实发送。
   不直接重用历史 LISTAGG+MAX 作为完整码明细，不把现有宽权限账号当成生产受限只读验收。
3. **DTR-L3-FIRSTBILL（平台资格）：** 使用已登记凭据，完成官方 SDK/适用签名向量对照，
   留下来源、版本、哈希、请求与响应脱敏证据，再按批准修订签名资格并发布发行。
   不手改布尔量制造通过。确认实际上传权限/有效期/授权模式及 bill_type、企业/药房字典。
   创建并发布真实目的地 ConfigRevision，禁止用 Mock 测试结果替代。
4. **应用链路只读联调：** 从真实 worker 经当前 mTLS 转发器调用固定只读查询，验证
   `<prod-host>→DMZ→平台` 全链响应及错误分类；不再仅用 Windows/DMZ 旁路结果验收应用。
   保留系统证据精确关联 tenant、profile、destination、config release。
5. **首张上传：** 落实 EXT-10/12：确认未由别家系统上报的完整单据、药房、日期范围，
   max_bills=1；已有聊天授权落到具体 scope，不扩大为全院/历史批量。核对所有发送必需项后，
   DMZ 白名单只增加本期 `uploadretail`，保持库存接口关闭；从应用发送1张并保留回执。
   查询最终处理结果，逐码证据不足时明确未完成码级对账；处理中/部分成功/UNKNOWN 不自动重发。
   不以 HTTP200、上传受理或单据存在作为最终成功。
6. **收尾：** 记录单据成功/失败/未知及配额消耗、可恢复停发方式；先解决异常再扩大范围。
   部署回归曾380 passed/7 skipped/2 failed，修补 fake 后局部56通过，完整回归尚未重跑；
   下次变更发行应补齐相关检查与必要全量回归，不声称目前全量全绿。

账号和 AppKey/AppSecret 已登记，不需再次在聊天里提供。供下一AI定位：
[平台凭据说明](../platform-credentials-20261020/README.md)、
[数据库统一入口](../../startup/DATABASE_ACCESS.md)、
[上一棒安装报告](../../deployment/drug-trace-install-20261021/EXECUTION-REPORT.md)。

## 证据边界及复测

实测通过：两台登录、发行/进程、迁移、源端 TCP、worker mTLS health、DMZ 出口 TLS、
真实签名拒绝、租户草稿和计数。本轮没有 SDK 对照、HIS 活库查询、业务签名查询或实际上传。
没有修改服务/时钟/证书/字典/证据/签名资格。

只读脚本：[probe_readiness.py](probe_readiness.py)。三个已执行命令退出码均0：

```powershell
python docs/development/drug-trace-push-review-20261021/probe_readiness.py target
python docs/development/drug-trace-push-review-20261021/probe_readiness.py dmz
python docs/development/drug-trace-push-review-20261021/probe_readiness.py followup
```

CAPTURED 表示取得结构化证据，不代表每个子检查通过，需逐项读 exit_code。
证据用排他创建保护，本轮文件已存在时脚本拒绝覆盖；后续新一轮应复制为新任务目录并保留旧证据。

另一个后续维护项：`apps.init_drug_trace.readiness_report` 的 SOURCE/PLATFORM 等层仍为固定
NOT_READY 文案；正式启用后须改为依据当前配置和有效证据计算，避免修好后仍显示旧缺口。
本轮判定依据直接查询的配置、计数和实际签名拒绝，不以这段静态文案作为唯一证据。
