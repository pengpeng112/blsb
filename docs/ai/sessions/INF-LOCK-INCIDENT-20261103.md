# INF-LOCK-INCIDENT-20261103 · 传染病上报"都失败"排查：取数挂起占锁 71 小时（恢复+双层根治）

- 会话：`20261103T012920Z-7f5f429c`（INF-LOCK-INCIDENT-20261103）
- 触发：用户 2026-11-03 上午报"传染病上报都是失败了？什么原因如何预防"
- 执行：ZCode / GLM-5.3。范围：`数据上报/4、传染病病历数据上报`、`implementation/deploy/inf_pusher`
- 结论先行：**不是推送失败，是及时性失败**——数据零丢失（三天 reconcile 补推全部受理 failed=0），但 10-31 13:30 起白天全部班次被跳，所有数据迟到约一天。

## 一、诊断（全部只读取证）

| 证据 | 命令 | 结果 |
|---|---|---|
| cron 表完好 | `crontab -l` | 五行原样（*/15/8:30/23:58/00:27/00:05 reconcile） |
| 班次秒退 | `grep CROND /var/log/cron \| tail` | 09:00/09:15 两班 CMD 与 **CMDEND 同秒**（正常 15–30s）→ flock -n 拿不到锁静默退出 |
| 挂起进程 | `ps -eo pid,ppid,etime,...` | `python pusher.py --table all --mode real`（PID 3235597）+ flock/docker exec/sh 父链，**etime 2-19:59:03**（10-31 13:30 起） |
| 锁被占 | `flock -n .../push.lock -c 'echo LOCK-FREE'` | `LOCK-HELD` |
| 推送未失败 | `cat reconcile-2026110{1,2,3}.json`、ALERT-* | 00:05 reconcile 每日补推 375/1229/2419 条 **failed=0**；GAP-RECOVERED 行在 cron.log |
| 跳班规模 | syslog CMD/CMDEND 同秒配对统计 | 可见窗口（11-01 00:00 后）245/273 班秒退；全程约 **300 班**（71 小时） |
| 最后成功班 | `ls -la last-run-real.json` | mtime **10-31 13:15**（13:30 班挂起） |

根因：`fetch_rows` 的 oracledb 连接/取数**无任何超时**，一次 Oracle 侧无响应即永久挂起；挂起进程持有 flock → 后续所有班被 `flock -n` 秒退。与 10-31 10:45 挂起同源（当时已记 BACKLOG P2，未根治，当天下午复发）。

## 二、恢复（09:29–09:33）

1. `kill 3235597`（挂起 python）→ `kill 3235574 3235572 3235569`（docker exec/flock/sh 链）→ 进程 0、`LOCK-FREE`。挂起班零输出=零发送，kill 无数据风险（与 10-31 处置一致）。
2. 09:30 cron 班自动执行：**24 秒**（09:30:01→09:30:25）推完恢复期积压 792 条（death1/admission22/exam159/first_course189/daily_course151/exam_item270），全部 `failed=0`；receipts-20261103.jsonl 开始增长。

## 三、双层根治（预防）

### 第 1 层：代码（pusher.py，事故根因处）

- `fetch_rows`：`oracledb.connect(..., tcp_connect_timeout=15)` + **`conn.call_timeout = 300000`**（毫秒；单次往返上限，超时抛 DPI-1067 而非永久阻塞 → 进程退出 → 锁释放 → 下一班自动重试）。
  - **踩坑（如实）**：首版把 call_timeout 当 connect() 参数传，容器 oracledb 3.4.2 报 `connect() got an unexpected keyword argument 'call_timeout'`——被"容器内手动真实班"这道红线关卡当场暴露（此时新增的单表异常隔离把 6 表错误优雅记为 `{"table":..,"error":..}` 而非崩溃，机制本身得到实战验证）；改为连接属性后通过。本地 fake-oracledb 打桩用例同步修正（并挪到打桩用例之前，避免拿到旧桩）。
- 单表异常隔离：`main()` 循环与 `run_reconcile()` 循环各包 try/except——单表取数/网络异常记 `{"table":n,"error":...}`（reconcile 计 failed=1 触发 ALERT），其余表照常完成。
- selftest：51 → **57 项**（fetch 连接超时参数×2、单表隔离×5），本地/<prod-host> 宿主/容器三环境全绿。

### 第 2 层：crontab（兜底，防任何未预见挂起）

五行加 `timeout -k 60 1200`（reconcile 1800）：任何班最多 20 分钟被强杀、锁必释放。备份 `/root/crontab.inf.bak-20261103`；`grep -c $'\x1b'`=0 验无污染。
（取舍：`timeout` 杀 flock 后 docker exec 可能成孤儿——第 1 层连接超时保证 python 自身会退出，两层互补；reconcile 行保持无 flock 设计，本次事故正是它每日兜底。）

### 部署与验证顺序（守红线）

本地 57/0 → `deploy_8156_infpusher.py` 上传（sha256 OK + 宿主 57/0）→ 备份旧版 `/root/infpusher-bak-20261103/` → 容器落位 chown 10001 → 容器 57/0 → 手动真实班（6 表正常 sent 51 failed=0）→ crontab 加 timeout → **09:45（15s）/10:00（17s）两班验证通过**（CMDEND 延迟出现=真跑，非秒退）。

## 四、影响面如实评估

- **数据完整性：零丢失**。10-31 13:30–11-03 09:30 的全部文书已由每日 00:05 reconcile 补推并受理（合计 375+1229+2419=4023 条 + 恢复班 792 条中的当日部分）。
- **及时率质控：10-31 至 11-03 晨的文书全部迟到约一天**（向国家平台的解释区间，已写 BACKLOG 口径备忘）。
- 11-01/11-02/11-03 三个 ALERT 文件与 GAP-RECOVERED 行为**事故痕迹**（每日凌晨大批量补推触发），保留作证据，不需清理（次日正常班次下 repushed 回落至个位数即恢复常态）。

## 五、遗留

- 今晚三班（23:58/00:05/00:27）加固后首夜证据次晨复核（BACKLOG P1）。
- 常驻守护评估（T0 计划 §4）不变：*/15 连续无 GAP ≥5 天后再议。
- 未触碰：state/ 历史回执零改动；inf.env 未读未改；无 DDL；双推红线未近（仅 <frontend-host>）。

## 六、铁律回写

01 时间线 +1 行（11-03 故障修复，含 call_timeout 属性踩坑）；02 手册三处（cron 表 timeout 说明、巡检"跳班识别"处置法、回退备份名）；STATE 顶部 1 条；BACKLOG INF 节改写（P2 根治划掉+首夜观察+质控口径）。03/interfaces 无接口状态变化未动。本文件 + ai_session end(completed)。
