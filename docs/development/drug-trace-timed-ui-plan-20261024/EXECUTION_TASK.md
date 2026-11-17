# 定时上报可视化（前端+API）长任务执行书

版本 r1，2026-10-24。编制：ZCode/GLM-5.3（DTR-TIMED-UI-PLAN-20261024）。
执行者：接手 AI（长任务）。目标：把定时上报的**授权、运行健康、每日结果、平台终态、错误明细**
呈现到管理界面（admin_web 药品追溯页），并让每日回执从"只在服务器文件里"变为"入库可查"。

**本任务只做可视化与报表，不改任何发送/门禁/质量逻辑；生产定时器在跑，部署须避开触发窗口。**

## 一、背景与现状（必读）

定时上报已于 2026-10-24 上线（[上线记录](../drug-trace-timed-report-20261024/RESULT.md)）：

- <prod-host> systemd `blyb-daily-upload.timer` 每日 06:10 Asia/Shanghai 触发；驱动
  `/opt/blyb/tools/blyb-daily-upload.py`（仓库副本
  [daily_upload_driver.py](../drug-trace-timed-report-20261024/daily_upload_driver.py)）在
  blyb-core-1 容器内：幂等采集昨日窗口 → VALID 门诊单逐单串行发送 → 查询补进 →
  **打印 JSON 回执**（宿主 unit 重定向到 `/var/log/blyb/daily-upload/*.json`）。
- 发送授权：send 证据 `0eb0b906-3a3d-497b-abba-62117dd1e672`（standing scope：窗口/药房白名单/
  max_bills，配额台账 `drug_trace_send_quota`）。

### 现有界面（`implementation/apps/admin_web/src/views/DrugTrace.vue`，389 行）

五个卡片：接入就绪（证据/缺项，含 send scope 登记表单与撤销按钮）、采集运行、单据、
提交与回执（调通用 `/api/v1/submissions?limit=100`）、对账。辅助函数 `api.coreGet/corePost`，
角色取 `localStorage['blyb.roles']`。

### 差距清单（本任务要补的）

| # | 差距 | 现状 |
|---|---|---|
| G1 | 每日运行回执只在服务器文件 | 驱动打印 JSON→宿主文件，DB 无记录，界面看不到 |
| G2 | 无按日上传统计 | submissions 接口仅 keyset 分页、无 state/日期过滤（`apps/core_api/app.py:1639`） |
| G3 | 无授权/配额总览 | readiness 只给证据状态，不含 scope、used/max、valid_until |
| G4 | 无自动化健康视图 | 定时器是否在跑、上次结果、下次预计，界面无 |
| G5 | 错误无下钻 | 单条失败要看 delivery_attempt，界面没有 |
| G6 | 页顶模式横幅写死"MOCK/合成（未启用正式）" | 生产已 ENABLED+真实发送，横幅过时误导 |

## 二、设计决策（已定，执行者照做，不再另选）

1. **日报入库而非读文件**：新表 `drug_trace_daily_report`，驱动跑完 POST 上报（幂等 upsert）。
   不让 API 去读宿主文件系统（容器边界、权限、升级漂移都是坑）。
2. **运行报告与平台终态分开展示**：日报表=当天驱动做了什么；"每日实际"=submissions/attempt
   按日聚合的平台结果（次日查询补进后变化）。两表并列，不混算"成功数"。
3. **健康判定纯数据推导**：不查 systemd（容器看不到宿主）；规则见 §5.4。
4. **提交列表增强走 drug_trace 域新端点**，不改通用 `/api/v1/submissions`（其他域在用）。
5. **驱动 v2 向后兼容**：POST 日报失败时行为与 v1 完全一致（文件回执照写、退出码不变），
   只在回执里加 `report_push` 字段。
6. **权限**：GET 类任何已登录主体可看；POST daily-report 要求 operator|platform_admin（驱动用
   bootstrap token 满足）；全部走既有 `auth`/audit 机制。
7. 迁移编号 **012**（drug_trace 现有 009/010/011，见 `plugins/domains/drug_trace/models.py` 底部
   与 `plugin.py:192-197` 的注册方式）。

## 三、材料与入口

| 内容 | 位置 |
|---|---|
| 业务源码根 | `implementation/`（保留路径与导入布局） |
| 领域后端 | `plugins/domains/drug_trace/{routes,service,models,plugin}.py` |
| 前端页面 | `apps/admin_web/src/views/DrugTrace.vue`、API 封装 `src/api.ts` |
| 通用提交接口（只读参考，不改） | `apps/core_api/app.py:1639` |
| 驱动（仓库副本，v2 基线） | `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py` |
| systemd 单元（不改） | 同目录 `blyb-daily-upload.service/.timer` |
| 上线背景与运维命令 | 同目录 `README.md`/`RESULT.md` |
| 测试参考 | `tests/integration/test_drug_trace_api.py`（API 测试模式）、`tests/e2e/serve_drug_trace_ui.py`（UI 本地起法） |
| 发行/升级 | `deploy/release/build_release.py`、`deploy/linux/upgrade.sh`（含备份/回退） |
| 接手规范 | `docs/ai/START_HERE.md`、STATE/HANDOFF/BACKLOG、根 AGENTS.md |

## 四、工作分解（TUI-00 — TUI-09，建议顺序执行）

### TUI-00 开工登记与基线

- `python scripts/ai_session.py status` 确认无范围冲突后
  `start --task DTR-TIMED-UI-20261024 --tool <实际> --model <实际> --scope implementation/plugins/domains/drug_trace --scope implementation/apps/admin_web/src/views/DrugTrace.vue --scope implementation/migrations --scope docs/ai --scope docs/development/drug-trace-timed-ui-20261024`
- 新建任务目录 `docs/development/drug-trace-timed-ui-20261024/`（过程证据、before 副本、交接都放这）。
- 基线：`python -m pytest tests/integration/test_drug_trace_api.py -q` 现状全绿记录；
  `cd implementation/apps/admin_web && npm run build` 现状通过记录。

### TUI-01 数据模型 + 迁移 012

`plugins/domains/drug_trace/models.py` 追加（沿用 009-011 的写法与注册）：

```
DrugTraceDailyReport（表 drug_trace_daily_report）
  id PK；tenant_id（FK tenant，复合唯一 tenant_id+report_date+source）
  report_date Date；source VARCHAR(16)（timer|manual）
  window_begin/window_end TS；candidate_count Integer
  collection JSON；send JSON；query_stats JSON
  outcome VARCHAR(16)（OK|PARTIAL_FAILURE|FAILED）
  elapsed_seconds Numeric；created_at/updated_at TS
migration_fix4.name = "012_drug_trace_daily_report"（手写 upgrade/downgrade）
```

- 约束：`UniqueConstraint("tenant_id","report_date","source",name="uq_dt_daily_report")`。
- JSON 字段直接存驱动回执对应结构（§5.1）。
- 验收 T-A01：隔离 PG 与 SQLite 双跑 012 up/down 幂等；存量 009-011 不受影响。

### TUI-02 日报 API（drug_trace 域）

`routes.py` 新增三个端点（全部走既有 auth/audit/ok() 封装）：

1. `POST /api/v1/drug-trace/daily-reports`
   - body：`{report_date, source, window_begin, window_end, candidate_count, collection,
     send, query_stats, outcome, elapsed_seconds}`（驱动 v2 的负载）
   - 幂等：按 (tenant, report_date, source) upsert（存在则整体更新并 `updated_at` 刷新）。
   - 角色：operator|platform_admin；audit `drug_trace.daily_report.ingest`。
   - 校验：report_date 为 ISO 日期；outcome 枚举；JSON 字段深度≤4、总长≤64KB（防滥用）。
2. `GET /api/v1/drug-trace/daily-reports?limit=30`：按 report_date 倒序，返回摘要行
   （不含 errors 明细，明细走单条）。
3. `GET /api/v1/drug-trace/daily-reports/{report_date}`：单日完整（含 send.errors）。

### TUI-03 统计与授权 API

1. `GET /api/v1/drug-trace/upload-stats?days=14`：
   SQL 按 `submission.created_at::date` 与 `state` 聚合，另按 `delivery_attempt.outcome` 聚合
   首末次尝试；返回 `[{date, by_state:{...}, attempts:{accepted,failed,...}, total}]`。
   仅统计本租户；日期参数上限 90。
2. `GET /api/v1/drug-trace/send-scope?profile=alihealth_yljg_retail_v1`：
   返回现行 send 证据 `{evidence_id, status, valid_until, scope:{window_begin,window_end,
   pharmacies[],max_bills}, quota:{used,max_bills}, config_release_id, destination:{id,code,
   status,is_mock}}`；多目的地/未登记时显式字段为空（不猜）。
3. `GET /api/v1/drug-trace/submission-outcomes?days=7&only=problem`：
   近 N 天提交，关联最近一次 `delivery_attempt`（outcome/http_status/ended_at）与
   package→bill 的 `source_bill_no`；`only=problem` 过滤
   state∈{REJECTED,PARTIAL,UNKNOWN} 或 outcome≠ACCEPTED；上限 200，倒序。

- 验收 T-A02：三端点在既有 API 测试框架（env fixture）下用合成数据断言：upsert 幂等
  （二次 POST 更新不重复）、角色拒绝（无角色 401/403）、stats 与手写 SQL 对照、
  send-scope 在 quota used 递增后反映、outcomes 的 only 过滤正确。

### TUI-04 驱动 v2：日报上报

改 `daily_upload_driver.py`（仓库副本即源头）：

- 结束前组装 `POST /api/v1/drug-trace/daily-reports`（source='timer'，report_date=昨日，
  token 已有）；失败仅记 `receipt['report_push']={'ok':false,...}`，**不改变发送/退出码语义**。
- 文件回执保留不变（宿主 unit 不改）。
- 部署时同步替换 <prod-host> `/opt/blyb/tools/blyb-daily-upload.py`（TUI-08）。
- 验收 T-A03：本机单测——用本地起的最小 HTTP stub 或 monkeypatch `call()`，断言成功推送、
  失败不影响 receipt/退出码、负载字段与 §5.1 契约一致。

### TUI-05 前端"定时上报"面板

`DrugTrace.vue` 在"接入就绪"卡之后新增卡片（沿用既有 card/table/mono 样式与 api 封装）：

```
┌ 定时上报（自动化）───────────────────────────────────────────────┐
│ 授权：窗口 10-24→11-23 ｜ 药房 5 家 ｜ 配额 2/80000 ｜ 有效期至 11-21 ｜ 证据 APPROVED │
│ 健康：上次运行 10-25 06:10 OK（耗时 74 分钟） ｜ 下次预计 10-26 06:10 ｜ 连续正常 N 天 │
│ 日报表：日期｜采集摘要｜候选｜发送｜跳过｜错误数｜结果｜耗时 ｜ [详情]                    │
│   详情区（点开）：send.errors 逐条（step/错误码/截断消息）＋ aborted_reason              │
│ 每日实际（平台终态，含次日补进）：日期｜CONFIRMED｜ACCEPTED｜PARTIAL｜REJECTED｜UNKNOWN｜合计 │
│ 异常提交：提交｜状态｜最近尝试 outcome/http｜单号｜时间 ｜ [查询状态]                       │
└──────────────────────────────────────────────────────────────┘
```

- 健康规则（§5.4）：`连续正常`、`下次预计=最后 report_date+1 天 06:10`、
  `应跑未跑`（now 已过当日 07:30 本地仍无昨日报告 → 红色 MISSING）。
- 数字列右对齐；状态色沿用 ok/warn/err 类；errors 数>0 或 PARTIAL_FAILURE 标红。
- onMounted 并入四个加载函数；刷新按钮；不新增轮询（手动刷新即可）。

### TUI-06 页顶横幅动态化

`modeLabel/modeClass` 改为 computed：从 readiness 取零售 profile 目的地——
`is_mock=true → 'MOCK/合成'`；`is_mock=false && status=ENABLED → '正式链路'`；
其余 '未启用'。文案与现有说明段落同步改写（不再说"未启用正式"）。

- 验收 T-A04：`npm run build` 通过；本地 serve（tests/e2e/serve_drug_trace_ui.py 的起法或
  等价 vite 预览）+ 合成栈截图：面板四区渲染、横幅随 mock/真实切换、空数据（无日报）显示
  "尚无运行记录"而非报错。

### TUI-07 测试汇总

- 后端：TUI-02/03 的集成测试并入 `tests/integration/`（新建 `test_drug_trace_timed_ui.py`
  或并入既有文件，遵循现有 fixture 隔离模式；不得连生产/真库）。
- 驱动：TUI-04 单测。
- 前端：build + 本地浏览器验证留截图于任务目录。
- 相关局部全绿后跑一次全量：`python -m pytest tests -q -ra`（W09 基线 277/6/0 之后无已知红）。

### TUI-08 构建发行与生产部署（须用户在场确认窗口）

1. `python deploy/release/build_release.py`（产出新发行；含前端产物）。
2. 部署 <prod-host> 按 `deploy/linux/upgrade.sh` 既有流程（自动备份、迁移 012 随升级执行）。
   **窗口避开每日 06:00–08:30**；升级前后各跑一次 `prod_status.py`（仓库
   `docs/development/drug-trace-timed-report-20261024/`）留证。
3. 替换驱动：宿主 `/opt/blyb/tools/blyb-daily-upload.py` ← 仓库 v2；
   `systemctl` 不动 timer（下次触发自动用新驱动）；容器内 AST 校验一次。
4. 部署后验证（全部只读/幂等）：
   - `GET daily-reports` 空列表 200；
   - 手动 `systemctl start blyb-daily-upload.service` 跑一次（幂等：昨日已处理则全跳过，
     产生当日报告或空跑报告），确认入库+界面可见；
   - `systemctl list-timers` 下次触发时间正确。
5. 回滚：`deploy/linux/rollback.sh` + 恢复旧驱动文件；012 downgrade 脚本可用。
   已入库日报为新增数据，回滚不删除（无害）。

- 验收 T-A05：发行清单/哈希、升级日志、迁移 012 applied、面板在生产显示真实数据、
  timer 仍 active、下次触发时间正确；全部留证。

### TUI-09 文档与交接

- 任务目录写 RESULT.md（实测/未测/阻断分开）、交付清单。
- 更新共享 STATE/HANDOFF/BACKLOG、
  `数据上报/1、码上放心平台/README.md`（界面查看入口一句话）。
- `ai_session.py end --handoff docs/ai/sessions/DTR-TIMED-UI-20261024.md`。

## 五、契约细节（照此实现，勿自造字段名）

### 5.1 日报负载（驱动 POST / daily-reports GET 共用）

```json
{"report_date": "2026-10-25", "source": "timer",
 "window_begin": "2026-10-24T00:00:00", "window_end": "2026-10-25T00:00:00",
 "candidate_count": 1780,
 "collection": {"id": "<run_id>", "state": "DONE",
                "summary": {"bills_seen": 3500, "revisions_created": 3400, "valid": 1780, "waiting": 1620}},
 "send": {"sent": 1780, "prepared_new": 0, "resumed": 5, "skipped_terminal": 2,
          "errors": [{"submission": "...", "step": "send", "res": {"error_code": "..."}}],
          "aborted_reason": ""},
 "query_stats": {"queried": 120, "errors": []},
 "outcome": "OK", "elapsed_seconds": 4480.2}
```

GET 列表行：`{report_date, source, outcome, candidate_count, sent, errors_count, elapsed_seconds, created_at}`。

### 5.2 upload-stats 响应

```json
{"days": [{"date": "2026-10-25",
           "by_state": {"CONFIRMED": 1770, "ACCEPTED": 8, "PARTIAL": 1, "REJECTED": 0, "UNKNOWN": 1},
           "attempts": {"accepted": 1780, "http_error": 2, "other": 0},
           "total": 1780}]}
```

### 5.3 send-scope 响应

```json
{"profile": "alihealth_yljg_retail_v1",
 "evidence": {"evidence_id": "0eb0b906-...", "status": "APPROVED", "valid_until": "2026-11-21T16:00:00+00:00",
              "config_release_id": "2e138e50-..."},
 "scope": {"window_begin": "2026-10-24T00:00:00+08:00", "window_end": "2026-11-23T00:00:00+08:00",
           "pharmacies": ["050121", "..."], "max_bills": 80000},
 "quota": {"used": 2, "max_bills": 80000},
 "destination": {"id": "345df8f4-...", "code": "alihealth-drugtrace-retail", "status": "ENABLED", "is_mock": false}}
```

### 5.4 健康规则（前端实现）

- `下次预计 = max(report_date)+1 天 06:10`（Asia/Shanghai，前端本地时区即院方时区）。
- `MISSING`：本地时间 > (昨日+1 天 07:30) 且无昨日报告 → 红。
- `连续正常`：从最新一天向前数连续 outcome=OK 的天数；遇到 MISSING/PARTIAL_FAILURE 停。
- 无任何报告：显示"尚无运行记录"，不判 MISSING（可能是刚部署）。

## 六、验收总表（TUI-A）

| 编号 | 条件 |
|---|---|
| A01 | 迁移 012 双库（隔离 PG+SQLite）up/down 幂等；009-011 不受影响 |
| A02 | 三类新 API 集成测试绿：upsert 幂等/角色拒绝/统计正确/scope 反映配额/outcomes 过滤 |
| A03 | 驱动 v2 单测绿：推送成功、推送失败不影响回执与退出码、契约字段一致 |
| A04 | npm build 通过；本地合成栈浏览器截图：面板四区+横幅动态+空态 |
| A05 | 生产部署留证：迁移 applied、面板真实数据、timer active、下次触发正确；升级窗口避开 06:00-08:30 |
| A06 | 全量回归 ≥ 基线（277 passed 水平，新增测试计入） |
| A07 | 门禁零改动：send/质量/证据/配额代码路径 diff 为零（或仅加只读查询） |
| A08 | 文档/状态/交接齐备，实测-未测-阻断分开 |

## 七、边界与禁止事项

- **不改**发送、质量、证据审批、配额语义；所有新端点只读或仅新增日报数据。
- 不让 API/容器读宿主文件系统；不查 systemd（健康用数据推导）。
- 不新增自动轮询/自动重试；CONFIRMED/REJECTED 语义不变。
- 测试不连生产、不真发平台请求（沿用合成/Mock 模式）；生产部署须用户在场确认窗口。
- 既有回执文件、上线证据、冻结 payload 不动；租户隔离（全部查询带 tenant_id）。
- 报告负载大小上限 64KB；errors 明细界面截断显示（≤200 字符/条）。

## 八、风险与回滚

| 风险 | 缓解 |
|---|---|
| 部署窗口撞上 06:10 触发 | 升级安排在其他时段；upgrade.sh 失败自动回退 |
| 驱动 v2 POST 死循环/阻塞 | 单次 POST、90s 超时、失败即放弃（回退文件回执） |
| 日报表膨胀 | 一天一行（timer 源），30 天列表+单日详情，无需分区 |
| 统计 SQL 慢 | days≤90、created_at 有索引；必要时加 LIMIT |
| 前端回归 | 仅新增卡片+横幅 computed，不动既有五卡片逻辑 |
