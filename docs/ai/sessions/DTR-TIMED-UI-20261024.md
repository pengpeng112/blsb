# 定时上报可视化执行交接

任务 DTR-TIMED-UI-20261024；session `20261024T052935Z-3c27a4bb`；工具 ZCode，模型 GLM-5.3；2026-10-24。
入口为用户提示词（按 [EXECUTION_TASK.md](../../development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md)
执行 TUI-00—09）。结果：[RESULT.md](../../development/drug-trace-timed-ui-plan-20261024/RESULT.md)。

## 改了什么（全部本地开发，生产零改动）

1. **迁移 012 + 模型**：`plugins/domains/drug_trace/models.py` 新增 `DrugTraceDailyReport`
   （uq tenant+report_date+source）与 `migration_fix4`/`migration_fix4_downgrade`；
   `plugin.py` 注册两行。
2. **六个新端点**（`routes.py` 尾部追加，既有端点零改动）：daily-reports POST（幂等 upsert、
   operator|platform_admin、深度≤4/合计≤64KB）+ GET 列表/单日；upload-stats（按日×state + 最近尝试
   分桶，days≤90，仅 drug_trace 域）；send-scope（证据+EXT-12 scope+配额台账实时值，多目的地显式空）；
   submission-outcomes（only=problem 过滤，无尝试的 PREPARED 不算异常，上限 200）。
3. **驱动 v2**（仓库副本 `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`）：
   跑完 `push_daily_report()`（单次 POST/90s/失败即放弃，只记 `receipt['report_push']`）；
   fatal 路径（退出码 2/3 不变）best-effort 推 FAILED 日报。宿主 unit/timer 未改。
4. **前端** `apps/admin_web/src/views/DrugTrace.vue`：「定时上报（自动化）」卡片（授权行/健康行§5.4/
   日报表+详情下钻/每日实际/异常提交）；modeLabel/modeClass 改 computed（MOCK｜正式链路｜未启用）；
   说明段落改写；scoped 样式（数字右对齐+状态色）。既有五卡片逻辑未动。
5. **测试**：新增 `tests/integration/test_drug_trace_timed_ui.py`（9 项：迁移环×2、日报 API、stats 对照、
   send-scope、outcomes、驱动契约/失败安全、驱动 PG 端到端）；`test_drug_trace_domain.py`、
   `test_drug_trace_pg.py`、`test_drug_trace_pg_fix3.py` 三处迁移名单断言补 012（合法新增枚举）。
6. **文档**：任务目录 RESULT.md + evidence/（基线、UI 验证记录、3 张截图、验证栈脚本）；
   `数据上报/1、码上放心平台/README.md` 界面入口更新；共享 STATE/HANDOFF/BACKLOG。

## 真实命令与结果

```powershell
python scripts/ai_session.py start/checkpoint/end --task DTR-TIMED-UI-20261024   # 登记无冲突
python -m pytest tests/integration/test_drug_trace_api.py -q          # 基线 5 passed
npm run build（admin_web）                                            # 基线 2.08s；改后 1.92s（js 171.94kB）
python -m pytest tests/integration/test_drug_trace_timed_ui.py -q     # 9 passed（含 Docker 隔离 PG×2）
python -m pytest tests -q -ra --junitxml=var/tui-full-regression.xml  # 首轮 442/7/2（2失败=旧迁移名单断言）
# 断言补 012 后复跑两文件 4 passed → 全量复跑（结果见 RESULT §三）
python evidence/serve_timed_ui.py 8788  # 合成栈；浏览器实测面板四区/下钻/横幅切换/空态，截图3张
```

A07 证据：门禁文件（service/quality/ingest/contracts/payload/destinations/core_api app）mtime 均早于
本会话开始 05:29Z，未触碰；本会话后端仅 models/plugin/routes 三文件且均为追加。
工作树前几轮未提交改动为既有基线，非本会话引入。

## 未完成与下一步

- **TUI-08 生产部署 BLOCKED**：须用户在场确认窗口且避开每日 06:00–08:30（timer 06:10 真实发送）。
  就绪清单在 RESULT §五（build_release→upgrade.sh→替换 /opt/blyb/tools 驱动→只读验证→回滚路径）。
- 生产部署后：观察首个用新驱动的日批回执入库与面板显示；`report_push.ok=false` 属日报推送失败可查。
- 既有观察项不变：2026-10-25 06:10 首个全量日批（旧驱动仍文件回执，日报入库从部署后开始）、
  E05'—E08' 业务确认、<prod-host> NTP。
