# 定时上报日检（2026-10-31 09:40）

任务 DTR-TIMED-CHECK-20261031；session `20261031T014013Z-ac750225`；ZCode/GLM-5.3。
用户问"当前上报是否正常"→ 只读检查（跳板 systemctl/ls/cat/psql SELECT + sjzc 连接器查索引）。
证据：`evidence/check-20261031-20261031T014045Z.txt`。

## 结论：不正常——两个凌晨批次均 FAILED、两天零发送，但补发条件已齐备

- 10-30 01:00（窗口 10-29）：FAILED `collect_progress_stalled`（02:29 放弃，采集停在 425 单）；
  采集服务端实际 **11:27 DONE**（3133 单/valid 1692）。
- 10-31 01:00（窗口 10-30）：FAILED `collect_timeout`（05:00 放弃，4h 上限，均速升至 ~3.5s/查询）；
  采集实际 **今晨 07:45 DONE**（2844 单/valid 1566）。
- 配额停 8413/80000（10-29 14:20 后零消耗）；**PREPARED 积压 3258 = 两窗口 valid 之和**；
  10-27 窗口遗留 ACCEPTED 正常自动推进（48h 内 CONFIRMED +809）。
- **FBI 未建**（ALL_INDEXES 无 IX_DT_BILLKEY）——DTR-VIEW-TUNING-20261029 的根治方案未落地，
  采集 ~3.5s/查询且出现过进度停滞，明晚批次预计仍失败。
- **补发条件齐备且无 CANCELLED 遮蔽**（两窗口最新运行均为 DONE，驱动秒匹配）：
  ① `systemctl start blyb-daily-upload.service` → 发 10-30 窗口 ~1566 单（~1-1.5h）；
  ② 随后容器内 `main('2026-10-29')` 定向补发 1692 单（BLYB_DAILY_REPORT_ENABLED=0 保留历史行）；
  两步严格串行。待用户确认执行。
