# 排队超时修复与三类业务缺项说明（2026-10-26）

执行：Codex，model unknown；session `20261026T004340Z-4d9bbc61`。依据用户“那个超时的也一起看看”及持续有效的修复、部署、定时发送授权。保留原审批、单号、冻结包、配额和查询上限，没有为消除报错改成功状态。

## 41、206、815分别是什么

这些是**10-25晚核查的旧批次数量**，主要对应10-24业务窗口，不是今天新产生的同一批错误。

| 当时数量 | 含义和原因 | 应如何处理 |
|---|---|---|
| 41 ACCEPTED | 平台已受理，本地尚无最终成功确认；可能仍处理，也可能未取回最终回执 | 与平台单号/回执核对；不能直接当失败重发 |
| 206 UNKNOWN | 205次上传收到429；另1次是之前临时验证在core执行handler而缺worker专属凭据。现有证据不能认定成功或可直接重发 | 保留原attempt、单号和digest，核对平台；12轮自动查询已用尽，不能删job清次数 |
| 815缺映射 | 050121药房810张、050116药房5张缺已批准drugtrace.ref_ent_id映射，在prepare阶段失败，尚未进入正常上传 | 确认两个药房实际企业身份后发布/批准新字典revision；不是重新申请AppKey或索取同一Secret |

“来源质量VALID”“药房已在发送白名单”不等于“平台企业字典已批准”。医院AppKey/AppSecret/ref_ent_id已有受限登记，不重复索取。正在向用户确认050121和050116是否均属此前医院且共用同一平台ref_ent_id；**未收到答复前不猜测、不修改批准字典**。

今天处理10-25业务窗口，最终自动prepare有782张缺映射（050121为775、050116为7），与旧815属于不同日期快照，不能当成同一数字反复加减。有效1352=已准备570+缺映射782；570中上午已发3张且现均CONFIRMED，余567为本轮恢复对象。707质量等待未强行放行。

## 超时根因与已部署修复

原第3张send在08:20:26入队，08:23:09才实际投递，08:23:10返回200，排队/执行合计约164秒；驱动只等90秒，先终止后续发送。这是队列拥堵，不是该笔平台网络超时。[原只读证据](../drug-trace-daily-health-20261026/README.md)。

已部署发行`queue-20261026T010000Z-r2`：

1. **排队和执行分别等待。** driver跟踪API返回的原job_id和提交状态；排队最多1800秒，从LEASED/SENDING起执行最多180秒。执行时限不因重试状态变化重置。超限报SEND_QUEUE_TIMEOUT或SEND_EXECUTION_TIMEOUT，不重发、不改UNKNOWN。
2. **使用现有任务优先级。** 通过现有批准入口创建的药品send设priority50，query60，prepare默认100；已有更高优先级不被降低。仍调用原claim_job/finish_job和lease_epoch fencing，没有新增消费者或扩容worker。
3. **保留后台进展。** 每5个执行槽第5个优先给非药品send/query任务；无后台任务才回退一般队列，避免查询长时间占满prepare/其他领域。没有改变病理候选、质量、租户、owner或发送证据规则。
4. **过滤无效补查。** driver按同一tenant和destination读取真实查询job计数，达到原QUERY_MAX_POLLS=12或已有PENDING/LEASED/RETRY_WAIT的提交不再POST query；准确统计skipped_exhausted/skipped_inflight。没有重置轮次或把查询错误改NOT_FOUND。
5. **看板分开计数。** 列表新增query_errors_count/query_skipped_exhausted，展示准备/发送错误、查询错误和轮次用尽；详情显示查询错误样例及在途/耗尽数。发送列明确为“发送请求”，不冒充平台CONFIRMED。

核心业务改动只有`apps/worker/engine.py`、`drug_trace/routes.py`、`drug_trace/service.py`、`DrugTrace.vue`及宿主driver。没有新依赖、没有新schema迁移（仍012）、没有修改HIS视图/源扫描或冻结payload。现有40/min传输节奏、429冷却和不重放规则保持。

## 部署和现场恢复证据

- 原发行`ui-20261025T140000Z`；离线继承其镜像和依赖，四个运行文件与本机最终源码一致，依赖pip freeze完全一致。
- 一致性备份`<prod-path>/backups/20261026T005833Z/`；原backup→install→迁移→健康门禁退出0，六容器healthy，台账仍1—12。
- 升级前检查无PENDING/LEASED/RETRY_WAIT发送/采集job、无SENDING且旧driver已退出，然后停timer，避免打断真实发送。
- 升级日志：`/root/blyb-maintenance-backups/upgrade-queue-20261026T010000Z-r2-r1/`；driver备份`/root/blyb-maintenance-backups/20261026T010142Z-driver/`。
- 新driver SHA256=`1d1862d93ca36449fa8658b7295b0b9a857d8b902059057ee25f9a68a1cd8c46`；unit仍`4bb3cb60258a88ac1cf290895b9f5d5674ac1d0994221077ac63006d6d396b06`，日报开关1，printf PUBLIC_DUMMY有效。
- 生产只读预检：原247个旧非终态全部skipped_exhausted，可自动补查0；新worker有公平槽。原quota659/80000，r10断点row_version46。
- **09:01:52通过原daily-upload.service恢复10-25窗口**，复用已有DONE采集及567 PREPARED；前三张确认成功跳过，没有重新访问HIS或另建采集运行。
- **09:09:14实测：新增71次已完成HTTP200/ACCEPTED，其中65个已CONFIRMED；剩496 PREPARED。** 此时全库477 CONFIRMED/47 ACCEPTED/206 UNKNOWN，used730；旧41+206行状态/版本摘要不变，采集断点仍46。
- DMZ截至09:10:31，当日325次200转发、0拒绝。观察口径包含上午原批次及恢复，不等于325张单据。
- 新页面/JS/CSS及看板GET返回200。旧失败日报仍显示PARTIAL_FAILURE，当前恢复回执在收尾时更新；本机TLS/正式证书问题仍按既有外部项记录，未声称浏览器渲染验收通过。

恢复任务**仍在运行**，以上仅是观察时点，不能说567张全部完成。旧206 UNKNOWN未重发，旧41受理未强改成功；两药房映射未动，因此782张仍可能使本次运行以PARTIAL_FAILURE结束，即使其他合格单都成功。

只读观察器`blyb-queue-observe-20261026.service`已启用，每分钟写`/var/log/blyb/queue-recovery-20261026/latest.json`和progress.jsonl；任务退出后写final.json，最长4小时后写observer-timeout.json。观察器不查询HIS/平台、不排job、不重试/发送、不写业务数据库；在途attempt的初始UNKNOWN不计作已结束失败。

回执：`/var/log/blyb/daily-upload/20261026-090152.json`；原06:10失败回执保留。正常timer已恢复，下一次10-27 06:10处理10-26。不要另起第二个driver/worker或在当前运行时手工补发。

## 验证

- 最终局部47 passed：`implementation/var/queue-repair-local-final4.xml`。包含1352 prepare积压时send/query优先、每5槽后台推进、排队164秒只等原job、区分队列/执行超时、UNKNOWN不重发、247耗尽过滤及隔离真实PG上的查询SQL。
- **最终全量482 passed / 7 skipped / 0 failed**，命令`python -m pytest tests -q -ra --junitxml=var/queue-repair-regression-final.xml`，exit0，627.21秒。跳过项为未授权真实源/专项大文件/Windows FD等原条件，不称这些专项通过。
- npm同源构建通过；离线镜像原依赖不变，镜像/发行源码和安装driver哈希复核一致。
- 初轮新增测试fixture曾缺幂等键、误用PG元组和污染共享迁移库，已修数据并用独立测试数据库隔离；保留原失败XML，没有删关键断言/xfail。一次过时全量验证在测试fixture修订时中止（-1），不能当作通过；以上最终全量为修订后的单次绿。
- 本机Docker daemon起初未启动，已启动既有Docker服务/Desktop后执行PG/镜像验证；未联网安装依赖。

## 剩余事项与下一棒

1. 看观察器final.json/本次回执/日报和真实终态，确认恢复最终收尾；若仍RUNNING只读观察，不能把0字节活动回执当崩溃，也不能把worker healthy当日批完成。
2. 确认两药房企业归属后，通过原字典API新建并批准revision，先验证一个未提交单的prepare，再按原范围恢复缺映射单；不原地改批准版或重发UNKNOWN。
3. 旧41/206已耗尽12轮，需要平台单号结果核对或另行设计有审计和上限的人工复查；本轮仅避免无效重复查询，未绕过上限解决它们。
4. **新增发现、未修复的看板明细边界**：submission-outcomes先取最新200条再在Python筛only=problem；新日有大量PREPARED时，旧UNKNOWN206可能不在前200条，问题列表会为空。聚合统计仍正确，列表0不等于无异常。后续应SQL先过滤后limit，保留LEFT JOIN无attempt/租户约束，并测“200条新正常+较旧异常”反例，不能仅提高limit掩盖。
5. <prod-host>→<relay-net-host> UDP123/回包/白名单仍待网络；DMZ NTP Normal。管理入口证书IP SAN/本机TLS仍待院方处理。原07:30日报SLA、长collect心跳/租约和跨日补偿仍未全部治理。

回退前停timer/等在途发送结束，按原rollback.sh回到ui发行并恢复旧driver。不要回滚/清空已计配额和新回执；旧driver仍有90秒缺陷，不能恢复后直接无人值守批量运行。候选`queue-20261026T010000Z`仅传输未激活，r2为实际运行版本。

复用只读命令：

```powershell
python docs/operations/drug-trace-queue-repair-20261026/check.py app observe
python docs/operations/drug-trace-queue-repair-20261026/check.py app status
python docs/operations/drug-trace-queue-repair-20261026/check.py app health
```

部署、install_driver、recover脚本是一次性有SHA/日期/运行状态基线的操作，不重复执行。凭据只从原受限通道取用，未复制到报告。
