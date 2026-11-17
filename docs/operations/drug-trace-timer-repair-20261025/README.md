# 定时上报修复与恢复记录（2026-10-25）

**启动主故障已修复并部署；正式worker中实测新增1单平台3/51、本地CONFIRMED。10-24全天补跑仍在运行，不能宣称全天已上报完成。**
用户在收到[首跑失败实测](../drug-trace-timer-health-20261025/README.md)后明确要求“进行修复，并看看还有其他问题”。本轮已实际修改<prod-host>定时服务/驱动、保留备份，并恢复原批准窗口，没有修改HIS视图或原始业务数据，没有重置配额/撤销证据/重发成功单。

## 一、已修复并部署

| 问题 | 修改及验证 |
|---|---|
| systemd把printf的%s展开为/bin/sh | unit改成%%s；daemon-reload后读取有效ExecStart，仅用PUBLIC_DUMMY验证确实原样传值及换行，真实token不回显 |
| 初次GET错误对象被当作list，str.get崩溃 | 校验HTTP状态、list形状和元素类型；初次查询失败在任何POST采集/发送前退出并生成完整JSON |
| 轮询不检查HTTP/结构，可能错误等待 | 轮询同样校验；明确collect_poll_failed，保留业务窗口/计数/安全错误码 |
| 网络/非JSON/非字典错误信封缺少保护 | 统一安全错误码和类型，不把URL/响应原文/秘密写入回执 |
| 异常时仅留下空回执 | fatal路径输出窗口/阶段/outcome/耗时；进程顶层捕获意外异常并输出有效失败JSON |
| UNKNOWN还会被驱动尝试send | UNKNOWN与在途状态均跳过发送，只走查询；只允许PREPARED续发，resumed计数补齐 |
| 失败日跨天后难以精确补跑 | CLI新增--date YYYY-MM-DD，仅允许已结束的北京时间日期；既有证据/scope/质量/配额仍由API/worker逐单检查 |
| 全天采集仍只等45分钟 | 等待上限改3小时，unit总上限6小时；实际替换只终止等待驱动，后台采集job和断点原样保留，没有新建第二个全天采集 |
| 上传和自动查询共用DMZ限流 | 串行间隔从1.2改2.2秒，给自动状态查询留出余量；并非保证任意网络/重试情况下绝不遇到限流 |
| v2驱动投向未部署日报API | 当前unit设BLYB_DAILY_REPORT_ENABLED=0，回执明确DAILY_REPORT_NOT_DEPLOYED；暂不向不存在的表/API写日报。未来TUI-08部署后改为1并验收 |

实际程序仍使用[原驱动路径](../../development/drug-trace-timed-report-20261024/daily_upload_driver.py)和[原unit路径](../../development/drug-trace-timed-report-20261024/blyb-daily-upload.service)，没有新建另一套业务骨架。对照源文件修改前副本在before/。
日期和字典、r10三键关联、UNKNOWN/退款/重复码门禁、冻结payload、来源profile、现行审批和成功单据均保留。

## 二、真实部署与恢复过程

1. 17:57首次修复部署成功，原unit SHA与已核查基线完全一致；先备份，再systemd-analyze verify、安装和daemon-reload。公开假值测试通过后启动日批。
2. 新日批窗口为`[2026-10-24 00:00,2026-10-25 00:00)`，采集任务17:57:41创建并进入RUNNING。两次小窗和更早单笔记录没有当成全天完成。
3. 发现源采集速度与小窗不同，18:06更新长窗等待。只在确认全天采集RUNNING且当时新增发送尝试0后，精确终止API容器中的等待脚本；worker和采集job未停止。新驱动复用了同一运行行和r10-batch-daily断点，未重复创建/采集整个窗口。
4. 最初替换等待脚本时容器默认用户权限不足，部署返回失败且未修改unit/driver；改为容器root后成功。该失败回执保留。
5. 六个业务容器均未重启。timer仍enabled/active，下一次计划2026-10-26 06:10北京时间。

现场备份：

- 原失败版本：`/root/blyb-timer-backups/20261025T095741Z/`。
- 长窗等待修订前版本：`/root/blyb-timer-backups/20261025T100610Z/`。
- 最新driver SHA：`1f275f0b20fb58ddc8319347d65981d3f253e85b1005ff2853ea7392e2726df3`。
- 最新unit SHA：`f89c0c81f5448818d4d05b58145d016621838f45710e4cf00b5ea76a7a5d33f0`。

[首次部署回执](evidence/deploy-20261025T095742Z.json)、[等待驱动替换回执](evidence/deploy-20261025T100612Z.json)。备份保留审计；不要把含未转义%s的旧unit直接恢复成生产配置。

## 三、真实单笔验证和本轮出现的一条异常

**成功验证**：[18:15 worker验证回执](evidence/one-bill-20261025T101503Z.json)。
在正式worker环境先构建目的地客户端，确认凭据/证书引用可用；对一张当前VALID、完成且未作废、在10-24批准范围内的单据，经既有prepare/send API和标准带租约handler执行。上传HTTP200/ACCEPTED，随后标准查询推进CONFIRMED。[实际查询分类](evidence/progress-20261025T102043Z.json)为result_type=3、sub_process_flag=51。
只处理drug_trace.send/query_status，没有领取/抢占长时间采集job；没有执行直连HIS取数或绕过发送门禁。

**本轮执行位置错误必须保留说明：**18:12首次临时验证错误地放在core容器执行handler。平台凭据只挂在worker，因此client构建报FileNotFoundError；系统按既有保护将该尝试记UNKNOWN，配额保守占用1次。随后query handler也因同样位置错误失败。
这是本轮临时验证的执行环境错误，不是定时服务的worker配置被改坏。已经改为worker执行，并在任何新发送前做客户端预检。

对这张UNKNOWN仅在worker调用正式query API/handler，平台全分页查询结果为NOT_FOUND；[查询回执](evidence/one-bill-20261025T101439Z.json)。它仍保留UNKNOWN/后续查询路径，**没有手工改为PREPARED/成功，没有重发，没有删失败尝试或退回额度**。UNKNOWN不得仅凭未找到结果就自动重发。

当前累计状态：此前2单成功＋本轮1单成功＝3单CONFIRMED，另1单UNKNOWN；used=4/80000（此前2＋本轮失败保守计1＋本轮成功1）。这不是4张成功上报。
这条异常后续应按正式人工复核流程处理，当前不能把它写成“已经成功”或“已补发”。本轮错误路径和待核身份保留在生产审计及租约job中，公共报告不写真实单号/码。

## 四、全天补跑状态和持续观察

截至18:22实测：同一全天采集仍RUNNING，已持久化400张单据：182 VALID正常门诊、204 UNKNOWN/WAITING、14正常门诊WAITING。数量将随分页提交增加；这些400张不是全天预期总数，也不等于400张已发送。
18:29复查推进至500张（219 VALID、262 UNKNOWN/WAITING、19正常门诊WAITING），采集仍RUNNING，驱动stderr无错误；[最新进度](evidence/progress-20261025T102939Z.json)。累计确认仍3单、UNKNOWN1，不能把500写成已上传数。
采集按页原子提交，r10-batch-daily断点持续推进；此前成功单和新验证成功单会被跳过，不重新上传。驱动等待全天采集DONE后再按候选集合执行日批发送。
运行中的最终回执文件可暂时为0字节；必须结合service正在activating/start和持久化断点判断，不能按早晨“同秒failed＋空文件”的规则误判正在运行的任务。

已部署**只读、最长6小时的本轮观察程序**：

- transient unit：`blyb-repair-observe-20261025.service`。
- 程序：`/opt/blyb/tools/blyb-repair-observe-20261025.py`。
- 每分钟安全进度：`/var/log/blyb/repair-observe-20261025/latest.json`、`progress.jsonl`。
- 业务service退出后：同目录生成`final.json`，原样保留最终成功/失败结果；观察超预算则生成`observer-timeout.json`。
- 观察器不重试、不发送、不改业务表、不连接HIS；只读本地生产Postgres和systemd状态，业务日批仍由原unit/driver执行。

[观察器安装回执](evidence/observer-20261025T101919Z.json)及[运行中回读证据](evidence/progress-20261025T102209Z.json)。后台观察没有替代最终验收，本报告不提前签认全天完成。

后续只读复核：

```powershell
python docs/operations/drug-trace-timer-repair-20261025/monitor.py
python docs/operations/drug-trace-timer-repair-20261025/monitor.py --details
```

若后续日期需人工恢复失败窗口，明确传`--date 2026-10-24`，不能假定启动“昨天”就会自动补回所有漏日。当前无自动跨多天补偿实现；10-12..23历史缺口仍未纳入发送。

## 五、测试证据与执行环境

- 新驱动回归覆盖401/403/500、错误列表形状/非字典元素、轮询失败、非JSON/错误信封、网络错误、未结束日期、错误时零发送、显式补跑日期、unit转义、旧服务日报兼容，以及UNKNOWN只查询/成功不重发/仅PREPARED续发。
- 首轮驱动＋既有timed_ui局部26通过；等待上限改动后新驱动13通过；补充4条状态用例后的最终局部**30通过、0失败**，见`implementation/var/timer-repair-local-final.xml`。
- 全量初跑421通过/19失败/28跳过，失败主要因误用Windows自带但无发行版的WSL bash、openssl不在PATH。
- 将**本次测试进程**PATH指向已有D:/Program Files/Git/bin及usr/bin后，全量450通过/11失败/7跳过；11失败为旧运维脚本测试的GBK/UTF-8输入输出问题，并非本轮修改驱动被断言失败。没有改断言或标xfail。
- 测试进程再设PYTHONUTF8=1，对上轮全部失败用例`--lf`复验：11通过、0失败（`timer-repair-env-confirm.xml`）。不把多次结果称为“一次全量全绿”；原失败XML均保留。新增4条状态用例另由最终局部回归覆盖。

最终完整数字、语法/文件摘要/链接检查见validation.json。未安装依赖、未修改机器全局PATH或编码。

## 六、其他核查发现及仍未完成事项

| 项目 | 当前处理/边界 |
|---|---|
| <prod-host>持续NTP | chronyc Leap非Normal，未同步；现有<relay-net-host>配置仍保留，需网络/服务ACL落实UDP123。未把offset显示0误称已校时 |
| 新定时看板 | 生产仍旧发行src-20261022T103000Z，迁移012/日报API/UI尚未部署；本轮只修独立驱动和unit，不以本地UI测试代替生产升级。按TUI-08独立维护窗口完成后再启用日报推送 |
| 全天吞吐 | 页面源整单读取明显慢于聚合查询；等待已加有界预算，但尚无完整全天耗时/最终结果，继续观察，不声明性能认证 |
| 长collect租约 | 实测运行时长超过默认120秒lease，但当前单worker未发生回收/重试，attempts=1。新增交付worker时必须排除collect或补heartbeat，不能无差别扩worker导致抢占 |
| prepare扩展规模 | 现service.prepare_submission存在遍历目的地既有submission并逐条取包的路径；本轮未改核心service，建议另做保持租户/配置换代语义的定向查询优化和规模验证，不宣称已实测未来长期容量 |
| 1条验证UNKNOWN | 已查平台NOT_FOUND，保留人工复核/查询，不自动重发；失败记录不清零 |
| 既有A01—A05/拆零/历史缺口 | 按已有任务和院方业务确认推进；本修复不放开退款/重复/拆零、包装关系或历史补报，不把一张成功扩为所有历史准确 |

## 七、范围与回退

修改了宿主driver/unit并启动恢复，保存原文件和失败日志；没有部署整个后端/前端，没有数据库迁移，没有HIS DDL/DML，没有修改r10/冻结包/批准字典或配额值。
如需回退程序，使用上述备份并核对版本；unit必须保留%%s修正，不能恢复已证实的错误传参。正在运行的采集是独立worker任务，停止等待脚本不等于停止采集，更不能清库或删除断点“重新开始”。
