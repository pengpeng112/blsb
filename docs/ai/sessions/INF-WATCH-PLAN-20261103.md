# INF-WATCH-PLAN-20261103 · 传染病守护告警执行计划编制（交外部执行 AI）

- 会话：`20261103T024922Z-0a790787`。编制：ZCode/GLM-5.3，2026-11-03。
- 产出：[数据上报/4、传染病病历数据上报/plans/20261103-守护告警执行计划.md](../../数据上报/4、传染病病历数据上报/plans/20261103-守护告警执行计划.md)（自包含，执行 AI 从 §0 通读即可开工）。
- 触发：用户令"生成详细计划，让别的 AI 一次性开发完，好好论证"。目标 = T0 计划 §4 遗留守护项，经 11-03 事故重新定义。

## 关键论证结论（详见计划 §0.3 D0–D7）

1. **D0 改道**：11-03 事故的失效模式是"检测缺失"（71h 无人知晓）而非推送节奏——不做"cron 换常驻进程"，新增独立检测告警层；原设想无限期搁置。
2. **D1** systemd timer+oneshot 即守护形态（<prod-host> 有 blyb-daily-upload.timer 成熟先例），无长驻进程风险。
3. **D2** 数据面/控制面分离：watchdog 纯只读+告警、零自愈动作（自愈已由 timeout 层+reconcile 承担），故障域隔离。
4. **D3** 告警通道 driver 三档降级（file 必开 > smtp > webhook）——全工作区无外发先例、<prod-host> 出站能力未知（药品追溯外联专门走 DMZ），通道参数列为 Ph0 用户输入，缺不阻塞。
5. **D4–D7** 宿主部署（非容器）；外发白名单脱敏（禁患者字段，receipts id 不进摘要）；防轰+恢复通知；5 分钟检测周期。
6. 检查项 C1–C10 阈值全部有实测依据（*/15 班 15–25s、维护窗 06:00–08:30、timeout 20min 上限、UTC 命名坑、client-validate 已知尾差等，计划 §2.5 坑清单）。
7. 一次性开发可行性：全新组件、零侵入主链路（验收含"主链路 sha256 开工前后一致"）、selftest 可全覆盖 → 适合 one-shot。

## 调研依据（本轮实际查看）

- `docs/development/drug-trace-timed-report-20261024/`：timer 单元格式（OnCalendar Asia/Shanghai、Persistent）、install_timer.py 安装/验证/回退模式——计划直接引用为先例。
- `implementation/deploy/` 全目录 grep：无任何 smtp/webhook/notify 告警代码 → D3 降级设计的依据。
- 11-03 事故证据链（[INF-LOCK-INCIDENT-20261103](INF-LOCK-INCIDENT-20261103.md)）：C1/C3/C5/C7 检查项直接对应事故暴露的盲区。

## 同步回写

README plans 行（新计划标注"待执行"）；STATE 顶部"待执行"条目；BACKLOG INF 节（P0 待执行 + §4 改道划线）。
交接本文件 + ai_session end。
