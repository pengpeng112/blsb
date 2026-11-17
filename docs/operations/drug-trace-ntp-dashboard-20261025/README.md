# 2026-10-25 定时修复、NTP与看板部署实录

执行：Codex，model=unknown；session `20261025T132833Z-14376269`。用户已授权修复、持续校时和看板部署。本文是现场结果，不能把软件部署成功当成全天上报成功。

## 实际结果

| 项目 | 结果 |
|---|---|
| <prod-host> 看板/API | 已部署 `ui-20261025T140000Z`；迁移012已登记，六个运行服务healthy |
| 页面和静态资源 | 服务器端用现有证书CN及显式信任验证：`/ui/`、两个JS/CSS资源均200 |
| 看板查询 | daily-reports、upload-stats、send-scope、submission-outcomes均200；日报单日详情另见最终验收 |
| 日报写入 | 通过原API补录10-24的**manual / PARTIAL_FAILURE**记录，明确由运行库重建；不是原timer完整成功回执 |
| 定时器 | 已恢复enabled/active，下一次2026-10-26 06:10 Asia/Shanghai；处理10-25窗口；日报开关已改1 |
| 限流 | 真传输按DMZ host+port共用1.5秒请求间隔（40次/分）；上传、每个查询窗口/分页共用；429使后续请求冷却60秒，不重放失败POST |
| 限流现场复核 | DMZ日志：修复前12:28—13:31 UTC有3679次429；修复后13:43—13:53 UTC有226次200转发、0次拒绝。只说明已观察窗口 |
| DMZ NTP | `<relay-net-host> → <relay-net-host>:123/UDP`成功；chronyd active+enabled，选中`^*`，Leap Normal，stratum2 |
| <prod-host> NTP | chronyd active+enabled；配置`server <relay-net-host> iburst minpoll 6 maxpoll 8`，每64—256秒持续轮询；**仍未同步**，UDP无响应 |
| 本机浏览器 | TCP443能建连，TLS握手EOF；Chromium同样断开。没有生产页面渲染截图，不称浏览器验收通过 |

证据均为本目录[evidence](evidence/)的脱敏JSON。NTP最终复验另见[app](../../deployment/drug-trace-ntp-20261023/evidence/app-verify-20261025T135224049154Z.json)、[DMZ](../../deployment/drug-trace-ntp-20261023/evidence/dmz-verify-20261025T135233239013Z.json)。

## 当前业务账实

采集已DONE：发现2414单，创建2363 revision，幂等51；VALID1470，WAITING944，INVALID0。VALID仅表示来源质量合格，**不表示企业字典等发送条件齐全**。

- 全库提交：CONFIRMED409、ACCEPTED41、UNKNOWN206；配额used656/80000。CONFIRMED不是码级逐码确认。
- 10-24窗口：CONFIRMED408、ACCEPTED41、UNKNOWN206，共655个提交；另815张VALID单因准备失败没有提交。因此655+815=1470，不能报告1470张已报成功。
- 815个prepare失败：药房050121有810个、050116有5个，缺已批准`drugtrace.ref_ent_id`映射。白名单允许5个药房，不等于5个药房都已完成字典映射。没有猜值、扩充批准字典或修改其历史revision。
- UNKNOWN206：205次上传HTTP429，另1次为上一轮临时验证在core执行handler而缺worker专用凭据；该次已查NOT_FOUND并保留。原失败与责任说明见[上一轮](../drug-trace-timer-repair-20261025/README.md)。本轮没有重发、清额度或改UNKNOWN为成功。
- 最终22:01实查：ACCEPTED41和UNKNOWN206各自均已用完**12轮**查询预算，当前无PENDING/LEASED查询job，自动查询已排空。这247个非终态须进一步核对平台结果；不能把查询任务SUCCEEDED当作平台确认或称仍在排队查询。原query API达到上限会409，**不要删job或重置计数绕过**。
- 本轮仅恢复worker的已有查询；未新增手动上传，未直连HIS，也未写入HIS。下一次完整06:10日批及跨日收尾尚未实测。

人工重建回执：`/var/log/blyb/daily-upload/20261025-maintenance-reconstruction.json`，source=manual；发送654取该窗口维护前实际delivery_attempt计数，**不是654张成功**。错误1021=815准备失败+206UNKNOWN，API保存50条样例和准确总数，完整分类见运行库/本报告。query.queried=0配`counter_available=false`表示原驱动计数不可恢复，不表示没有平台查询；耗时12238秒由驱动启动和暂停时间近似重建。原0字节中断文件保留，未伪造原timer回执。

## 修改与部署边界

1. 用户请求后先停timer及唯一`/tmp/daily-upload.py`进程，再停worker控制限流；原审批、来源断点、冻结包和配额不动。worker停止最终为137，当时无发送job；查询租约由现有机制恢复。
2. `transport.py`新增进程内共享RequestPacer，使用monotonic及线程锁；当前compose明确只有一个worker。多进程不会共用此内存预算，DMZ仍有每IP60/min强制限流，禁止另开临时发送/查询消费者或scale worker。
3. 驱动新增：发送后UNKNOWN或90秒仍PREPARED/SENDING即中止后续新发送；REJECTED/PARTIAL计失败，避免API入队200就把该批标OK。日报超过50条错误时上传样例+errors_total；文件回执保留完整错误。GET列表按准确总数展示，详情提示截取范围。小回执结构保持原兼容。
4. 离线继承现场原镜像及全部依赖，白名单构建同源前端；无pip/npm安装、无新依赖。发行manifest列出的源码变化只有DrugTrace.vue、transport.py、drug_trace/models.py、plugin.py、routes.py；r10视图、源扫描、冻结业务payload未改。
5. 通过原`backup.sh → install.sh → migrations → health gates`升级。宿主驱动单独按SHA基线安装；有效systemd printf用PUBLIC_DUMMY验证，日报开关1仅在迁移012存在后生效。

发行：`/opt/blyb/releases/ui-20261025T140000Z`；`/opt/blyb/current`指向该目录。构建脚本[build_release.py](build_release.py)，传输[deploy.py](deploy.py)。旧`ui-20261025T134500Z`仅候选未激活；更早134000候选构建因空profiles目录失败，未上线。首次升级因Windows打包shell无执行位在备份前退出126；归一化shell权限后r2成功，原失败日志保留。

恢复点：

- 一致性备份：`<prod-path>/backups/20261025T134213Z/`，含PG、exchange和受限配置；默认未加`--with-objects`，本轮不改对象内容，按原默认升级备份流程执行。
- 维护前unit/driver/chrony：`/root/blyb-maintenance-backups/20261025T133009Z/`。
- 安装新驱动前备份：`/root/blyb-maintenance-backups/20261025T134707Z-driver/`。
- 升级日志及退出码：`/root/blyb-maintenance-backups/upgrade-ui-20261025T140000Z-r2/`。
- 旧发行：`src-20261022T103000Z`。012只加表；回退应用不需删日报表。

**回退注意**：旧镜像没有本轮请求限速，不能直接恢复worker后任其大量查询。先停timer/worker，再按`deploy/linux/rollback.sh src-20261022T103000Z`回退；必要时对旧版单独补同等限速。旧unit日报开关0，不能用于新看板日报验收。恢复数据库前必须评估升级后新增查询回执，不覆盖新证据。

## 必须协调的剩余事项

### NTP：以下内容可直接转网络科

| 源 | 目的 | 协议/目的端口 | 返回流量 |
|---|---|---|---|
| <prod-host>，客户端动态UDP源端口 | <relay-net-host> | UDP 123 | <relay-net-host>:123返回到原客户端源端口，允许有状态回包 |
| <relay-net-host>，现已正常 | <relay-net-host> | UDP 123 | 同上，保持现有策略 |

不是TCP123。若策略只允许123→123，也已做固定源123实测，仍无响应；不能认定仅修改acquisitionport即可解决。请同时检查跨区ACL、NAT、回程和时钟服务器客户端白名单，允许<prod-host>或其实际NAT地址查询。现有证据仅定位到该端点对，不能凭超时断言具体哪台防火墙丢包。

曾短暂测试DMZ作NTP中继，仅allow<prod-host>/32；<prod-host>→DMZ UDP123也超时，故已撤回allow并恢复DMZ原配置SHA，没有遗留可用中继或用本机时钟冒充服务器。未执行手动date/makestep强行改钟。

放通后无需重新部署：chronyd会持续重试。验收：`chronyc -n sources`出现`^* <relay-net-host>`，`chronyc tracking`为Normal；不能仅看服务active或System time=0。

### 看板访问/证书

入口：`https://<prod-host>/ui/#/drugtrace`。生产页面/资产已验证200，新API200；本机TLS在收到HTTP响应前断开，浏览器未验收。另查明原UI证书只有CN=`blyb-<prod-host>`且无SAN，直接按IP做严格校验会失败。这是原部署证书缺项，未关闭生产TLS校验，也未替换成另一张测试证书冒充正式CA。

请提供覆盖院方正式管理域名或IP SAN=<prod-host>的服务端证书链，按既有轮换流程安装；同时检查操作电脑→<prod-host>:443的TLS访问策略。不要开放8000/8001/8002替代nginx。后台已部署不等于当前电脑能打开页面，二者分开验收。

### 业务补齐

1. 确认050121、050116是否确属当前医院ref_ent_id；由有权人员发布/批准新映射revision，不能因名称相似直接复制。补齐后仅重驱这815张未形成提交的单据，沿用原范围、幂等和配额。
2. 对205张429、1张本地失败、41张ACCEPTED分别导出平台单据结果并核对。平台未查到不自动等于可重发；先保留原attempt、冻结digest、单号和时间证据。若要新增受控查询轮次，需要单独实现有审计和上限的人工复查机制，不能删除历史job回避12轮上限。
3. 观察10-26 06:10的完整日批及日报入库/退出码/平台终态，确认3h采集等待与6hunit足够。最终新worker已连续18分钟healthy，但长collect/慢查询对主循环心跳及租约的影响仍需独立治理，不靠拉长心跳阈值造绿。看板仍按原07:30日报SLA告警，而实测采集2.5h；需先明确是优化取数满足SLA还是经确认调整SLA，不静默延迟阈值消除红灯。

## 验证记录

- 最终局部：`python -m pytest tests/integration/test_drug_trace_daily_driver_repair.py tests/integration/test_drug_trace_timed_ui.py tests/integration/test_drug_trace_transport_pacing.py -q -ra --junitxml=var/ntp-dashboard-local-verified.xml`：**38 passed**。
- 真实TLS传输/DMZ回归：`test_dmz_forwarder.py test_drug_trace_fix3.py`：**45 passed / 1 skipped**（Windows FD布局），证据`implementation/var/ntp-dashboard-transport.xml`。
- 全量`var/ntp-dashboard-regression.xml`：**467 passed / 1 failed / 7 skipped**。全量进程采样到旧unit断言仍要求日报开关0，而部署任务已改1；当前断言经上述局部和单项`var/ntp-dashboard-full-failure-recheck.xml`均通过。未称单次全量全绿，也未删除原失败证据。
- 同源npm build成功两次；离线镜像pip freeze与现场原发行完全相同。
- 升级最终退出0，012台账、六容器、四GET、人工日报POST200、站内TLS/CN校验页面/资源200、DMZ真实转发与NTP均有现场证据。
- 未实测：下一次完整日批、当前电脑浏览器页面、<prod-host>同步成功、247个非终态全部核清、两个新药房映射及815单补齐。
- 文档检查：目录清单已更新；本轮新增文档和交接链接0坏链、源/归档哈希0不符（evidence/doc-check.json）。全工作区链接检查退出1，检出236项其他既有文档坏链，未修改历史报告/归档或重写基线造绿。

代码和测试命令、证据随工作区交接；凭据仍在原受限位置，不复制到本文。
