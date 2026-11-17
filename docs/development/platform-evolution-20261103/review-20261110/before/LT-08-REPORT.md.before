# LT-08 报告：药品追溯采集管道"非查询开销"排查与优化（2026-11-04）

> 任务：[LT-08-药品追溯采集性能](LT-08-药品追溯采集性能.md)｜执行：ZCode / GLM-5.3｜
> 会话：`LT-08`（20261104T132225Z-27e1ebbb）｜交接：[sessions/LT-08.md](../../ai/sessions/LT-08.md)
> 性质：**本地分析+优化+测试，未部署、未连接任何真实系统**（红线遵守：未连 HIS/Oracle、
> 未 SSH <prod-host>、未改宿主驱动目录、未 pip install、未删断言/未加 xfail）。

---

## 一、结论速览

1. **生产"每单 ~11s 非查询开销"的构成**（本地复现+归因，证据见 §四）：
   应用侧每单 CPU 逻辑只有 **6.5—8.6ms**；瓶颈不在判定/入库代码本身，而在：
   - **页级事务提交的持久化 I/O**（本地预载数据库上占总耗时 **87%**；`synchronous=OFF`
     对照实验直接削掉 **82%**）；
   - **worker 全链路的每单 commit 放大**：采集本体 0.17 commits/单 + outbox 投递/prepare
     链 **+2.44 commits/单 ≈ 2.6 次/单**——生产 PG 每次.commit 是一次 fsync 往返；
   - **每单 2 次源端往返**（码明细+单头复核；本地注入 5ms 延迟即放大每单 10.5ms）。
2. **已落地的等价优化（O1—O4）**：每单 SQL 12.45→9.53 条（**-23%**）、应用逻辑每单耗时
   -21%；`batch_fetch` 打开后源端查询 1041→61 次（**-94%**），5ms 延迟下每单总耗时
   21.7→8.1ms（**-63%**）。全部由"优化前后逐行等价"黄金快照守护（8 项测试）。
3. **"每单非查询开销降低 ≥70%"未在总数字上达到，原因已定位**：预载数据库上 87% 的
   耗时是页提交 I/O——这是页级原子性语义（失租整页回滚，BACKLOG 10-27 既定）不能
   触碰的部分；应用逻辑份额（~6.5ms/单）本已远小于停止条件的 0.3s 阈值。降 I/O 的
   正确路径是**部署侧参数**（页大小/PG 同步策略/源端批量化），见 §六。

## 二、方法

- **基准脚本**：`implementation/tests/performance/bench_drug_trace_collect.py`。
  - 合成数据（固定种子 42/43 可复现）：500 单计时窗 + 可选 8000 单历史预载（模拟生产
    累积存量，验证 O(N) 行为）；分布含 1—20 码常规单、缺码（OLD 锚 2020→NOT_REPORTABLE /
    FRESH 锚 2035→WAITING）、>200 码大处方（码明细翻页）、重复扫码/重复行/父子同单/
    数量非法/码数不符/未完成/删除码/前导零 record_id、住院 PATIENT_INOUT（含 3600 码大单）、
    门诊退药（无码等待）、UNKNOWN 分支（KIND-UNKNOWN+BRANCH-UNAPPROVED）、作废单。
  - 源侧：`OracleDrugTraceWarehouse` + SQLite 视图连接工厂（复用
    `test_drug_trace_oracle_first_page.py` 的 ROWNUM→LIMIT/空串→NULL 适配），视图库建
    复合键+游标序索引模拟生产 FBI；`--latency-ms` 注入每次查询模拟网络往返。
  - 形态：默认 `production`（`page_limit=25, max_pages=1` 切片循环，与
    `plugin._j_collect` oracle 模式逐字一致）；另支持 `single`（100/页整窗）对照。
  - 计数：应用库 SQL 按模板（字面量归一 ?）聚合、Connection commit 事件计数；
    `--profile` 打开 `BLYB_DT_PROFILE=1` 内建分阶段计时；`--after-worker` 用真实
    WorkerEngine 跑完 outbox→prepare 链量化 commit 放大。
- **环境**：Windows 工作站、Python 3.14、SQLAlchemy 2.0.49、SQLite WAL+`synchronous=FULL`
  （与 `core_db.engine` 默认一致）；Docker 不可用 → PostgreSQL 组按任务书"若本机有"
  条件跳过（PG 验证列入 §六部署建议）。
- **优化前基线的可信度**：工作区存在大量未提交的 W 系列改动，不能用 git HEAD 当基线；
  改为在 `var/lt08-baseline-code/` 重建优化前四个文件（ingest/quality/sources 用会话初
  原文，service.py 逐条反转本会话编辑），并以**黄金快照逐行一致**验证基线忠实性
  （`var/lt08-baseline-run.py check` 通过）。基线与优化后跑同一份 pristine 预载库
  （`--from-prepared` 每次从原始备份恢复）。

## 三、基线 vs 优化后（同库 A/B，各 2 次取代表值）

### 3.1 空库（500 单，源延迟 0ms）

| 指标 | 基线 | 优化后(O1+O2+O3) | Δ |
|---|---|---|---|
| 每单总耗时 ms | 8.63 / 8.35 | 6.78 / 6.55 | **-21%** |
| 每单非查询耗时 ms | 8.19 | 6.34 | -22.6% |
| 每单 SQL 语句 | 12.45 | 9.53 | **-23%** |
| 每单 commit | 0.17 | 0.17 | 持平（页级事务语义不变） |

### 3.2 预载 8000 单存量库（模拟生产累积，500 单新窗）

| 指标 | 基线 | 优化后 | Δ |
|---|---|---|---|
| 每单总耗时 ms | 56.4 / 61.0 | 55.4 / 56.0 | ~-3% |
| 每单 SQL 语句 | 12.45 | 9.53 | -23% |
| 分阶段（优化后） | — | `page_commit` 24.14s / 27.7s = **87%** | — |
| 诊断：`synchronous=OFF` | — | 55.4 → **9.94 ms/单（-82%）** | fsync/WAL 归因实锤 |

> 预载基线 8000 单采集本身耗时 360s（平均 45ms/单，且随存量增长——O(N²) 形态），
> 本身即"存量越大每单越慢"的直接证据。

### 3.3 源端延迟 5ms（模拟网络往返，500 单空库）

| 指标 | 基线 | 优化后(批关闭) | 优化后+`batch_fetch` |
|---|---|---|---|
| 每单总耗时 ms | 21.69 | 21.36 | **8.09（-63%）** |
| 源端查询次数 | 1040 | 1040 | **61（-94%）** |

### 3.4 worker 全链路 commit 放大（`--after-worker`，200 单空库）

| 指标 | 数值 |
|---|---|
| 采集后 outbox→prepare 链 | 200 事件投递 + 139 个 prepare job，10 tick 收敛 |
| **每单额外 commit** | **+2.44**（合计全流程 ≈2.6 commits/单） |
| 每单额外耗时 | +11.0 ms（本地 SSD；生产 PG=每次 commit 一次 fsync 往返） |

### 3.5 每单路径 SQL 模板计数（预载库，500 单）

基线 6226 条 → 优化后 4766 条。被消除的每单 3×SELECT+1×DELETE（各 500 次）：

| 模板（截断） | 基线 | 优化后 | 消除手段 |
|---|---|---|---|
| `SELECT drug_trace_bill ... WHERE tenant_id/connector_id/复合键` | 500 | 0 | O1 页级预取（21 次/窗口） |
| `SELECT drug_trace_bill_revision ... bill_id+source_revision` | 500 | 500→页级合并* | O1（新单路径免查） |
| `SELECT source_record ... external_id` | 500 | 0 | O1 页级预取 |
| `DELETE FROM quality_issue WHERE tenant+entity_ref+OPEN` | 500 | 0 | O2（新单 entity_ref 不可能命中） |

\* 预取把 revision 查询改为每页一次 `bill_id IN (...)`（21 次覆盖全部页内单）；上表
500 次的模板出现在"每单插入后的 flush 相关"——实际语句数由 6226→4766（-1460 = 3×500
SELECT/DELETE − 63 页级查询）。

## 四、前 3 大根因与证据

### 根因 1：每单持久化提交次数多 × 每次提交是一次 fsync（占生产瓶颈的主导份额）

- 证据 A（本地复现）：预载库上 `page_commit` 占 87%（24.1s/27.7s）；`synchronous=OFF`
  对照 -82%（§3.2）。
- 证据 B（commit 放大）：全流程 ≈2.6 commits/单（§3.4）——采集页事务 0.17/单 +
  `drain_outbox` 每事件 1 commit + prepare job 声称/执行/完成各 1 commit + claim 扫描。
- 证据 C（代码路径，`workflow_core/jobs.py`+`apps/worker/engine.py`）：`claim_job`
  每次领取 `q.all()` 载入全部待处理 job（prepare 积压 1500 行时每 claim 全量装载）；
  `drain_outbox` 每事件 `db.commit()`；`finish_job`/`_finalize_operation` 各自 commit。
- 生产推算：2.6 commits/单 × PG fsync 数百 ms（夜间备份/IO 争用时更差）≈ 每单秒级，
  与 10-31 夜间 2502 单/7.1h（≈10.3s/单）同量级；页推进被拉长到 >20 分钟即触发
  `collect_progress_stalled`（驱动 `COLLECTION_STALL_SECONDS=1200`）。

### 根因 2：每单 2 次源端往返（网络 RTT + 游标打开）

- 本地 5ms 延迟注入：每单 +10.5ms（1041 次查询 × 5ms ≈ 5.2s/500 单）；DBA 侧查询执行
  仅毫秒级（FBI 后 1,479 查询共 1.99s）→ 剩余耗时在**往返/游标打开/解析**。
- `batch_fetch`（O4，默认关）：源查询 1041→61（每页 poll+批量码+批头复核 3 次），
  5ms 延迟组每单 21.7→8.1ms。**开启前须 DBA EXPLAIN 确认 OR 展开走 IX_DT_BILLKEY**
  （谓词保持复合键等值形式）。

### 根因 3：每单 3 次 O(N) 查找 + 1 次 O(N) DELETE（应用层 N+1，已修）

- `drug_trace_bill` 无自然复合键索引（仅 tenant/connector 单列 + updated_at 组合）：
  生产每单身份查找 = 扫该租户全部单（随存量线性涨）；
- `quality_issue` 仅 (tenant,state) 索引：每单清旧 DELETE 扫全部 OPEN 项（WAITING 单
  的项永不关闭、持续累积）；
- O1/O2 消除后每单 -3 SELECT -1 DELETE（§3.5）；PG 侧建议补索引（§六提案，未实施）。

## 五、cProfile 前 16（按 cumtime，500 单空库）

```
基线（总 10.84s，含剖析开销）                优化后（总 10.25s）
import_bill        6.874s  500 次            import_bill        5.318s  500 次
session.flush      2.751s 2148 次            session.flush      2.827s 2148 次
engine.execute     2.378s 6443 次            engine.execute     2.365s 4983 次
unitofwork.execute 2.218s 2045 次            unitofwork.execute 2.286s 2045 次
persistence.save_obj 1.924s 4082 次          persistence.save_obj 1.988s 4082 次
```

- 完整档案：`var/lt08-baseline.prof`、`var/lt08-optimized.prof`（`pstats` 按 cumtime
  前 30 见档案；ORM flush/INSERT 为结构性成本，与单据 JSON 双写（rev 快照 +
  SourceRecordRevision.raw_payload）相关——语义要求，不改）。
- engine.execute 调用次数 6443→4983（-22.6%）：O1/O2 的直接效果。

## 六、部署建议（**不执行**，须用户在场 + 避开 00:30–07:00 与 01:00 药品批次）

### 6.1 变更文件清单（本次已实现部分）

| 文件 | 变更 |
|---|---|
| `plugins/domains/drug_trace/ingest.py` | O1 页级预取 `_prefetch_page`；O4 批量取数消费点；`BLYB_DT_PROFILE` 分阶段计时（`DtProfile`） |
| `plugins/domains/drug_trace/service.py` | O3 gate 合并单次独立会话读；`make_warehouse` 支持 `batch_fetch` 配置；profiling 贯通；`gate_override` 测试接缝 |
| `plugins/domains/drug_trace/quality.py` | `persist_issues(assume_new=)` 跳过新建 revision 的空 DELETE |
| `plugins/sources/db_view/drug_trace.py` | `fetch_bills_batch`（Oracle 真驱动 + 合成仓储）；`batch_fetch` 开关 |
| `tests/performance/bench_drug_trace_collect.py` | 新增基准脚本 |
| `tests/integration/test_drug_trace_collect_equivalence.py` + `goldens/drug_trace_collect_equivalence.json` | 新增等价守护（8 用例） |

### 6.2 风险与回退

- **取消响应延迟**：不变（gate 仍在每页边界；`batch_fetch` 只合并源读取，复核语义保留
  ——版本变化/头缺失/超限自动逐单回退）。O3 使 gate 三项复核同快照读取（更一致）。
- **批量回滚粒度**：不变（仍为页级 25 单原子；O1/O2/O4 只动读路径与等价空删）。
- 回退：回滚本次发行（`git` 一次 revert 覆盖上表 4 个插件文件；基准/等价测试独立无副作用）。

### 6.3 建议的后续动作（按收益排序，均需另行授权）

1. **生产计时验证**：白天补跑一次带 `BLYB_DT_PROFILE=1` 的采集，读取 collection run
   summary 的 `profile.stages_ms`（gate/poll_fetch/read_bill/import_bill/page_commit）
   ——可直接验证 §四根因 1/2 在 PG 上的份额。
2. **PG 索引提案**（LT-08 明确只提案不实施，迁移编号规则不变）：
   - `CREATE INDEX ix_dt_bill_natural ON drug_trace_bill(tenant_id, connector_id, source_system, pharmacy_code, business_kind, source_bill_id)`；
   - `CREATE INDEX ix_quality_issue_entity ON quality_issue(entity_ref)`（或扩列
     `(tenant_id, entity_ref, state)`）。
   O1 落地后收益变小但兜底（rescan/重驱路径仍逐单查）。
3. **PG 提交策略评估**（DBA 决策）：评估 `synchronous_commit` 分级或 commit_delay 类
   参数——本地证据显示 fsync 占 87%；该项动生产数据库配置，必须单独评审。
4. **`batch_fetch` 开启流程**：DBA 对批量 SQL 做 EXPLAIN（确认 OR 展开走 FBI）→ 修改
   连接器配置 `batch_fetch: true`（发布新 ConfigRevision）→ 观察一个白天窗口。
5. **页大小实验**（次优先）：评估 oracle 模式 `page_limit` 25→50/100（减少切片与 gate
   频次）；会加粗提交粒度与取消响应上限，需与值守脚本 `COLLECTION_STALL_SECONDS` 联调。

### 6.4 观察指标（部署后）

- collection run summary：`profile.stages_ms`（各阶段）、每单平均（总时长/bills_seen）、
  `source_metrics.query_seconds/connect_seconds`；
- 夜间批次是否仍 `collect_progress_stalled`；`slices_completed` 推进间隔（驱动 10s 轮询）；
- 等价守护：本地黄金快照测试在 CI/回归中保持通过。

## 七、等价性验收（详见交接）

`tests/integration/test_drug_trace_collect_equivalence.py` 8 项全过：

1. 单次整窗快照==黄金；2. 生产切片形态快照==黄金（形态无关）；
3. 同窗重复采集零新 revision 且快照不变；4. 页边界暂停（gate=paused）部分状态==黄金
（25 单+断点 checkpoint）；5. `batch_fetch` 快照==黄金；6. 版本变化单逐单回退（@1→@2
仅该单差异）；7. 批量超限返回 None 整页回退；8. `BLYB_DT_PROFILE=1` 业务结果不变。
黄金文件由优化前代码生成（`BLYB_DT_REGEN_GOLDEN=1`），优化后未改动一字。

## 八、本地无法复现的部分

- PostgreSQL 行为（索引选择、fsync 成本、autoanalyze/wal 压力）：本机 Docker 不可用；
  上述生产推算（根因 1 数量级）需 §6.3-1 的生产计时验证闭环。
- 真实 HIS 视图的数据分布（长尾大处方比例、无码单比例）与真实网络 RTT：合成分布按
  任务书参数构造；`--latency-ms` 仅做 0/5ms 两组敏感性。
