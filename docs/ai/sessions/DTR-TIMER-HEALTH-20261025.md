# 定时任务首跑只读检查交接

任务DTR-TIMER-HEALTH-20261025，session `20261025T093929Z-0568a395`，工具Codex，模型unknown。用户要求核查已开启的定时任务是否正常推送与处理。

[详细报告](../../operations/drug-trace-timer-health-20261025/README.md)、[最小service候选](../../operations/drug-trace-timer-health-20261025/candidate/blyb-daily-upload.service)、[本机校验](../../operations/drug-trace-timer-health-20261025/validation.json)。

## 实测

只读检查2026-10-25北京时间17:40—17:46，经原固定指纹跳板到<prod-host>；读取systemd元数据/日志安全摘要/宿主driver摘要，生产Postgres聚合使用default_transaction_read_only=on和20秒超时，内部API仅GET且令牌只经RAM/stdin。

- timer10-25 06:10:29触发；service同秒failed/exit1，文件回执0字节，last-error.log为AttributeError，/tmp/daily-upload.py第65行str无get。
- root cause：unit printf %s未写%%s，systemctl show的实际格式已为/bin/sh加换行。仅用PUBLIC_DUMMY模拟该静态格式，结果不是输入，确认令牌传递错误；未使用真实令牌模拟/打印。正确令牌GET limit200/10均200且data=list、3行；驱动未校验错误响应类型，首次列表迭代崩溃。
- 今日新增collection/submission/attempt/job均0；仍仅此前2张CONFIRMED、1张PREPARED、used2/80000。上次成功10-24 09:28北京时间；49UNKNOWN等待未放行。
- 六容器healthy，timer仍enabled/waiting且次日10-26 06:10计划不变。旧故障没修，不能标正常。
- 生产旧发行src-20261022T103000Z，v1驱动无report_push，日报表不存在，TUI-08未部署。整单确认下5条明细PENDING为已有A05，不刷状态。

## 产物及验证

本任务新增3个检查脚本、6份安全快照、报告和候选unit。现场原unit SHA与仓库10-24原文件完全相同；候选只将一个printf %s改%%s。AST解析3个脚本通过；候选未部署、未实机跑过。
实际本机命令`python docs/operations/drug-trace-timer-health-20261025/inspect_runtime.py`及`--transport`，6次捕获SSH退出均0。第一次生产SQL错误引用不存在的submission.domain_code，psql退出1；改用真实destination关联后后续SELECT成功，已保留失败证据，不与原06:10故障混淆。中间printf格式严格提取未匹配时仅做后续更窄元数据核验，没有重启服务。
完整JSON证据位于本任务evidence/，不含原始码、业务单号、密码或token。共享文件变更前副本在before/。
文档目录、链接和候选差异校验结果见validation.json；无产品代码更改，未跑产品全量测试。捕获命令成功只表示取得证据，不代表service健康。

## 下一步

优先最小修unit、补初次/轮询GET类型与HTTP错误回执，再在既有scope/质量/幂等/配额内监督恢复失败的10-24日批；后续日期不能直接start然后误以为补回10-24。已经CONFIRMED不重发，不扩10-12..23或拆零流，不复位used。
完整修复/验收步骤在报告§4。UI升级继续按原TUI-08维护窗口，先后顺序不要用“本地修复已通过”代替生产运行证据。
本次生产配置/业务修改0，业务重启0，手动触发0，HIS查询0，平台业务调用0，新上传0。任务完成的是检查和可审查候选，未完成现场修复或监督补跑。
