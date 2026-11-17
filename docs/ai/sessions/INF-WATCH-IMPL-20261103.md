# INF-WATCH-IMPL-20261103 · 传染病推送链路守护告警（inf_watchdog）一次性开发交接

- 会话：`20261103T101746Z-3aec6594`（ai_session start：--tool ZCode --model GLM-5.3，
  --scope 数据上报/4、传染病病历数据上报 + implementation/deploy/inf_watchdog）
- 依据：`数据上报/4、传染病病历数据上报/plans/20261103-守护告警执行计划.md`（唯一权威指令）
- 结果：**Ph0–Ph7 全部完成**；watchdog 已在 <prod-host> 上线运行（file-only），推送主链路零改动。

## 交付物

仓库 `implementation/deploy/inf_watchdog/`（9 文件）→ 宿主 `<prod-path>/exchange/inf-watchdog/`
（700 root:root，config.json 600 root，state/ 700）+ 4 个 systemd unit；中转 `<watchdog-path>`。

| 文件 | 说明 |
|---|---|
| watchdog.py | check（C1–C10）/daily（晨报）/test-alert；stdlib only，Python 3.9 |
| selftest.py | 68 项自测（fixture 驱动/无网络/不触真实 state/只读纪律哈希断言） |
| config.template.json | 模板（真实 config 仅宿主） |
| inf-watchdog{,-daily}.{service,timer} | 5 分钟 check + 每日 08:00 Asia/Shanghai 晨报，均 Persistent=true |
| deploy_8156_infwatchdog.py | upload / units / uninstall 三阶段部署（复用 probe_ssh+deploy_remote 通道） |
| README.md | 部署/巡检/回退/已知坑 |

## 逐阶段证据（真实命令 → 退出码/输出摘要）

### Ph0 输入确认
- 本机 18:17 CST（白天窗口合规）；宿主 `date` 18:18:38 +0800，偏差 <2 分钟（§0.2 校准通过）。
- 向用户发起 AskUserQuestion（通道二选一/窗口确认）**未收到回答** → 按计划 §8.c/D3 预设降级
  **file-only**（smtp/webhook 驱动已实现但 enabled=false），通道后补入 BACKLOG。非偏差，是计划自带回退路径。
- 开工基线（零改动验收对照，全部只读抓取）：
  - crontab sha256 `dbc6a854c94d0a7627001cbc1258bcc159c0d3b7e76381576f5ca3e8b3645fa1`（5 行原样记录在案）
  - pusher 代码 sha256：pusher.py `c711978d…`、selftest.py `fb685304…`、tables.py `228575b2…`、
    run.sh `4d44a89c…`、config.json `47d00e80…`、config.template.json `0bec8eb5…`
  - state/ 全量 22 文件 sha256 已快照（ receipts-20261028..20261103 / ALERT-2026110{1,2,3} /
    watermark / reconcile-* / last-run-* / sent.jsonl / push.lock / cron.log 等）
  - 仓库 pusher 源码与宿主逐字节一致（本地 sha 与宿主相同）。
- state 文件格式确认（源码+实测样例）：last-run-real.json 为数组（单表异常含 error 键）；
  watermark 表→"YYYY-MM-DD HH:MM:SS"（CST）；receipts 行内 ts 与文件名均为**容器 UTC naive**
  （`dt.datetime.now()` 于 UTC 容器）；reconcile day_str 为 CST；/var/log/cron 行含
  `CMD (`/`CMDEND (`（CROND pid 互不相同，按时间顺序配对）。

### Ph1 本地 selftest
- `python selftest.py` → `==== 66/66 PASS ====` EXIT=0（首轮）；修复 C3/C10 后 `==== 68/68 PASS ====` EXIT=0。

### Ph2 通道探针
- **免探**：Ph0 未取得 SMTP/webhook 参数，file-only 模式无外发通道可探（计划 §8.c）。
  通道后补时按计划 §Ph2 命令探测（`/dev/tcp/<host>/<port>`、`curl -m 5` GET）再启用。

### Ph3 上传落位
- `python deploy_8156_infwatchdog.py upload` → DEPLOY_EXIT=0：
  环境探测（Python 3.9.9、pusher state 在位、无既有 unit）→ 8 文件 sha256 全 OK（base64 分块）→
  落位 700 root + config.json 600 → 宿主 `python3 selftest.py` **66/66 PASS SELFTEST_RC=0**。
- C3/C10 修复后重跑 upload → 8 文件 sha256 OK → 宿主 **68/68 PASS SELFTEST_RC=0**。

### Ph4 手动演练
- 第一次 `check`（EXIT=1）：暴露两处实现缺陷（见"偏差与修复"）＋3 项真实退化（C5/C6/C7）。
- 修复重传后第二次 `check` → CHECK_RC=1，OVERALL WARN：
  - C5 WARN：24h 失败 19 行（network=19）——真实回执（前置机瞬时拒连、重试后成功的已知模式）
  - C6 WARN：昨日对账 total_repushed=2419>500——11-02 事故 reconcile 兜底补齐的真实结果，次日自清
  - C7 WARN：转发既有 ALERT-20261103/02（GAP-RECOVERED 行）——计划 Ph4 明示属正常
  - status.json 产出（c5 耗时 5.8s，receipts 扫描）；OPS-ALERT-20261103 落盘
- `test-alert` → `test-alert OK drivers: file` TEST_RC=0，OPS-ALERT 含 [TEST] 段。

### Ph5 挂 timer
- `python deploy_8156_infwatchdog.py units` → UNITS_EXIT=0：4 unit 安装 644、daemon-reload、
  enable --now 两 timer。list-timers 留证：
  `inf-watchdog.timer` NEXT Tue 2026-11-03 18:55:00 CST；`inf-watchdog-daily.timer` NEXT Wed 2026-11-04 08:00:00 CST。

### Ph6 观察（≥3 周期，实测 4 周期）
- 18:55/19:00/19:05/19:10 四周期全执行：journalctl 每轮 `OVERALL WARN exit=1 driver_errors=0`
  （如实退化、无假 CRITICAL）；status.json 每轮刷新（末轮 mtime 19:10:25，level=WARN）。
  18:55 首轮实时确认：timer 准点触发（18:55:10 Starting，Main PID python3）。
- 注：systemd 对退出码 1 显示 "Failed with result 'exit-code'" 属预期语义（degraded 即非零），
  timer 不受影响继续触发（19:05/19:10 均正常续跑）；已写入组件 README 说明，真实状态看 status.json。
- 手动 `systemctl start inf-watchdog-daily.service`（19:12:18 完成）→
  `state/daily-20261102.txt` 产出：班次 **cron CMD 88 班 / CMDEND 88 完成**（与班表精确吻合：
  0-5 点 24 + 9-23 点 60 + 8:30/23:58/00:27/00:05 = 88）；各表 admission 124/daily_course 123/
  exam 487/exam_item 393/first_course 102，failed=0，client-validate 尾差 17 行单列；
  对账 total_repushed=2419 failed=0；水位最大 opTime 18:55:09（0.3h）；**无患者字段**；
  "昨日告警 0"如实显示（watchdog 昨日尚未部署）。

### Ph7 收尾（实测）
- **零改动复验**（开工基线对照）：
  - crontab sha256 `dbc6a854…` **一致**；pusher 6 文件（pusher/selftest/tables/run.sh/config.json/
    config.template.json）sha256 **全部一致**。
  - state/ 22 文件：**17 个逐字节一致**（ALERT-2026110{1,2,3}、receipts-20261028..20261102、
    reconcile-*、reconcile.log、fullrun.log、last-run-mock/reconcile.json、push.lock 空文件）；
    5 个变化文件 `last-run-real.json`/`cron.log`/`receipts-20261103.jsonl`/`watermark.json`/
    `sent.jsonl` 全部为推送器 */15 班次既定写入路径（计划 §0.2 明载），无新增/删除文件，
    watchdog 零写入（与 selftest T53 哈希断言互证）。
- 组件 README 补退出码语义说明后已单独重推宿主（sha256 OK）。
- §5 回写实测：01 时间线 +1 行（守护告警）、02 手册（§三补 watchdog 平行说明/§四巡检改首选看
  watchdog+旧命令留后备/§六补 watchdog 回退）、03/interfaces 无接口变化不动、STATE 顶部 +1 条、
  BACKLOG（新增"通道后补 P2/首夜观察 P1"，原 P0 待执行划线转已交付）、业务 README（更新时间/
  plans 状态/证据路径表+watchdog 行）、数据上报总索引第 4 号行；`python scripts/project_docs.py
  inventory` 通过（见下）。

## 偏差与修复（如实记录）

1. **Ph0 占位符未填**：任务指令中告警通道输入为未填写的【填：…】模板。向用户提问未获回答，
   按计划 §8.c/D3 file-only 降级（计划明文"不得因此阻塞其余交付"），通道后补入 BACKLOG。
2. **C1 维护窗边界实现为 06:00–08:34**（计划文字 06:00–08:29）：8:30 迟班正常完成需 15–25 秒，
   而 watchdog timer 恰在 08:30:00 整点触发，按字面截到 08:29 会每天 08:30 一次假 CRITICAL
   （与 D6 防轰初衷矛盾）。补 5 分钟完成宽限；真缺席最迟 08:35 告警。selftest T04/T05 覆盖边界。
3. **C3 只按"当前连续"跳班告警**（计划文字"连续同秒≥3/≥8"未区分当前/历史）：宿主演练实测——
   当前日志文件含 11-01~11-03 事故的 63 次同秒连击，若按历史最大值告警会在轮转前永久假 CRITICAL。
   修为只看 trailing 连击，历史最大仅入 detail 参考。selftest T14b 复现该场景。
4. **C10 按 docker 实际小写输出比较**（'running'）：演练实测 `docker inspect --format
   '{{.State.Status}}'` 返回小写；原实现按 'Running' 严格比较导致假 CRITICAL。selftest 桩同步修正。
5. **C8 按六表最大 opTime**（计划原文即"六表最大"）：death 表实测可 8 天不推进（稀疏），
   逐表判会天天误报——按计划原文取最大值实现，selftest T35 覆盖。
6. **OPS-ALERT 行级别前缀重复**（外观）：file 驱动写了 level+已含 level 的 subject，去重。
7. **watchdog state/ 保留演练期记录未清**：首轮演练的假 CRITICAL（修复前）与后续 [RECOVERED]
   忠实留痕于 OPS-ALERT/alerts.jsonl/dedupe；首轮 c3 假警的恢复通知会在首夜由 timer 自然发出，
   属诚实记录而非缺陷。

## §7 验收清单（9 项）

- [x] 本地+宿主 selftest 全绿（68/68×2，含白名单脱敏断言 T55/T56/T62）
- [x] check 手动运行（OVERALL WARN＝如实报告既有退化，非 HEALTHY 但零假警）+ status.json 产出
- [x] test-alert 每个启用驱动送达（file 必有，file-only 模式下 file=OK）
- [x] 两个 timer 激活，list-timers 留证（Ph5 输出）
- [x] ≥3 个 check 周期无假告警，status.json 持续刷新（Ph6 四周期）
- [x] daily 手动触发产出晨报（聚合数正确、无患者字段）
- [x] 推送主链路零改动（crontab/pusher 代码 sha256 前后一致；state/ 仅推送器自身演化——见 Ph7 明细）
- [x] 通道不可达时优雅降级 file-only 并入 BACKLOG（适用：Ph0 即 file-only）
- [x] §5 回写完成，session ended，交接含全部命令/输出/偏离

## 运维速查（详见 implementation/deploy/inf_watchdog/README.md）

- 状态：`cat <prod-path>/exchange/inf-watchdog/state/status.json`
- 告警：`cat <prod-path>/exchange/inf-watchdog/state/OPS-ALERT-$(date +%Y%m%d)`
- 晨报：`state/daily-YYYYMMDD.txt`（08:00 自动；手动 `systemctl start inf-watchdog-daily.service`）
- 回退：`python deploy_8156_infwatchdog.py uninstall`（推送主链路零影响）
- 通道后补：仅改宿主 config.json smtp/webhook 段 + test-alert 验证，无需重部署
