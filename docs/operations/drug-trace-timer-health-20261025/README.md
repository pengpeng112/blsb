# 定时上报实际运行检查（2026-10-25）

**结论：定时器已开启，但首个日批没有正常运行。** <prod-host>于北京时间2026-10-25 06:10:29触发任务，服务同秒退出，状态failed/exit=1。今天这轮没有产生新的采集、提交、发送尝试或后台任务；不能把timer active或容器healthy当成上报成功。
本次由用户要求检查“开启定时任务后是否正常推送和处理”。只读检查在17:40—17:46进行；未手动触发日批、未新增发送、未改配置或重启业务。

## 1. 现场实测

| 检查项 | 当前实际结果 |
|---|---|
| 主机/链路 | 经既有固定指纹SSH跳板连接<prod-host>成功 |
| 定时器 | blyb-daily-upload.timer enabled、active/waiting |
| 首次触发 | 2026-10-25 06:10:29 Asia/Shanghai |
| 上报service | failed，Result=exit-code，ExecMainStatus=1，启动/退出同秒 |
| 下次计划 | 2026-10-26 06:10；保留当前配置会再次遇到同一启动缺陷 |
| 六个容器 | 均healthy，不是容器整体停止 |
| 文件回执 | /var/log/blyb/daily-upload/20261025-061029.json，0字节，不能解析为JSON |
| 错误日志 | last-error.log 537字节，1个Traceback，AttributeError，非ORA错误 |
| 本轮错误位置 | /tmp/daily-upload.py第65行生成表达式，str对象没有get方法 |
| 今日新增业务活动 | 自触发前时点2026-10-24 22:10 UTC起，collection/submission/delivery_attempt/job均0 |
| 现存整单状态 | CONFIRMED 2，PREPARED 1；成功仍是此前两笔，不是今天批量新增 |
| 现存上传尝试 | 2笔HTTP200/ACCEPTED，最后一笔2026-10-24 01:28:11 UTC |
| 最近整单确认更新 | 2026-10-24 01:28:24 UTC，即北京时间10-24 09:28:24 |
| 发送配额 | used=2/max=80000，没有因本次启动失败新增消耗 |
| 10-24已采集本地数据 | 仅此前小窗51单：2个正常VALID、49个UNKNOWN/WAITING；不是全天已完成 |
| 已有提交明细 | 两张CONFIRMED整单下5条SubmissionItem仍PENDING；属既有A05显示/码级核验边界，不宣称今天新失败5码 |

四类证据仍APPROVED且当前未到期。发送证据valid_until实际为2026-11-21 16:00 UTC（北京时间11-22零点），后续续期检查应以实际证据和scope共同判断，不能只看“30天”口头描述。这不是今天秒退原因。
本次仅查询生产库保存的整单状态，没有重新向阿里平台查询每码终态。

## 2. 启动根因已直接验证

service里有如下printf格式（仅展示无秘密的格式片段）：

```text
printf "%s..."
```

systemd会在ExecStart解释它自己的百分号说明符。当前文件未把`%s`写成`%%s`，实际ExecStart中的该格式已变成`/bin/sh`加换行。

现场只读`systemctl show ... --property=ExecStart`，随后**仅提取静态格式串**；用公开假字符串PUBLIC_DUMMY模拟printf，结果输出shell路径而不是输入。测试从未使用或打印真实令牌。
因此容器收到的stdin不是应传入的bootstrap token。正确令牌通过内存/管道调用两个只读GET时，接口均HTTP200、data为list、3条采集运行；认证令牌本身及现有列表API可用。

驱动又把初次GET返回值直接当作运行列表遍历，没有先检查HTTP状态和data类型。实际堆栈停在第65行`r.get('window')`，其中r是str，错误响应没有被转成可读失败回执，最终只留下空JSON文件。
“printf参数传递错误”和“驱动未校验错误响应”应分别修复；不能只在页面上把服务状态标正常。

证据：[有效ExecStart及公开假值模拟](evidence/snapshot-20261025T094646Z.json)、[错误帧/活动/正确令牌GET形状](evidence/snapshot-20261025T094435Z.json)。共享证据不含token、密码、完整码或原业务单号。

## 3. 新看板还没部署，不是当前故障的替代解释

生产发行仍`/opt/blyb/releases/src-20261022T103000Z`。宿主驱动SHA为`90fc2814186b0bd9cef862a2a3fa48857818c7afb25cfb365010a079f6ddfaa8`，没有v2的push_daily_report函数；生产数据库也不存在drug_trace_daily_report表。
与[既有TUI-08待部署交接](../../development/drug-trace-timed-ui-plan-20261024/RESULT.md)一致：本地新界面/迁移/驱动完成不等于生产已升级。不能依赖尚未部署的日报看板观察本次失败。
这项升级和printf最小修复可分开安排；本次未执行其中任何一项。

## 4. 已准备的最小修订及后续顺序

已生成[service候选文件](candidate/blyb-daily-upload.service)，**只将printf的`%s`改为`%%s`，其余内容不变**。候选源文件SHA与现场原unit完全一致，校验见[validation.json](validation.json)。候选尚未安装，没有声称现场修复成功。

后续处理顺序：

1. **先修最小启动配置。** 备份现unit，核对SHA仍匹配，再安装候选、校验unit并daemon-reload。继续保留日期格式原有转义。不要为这一个问题重建数据库或全栈。
2. **补驱动错误处理。** 初次GET及轮询GET均先校验HTTP状态、data必须list、各元素必须dict；失败生成含阶段/HTTP状态/安全错误码的非空JSON并非零退出。网络异常和JSON解析失败同样落回执，绝不把失败当空列表后继续创建/发送。v1/v2共用的调用方式均需复核。
3. **复验输入令牌传递。** 沿用公开假值和格式检查，确认有效printf保留输入；真实GET只返回HTTP状态/结构，不打印令牌。驱动401、403、500、非JSON、错误data类型应有局部回归。此项不需要真实上传。
4. **监督恢复10-24日批，不能只等明日。** 今日任务原本应处理`[2026-10-24 00:00,2026-10-25 00:00)`。如果仍在10-25，现驱动手动启动会取这一窗口；如果已到后续日期，直接start只会取“新昨天”，必须用明确历史窗口的受控补跑方式，不能误以为已经补回10-24。
5. **发送仍走原门禁。** 保留既有成功单和used=2，按当前批准scope/药房/质量/配额发送；PREPARED续发、CONFIRMED不重发，不刷明细状态，不放行49条UNKNOWN，不扩大到10-12..23缺口或拆零流。监督补跑属于真实采集/发送动作，本次检查没有执行。
6. **按业务结果验收。** 不只看service退出0：核对全天collection DONE、候选/质量等待/发送/跳过/失败数守恒、真实delivery_attempt与回执对应、非终态查询推进、配额增量；平台3/51和部分/失败/未终态分别记录。码级独立核验与整单确认分开。
7. **再处理看板部署和告警。** 按原TUI-08维护窗口部署，不混入未批准变更。必须展示service失败/回执空缺或缺日，文件回执与日报表一致；仅timer active不能算健康。后续观察次日自动触发，保留失败和恢复两套证据。

本任务不安装候选、不重启服务、不补跑，不产生真实批量发送。下一步应按上述证据修复并监督恢复，不能将本次报告标为“定时推送正常”。

## 5. 执行与验证记录

本机执行：

```powershell
python docs/operations/drug-trace-timer-health-20261025/inspect_runtime.py
python docs/operations/drug-trace-timer-health-20261025/inspect_runtime.py --transport
```

默认模式只读systemd/容器健康/安全日志摘要/本地生产Postgres聚合及两次内部GET；transport模式只查unit静态格式并用公开假值演示printf，不访问HIS/平台。
生产SQL通过`default_transaction_read_only=on`和20秒statement_timeout执行，没有改业务表。完整运行证据在[evidence目录](evidence/)，共6份快照；SSH均退出0，含一次初版诊断SQL字段不符导致psql退出1，修正为真实destination关联后后续聚合均成功。该诊断SQL错误与06:10生产故障无关。
三份本机检查脚本AST校验通过，候选unit只改一个printf说明符；未执行修复后的真实运行测试。原unit哈希与候选基线对照通过。

本次源HIS查询0、平台业务调用0、手动上传0、远程配置/业务修改0。六容器和原timer保持原状。
