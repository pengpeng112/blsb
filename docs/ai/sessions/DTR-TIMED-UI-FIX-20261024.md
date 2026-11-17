# DTR-TIMED-UI-FIX-20261024 交接（定时上报可视化·复核修复）

- 执行：ZCode / GLM-5.3，2026-10-24，session `20261024T094555Z-17f92c5b`。
- 依据：[FIX_TASK.md](../../development/drug-trace-timed-ui-plan-20261024/FIX_TASK.md)
  （唯一执行依据；G1—G10、FIX-00—09、F-A01—08、§四禁改、§七基线）。
- 结果：[RESULT.md 复核修复节](../../development/drug-trace-timed-ui-plan-20261024/RESULT.md)。

## 改了什么

- `implementation/plugins/domains/drug_trace/routes.py`（仅三处）：
  - upload-stats（FIX-01）：两条分组聚合 SQL（状态分组 + ROW_NUMBER 窗口子查询取每提交
    最新尝试），端点内无任何 `.in_()`；响应与 §5.2 逐字段不变。
  - submission-outcomes（FIX-02）：窗口子查询 LEFT JOIN 一次取齐最近尝试；单号一次
    `id.in_(≤200)`（带 tenant）批量回填；响应结构不变。
  - ingest_daily_report（FIX-03）：wb/we 均非空且 wb>=we → 422；不加 report_date
    新近度限制（manual 补录历史日合法，注释留痕）。
  - 顶部 import `func` → `and_, func, select`。
- `implementation/apps/admin_web/src/views/DrugTrace.vue`：其他列+口径脚注（FIX-04）、
  streak 日期连续性（FIX-05）、timedError 四段错误提示/canOperate 按钮显隐/删
  report_push 死代码/横幅 readinessLoaded+readinessError 加载与失败态（FIX-06）。
- `implementation/tests/integration/test_drug_trace_timed_ui.py`：fixture 重构
  （`_build_api` 共享构造器 + 新 `api_pg`；共享 PG 容器 tenant.code 唯一→改唯一 code），
  新增 4 测试（35k 规模回归锁 / days clamp / 窗口序 422 / PG 端到端）。
  **两个既有断言测试零修改。**
- `evidence/serve_timed_ui.py`：种子改 D-1 OK、D-3 OK、D-2 缺失（断档演示）；D-3 干净 OK。
- 文档：四类实施文档（API 矩阵 6 行/实施状态节/测试记录两节/差异一条）+ RESULT 追加 +
  共享 STATE/HANDOFF/BACKLOG + 本文件。

## 真实命令与结果（全部本机，exit 0）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_drug_trace_timed_ui.py tests/integration/test_drug_trace_api.py -q`（改动前/后） | 12P/2S（docker 未跑）→ 复跑 14P → 改动后 **18P/0** |
| `pytest …timed_ui …api …domain …pg …pg_fix3 -q` | **56 passed / 0 failed**（60.44s） |
| `pytest tests -q -ra --junitxml=var/tui-fix-regression.xml` | **448 passed / 7 skipped / 0 failed**（455 cases，566.78s；上轮 444+新增 4） |
| `npm run build`（apps/admin_web） | ✓ 1.43s，js 173.06 kB |
| 浏览器（IAB + serve_timed_ui.py:8788） | 四项过：断档 streak=1、其他列=1+脚注、死端口错误提示+横幅「未知」+恢复正常、auditor 无查询按钮；截图 evidence/fix-01/03/04.png |
| 禁改核对 | 30 冻结文件会话前后 SHA256 比对 changed:NONE（evidence/FIX-F-A07-frozen-check.md + git-status.txt） |

## 未完成 / 下一步

- **TUI-08 生产部署 BLOCKED**（须用户在场+避开每日 06:00–08:30）：本轮未构建发行、
  未触 <prod-host>/HIS/平台、零真实请求。就绪清单 RESULT §五。
- 观察：2026-10-25 06:10 首个全量日批（仍 v1 驱动）；部署后首个 v2 日批回执含 report_push。
- 小项（可选）：既有「提交与回执」卡「查询状态」按钮统一 operator 显隐。
- 既有外部项不变：E05'—E08'、<prod-host> NTP、A02/A03/A05。

## 备注

- 整页刷新+死端口会触发 App 级登录复验回登录页（FIX2-07 既有设计）；浏览器第 3 项按
  「会话中切 blyb.core+面板刷新」等价复现面板级场景，未改 App 逻辑。
- 35k 规模锁种在 SQLite（32766 是更紧上限）；PG 由端到端小规模方言锁覆盖，如实记录。
