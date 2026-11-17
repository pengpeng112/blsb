# FIX-09 浏览器复验记录（DTR-TIMED-UI-FIX-20261024，2026-10-24）

栈：`evidence/serve_timed_ui.py`（端口 8788；seed 已改：D-1 OK、D-3 OK、D-2 缺失）。
浏览器：ZCode 内置浏览器（IAB）；断言以 DOM snapshot 为准，截图存本目录。
登录态：admin 令牌（platform_admin+operator+business_reviewer）；auditor 为本轮向
栈 DB 追加的真实 AuthSubject（token ui-auditor-token-…，roles=["auditor"]）。

| # | 复验项 | 结果 | 断言依据（DOM） |
|---|---|---|---|
| 1 | 断档 streak：D-2 缺失 → 连续正常=1 天（非 2） | ✓ | 健康行「上次运行 2026-10-23（timer）OK 耗时 75 分钟｜下次预计 2026-10-24 06:10｜**连续正常 1 天**」；日报表恰两行（10-23 OK、10-21 OK，无 10-22）。旧实现按行累计会显示 2 |
| 2 | 「其他」列+口径脚注 | ✓ | 每日实际行 `2026-10-23｜CONFIRMED 1｜ACCEPTED 0｜PARTIAL 0｜REJECTED 0｜UNKNOWN 1｜其他 1｜合计 3｜尝试失败 1`——五列+其他=1+0+0+0+1+1=3=合计；表下脚注「日期按提交创建时间（UTC）分组；上方日报表日期为业务窗口（GMT+8）。两表口径不同，同日数字不可直接对齐。」可见 |
| 3 | 接口失败显示错误提示、横幅「未知（就绪读取失败）」、恢复后正常 | ✓ | 会话中把 `blyb.core` 切到 `http://127.0.0.1:9`（api.ts 每次请求读取该键）后点「刷新定时上报/刷新就绪状态」：面板出现「授权读取失败；日报读取失败；统计读取失败；异常提交读取失败（可点击刷新重试）」；页顶横幅变「当前模式：**未知（就绪读取失败）**」；恢复地址后再点刷新——错误行消失、授权/健康行恢复、横幅回「MOCK/合成」 |
| 4 | auditor 无「查询状态」按钮 | ✓ | auditor 登录态（整页刷新，App 登录复验通过）下面板正常渲染；异常提交表行 `105503f0 UNKNOWN UNKNOWN 500 DTRT-SEED-UNKN …` 的操作列**为空**（admin 态下该处是「查询状态」按钮）；`canOperate` 生效 |

截图（本目录）：
- `fix-01-streak-other-column.png`：断档连续正常=1 天 + 每日实际「其他」列与脚注
- `fix-03-backend-error-banner.png`：死端口下四段错误提示 + 横幅「未知（就绪读取失败）」
- `fix-04-auditor-no-query-button.png`：auditor 态异常提交表无操作按钮

## 过程备注（如实）

- 整页刷新 + 死端口组合会触发 App 级登录复验失败回登录页（App.vue FIX2-07 既有
  设计），面板不挂载；因此第 3 项按「会话中切后端地址 + 面板刷新按钮」复现——
  与 G5/G9 针对的面板级吞错场景一致（mid-session 后端不可达），未改任何 App 逻辑。
- IAB 下 Playwright `click()` 仍被拦截（上轮已知），按钮以页面内 DOM click 等价触发，
  渲染结果以 DOM snapshot 断言。
- 既有「提交与回执」卡（非本轮修复范围）的「查询状态」按钮未加 operator 显隐；
  FIX-06 范围是定时上报面板（G5 指向 queryProblem），此差异如实记录。
- 横幅 modeNote 在读取失败时保留上次成功数据（FIX_TASK 规定 modeLabel/modeClass
  适配、其余逻辑不变），无功能影响。
