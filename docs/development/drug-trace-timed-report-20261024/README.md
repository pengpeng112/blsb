# 定时上报落地（2026-10-24）

任务 DTR-TIMED-REPORT-20261024（ZCode/GLM-5.3）；用户当日明确授权"连接数据库核查剩余待办并
进行定时的上报"。结论入口：[RESULT.md](RESULT.md)。

**已上线**：<prod-host> systemd 定时器 `blyb-daily-upload.timer`（~~每日 06:10~~ **2026-10-29 起改为每日 01:00 Asia/Shanghai**，用户授权；原单元备份 <prod-host> `/root/blyb-daily-upload.timer.bak-20261028T232439Z`）→
容器内驱动 `daily_upload_driver.py`：采集昨天全窗口 → 自动/续排 PREPARED 的 VALID 门诊单逐单
发送（≤40/分钟，遵 DMZ 60/min 限流）→ 非终态提交查询补进 → 回执落 `/var/log/blyb/daily-upload/`。
首次触发 **2026-10-25 06:10 CST**（处理 10-24 全天）。

**发送授权（EXT-12 standing scope，可随时 revoke）**：窗口 2026-10-24 00:00 → 2026-11-23 00:00（GMT+8），
药房白名单 050121/050102/050101/050116/050117，max_bills 80000，证据 0eb0b906（used 计数保留不重置）。
**10-12..10-23 缺口未发送**（待原厂商/药学部确认 E08'）；既有 CONFIRMED 单永不重发。

**实测**：监督采集 51 单（49 UNKNOWN 正确等待 + 2 VALID）幂等重扫 0 重复；冒烟发送 1 单
HTTP200 受理 → 自动查询推进 **CONFIRMED**（真实成功第 2 单）；配额 used=2/80000。

| 文件 | 内容 |
|---|---|
| [RESULT.md](RESULT.md) | 结论、验证记录、运维/回滚、剩余风险 |
| [daily_upload_driver.py](daily_upload_driver.py) | 容器内每日驱动（幂等采集→串行发送→查询补进） |
| [blyb-daily-upload.service](blyb-daily-upload.service) / [.timer](blyb-daily-upload.timer) | systemd 单元（已装 <prod-host>，active） |
| [evidence_api.py](evidence_api.py) / [run_remote_api.py](run_remote_api.py) | 证据登记 API 脚本与跳板执行器 |
| [pipeline_api.py](pipeline_api.py) / [smoke_api.py](smoke_api.py) | 监督采集验证 / 发送冒烟（已执行） |
| [prod_status.py](prod_status.py) / [run_timed_verify.py](run_timed_verify.py) | 生产只读状态 / HIS 复核（已执行） |
| [install_timer.py](install_timer.py) | 定时器安装脚本（重装/修复用） |
| [evidence/](evidence/) | 全部运行回执（状态/证据/采集/冒烟/安装） |

**运维速查**（<prod-host> root）：
- 状态：`systemctl list-timers blyb-daily-upload.timer`；`ls /var/log/blyb/daily-upload/`
- 手动触发一次：`systemctl start blyb-daily-upload.service`（幂等：CONFIRMED/REJECTED 跳过）
- **定时窗口（2026-10-29 起 01:00）**：采集实际需 ~6h+（HIS 查询 ~1.7s/次）超过驱动采集等待上限（4h），
  日批可能仍 collect_timeout——失败后白天手动补跑一次即可完成当日发送（10-29 实例）。
  **生产部署/升级避让窗口相应改为避开 00:30–07:00**（原 06:00–08:30 基于 06:10 触发）。
- **定向补发某历史日积压**（2026-10-29 实测 recipe，绕开"最新运行遮蔽"坑）：
  ① 若该窗口最新运行是 CANCELLED/FAILED，驱动会匹配到它并秒退 `collect_not_done`——
  先经 API 新建同窗口采集运行（幂等重扫 ~6h）；
  ② 宿主值守等其 DONE 后在容器内以 `main('YYYY-MM-DD')` 运行驱动
  （`BLYB_DAILY_REPORT_ENABLED=0` 可保留该日原日报行不被覆盖；参考本目录 evidence/
  `backfill-setup-20261029T075113Z.txt` 与 `monitor7-*.txt`）。
- **暂停上报**：`systemctl stop blyb-daily-upload.timer`（回执保留）
- **解除发送授权**：POST `/api/v1/drug-trace/evidence/0eb0b906…/revoke`（或管理 UI 撤销）
- 彻底卸载：stop/disable timer + 删 `/etc/systemd/system/blyb-daily-upload.*` 与 `/opt/blyb/tools/blyb-daily-upload.py`
