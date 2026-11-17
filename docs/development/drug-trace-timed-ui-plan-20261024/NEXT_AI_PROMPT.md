# 可直接复制给下一AI

请在 `F:/python/blyb` 执行码上放心**定时上报可视化**长任务，先读：

1. 根 AGENTS.md 与 docs/ai/START_HERE.md、STATE.md、HANDOFF.md、BACKLOG.md。
2. docs/development/drug-trace-timed-report-20261024/README.md 与 RESULT.md（定时上报已上线的事实与运维命令）。
3. **本次完整执行任务书：docs/development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md**（TUI-00—TUI-09、API 契约 §5、验收 A01—A08、边界 §7 全部以它为准）。

任务：把定时上报的授权/配额、运行健康、每日回执、平台终态、错误明细呈现到管理界面药品追溯页；日报从服务器文件改为入库（新表 012 迁移）+ 三个新查询 API + 驱动 v2 末尾上报；页顶过时的"MOCK/合成"横幅改为动态。契约字段名、健康规则、UI 布局均已在任务书 §5 给定，照做不另造。

要求：先 ai_session 登记实际工具/模型与范围，顺序执行 TUI-00—TUI-09；一次接力完成可执行项并交付可审查成果，不在每步询问是否继续。生产 <prod-host> 定时器每日 06:10 在跑——**部署窗口避开 06:00–08:30 且须用户在场确认**；本任务零门禁改动（A07），测试不连生产不真发。

明确禁止：改发送/质量/证据/配额语义；让 API 读宿主文件或查 systemd；自动轮询/重试；动既有回执文件与冻结包；无范围全表扫。

结束交付：任务目录 RESULT/证据/截图、生产部署留证、全量回归不降基线、更新共享 STATE/HANDOFF/BACKLOG 与业务 README、写 session 交接并 ai_session end。实测/未测/阻断分开写；未取得用户部署窗口确认时，本地完成 TUI-00—TUI-07 并把 TUI-08 标 BLOCKED 交付，不得擅自上生产。
