# 定时上报日批只读检查（2026-10-29）

任务 DTR-TIMED-CHECK-20261029；session `20261028T231447Z-c7b668af`；ZCode/GLM-5.3。
用户问"今天推送是否正常"→ 授权只读检查。工具：`check_today.py`（跳板→<prod-host>，
systemctl/ls/cat + 仅 SELECT psql；零写入零发送）。证据：
[evidence/check-today-20261028T231519Z.json](../../development/drug-trace-timed-report-20261024/evidence/check-today-20261028T231519Z.json)、
[check-today-r2.json](../../development/drug-trace-timed-report-20261024/evidence/check-today-r2.json)。

## 结论（07:16 CST 时点）

1. **今天 06:10 timer 准时触发，此刻仍在采集阶段未到发送**：run RUNNING（bills_seen 675、
   valid 293、window_completed=false、progress 07:16 持续推进）；service activating；
   last-error 空。按近两天节奏（采集 3-4h 完不成、驱动等待上限≈4h），今天很可能再次
   collect_timeout（约 10:10 前后见分晓）。
2. **10-27、10-28 两个日批 FAILED（collect_timeout）**：HIS 采集查询变慢——10-28 回执
   source_metrics：4641 次查询/8143 秒（均值 ~1.75s/次），2275 单 4 小时仍未完成窗口。
   10-26 窗口靠当天 09:01 手动补跑成功（DB 日报行 manual OK sent 976）。
3. **积压：10-27 窗口 1792 张 VALID 单已 prepared 从未发送**（近 24h 零发送尝试、
   attempts_1d=null、配额停在 4820/80000 最后更新 10-27 15:21、PREPARED 积压 2074）。
   10-29 02:17 有人对 10-27 窗口新建采集又 CANCELLED（bills_seen 100），干预未完成。
4. **生产驱动为 v3**（回执含 report_push/error_code/candidate_count_known、SEND_WORKER_TIMEOUT 处理、
   10-25 有 waiting_driver_replaced 与 maintenance-reconstruction）。归因已确认：昨夜 03:10 由
   DTR-SCOPE-DEPLOY-20261028 会话部署（STATE 置顶条目：驱动 v3 SHA 2f33c026、迁移 013 系 10-27 既有），
   非本会话所为；其待办"明晨复核 06:10 v3 首跑"即本次检查对象。该会话还确认 v1/v2 曾误复用同窗口
   门诊日批运行（列表接口无连接器字段）及住院视图分支二全表扫 3.9s/查询——与 10-27/10-28
   collect_timeout 直接相关。生产日报表（迁移012）已有 6 行，report_push ok=true 证明上报链路工作。

## 建议（未执行，须用户决定）

- 采集慢根因查源端（视图执行计划/数据量增长），或驱动改"提前采集/未完成不断言 FAILED/次日续跑"。
- 积压补发：手动 `systemctl start blyb-daily-upload.service`（幂等，CONFIRMED 不重发）。
- 生产 v3 驱动回写仓库或归档副本说明，恢复"仓库副本=源头"。
- 10-24 快照遗留 UNKNOWN 206 与查询 VERSION_CONFLICT 轮询上限错误量较大，另立观察。
