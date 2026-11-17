# 定时上报可视化 · 开发计划（2026-10-24）

任务 DTR-TIMED-UI-PLAN-20261024（ZCode/GLM-5.3）：应用户要求编制的长任务开发计划，
**本轮只做分析与计划，未改任何代码**。执行者按
[EXECUTION_TASK.md](EXECUTION_TASK.md)（TUI-00—TUI-09）实施，可直接把
[NEXT_AI_PROMPT.md](NEXT_AI_PROMPT.md) 交给执行 AI。

## 要做什么（一句话）

管理界面药品追溯页新增"定时上报"面板：授权/配额卡、自动化健康横幅、每日运行日报表（含错误下钻）、
平台终态按日统计、异常提交清单；配套：日报入库（迁移 012 新表）、三个新查询 API、驱动 v2 末尾上报、
页顶过时"MOCK/合成"横幅动态化。

## 差距分析结论（详见任务书 §1）

| 差距 | 方案 |
|---|---|
| 回执只在服务器文件 | 新表 drug_trace_daily_report + 驱动跑完 POST（幂等 upsert），API 不读宿主文件 |
| 无按日统计 | upload-stats API（submission×state×attempt 聚合） |
| 无授权/配额视图 | send-scope API（证据 scope + used/max + 有效期） |
| 无自动化健康 | 数据推导：下次预计=最后报告日+1 天 06:10；MISSING/PARTIAL 规则见 §5.4 |
| 错误无下钻 | submission-outcomes API + 面板详情区 |
| 横幅过时 | readiness 动态 computed |

## 关键边界

零门禁改动（验收 A07）；生产定时器在跑，部署窗口避开 06:00–08:30 且须用户确认；
测试合成数据不连生产；文档/状态同步与 session 交接齐备。
