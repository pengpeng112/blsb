# 定时驱动修复/部署/恢复交接

任务DTR-TIMER-REPAIR-20261025，session `20261025T095338Z-7bddd6ff`；Codex，模型unknown。用户明确授权“进行修复，并看看还有其他问题”。

[完整报告](../../operations/drug-trace-timer-repair-20261025/README.md)、[聚合证据](../../operations/drug-trace-timer-repair-20261025/evidence/)、[校验与测试统计](../../operations/drug-trace-timer-repair-20261025/validation.json)。

## 已完成

修复原canonical daily_upload_driver.py和service：%%s、GET状态/形状/错误信封/网络失败校验、完整失败JSON、UNKNOWN只查询/只PREPARED续发、准确resumed计数、显式--date历史日、完成日期检查；等待3h、unit6h、2.2s节奏。生产旧版暂无日报API，unit显式BLYB_DAILY_REPORT_ENABLED=0；未来TUI-08部署后启用1。
备份后验证/部署到<prod-host>，公开假值验证有效printf，恢复10-24全天窗口；六个业务容器未重启。17:57启动的同一collect job和断点继续推进，18:06只替换等待驱动，未再创建第二个全天任务。最初替换因容器默认UID不能kill根用户等待脚本失败、未改文件；改容器root后成功。原早晨空回执和失败日志保留；中途等待替换回执明确写管理员替换原因，不冒充成功。
原件/热修前版本在`/root/blyb-timer-backups/20261025T095741Z/`及`20261025T100610Z/`；确切hash见部署JSON。

## 真实发送与本轮错误（不能省略）

本轮首个临时验证执行位置错误：把handler放在core而非worker，worker专属SecretRef/证书不可见，生成FileNotFoundError→UNKNOWN、无HTTP状态，计1次保守额度；紧接的查询handler也因同样位置失败。此后已修辅助脚本为worker并在提交前build_client预检。
仅对该UNKNOWN经正式API/带租约QUERY handler查询，平台完整返回NOT_FOUND；保留UNKNOWN/后续查询/人工复核，没有手工改PREPARED/成功，没有重发或清额度。
另选当前10-24 VALID正常完成且未作废单据，在worker先预检，经prepare/send API和标准带租约SEND/QUERY handler，新实测1单HTTP200/ACCEPTED→平台result_type3/sub_process_flag51→CONFIRMED，单尝试1次。临时consumer只领取send/query类型，未抢占过期collect租约、未执行HIS查询。
累计CONFIRMED3、UNKNOWN1、quota used4/80000；此前成功2单未撤销/重发。

## 仍在运行/未签认

18:22全天collect RUNNING、400条已提交，非全天总数；后续数值以最新progress JSON为准，未把这些都称发送成功。
18:29再次复核500条已提交、collect仍RUNNING，219 VALID/262 UNKNOWN等待/19正常门诊等待；累计CONFIRMED3/UNKNOWN1/used4未变，未见新驱动错误。
timer次日10-26 06:10保留。观察器`blyb-repair-observe-20261025.service`已active，每分钟只读记录`/var/log/blyb/repair-observe-20261025/latest.json`/progress.jsonl，业务service退出后写final.json，最多6h。观察器不发送或重试、不改业务表/源库。
本会话完成的是热修部署和真实单笔验证；全天最终结果、吞吐和后续日批仍须读取观察器/文件回执/生产终态，不能标成已全量通过。已有UNKNOWN人工核查、NTP未同步、TUI-08未部署及A01—A05继续在BACKLOG。

## 命令及实际结果

根目录：

```powershell
python scripts/ai_session.py status
python docs/operations/drug-trace-timer-repair-20261025/deploy_repair.py --authorized --start
python docs/operations/drug-trace-timer-repair-20261025/deploy_repair.py --authorized --extend-wait --start
python docs/operations/drug-trace-timer-repair-20261025/run_smoke.py --authorized-one-bill
python docs/operations/drug-trace-timer-repair-20261025/run_smoke.py --authorized-one-bill --query-only
python docs/operations/drug-trace-timer-repair-20261025/install_observer.py
python docs/operations/drug-trace-timer-repair-20261025/monitor.py
```

不要原样重复部署/发送/观察器安装命令：安装guard会校验原hash、进程状态与是否已安装，单笔脚本会选择新待发单，不能当只读状态检查使用。后续只读用monitor.py。
第一次延长等待部署退出1（容器UID），修正后退出0；第一次smoke退出1（错误容器，已说明），query-only与正式worker重验退出0。观察器安装/只读捕获退出0。一次monitor查询receipt_event.event_type字段不符，改实际received_at后通过，错误证据保留，与业务故障无关。

implementation目录的测试：局部首轮26/0，新驱动13/0，补4个状态用例后最终30/0。
全量首轮421/19/28（退出1，WSL bash与openssl环境）；一次误指C:/Git后重跑被主动Ctrl+C中止，XML保留，不当通过；正确D:/Git后450/11/7（退出1，旧运维中文stdout按GBK解码失败）；加测试进程PYTHONUTF8=1对全部失败项--lf复验11/0（退出0）。未改旧断言/xfail；未再声称单次全量全绿。新4条状态用例最终局部覆盖。
XML在implementation/var/timer-repair-*.xml，统计见validation.json。未安装新依赖或修改全机PATH。

## 文件范围与下一步

共享STATE/HANDOFF/BACKLOG、业务README和四类实施文档已同步，修改前本机文本副本在本任务before/。HIS视图/源数据、批准字典、业务quality/ingest/service、旧payload未改；源写入0，配额未直接写/重置，正常业务尝试按原服务增加2（失败1、成功1）。
优先读取服务器观察器final/latest与主回执，核对10-24全天collection DONE、candidate/发送/质量等待/失败数和平台终态。不得仅凭3单累计成功标全天完成。
生产看板升级仍按TUI-08执行；长collect lease无heartbeat/准备提交规模性能/跨多日补偿须另给有证据的改进，不自行启动无过滤的额外worker抢占collect；本轮辅助consumer是一次、有界且仅send/query。
