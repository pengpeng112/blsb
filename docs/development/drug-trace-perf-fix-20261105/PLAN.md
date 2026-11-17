# 药品追溯夜间批次超时根修计划（2026-11-05，交执行 AI 一次性完成）

> 编制：ZCode/GLM-5.3（依据 2026-11-05 生产只读诊断 + LT-08 本地基准）。
> 性质：**开发+测试在本地完成，部署与补发为独立用户授权阶段**。
> 目标：夜间 01:00 批次在 4h 预算内完成（采集+发送+查询+日报全绿），清空 9,426 单 PREPARED 积压。
> 执行 AI 必读：`AGENTS.md`、本文件全文；开工先 `python scripts/ai_session.py status` 再登记
> `--task DTR-BATCH-FIX-20261105 --tool <真实工具> --model <真实模型> --scope <本文§9 路径清单>`。

---

## 0. 背景：连续 4 晚失败的事实链（生产实测）

- 11-01 起每晚 01:00 `blyb-daily-upload.service` 启动 → 05:00 `collect_timeout`（4h 预算 14400s 用尽），连续 4 晚同一失败；采集本体由 worker 在驱动放弃后继续跑完（每窗 ~11h，DONE 在次日中午）。
- **发送从未执行**：10-31 窗口（1,509 单 CONFIRMED）之后再无外发；11-01～11-04 四窗 VALID 单冻结为 PREPARED 后积压 **9,426** 张（已核实全部为 current revision、distinct bill，旧版本提交不会重发——驱动候选 SQL 按 `bill.current_revision_id` 选取）。
- 查询层正常：11-04 窗口 3,162 次源查询共 4.27s（FBI 生效）。慢在应用侧每单 ~10s。

## 1. 根因→修复映射（全部带生产证据）

| 编号 | 根因 | 生产证据（pg_stat_*，2026-11-05 08:18 实测） | 修复 |
|---|---|---|---|
| R1 | `service.prepare_submission` 换代检查 O(N)：`.all()` 载入该目的地全部 submission 再逐行 `db.get(PackageRevision)` | package_revision idx_scan=**261,049,382** ≈ Σ1..22734（每冻结一单扫全部历史）；submission seq_scan=27,897 | Fix1：定点化单查询 |
| R2 | 每单 `quality.persist_issues` DELETE 顺序扫 quality_issue（索引仅 (tenant_id,state)，OPEN 7.4 万行堆积） | quality_issue seq_scan=**37,280** ≈ bills 37,011；live 74,733 | Fix2（部署 LT-08 O2）+ Fix3（entity_ref 索引兜底 rescan/重判路径） |
| R3 | worker tick 5Hz 空转：claim_job 顺序扫 job 表 69,797 行（seq_scan **1,273,094**）、drain_outbox 扫 outbox 61,730 行（seq_scan **562,978**）、`_rearm_waiting` 每秒无条件 commit；worker CPU 87.6%；xact_rollback 643 万 | docker stats + pg_stat_user_tables + pg_stat_database | Fix4（tick 无事不提交）+ Fix5（留存清理，恢复索引选择性） |
| R4 | 历史行只增不清（job/outbox/quality_issue），统计退化 → 优化器弃索引 | 上行计数全为顺序扫描 | Fix5 |
| R5 | 长 handler 阻塞主循环 → 心跳超 5s 不刷新 → healthcheck unhealthy 抖动 | `docker inspect <worker-container>` Health=unhealthy（07:30 仍 healthy，间歇抖动） | Fix6（心跳独立线程）+ Fix1（prepare 变快后自然缓解） |
| R6 | 每单 3 次 SELECT + 源端每单 2 次往返（LT-08 结论） | LT-08-REPORT §四（本地同库 A/B） | Fix2（部署 LT-08 O1/O3/O4） |
| R7 | 驱动 4h 预算 < 实际耗时；10-31 的一次性值守脚本未常态化 | journalctl：11-01..11-05 每晚 01:00 start → 05:00 Failed | Fix7（预算 8h + 每日 08:30 幂等 catchup timer） |

**预期修复后量级**：采集每单 ~10s → ~0.1-0.3s（R1/R2/R6 消除）+ prepare ~800 单 × <100ms；1,500 单窗口采集 ≤30min、整批 <1h，4h 预算余量充足。

---

## 2. Fix1 · prepare 换代检查定点化（核心修复，R1）

文件：`implementation/plugins/domains/drug_trace/service.py` → `prepare_submission`。

现状（O(N)+N+1）：

```python
prior = None
for s in (db.query(Submission)
          .filter(Submission.tenant_id == tenant_id,
                  Submission.destination_id == dest.id).all()):
    prx = db.get(PackageRevision, s.package_revision_id)
    if prx is not None and \
            (prx.provenance or {}).get("bill_revision_id") == revision.id \
            and s.state != C.SUBMISSION_STATE_SUPERSEDED:
        if prior is None or (s.created_at or utcnow()) > \
                (prior.created_at or utcnow()):
            prior = s
```

改为**单条 JOIN 查询**（语义逐字保持：同租户+同目的地+非 SUPERSEDED+冻结包指向本 revision，取 created_at 最新一条）：

```python
prior = (db.query(Submission)
         .join(PackageRevision,
               PackageRevision.id == Submission.package_revision_id)
         .filter(Submission.tenant_id == tenant_id,
                 Submission.destination_id == dest.id,
                 Submission.state != C.SUBMISSION_STATE_SUPERSEDED,
                 PackageRevision.provenance["bill_revision_id"]
                   .as_string() == revision.id)
         .order_by(Submission.created_at.desc())
         .first())
```

- 等价性论证：过滤条件与排序语义与原循环完全一致（原循环即"取匹配中 created_at 最大者"）；`.first()` + desc 排序即最大值。原循环遍历顺序未定义、只取最大，顺序无语义。
- 方言注意：`JSON ->> 'bill_revision_id'`（PG）与 SQLite JSON1 `json_extract` 均由 SQLAlchemy 2.0 `.as_string()` 生成；**若 SQLite 测试环境报 JSON 方言问题**，退回"单查询取回 (Submission, provenance) 二元组再在 Python 过滤"版本（仍是一次往返、无逐行 get，性能目标同样达成）。
- **必测**：`tests/integration/test_drug_trace_fix3.py` 中 prepare 换代全部既有用例（PREPARED→SUPERSEDED、已发送拒绝换代、同代幂等返回）不改一字必须全绿；等价黄金 8 项不受影响（prepare 不在采集快照内）。
- 新增 1 个守卫测试（建议放 `test_drug_trace_fix3.py` 或新文件）：用 SQLAlchemy `before_cursor_execute` 事件计数，断言"对已有 N≥200 个 submission 的目的地执行一次 prepare，SELECT 语句数 ≤ 常数（如 6）"——防止回退成 O(N)。

## 3. Fix2 · 发布 LT-08 已实现的采集优化（R2/R6）

**LT-08（2026-11-04）已在工作区完成并测试，未部署**。随本包一起发布，不要重写：

- `plugins/domains/drug_trace/ingest.py`：O1 页级预取（每单 3 SELECT→每页 3）、O2 新建 revision 跳过 quality_issue 空 DELETE（直击 R2 采集路径）、O4 batch_fetch 消费点；
- `plugins/domains/drug_trace/service.py`：O3 gate 合并单次独立会话读、`BLYB_DT_PROFILE` 分阶段计时；
- `plugins/sources/db_view/drug_trace.py`：`fetch_bills_batch`（默认关）；
- 守护：`tests/integration/test_drug_trace_collect_equivalence.py` 8 用例 + 黄金快照必须全绿且黄金文件零改动。

部署时 worker 容器环境加 `BLYB_DT_PROFILE=1`（近零开销，`LT-08-REPORT` §6.3-1 要求的首周生产计时，读取 collection run summary 的 `profile.stages_ms`）。

## 4. Fix3 · 迁移：两个表达式/列索引（R2 兜底 + Fix1 加速）

迁移编号规则：**014—019 已被 LT 计划预留（README §5.3），必须先用
`Select-String -Path packages/core_db/migrate.py -Pattern "register\("` 确认当前最大号，用 ≥020 的空号**（预期 020）。

参照 012/013 的写法（`packages/core_db/migrate.py` + 域模型迁移挂载），SQLite 与 PG 方言均可执行、带 downgrade：

```sql
-- upgrade
CREATE INDEX ix_quality_issue_entity ON quality_issue (entity_ref);
CREATE INDEX ix_pkg_rev_bill_revision ON package_revision ((provenance->>'bill_revision_id'));  -- PG
-- SQLite 版本用表达式等价写法（参照域迁移现有 JSON 索引写法；若无先例则仅在 PG 建此索引，按方言分支）
-- downgrade
DROP INDEX IF EXISTS ix_quality_issue_entity;
DROP INDEX IF EXISTS ix_pkg_rev_bill_revision;
```

- `ix_quality_issue_entity`：rescan/重判/迁移路径仍逐单清旧 DELETE，O2 未覆盖这些路径，此索引兜底（74,733 行建索引秒级）。
- `ix_pkg_rev_bill_revision`：让 Fix1 的 JOIN 走索引（当前 22,734 行，全扫也只 ~20ms，属锦上添花）。
- PG 上不用 `CONCURRENTLY`（域迁移在事务内执行；表小、部署窗口在白天，短暂锁可接受）——与现有迁移保持一致。

## 5. Fix4 · worker tick 无事不提交（R3）

文件：`implementation/apps/worker/engine.py`。**只做减法，不改任何投递/租约语义**：

1. `_rearm_waiting`：仅当确有 step 翻转（`flipped > 0`）才 `db.commit()`；无变化直接返回（当前每 tick 无条件 commit）。
2. `drain_outbox`：**保持每事件一 commit 的 at-least-once 语义不变**（明示不批量化——语义红线）；仅确认其查询走 `ix_outbox_unpublished`（Fix5 清理后 planner 自然回索引，可加 `ANALYZE outbox_event` 到清理脚本尾部）。
3. `claim_job` 查询不改（租约/fencing 语义不动）；行数下降（Fix5）后顺序扫描消失。
4. `_connector_poll` / `_advance_workflows`：确认空结果路径当前已无多余 commit（读代码核实，若有同样改为条件提交）。

## 6. Fix5 · 终态行留存清理（R4，恢复索引选择性）

新增**主机侧维护脚本 + systemd timer**（默认安装不启用，用户确认后 enable——新调度默认停用是工作区铁律）：

- 脚本 `implementation/deploy/linux/purge_terminal_rows.sh`（或 .py，仿 `deploy/linux/` 现有脚本风格）：
  - `docker exec blyb-db-1 psql -U blyb -d blyb` 内分批（每批 ≤5000 行、独立事务）删除：
    `DELETE FROM job WHERE state='SUCCEEDED' AND finished_at < now()-interval '14 days'`（同条件先 SELECT COUNT 输出到日志）；
    `DELETE FROM outbox_event WHERE published_at IS NOT NULL AND published_at < now()-interval '14 days'`；
  - 尾部 `ANALYZE job; ANALYZE outbox_event; ANALYZE quality_issue;`
  - `--dry-run` 默认开（只 COUNT 不 DELETE），显式 `--apply` 才执行。
- systemd unit `blyb-purge-terminal.timer`（每日 12:30 Asia/Shanghai，Persistent=true），**install 时不 enable**，部署清单里单列"用户确认后 enable"。
- 不动 quality_issue 的行（业务质量项历史保留；R2 已由 O2+Fix3 解决）。
- 首次执行前人工跑一次 `--dry-run` 把 COUNT 截图/贴日志入交接。

## 7. Fix6 · worker 心跳独立线程（R5）

文件：`implementation/apps/worker/engine.py` → `main()`：

- 把主循环内的 heartbeat 写块（`if hb_path and now - hb_last >= 5.0: ...`）抽成 daemon 线程（`threading.Event().wait(5.0)` 循环 + 原子 tmp/rename 写，与现写法一致），`main()` 启动时 start；
- 主循环删除原内联块；LeaseGuard 自己的心跳写保留不动（重叠无害）；
- 效果：任何长 handler（prepare/采集页）都不再使 healthcheck 误报 unhealthy。

## 8. Fix7 · 驱动预算与常态化兜底（R7）

文件：`docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py`（工作区即部署源，宿主实际路径 `/opt/blyb/tools/blyb-daily-upload.py`）：

1. `COLLECTION_WAIT_SECONDS = 4 * 60 * 60` → `8 * 60 * 60`（注释注明 2026-11-05 根修背景；修复后实际耗时 <1h，8h 为极端兜底）。
2. 新增 `blyb-daily-upload-catchup.timer`（每日 **08:30** Asia/Shanghai，Persistent=true）跑同一脚本、无参（窗口=昨天，与 01:00 同窗）：
   - **幂等重入安全论证**（写进 unit 注释）：驱动按窗口查 run——DONE 窗口跳过采集直接进 send（补发夜里未及发送的 PREPARED）→ query → 日报幂等 upsert；01:00 已成功的窗口重入时 send 步骤全 `skipped_terminal`，零副作用。这正是 10-31 值守脚本手工做的事，常态化为 timer。
3. 部署方式：沿用该目录 `install_timer.py`（或手动 scp + sha256 校验），**同步更新宿主副本并核对 sha256 一致**。
4. 红线说明：该目录此前对 LT 任务只读；本次为用户明确委托的根修，允许修改，**改动逐行留 diff 于交接文件**。

## 9. 补发 9,426 单积压（用户授权后执行，属部署阶段）

前置：Fix1—Fix7 已部署且 11-05 窗口采集 DONE（预计部署日中午前）。

```bash
# 逐窗重入驱动（DONE 窗直接进 send；40/min 限速，9,426 单 ≈ 4h）
for D in 2026-11-01 2026-11-02 2026-11-03 2026-11-04; do
  python3 /opt/blyb/tools/blyb-daily-upload.py "$D"   # 按驱动实际入参格式；先 --help 核对
done
# 11-05 窗口由当晚 01:00 批次自然处理（或当天下午手动补跑）
```

- 配额核验：evidence scope max_bills=80,000，已用 13,180（10-31 口径）+9,426 → ~22.6k，余量充足；每窗完成后核对 `submission` 状态分布与日报行。
- **必须用户在场+明确授权后才执行**（真实外发阿里健康）。

## 10. 修改文件清单（本任务 scope，登记 ai_session 用）

```
implementation/plugins/domains/drug_trace/service.py          （Fix1）
implementation/apps/worker/engine.py                          （Fix4/Fix6）
packages/core_db/migrate.py 或 plugins/domains/drug_trace/models.py（Fix3 迁移，按现有挂载点）
implementation/deploy/linux/purge_terminal_rows.*             （Fix5 新增）
implementation/deploy/linux/blyb-purge-terminal.{service,timer}（Fix5 新增，默认不启用）
docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py（Fix7.1）
docs/development/drug-trace-timed-report-20261024/blyb-daily-upload-catchup.{service,timer}（Fix7.2 新增）
tests/...（Fix1 守卫测试新增；不改任何既有断言）
docs/ai/*、implementation/docs/测试执行记录.md（收尾回写）
docs/development/drug-trace-perf-fix-20261105/（本目录，交接与证据）
```

LT-08 已改的 4 个插件文件+2 个测试文件随包发布，不再重复登记。

## 11. 部署顺序（白天窗口、避开 00:30–07:00 与 01:00 批次、用户在场）

1. 本地全量验证通过（§12）→ 按现有 release 流程构建（W09 白名单机制，把新增 deploy 文件入白名单）。
2. 宿主升级应用栈（core/worker 容器镜像），worker env 加 `BLYB_DT_PROFILE=1`。
3. 迁移随应用启动自动执行（020 建索引）。
4. 上传驱动新副本 + catchup/purge 两个 unit（**都不 enable**），核对 sha256。
5. 手动验证：跑一次白天小窗采集看 `profile.stages_ms`；手动 `systemctl start blyb-purge-terminal` 的 dry-run。
6. 用户确认后：enable 两个 timer；按 §9 补发积压。
7. 次日核验：01:00 批次 receipt outcome=OK、elapsed <1h、CONFIRMED 率、08:30 catchup 空转无副作用、purge 日志。

## 12. 验收（分三类如实记录，未跑不写通过）

| 类别 | 项 |
|---|---|
| 实测（本地） | 等价黄金 8/8（零改动）；fix2/fix3 prepare 换代用例全绿；Fix1 守卫测试（SELECT 数常数级）；迁移 SQLite 幂等+downgrade；`python -m pytest tests -q -k drug_trace` 不新增失败；全量 `python -m pytest tests -q -ra -p no:cacheprovider --junitxml=../var/DTR-BATCH-FIX-regression.xml` 不新增失败（基线：606/18/0，LT-08 口径；复核探针脚本本工作区缺失，沿用已知登记） |
| 实测（部署后生产，用户在场） | 白天验证窗 stages_ms 采集 ≤0.3s/单量级；当晚 01:00 outcome=OK、elapsed<1h；08:30 catchup 正常；worker CPU 回落（<20% 空闲）、healthcheck 不再抖动 unhealthy；job/outbox 顺序扫描计数停止增长 |
| 实测（补发后） | PREPARED 清零；四窗终态 CONFIRMED 主导；配额台账核对；日报四行回填 OK |
| 未测/外部 | PG 真实行数下的 Fix1 JOIN 计划（部署后 EXPLAIN 留证）；DBA 可选项：`ALTER SYSTEM SET track_io_timing=on`、`wal_compression=on`（与 pg_reload_conf()，仅观测/WAL 减量，非本任务必需） |

## 13. 回退

- 应用：回滚发行镜像（迁移 020 downgrade 或保留无害索引）。
- 驱动：`/root/blyb-daily-upload.timer.bak-*` 模式再备份一份当前版；常量回 4h；两个新 timer `disable --now` 即可（不删）。
- Fix1/4/6 均纯应用代码，随镜像回滚自然回退。

## 14. 红线（违反即任务失败）

1. 开发/测试阶段不连接真实 HIS/Oracle/阿里健康、不 SSH 生产改东西（部署阶段动作全部在用户在场时执行）。
2. 不 pip install 新依赖；不删测试断言、不加 xfail、不改黄金快照。
3. 不改发送/查询/对账语义、幂等键、content_digest、证据门禁、40/min 限速、租约/fencing。
4. drain_outbox 每事件一提交的 at-least-once 语义不动。
5. 迁移号先 grep 再用 ≥020 空号；带 downgrade。
6. 收尾按 AGENTS：交接 `docs/ai/sessions/DTR-BATCH-FIX-20261105.md`、STATE/BACKLOG/HANDOFF 顶部、测试执行记录、`python scripts/project_docs.py inventory`、`python scripts/ai_session.py end`（未全完成只能 handed_off）。

## 15. 卡住时

- Fix1 的 JSON 方言问题：用§2 的 Python 过滤退化版（一次往返）即可达标，勿死磕。
- 生产 stages_ms 与预期不符（如 page_commit 仍占大头）：如实记录到交接，转 DBA 项（PG 同步策略），不要自行改 synchronous_commit。
- 同一错误 3 次不成功：停下写交接转 handed_off。
