# TUI-05/06 前端验证记录（2026-10-24，ZCode/GLM-5.3）

## 构建产物

- `npm run build`：**✓ built in 1.92s**（dist/index.html 0.40kB、css 3.61kB、js 171.94kB；
  基线 js 161.36kB，增量 +10.58kB 为新面板+横幅 computed+scoped 样式）。
- 产物由 core_api 同源托管 `/ui`（apps/core_api/app.py:1631-1636），验证栈直接使用。

## 本地合成栈浏览器验证（T-A04）

栈：`evidence/serve_timed_ui.py`（复用 tests/e2e/serve_drug_trace_ui.py 合成栈 +
seed：send 证据 APPROVED/scope 5 药房/配额台账 used=2、日报 3 行
[10-23 OK / 10-22 PARTIAL_FAILURE 带 2 条错误 / 10-21 FAILED]、提交 3 笔
[CONFIRMED+ACCEPTED/UNKNOWN+HTTP500/PREPARED 未发]）。全部合成数据，零外发。

浏览器（ZCode 内置浏览器，DOM snapshot 断言 + 截图）实测通过：

| 项 | 结果 |
|---|---|
| 面板四区渲染 | ✓ 授权行（窗口 10-24→11-23｜药房 5 家｜配额 2/80000｜APPROVED｜目的地）；健康行（上次 10-23 OK 耗时 75 分钟｜下次预计 10-24 06:10｜连续正常 1 天）；日报表 3 行（候选/发送/错误/结果/耗时/详情）；每日实际（10-23: CONFIRMED 1/UNKNOWN 1/合计 3/尝试失败 1）；异常提交（UNKNOWN/500/DTRT-SEED-UNKN + 查询状态按钮） |
| 错误下钻 | ✓ 点 10-22 详情 → 日报详情：窗口/采集 DONE 摘要/sent=1740/prepared_new/skipped_terminal/查询补进 120 + errors 两条（REMOTE_REJECTED send / VERSION_CONFLICT prepare） |
| 横幅动态化 | ✓ is_mock=true → 「MOCK/合成（仅本地演练，不外发）」；DB 切 is_mock=false 后刷新 → 「正式链路（目的地已启用且非 Mock）」 |
| 空态 | ✓ 删除日报后刷新 → 健康显示「尚无运行记录」（不判 MISSING）、日报表显示提示文案，无报错；其他区域不受影响 |

截图（本目录）：
- `ui-01-mock-banner-panel.png`：MOCK 横幅 + 面板四区 + 10-22 详情下钻展开
- `ui-02-real-banner.png`：同一栈切真实目的地后横幅「正式链路」
- `ui-03-empty-state.png`：无日报空态

## 过程备注（如实）

- 打包版登录页默认后端 127.0.0.1:8000（BACKLOG 已登记的已知小项），验证栈在 8788，
  浏览器内通过 localStorage 写入等价登录态（与 login() 成功路径写入的键一致）后复验；
  该小项不影响生产同源发行（VITE_CORE_DEFAULT 注入）。
- IAB 环境下 Playwright `click()` 动作被拦截（fill/press/evaluate 正常），详情/刷新
  按钮改用页面内 DOM click 触发——等价用户点击，DOM 断言确认了渲染结果。
- UI 列适配说明：日报表列取自 §5.1 列表行契约字段（日期/来源/候选/发送/错误/结果/
  耗时），「采集摘要」「跳过」按任务书布局归入单日详情区展示（列表契约无此二字段，
  未另造 API 字段）。
