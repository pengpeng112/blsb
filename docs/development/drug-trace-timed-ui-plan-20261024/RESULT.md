# 定时上报可视化执行结果（DTR-TIMED-UI-20261024）

执行：ZCode/GLM-5.3，2026-10-24，session 20261024T052935Z-3c27a4bb。
任务书：[EXECUTION_TASK.md](EXECUTION_TASK.md)（TUI-00—09、§5 契约、A01—A08 验收）。
本文件区分**实测通过 / 实现未测 / 阻断**；未运行的一律不写通过。
## 一、实测通过（本机隔离环境，全部合成数据）

### 代码交付（TUI-01—04、05、06）

| 项 | 内容 | 证据 |
|---|---|---|
| 迁移 012 | `DrugTraceDailyReport`（表 drug_trace_daily_report，uq tenant+report_date+source）；`migration_fix4`（step 12）+ `migration_fix4_downgrade`；plugin.py 注册 | models.py / plugin.py；SQLite+隔离 PG(Docker postgres:16) up→幂等→down→up 全程绿 |
| 日报 API | POST /api/v1/drug-trace/daily-reports（operator\|platform_admin，幂等 upsert，audit `drug_trace.daily_report.ingest`，ISO 日期/outcome/source 枚举校验，JSON 深度≤4、合计≤64KB→413）；GET 列表（摘要行按 §5.1）；GET /{report_date} 单日完整（含 send.errors） | test_drug_trace_timed_ui.py |
| 统计/授权 API | GET upload-stats?days≤90（按 created_at 日期×state 聚合 + 最近尝试分桶 accepted/http_error/other，仅 drug_trace 域，func.date 跨方言）；GET send-scope（现行 send 证据+scope+配额台账实时值+目的地；多目的地/未登记显式空）；GET submission-outcomes?days&only=problem（近 N 天+最近尝试+source_bill_no/bill_code；无尝试的 PREPARED 不算异常；上限 200） | 同上；stats 与手写 SQL 对照一致 |
| 驱动 v2 | `push_daily_report()`（单次 POST、90s 超时、失败仅记 receipt['report_push']）；成功/失败/异常三路安全；fatal 路径（退出码 2/3 不变）best-effort 推送 FAILED 日报；文件回执仅新增 report_push 字段 | 单测 + **隔离 PG 端到端跑 main()**（成功 rc=0 / 推送 500 rc=0 / 采集创建失败 rc=2），负载字段与 §5.1 逐项断言 |
| 前端面板 | DrugTrace.vue「接入就绪」卡后新增「定时上报（自动化）」：授权行/健康行（§5.4：下次预计、连续正常、MISSING、空态不误报）/日报表+详情下钻（errors 截断 200 字符）/每日实际/异常提交（查询状态按钮走既有 query 端点）；手动刷新，无轮询；数字列右对齐+状态色 | npm build ✓（1.92s，js 171.94kB）+ 浏览器实测 + 截图 3 张 |
| 横幅动态化 | modeLabel/modeClass 改 computed：retail 目的地 is_mock→「MOCK/合成」；is_mock=false&&ENABLED→「正式链路」；否则「未启用」；说明段落同步改写 | 浏览器实测：同一栈切 is_mock 后刷新即切换（两态截图） |

### 测试（TUI-07）

- 新增 `implementation/tests/integration/test_drug_trace_timed_ui.py`：**9 passed**
  （SQLite 迁移环、PG 迁移环、日报 upsert/角色/校验、stats 对照、send-scope 配额递增+多目的地、outcomes 过滤、驱动契约/失败安全、驱动 PG 端到端）。
- 既有 `test_drug_trace_domain.py` 迁移名单断言更新加入 012（合法新增，34 passed）。
- **全量回归：见 §三**（运行完成后回填）。

### A07 门禁零改动（实测核对）

本会话仅修改 3 个后端文件：`models.py`（新增模型+迁移012）、`plugin.py`（迁移注册 2 行）、
`routes.py`（新增 6 端点，全部追加在文件尾部；既有端点零改动）+ 前端 DrugTrace.vue + 测试。
发送/质量/证据/配额路径文件 mtime 均早于本会话开始（2026-10-24 05:29Z）：
service.py=10-22、quality.py=10-17、ingest.py=10-20、contracts/payload/destinations/*/
core_api app.py 均 ≤10-22 —— **未触碰**。
（工作树存在前几轮未提交改动属既有基线，非本会话引入。）

### 浏览器验证（T-A04）

见 [evidence/TUI-05-06-ui-verification.md](evidence/TUI-05-06-ui-verification.md)；
截图 ui-01/02/03（MOCK 面板+下钻、正式链路横幅、空态）。

## 二、实现未测（已实现，生产未验证）

- 生产 <prod-host> 未见新代码：迁移 012 未在生产 applied、面板未在生产显示真实数据（依赖 TUI-08）。
- 驱动 v2 未在生产容器运行（仓库副本已就绪，待部署窗口替换 /opt/blyb/tools/blyb-daily-upload.py）。
- 日报 POST 超 64KB 的生产行为：单测验证 413 拒绝+驱动安全放弃，未在真实大规模错误日验证
  （极端错误清单日日报不入库、文件回执照写——可接受的 fail-open，界面该日显示无记录）。

## 三、全量回归

- 首轮：442 passed / 7 skipped / **2 failed**（562.15s）——2 个失败均为既有 PG 迁移测试的
  名单断言未含新 012（`test_drug_trace_pg.py`、`test_drug_trace_pg_fix3.py`），属合法新增枚举更新。
- 断言补 012 后局部复验两文件 **4 passed**；全量复跑：
  **444 passed / 7 skipped / 0 failed（550.14s，2026-10-24，
  `python -m pytest tests -q -ra --junitxml=var/tui-full-regression.xml`，JUnit XML 已存）**。
- 7 skipped 与既有基线一致（真 PG>2GB、live 栈、Windows fd、B01/B05 DSN×2、pyodbc、50GB 磁盘窗口），
  非本轮新增。基线对比：W09 轮 277/6 → 本轮 444/7（含此前 FB/fix 轮已计入用例 + 本任务新增 9 项）。

## 四、阻断（BLOCKED）

- **TUI-08 构建发行与生产部署：BLOCKED**——须用户在场确认窗口且避开每日 06:00–08:30
  （生产 blyb-daily-upload.timer 每日 06:10 真实发送）。未取得确认，本轮未构建发行、
  未触碰 <prod-host>。恢复执行清单见 §五。

## 五、部署窗口就绪清单（供用户确认后执行）

1. `python deploy/release/build_release.py --out <dir>`（含同源前端构建，随发行迁移 012 自动执行）。
2. <prod-host> 按 `deploy/linux/upgrade.sh` 升级（自动备份+回退）；**窗口避开 06:00–08:30**；
   升级前后各跑一次 `prod_status.py` 留证。
3. 替换驱动：宿主 `/opt/blyb/tools/blyb-daily-upload.py` ← 仓库
   `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`（v2）；
   systemctl 不动 timer（下次触发自动用新驱动）；容器内 AST 校验一次。
4. 部署后验证（只读/幂等）：GET daily-reports 200；手动
   `systemctl start blyb-daily-upload.service` 跑一次（幂等）确认入库+界面可见；
   `systemctl list-timers` 下次触发正确。
5. 回滚：`deploy/linux/rollback.sh` + 恢复旧驱动文件；`migration_fix4_downgrade` 可用
   （已入库日报为新增数据，回滚不删除亦无害）。

## 六、过程备注（如实）

- 打包版登录页默认后端 127.0.0.1:8000 的既有小项（BACKLOG 已登记）在本地验证栈需手填
  后端地址/等价登录态；同源发行不受影响。未改动该行为。
- IAB 浏览器 click() 动作被拦截（fill/press 正常），UI 按钮用页面内 DOM click 等价触发，
  渲染结果以 DOM snapshot 断言。
- upload-stats/outcomes 的"日期"按 submission.created_at（UTC 存储口径）分组，与手写 SQL
  一致；健康规则的"昨日/06:10/07:30"按前端本地时区（院方 GMT+8）推导（§5.4 约定）。
- 日报表列表列取自 §5.1 契约字段；任务书布局中的「采集摘要」「跳过」在单日详情区展示
  （契约列表行无此二字段，未另造字段）。

---

# 复核修复（DTR-TIMED-UI-FIX-20261024）

执行：ZCode/GLM-5.3，2026-10-24，session 20261024T094555Z-17f92c5b。
执行依据：[FIX_TASK.md](FIX_TASK.md)（G1—G10、FIX-00—09、F-A01—08）。按实测/未测/阻断分开。

## 一、实测通过（本机隔离环境）

### FIX-00 基线

- 无进行中会话；登记 6 scope 成功。基线：局部 12 passed/2 skipped（2 skip＝PG Docker
  当时 daemon 未运行，启动后复跑 14 passed 0 skip）+ `npm run build` ✓（171.94 kB）。
  30 个禁改清单文件会话前 SHA256 存 `evidence/FIX-frozen-baseline.json`。

### 后端（FIX-01/02/03，routes.py 仅三处）

| 项 | 内容 | 验证 |
|---|---|---|
| FIX-01（G1，P1） | upload-stats 重写为两条分组聚合 SQL：①状态分组（GROUP BY date×state）；②最近尝试＝`ROW_NUMBER() OVER (PARTITION BY submission_id ORDER BY attempt_no DESC)` 窗口子查询取 rn=1 再按日×outcome×http 分组；Python 仅小结果集分桶（规则不变）。**端点内无任何 `.in_(...)`**（文件级核查：唯一代码级 `.in_` 在 submission-outcomes 的 bill 批量 ≤200，规格允许）；响应与 §5.2 逐字段不变 | `test_upload_stats_matches_handwritten_sql` 断言零改通过；**35k 规模回归锁**：35,000 提交+~12k 尝试（含 2,334 双次取最新）分布 88 天，Core executemany seed <60s，`days=90` 200 且逐日 total/by_state/attempts 三桶精确匹配（旧实现该规模必 500） |
| FIX-02（G8，P3） | submission-outcomes：最近尝试改同款窗口子查询 **LEFT JOIN**（无尝试 attempt_* 为 null 保持）；单号收集后**一次** `DrugTraceBill.id.in_(≤200)`（带 tenant 过滤）建 map 回填；响应结构不变 | `test_submission_outcomes_problem_filter` 断言零改通过；PG 端到端再锁 |
| FIX-03（G7，P3） | ingest_daily_report：wb/we 均非空且 `wb>=we` → 422 VALIDATION_FAILED；**不加** report_date 新近度限制（manual 补录历史日合法，代码注释留痕，manual+历史日 200 有测试） | `test_daily_report_window_order_rejected` |

### 前端（FIX-04/05/06，DrugTrace.vue）

| 项 | 内容 | 验证 |
|---|---|---|
| FIX-04（G3） | 每日实际表 UNKNOWN 后加「其他」列（by_state 五主状态之外求和）；表下口径脚注（UTC 分组 vs 日报表 GMT+8，同日不可对齐） | DOM：`1+0+0+0+1+其他1=合计3`；脚注可见（fix-01.png） |
| FIX-05（G4） | streak 改日期连续性：Map(report_date→outcome)，从 max report_date 逐日回退，`=== 'OK'` 才累计，缺整天即停；MISSING/空态规则不变 | 种子 D-1 OK、D-3 OK、D-2 缺失 → **连续正常=1 天**（旧实现显示 2）（fix-01.png） |
| FIX-06（G5/G6/G9） | ①timedError：四段 catch 各设文案「；」连接，标题下 `err small` 渲染+重试提示；②canOperate=operator\|platform_admin，「查询状态」按钮 v-if；③删 report_push 死代码 `<p>`；④横幅 readinessLoaded/readinessError：未加载→「加载中…」（muted）、加载但出错→「未知（就绪读取失败）」（warn）、其余逻辑不变 | DOM 实测：死端口下四段错误+横幅「未知」、恢复正常后消失（fix-03.png）；auditor 操作列空（fix-04.png）；`npm run build` ✓（173.06 kB） |

### FIX-07 测试与 serve 种子

- 新增 4 测试：`test_upload_stats_scale_35k`（35k 规模回归锁）、
  `test_upload_stats_days_clamped_to_90`（days=500→90 生效，100 天前不计入）、
  `test_daily_report_window_order_rejected`（wb>we 与 wb==we 均 422，manual 历史日 200）、
  `test_upload_stats_and_outcomes_on_pg`（新 `api_pg` fixture=Docker postgres:16 + `_build_api`
  共享构造器；锁 PG 方言 func.date/窗口函数/LEFT JOIN）。
- 过程修复（fixture 交互，非业务码）：共享 PG 容器 tenant.code 唯一约束冲突 →
  `_build_api` 改唯一 code；既有测试断言零改动。
- serve_timed_ui.py 种子改 D-1 OK、D-3 OK、D-2 缺失（断档演示）；D-3 改干净 OK
  （OK 带 errors 明细语义矛盾）。

### FIX-08 四类实施文档（上轮欠账+本轮）

- `04-API目录对照矩阵.md`：药品追溯节新增 6 端点行（daily-reports POST/列表/单日、
  upload-stats、send-scope、submission-outcomes，标注 TUI/FIX 轮语义）。
- `实施状态矩阵.md`：新增 2026-10-24 TUI+FIX 节（7 行：迁移+端点/FIX-01/FIX-02·03/
  前端/PG 端到端/全量回归/TUI-08 BLOCKED）。
- `测试执行记录.md`：追加 2026-10-24 两节（TUI 轮与 FIX 轮，命令/数字/XML 齐全）。
- `接口与设计差异.md`：一条展示口径差异（日报表 §5.1 列 vs 详情区；每日实际 UTC 分组+脚注+其他列）。

### FIX-09 回归与浏览器复验

- FIX-09 指定局部五文件：**56 passed / 0 failed**（60.44s，含 Docker PG）。
- **全量回归：448 passed / 7 skipped / 0 failed**（455 cases，566.78s，exit 0，
  `python -m pytest tests -q -ra --junitxml=var/tui-fix-regression.xml`）。
  上轮基线 444/7/0 + 新增 4 = 448；7 skipped 与基线一致（环境依赖项，清单见测试记录）。
- `npm run build` ✓（1.43s，js 173.06 kB）。
- 浏览器四项复验全过（DOM 断言+截图 fix-01/03/04.png，
  [记录](evidence/FIX-browser-verification.md)）。

### F-A 验收对照

| 编号 | 结果 |
|---|---|
| F-A01 | ✓ upload-stats 无 `.in_(...)`（文件级核查）；35k 测试 200 且逐日计数正确 |
| F-A02 | ✓ stats/outcomes 响应契约逐字段不变；两个既有断言测试零改通过；PG 端到端过 |
| F-A03 | ✓ 窗口序 422（>与=两例）+ clamp 测试过 |
| F-A04 | ✓ 其他列+脚注+断档 streak+错误提示+按钮显隐+无 report_push 死代码+横幅加载态；build ✓；浏览器四项+截图 |
| F-A05 | ✓ 四类实施文档齐（6 端点行/状态节/两节测试记录/差异条目） |
| F-A06 | ✓ 448/7/0 ≥444+4；JUnit XML `var/tui-fix-regression.xml` |
| F-A07 | ✓ 禁改清单 30 文件会话前后哈希比对 changed:NONE（[证据](evidence/FIX-F-A07-frozen-check.md)；git status 留证 FIX-F-A07-git-status.txt） |
| F-A08 | ✓ 本节+共享状态+session 文件+ai_session end |

## 二、实现未测（生产未验证）

- 全部改动仅在本机隔离环境验证；生产 <prod-host> 未见 FIX 代码（随 TUI-08 一起部署）。
- 35k 规模在 PG 的实跑：规模测试在 SQLite（回归锁目的——SQLite 32766 是更紧的上限）；
  PG 侧由 65535 上限论证+PG 端到端小规模方言锁覆盖，未种 35k PG 数据。
- 浏览器第 3 项按「会话中切后端地址」复现（整页刷新+死端口会触发 App 级登录复验回登录页，
  为既有 FIX2-07 设计，非本轮范围）——与 G5/G9 面板级场景等价，未改 App 逻辑。

## 三、阻断（BLOCKED，与上轮一致）

- **TUI-08 生产部署仍 BLOCKED**：须用户在场确认窗口且避开每日 06:00–08:30
  （timer 06:10 真实发送）。本轮未构建发行、未触碰 <prod-host>/HIS/平台，零真实请求。
  就绪清单仍见上方 §五（build_release → upgrade.sh → 替换驱动 → 只读验证 → 回滚）。

## 四、遗留说明（如实）

- 既有「提交与回执」卡的「查询状态」按钮未加 operator 显隐——FIX-06 范围是定时上报
  面板（G5 指向 queryProblem）；如需统一可另立小项。
- 横幅 modeNote 在读取失败时保留上次成功数据（FIX_TASK 规定仅 modeLabel/modeClass 适配）。
- 2026-10-25 06:10 首个全量日批观察项不变（届时仍为 v1 驱动+文件回执）。
