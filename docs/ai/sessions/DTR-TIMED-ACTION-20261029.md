# 定时上报三项处置执行交接（01:00 定时 + 两窗口补发）

任务 DTR-TIMED-ACTION-20261029；session `20261028T232355Z-d76fb345`；ZCode/GLM-5.3；2026-10-29。
用户授权：回执复核、10-27 积压补发（手动 systemctl start）、定时器改凌晨 01:00。

## 结果（全部实测，证据在本目录 evidence/）

1. **定时器 06:10→01:00 Asia/Shanghai**（07:25 执行）：unit 备份
   `/root/blyb-daily-upload.timer.bak-20261028T232439Z`；list-timers 实测下次 2026-10-30 01:00 CST。
   `evidence/timer-change-20261028T232439Z.txt`。部署避让窗口改为避开 00:30–07:00（README 已更新）。
2. **今日 06:10 批次第 3 次 collect_timeout**（回执 `20261029-061029.json`）：采集 2050 单时到 4h 上限，
   均速 1.73s/查询；发送未开始。
3. **10-28 窗口补发**（10:15 systemctl start → 15:36 结束，回执 `20261029-101514.json`）：
   采集续至 DONE（3255 单/valid 1801）→ **1801/1801 发送零失败**，终态 CONFIRMED 1800+PARTIAL 1，
   配额 4820→6621，elapsed 5h21m，日报行 10-28 更新 OK（sent 1801）。lookback 因 rescan 运行超 30 分钟
   等待报 LOOKBACK_RUN_NOT_DONE（best-effort，keys 570 未发）。
4. **10-27 积压定向补发**（15:46–22:37，rc=0）：
   - 首次尝试秒退 `collect_not_done`——驱动按"窗口匹配取列表第一个（最新）"复用运行，而 10-27 窗口最新
     运行是 scope 冒烟误建后被 CANCELLED 的 `a33cca7d`，遮蔽了旧 DONE 运行 `afa0bdef`
     （回执 `20261029-154639-backfill-1027.json`）。
   - 对策：经 API 新建同窗口同参数采集运行 `238c79c2`（15:51 起幂等重扫 3205 单全部 idempotent、
     19:02 DONE）+ 宿主值守脚本（`/root/backfill-1027-launch.sh`，等 DONE 自动在容器内运行
     生产驱动 `main('2026-10-27')`、`BLYB_DAILY_REPORT_ENABLED=0` 保留该日 FAILED 历史日报行）。
   - 结果：**1792/1792 补发零失败**（resumed 1792），配额→8413/80000，**PREPARED 清零**；
     10-27 终态 CONFIRMED 852+ACCEPTED 939+PARTIAL 1（ACCEPTED 待查询补进）。
     回执在 `20261029-backfill-1027-launch.log`；设置证据 `backfill-setup-20261029T075113Z.txt`。
5. **共享文档**：timed-report README（01:00、避让窗口、定向补发 recipe）、STATE、BACKLOG 已更新。

## 未完成与风险（如实）

- **采集慢未根治**：~1.7s/查询、全天窗口 ~6h，超驱动 4h 采集等待上限——**10-30 01:00 批次大概率仍
  collect_timeout**；失败后白天手动补跑即可完成当日发送（本日已两次验证）。根治=视图调优
  （scope 待办 DBA EXPLAIN）或调大驱动 COLLECTION_WAIT_SECONDS（驱动属 scope 会话 v3，勿双头改）。
- 10-24 遗留 UNKNOWN 206、查询轮询上限 skipped_exhausted（205/1131）待观察；lookback 0 码单补报
  两次未跑成（等待窗口 30 分钟内 rescan 未完成），随根治后自然缓解。
- 今晚 01:00 定时批次（窗口 10-29）无需干预即会自动触发；晨间建议查回执+必要时补跑（recipe 在 README）。
