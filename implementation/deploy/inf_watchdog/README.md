# inf_watchdog · 传染病推送链路守护与告警（<prod-host> 宿主）

依据 `数据上报/4、传染病病历数据上报/plans/20261103-守护告警执行计划.md`（D0–D7 决策见该计划 §0.3）。
**完全外挂、零耦合**：只读检测推送链路 + 告警外发；不 kill 进程、不触发推送、不改 crontab、
不写推送器 `state/`（对账唯一依据）；自身状态只写 `<prod-path>/exchange/inf-watchdog/state/`。

## 组件

| 文件 | 说明 |
|---|---|
| `watchdog.py` | 主程序（stdlib only）：`check`（C1–C10，5 分钟）/`daily`（晨报，每日 08:00）/`test-alert`（通道演练） |
| `selftest.py` | 自测 66 项：fixture 驱动、无网络、不触碰真实 state（本地与宿主都要全绿） |
| `config.template.json` | 配置模板（**仓库只有占位**；真实 config 仅宿主 600 root） |
| `inf-watchdog{,-daily}.{service,timer}` | systemd timer+oneshot（unit 内一律绝对路径） |
| `deploy_8156_infwatchdog.py` | 部署脚本（upload / units / uninstall 三阶段） |

检查项速查：C1 班次新鲜度(40min)｜C2 班统计 error｜C3 跳班连击(同秒 CMD/CMDEND ≥3/≥8)｜
C4 锁悬挂(>25min)｜C5 24h 推送失败率(>10/>50，client-validate 尾差不计)｜C6 日终对账
(00:40 缺失/failed>0/repushed>500)｜C7 推送器 ALERT 转发｜C8 水位陈旧(六表最大>26h)｜
C9 磁盘(>80%)｜C10 <worker-container> 容器在跑。

已知坑（计划 §2.5，代码注释均有标注）：receipts 文件名/行内 ts 均为**容器 UTC**（一切按行内 ts
过滤）；维护窗 06:00–08:30 无班（C1 排除，实现至 08:34 含 8:30 迟班完成宽限，防整点假警）；
cron.log 属主 10001 只读勿修；client-validate/skipped_invalid 是已知尾差；/var/log/cron 会轮转。

## 部署（顺序铁律：selftest 全绿 → 手动演练 → 才挂 timer）

```bash
# Ph3 上传落位 + 真实 config（file-only 时与 template 同值）+ 宿主 selftest
python deploy_8156_infwatchdog.py upload
# Ph4 手动演练（宿主）：
#   /usr/bin/python3 <prod-path>/exchange/inf-watchdog/watchdog.py --config <prod-path>/exchange/inf-watchdog/config.json check
#   /usr/bin/python3 <prod-path>/exchange/inf-watchdog/watchdog.py --config <prod-path>/exchange/inf-watchdog/config.json test-alert
# Ph5 挂 timer（仅当 Ph4 通过）
python deploy_8156_infwatchdog.py units
```

真实 config 权限 600 root（`<prod-path>/exchange/inf-watchdog/config.json`）；SMTP 口令（如启用）
另放宿主 env 文件 600 并以 `INF_WATCHDOG_SMTP_PASSWORD` 注入，不进 config 不进仓库。

## 日常巡检（替代旧手动命令，旧法保留为后备）

- 看状态：`cat <prod-path>/exchange/inf-watchdog/state/status.json`（各检查级别/耗时/总体）。
- 看告警：`cat <prod-path>/exchange/inf-watchdog/state/OPS-ALERT-$(date +%Y%m%d)`（file 驱动必落盘）。
- 晨报：每日 08:00 自动产出 `state/daily-YYYYMMDD.txt`；手动补跑
  `systemctl start inf-watchdog-daily.service`。
- timer 在不在：`systemctl list-timers 'inf-watchdog*'`；单次手动检查
  `systemctl start inf-watchdog.service`。

外发通道当前为 **file-only**（Ph0 未取得 SMTP/webhook 参数，按计划 §8/D3 走降级路径）；
补通道时仅改宿主 config.json 对应 `smtp`/`webhook` 段（enabled=true + 参数）再
`test-alert` 验证，不需要重新部署代码。防轰：同一检查项+级别 1 小时内最多外发 1 次
（file 落盘不限次）；degraded→healthy 转换外发一条 `[RECOVERED]`。

退出码语义：0=HEALTHY，1=degraded（任一 WARN/CRITICAL），2=watchdog 自身/配置错误。
**systemd 将退出码 1 显示为 "Failed with result 'exit-code'" 属预期**（检查发现退化即非零
退出），timer 不受影响继续按 5 分钟触发；真实状态看 `state/status.json` 与 journald 的
`OVERALL` 行。

## 回退

```bash
python deploy_8156_infwatchdog.py uninstall
# 等价手动命令（计划 §6）：
#   systemctl disable --now inf-watchdog.timer inf-watchdog-daily.timer &&
#   rm /etc/systemd/system/inf-watchdog*.service /etc/systemd/system/inf-watchdog*.timer &&
#   systemctl daemon-reload && rm -rf <prod-path>/exchange/inf-watchdog
# 单项回退：config.json 对应驱动 enabled=false 即停外发；timer disable 保留落盘告警。
```
