# 定时上报可视化 · 复核修复执行书（FIX-00 — FIX-09）

版本 r1，2026-10-24。编制：ZCode/GLM-5.3（DTR-TIMED-UI-FIXPLAN-20261024，基于对
DTR-TIMED-UI-20261024 交付的逐项复核）。执行者：接手 AI，一次性完成。

## 执行入口（复制给执行 AI 的提示词，位于本文件头部）

> 请在 F:/python/blyb 工作区执行任务 DTR-TIMED-UI-FIX-20261024。
> 按顺序读：根 AGENTS.md → docs/ai/START_HERE.md、STATE.md、HANDOFF.md、BACKLOG.md →
> docs/development/drug-trace-timed-ui-plan-20261024/EXECUTION_TASK.md（原任务书，背景与 §5 契约）、
> RESULT.md（上一轮交付）、**本文件 FIX_TASK.md（唯一执行依据）**。
> 登记：`python scripts/ai_session.py start --task DTR-TIMED-UI-FIX-20261024 --tool <实际> --model <实际>
> --scope implementation/plugins/domains/drug_trace/routes.py --scope implementation/apps/admin_web/src/views/DrugTrace.vue
> --scope implementation/tests/integration/test_drug_trace_timed_ui.py --scope implementation/docs --scope docs/ai
> --scope docs/development/drug-trace-timed-ui-plan-20261024`。
> 顺序执行 FIX-00—FIX-09 一次完成，不在每步询问；相关局部测试绿后跑全量回归
> （当前基线 **444 passed / 7 skipped / 0 failed**，新增测试计入）。
> 硬边界见 §七：禁改清单外文件零改动；不部署生产；不改 §5 契约；不触门禁。
> 结束交付：任务目录 RESULT.md 追加修复节 + 四类实施文档 + 共享状态 + session 交接 + ai_session.py end。

## 一、背景：复核发现（均已实测核实，执行者不必重查）

上轮 DTR-TIMED-UI-20261024 交付（迁移 012、6 端点、驱动 v2、前端面板、全量 444/7/0）。
复核发现以下问题，本执行书一次性修完：

| # | 级别 | 问题 | 实测证据 |
|---|---|---|---|
| G1 | P1 | upload-stats 把窗口内全部 submission id 取到 Python 再 `in_(ids)` 回查尝试——超过方言参数上限即 500 | 本机实测 SQLite 33000 变量报 "too many SQL variables"（上限 32766）；PG 扩展 IN 70000 参数报错（上限 65535）。生产 ~2,000 单/日：`days=90` 约 33 天数据后必炸；默认 `days=14` 也逼近 3 万 |
| G2 | P1 | 四类实施文档未同步（AGENTS.md 硬约定） | grep 核实：04-API目录对照矩阵.md 无 6 新端点；实施状态矩阵.md 无 TUI 项；测试执行记录.md 最后一条停在 2026-10-22 |
| G3 | P2 | 「每日实际」合计含 PREPINED/SUPERSEDED 但无列展示（截图 `1 0 0 0 1 3` 可见列和=2≠合计3）；日期按 UTC 分组而日报表按 GMT+8 窗口，同日不可对齐且 UI 无说明 | 上轮截图 ui-01 DOM 断言 |
| G4 | P2 | 健康「连续正常」不查日期连续性，隔整天缺报仍累计 | DrugTrace.vue health computed 现实现为逐行计数 |
| G5 | P2 | 面板四段 `catch {}` 静默吞错（接口失败区域直接消失）；「查询状态」按钮对 auditor 可见但必 403 | loadTimed/queryProblem 现实现 |
| G6 | P2 | 死代码：`dailyDetail.report_push === false` 永不生效（GET 详情不返回该字段，驱动标记未入库） | routes.py detail 返回键无 report_push |
| G7 | P3 | POST daily-reports 不校验 window_begin<window_end | 现实现无该校验 |
| G8 | P3 | submission-outcomes 每行 1 次尝试查询 + 1 次 bill get（≤200 行 ≈ 400 查询） | 现实现循环内单查 |
| G9 | P3 | 横幅在 readiness 加载完成前闪现「未启用」 | modeLabel 初值取自空 readiness |
| G10 | P3 | 测试缺口：days=90 收紧、streak 断档、stats 规模 | 无对应测试 |

## 二、工作分解

### FIX-00 基线

- `python scripts/ai_session.py status` 确认无冲突后按上方登记。
- 基线记录：`python -m pytest tests/integration/test_drug_trace_timed_ui.py tests/integration/test_drug_trace_api.py -q`
  当前应 **9+5 全绿**；`cd implementation/apps/admin_web && npm run build` 通过。写入任务目录 `evidence/FIX-00-baseline.md`。

### FIX-01（G1，P1）upload-stats 改单条 SQL 聚合，消灭 IN 列表

`implementation/plugins/domains/drug_trace/routes.py` 的 `upload_stats` 重写主体：

1. **状态聚合**（单条，分组行 ≤ 90 天×状态数）：
   ```sql
   SELECT date(s.created_at) AS d, s.state, count(*) AS n
   FROM submission s
   JOIN package_revision pr ON pr.id = s.package_revision_id
   JOIN data_package dp ON dp.id = pr.package_id
   WHERE s.tenant_id = :t AND dp.domain = 'drug_trace' AND s.created_at >= :since
   GROUP BY 1, 2
   ```
   （SQLAlchemy：`func.date(Submission.created_at)`；`since = utcnow() - timedelta(days=days)` 不变。）
2. **最近尝试聚合**（同一次请求内第二条，窗口子查询 SQLite≥3.25 与 PG 均支持）：
   ```sql
   SELECT date(s.created_at) AS d, a.outcome, a.http_status, count(*) AS n
   FROM submission s
   JOIN package_revision pr ON pr.id = s.package_revision_id
   JOIN data_package dp ON dp.id = pr.package_id
   JOIN (
       SELECT da.submission_id, da.outcome, da.http_status,
              ROW_NUMBER() OVER (PARTITION BY da.submission_id
                                 ORDER BY da.attempt_no DESC) AS rn
       FROM delivery_attempt da WHERE da.tenant_id = :t
   ) a ON a.submission_id = s.id AND a.rn = 1
   WHERE s.tenant_id = :t AND dp.domain = 'drug_trace' AND s.created_at >= :since
   GROUP BY 1, 2, 3
   ```
3. Python 侧只做小结果集的分桶（ACCEPTED→accepted；http_status≥400→http_error；其余→other），
   规则与现状完全一致。
4. 硬性要求：**该端点内不得出现任何 `.in_(...)`**；响应结构与 §5.2 逐字段不变（前端本项不改）；
   既有 `test_upload_stats_matches_handwritten_sql` 断言不得修改且必须通过。

### FIX-02（G8，P3）submission-outcomes 批量化

同文件 `submission_outcomes`：

- 最近尝试改用与 FIX-01 相同的窗口子查询 **LEFT JOIN**（无尝试的提交保留，`attempt_*` 为 null）。
- bill 单号：主查询仍取 `pr.provenance`，Python 侧收集 bill_id 后**一次**
  `db.query(DrugTraceBill).filter(DrugTraceBill.id.in_(bill_ids))`（≤200 个 id，安全）建 map 回填。
- 响应结构不变；既有 `test_submission_outcomes_problem_filter` 断言不得修改且必须通过。

### FIX-03（G7，P3）日报窗口序校验

`ingest_daily_report`：解析出 wb/we 后，若两者均非空且 `wb >= we` →
`ApiError("VALIDATION_FAILED", "window_begin must < window_end")`。
**不加** report_date 新近度限制（manual 补录历史日为合法场景，审计已有留痕）——此决策写入代码注释一行。

### FIX-04（G3，P2）前端「每日实际」补「其他」列与口径脚注

`DrugTrace.vue` 每日实际表：

- `UNKNOWN` 列后、`合计` 前加列 **其他**：`by_state` 中不属于
  {CONFIRMED, ACCEPTED, PARTIAL, REJECTED, UNKNOWN} 的键值求和（PREPARED/SUPERSEDED/SENDING 等）。
  加上后五列+其他=合计，口径自洽。
- 表下加脚注：`<p class="muted small">日期按提交创建时间（UTC）分组；上方日报表日期为业务窗口（GMT+8）。两表口径不同，同日数字不可直接对齐。</p>`

### FIX-05（G4，P2）连续正常按日期连续性计算

`health` computed 中 streak 改为：

```ts
const map = new Map(rows.map((r: any) => [r.report_date, r.outcome]))
let streak = 0
let d = new Date(Y, M - 1, D)            // Y/M/D 来自 max report_date
const fmt = (x: Date) => `${x.getFullYear()}-${String(x.getMonth()+1).padStart(2,'0')}-${String(x.getDate()).padStart(2,'0')}`
while (map.get(fmt(d)) === 'OK') { streak++; d.setDate(d.getDate() - 1) }
```

缺整天（map 无该日期）即停。MISSING 规则、空态规则（§5.4）不变。

### FIX-06（G5+G6+G9，P2）错误提示 / 按钮显隐 / 死代码 / 横幅加载态

同一文件：

1. `const timedError = ref('')`；`loadTimed` 开头清空，每段 catch 设对应文案
   （如 `授权读取失败`/`日报读取失败`/`统计读取失败`/`异常提交读取失败`，多个用「；」连接）；
   面板标题行下渲染 `<p v-if="timedError" class="err small">{{ timedError }}（可点击刷新重试）</p>`。
2. `const canOperate = computed(() => roles.includes('operator') || roles.includes('platform_admin'))`
   （roles 取法与 canApprove 相同）；「查询状态」按钮加 `v-if="canOperate"`。
3. 删除 `dailyDetail.report_push === false` 那段 `<p>`（死代码）。
4. 横幅：加 `readinessLoaded` 标志（loadReadiness 的 finally 置 true）。modeLabel：
   未加载完成 → `加载中…`（class 用 `muted`）；加载完成但 loadReadiness 出错 → `未知（就绪读取失败）`（warn）；
   其余逻辑不变。modeClass 同步适配。

### FIX-07（G10）测试补充

`implementation/tests/integration/test_drug_trace_timed_ui.py` 追加（沿用现有 fixture/风格）：

1. **规模测试** `test_upload_stats_scale_35k`：合成 **≥35,000** 条提交（每条带 data_package +
   package_revision；约 1/3 带 delivery_attempt，含少量多次尝试）分布 90 天，用 Core
   `conn.execute(insert(...), rows)` 批量插入（勿逐 ORM add）控制耗时 <60s；
   `GET upload-stats?days=90` → 200，按日合计与插入计数一致。此测试在旧实现下必因
   >32766 变量失败，即修复的回归锁。
2. **clamp 测试**：种 5 天前与 100 天前各 1 条提交，`days=500` → 仅 5 天前那条计入（证明收紧到 90）。
3. **窗口序测试**：POST daily-reports `window_begin >= window_end` → 422。
4. **PG 端到端**：新增 `api_pg` fixture（复制 `api` 但 `BLYB_DATABASE_URL` 指向既有 `pg`
   docker fixture 的 URL；迁移已验证可在 PG 跑）；一个测试种 3 笔提交（CONFIRMED+ACCEPTED、
   UNKNOWN+两次尝试取最新、PREPARED 无尝试），对 `upload-stats` 与 `submission-outcomes`
   断言与 SQLite 版相同的期望——锁 PG 方言（func.date/窗口函数）。
5. **serve 栈种子扩展** `evidence/serve_timed_ui.py`：日报改为 **D-1 OK、D-3 OK、D-2 缺失**
   （streak 断档演示）；保留其余种子。用于浏览器复验。

### FIX-08（G2，P1）四类实施文档同步（上轮欠账 + 本轮结果一并写入）

1. `implementation/docs/04-API目录对照矩阵.md`：药品追溯节新增 6 行（沿用现有
   `| 端点 | 说明 | 验证 |` 格式）：daily-reports POST（幂等 upsert/角色/64KB）、GET 列表、
   GET 单日、upload-stats、send-scope、submission-outcomes。
2. `implementation/docs/实施状态矩阵.md`：新增 DTR-TIMED-UI-20261024 + FIX 行（本地完成/
   全量 444+新增/0、TUI-08 BLOCKED 待窗口），沿用该表行格式。
3. `implementation/docs/测试执行记录.md`：追加 2026-10-24 两节——上轮（基线 5、新增 9、
   全量首轮 442/7/2[旧迁移名单断言]→补 012→复跑 **444/7/0**，XML `var/tui-full-regression.xml`）
   与本轮 FIX（新增测试数、局部、全量结果，XML `var/tui-fix-regression.xml`）。
4. `implementation/docs/接口与设计差异.md`：一条——日报表列表列按 §5.1 契约，
   「采集摘要/跳过」展示在单日详情区；「每日实际」按 created_at(UTC) 分组（契约原文），
   UI 已加口径脚注与「其他」列。
5. 共享状态：STATE/HANDOFF/BACKLOG 由 FIX-09 收尾时更新（见下）。

### FIX-09 全量回归、浏览器复验与交接

1. 局部：`python -m pytest tests/integration/test_drug_trace_timed_ui.py tests/integration/test_drug_trace_api.py tests/integration/test_drug_trace_domain.py tests/integration/test_drug_trace_pg.py tests/integration/test_drug_trace_pg_fix3.py -q` 全绿。
2. 全量：`python -m pytest tests -q -ra --junitxml=var/tui-fix-regression.xml`
   → **≥444+新增 passed / 0 failed**（7 skipped 预期不变）。
3. `npm run build` 通过。
4. 浏览器复验（起 `evidence/serve_timed_ui.py`，沿用上轮浏览器技能流程；截图存任务目录
   `evidence/fix-*.png`）：
   - 断档健康：D-2 缺失 → 连续正常=1 天（非 2）；
   - 「其他」列=1（PREPARED）且五列+其他=合计；口径脚注可见；
   - 把 localStorage `blyb.core` 指向死端口后刷新 → 面板出现错误提示、横幅「未知（就绪读取失败）」，
     恢复地址后一切正常；
   - auditor 登录态下「查询状态」按钮不显示。
5. 任务目录 RESULT.md 追加「复核修复（DTR-TIMED-UI-FIX）」节（实测/未测/阻断分开）；
   更新共享 STATE/HANDOFF/BACKLOG（TUI-08 仍 BLOCKED 待用户窗口）；
   写 `docs/ai/sessions/DTR-TIMED-UI-FIX-20261024.md` 交接并 `ai_session.py end`。

## 三、验收总表（F-A）

| 编号 | 条件 |
|---|---|
| F-A01 | upload-stats 无任何 `.in_(...)`；35k 规模测试 200 且计数正确 |
| F-A02 | stats/outcomes 响应与 §5.2/契约逐字段不变；既有断言测试未修改且通过；PG 端到端过 |
| F-A03 | window-order 422 测试过；clamp 测试过 |
| F-A04 | 前端：其他列+脚注+断档 streak+错误提示+按钮显隐+无 report_push 死代码+横幅加载态；build 过；浏览器四项复验+截图 |
| F-A05 | 四类实施文档齐（6 端点行/状态行/两节测试记录/差异条目） |
| F-A06 | 全量回归 ≥444+新增 passed / 0 failed；JUnit XML 留证 |
| F-A07 | 门禁零改动：§四禁改清单文件零 diff（git status 核对并留证） |
| F-A08 | 交接齐备：RESULT 修复节、共享状态、session 文件、ai_session end |

## 四、禁改清单（F-A07 核对项；违者返工）

以下文件本轮**零改动**：`plugins/domains/drug_trace/{service,quality,ingest,contracts,payload,models,plugin,readiness,source_config,business_rules}.py`、
`plugins/destinations/**`、`apps/core_api/app.py`、
`docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`（驱动 v2 本轮无需改）、
systemd 单元、既有回执/上线证据/冻结 payload。
允许改动仅限：`plugins/domains/drug_trace/routes.py`（仅 upload-stats/submission-outcomes/
ingest 三处）、`apps/admin_web/src/views/DrugTrace.vue`、
`tests/integration/test_drug_trace_timed_ui.py`、任务目录 `evidence/serve_timed_ui.py` 与文档、
`implementation/docs/` 四文档、共享 STATE/HANDOFF/BACKLOG、session 文件。

## 五、边界与禁止事项

- 不部署生产、不连 <prod-host>/HIS/平台、不发起真实请求；TUI-08（生产部署）仍 BLOCKED 待用户窗口。
- 不改 §5 契约字段、CONFIRMED/REJECTED 语义、门禁/配额/证据路径；不新增轮询或自动重试。
- 全部查询带 tenant_id；日报 64KB/深度守卫不变。
- 未运行的不写通过；静态检查不冒充验收；历史通过数不抵新缺陷。

## 六、风险与回滚

| 风险 | 缓解 |
|---|---|
| 窗口函数子查询在旧 SQLite 不可用 | 本机 sqlite 3.50 已实测支持；测试即锁（规模测试过=方言过） |
| LEFT JOIN 改动引入 outcomes 行为漂移 | 既有 outcomes 测试断言不改必须过；PG 端到端再锁 |
| 规模测试拖慢全量 | 批量插入 + 上限 60s；超限时优化 executemany batch 而非砍行数 |
| 前端改动破坏既有五卡片 | 仅动「定时上报」卡与横幅；build+浏览器复验覆盖 |
| 回滚 | 全部为本地未部署改动，git 工作区即可回退；无迁移/数据变更 |

## 七、当前基线（对照用）

- 上轮全量：444 passed / 7 skipped / 0 failed（`var/tui-full-regression.xml`）。
- 局部：test_drug_trace_timed_ui.py 9 passed；test_drug_trace_api.py 5 passed。
- 前端：npm run build ✓（js 171.94kB）。
