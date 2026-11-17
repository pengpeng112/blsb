# DTR-BATCH-FIX-20261108 交接：药品批次超时根修部署 + 积压补发（2026-11-08）

任务：执行 [drug-trace-perf-fix-20261105/PLAN.md](../../development/drug-trace-perf-fix-20261105/PLAN.md)
（用户 2026-11-08 授权：部署 + 补发 + 两 timer 启用）｜会话 `20261108T001308Z-5ae57059`（ZCode/GLM-5.3）

## 一、结果速览（全部实测）

1. **根修部署完成并验证**：白天验证窗（11-08 07:40–09:00，221 单）采集 **13 秒完成 =
   59ms/单**（根修前生产 ~10s/单，约 **170 倍**）；worker CPU 88%→**3%**；worker 容器
   恢复 **healthy**（心跳独立线程 Fix6 生效）。
2. **积压补发进行中（自治）**：`systemd-run --unit=batchfix-catchup` 顺序补发
   11-01→11-07 共 7 窗（~16,372 单 PREPARED）。截至 10:20 已发 ~360 单、平台受理
   （ACCEPTED 1,462→1,821，配额 13,180→13,539）。速率 ~10 单/分钟（驱动节流），
   预计 **~24-30 小时**完成（11-09 午后）。限速器跨进程 flock 共享（DMZ 60/min 全局
   协调），与今夜 01:00 批次并发安全、幂等无重发。
3. 今晚 01:00 批次为首夜验证：新代码 + 8h 预算 + 08:30 catchup timer 兜底。

## 二、改动清单

| 文件 | 内容 |
|---|---|
| `plugins/domains/drug_trace/service.py` | Fix1 prepare 换代检查 O(N)→定点 JOIN 单查询（生产实锤 2.61 亿次 package_revision 索引扫描=Σ1..22734）；含方言兜底（单往返 Python 过滤） |
| `plugins/domains/drug_trace/models.py` + `plugin.py` | Fix3 迁移 **020_drug_trace_perf_indexes**：`ix_quality_issue(entity_ref)` + PG `ix_pkg_rev_bill_revision((provenance->>'bill_revision_id'))`；带 downgrade；编号避让 LT 预留 014–019 |
| `apps/worker/engine.py` | Fix4 `_rearm_waiting` 无翻转不 commit（原先 5Hz 空转 commit）；Fix6 心跳独立 daemon 线程（首写立即，长 handler 不再致 healthcheck 误报） |
| `deploy/compose/drug-trace.override.yml` | worker env `BLYB_DT_PROFILE: "1"`（分阶段计时，近零开销） |
| `deploy/linux/purge_terminal_rows.sh` + `blyb-purge-terminal.{service,timer}` | Fix5 终态行留存清理（job SUCCEEDED>14d、outbox 已发布>14d；分批 5000+ANALYZE；默认 dry-run）；timer 每日 12:30 已启用 |
| `docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py` | Fix7.1 `COLLECTION_WAIT_SECONDS` 4h→8h（宿主 /opt/blyb/tools 已同步 sha256 校验） |
| `…/blyb-daily-upload-catchup.{service,timer}` | Fix7.2 每日 08:30 幂等重入兜底（DONE 窗直接进 send→query→report，成功窗零副作用）已安装启用 |
| `tests/integration/test_drug_trace_perf_guard.py` | Fix1 守卫：200 历史提交下 prepare SELECT<150（防 O(N) 回归） |
| `tests/integration/test_drug_trace_domain.py` / `test_drug_trace_pg_fix3.py` | 迁移枚举断言更新（+020；非弱化，新增迁移的正当枚举） |

LT-08 四文件优化（O1–O4+计时段）随本发行一并上线（PLAN Fix2）。

## 三、部署与验证时间线（证据：var/batchfix-*.json）

09:07 preflight（scope-20261028 在位/磁盘足）→ 09:08 backup（BACKUP_HASH_OK，
`<prod-path>/backups/20261108T010733Z`）→ 09:09 upgrade（发行 `batchfix-20261108T0900Z`
六容器 healthy）→ postcheck（020 两索引在位、schema_migrations=020、worker env
BLYB_DT_PROFILE=1）→ driver（新驱动 sha256 OK + 4 unit daemon-reload）→ 09:12-09:13
白天验证窗 DONE（221 单/13s/9 切片）→ enable（catchup timer 明 08:30、purge timer 今
12:30）→ purge dry-run+apply（>14d 终态行 12+55，ANALYZE 完成）→ 09:43/10:03 catchup
systemd 单元启动（7 窗顺序）。

回退路径：`rollback.sh scope-20261028`（迁移 020 索引可留可 DROP）；驱动备份
`/root/blyb-daily-upload.py.bak-*`；两 timer `disable --now`。

## 四、测试与验收（本地）

- 等价黄金 8/8、perf_guard 1/1、fix2+fix3 52/52、domain 34/34 全绿（黄金零改动）。
- 全量：**617 passed / 0 failed / 7 skipped / 1 error**（`var/DTR-BATCH-FIX-regression.xml`）。
  error=test_drug_trace_pg（需 Docker 起隔离 PG，本机 55432 端口被无关任务容器
  `409-test-pg` 占用——环境阻断非代码问题；LT-08 基线该测试为 skip。未动他人容器）。
- 修复过程中两处首红：a04/pg_fix3 迁移枚举（已如上更新）；B_HB_01 心跳首写时序
  （Fix6 改为先写后等，语义与原主循环一致）。

## 五、未完成/观察项

1. **补发收尾（~11-09 午后自然完成）**：查 `journalctl -u batchfix-catchup | tail`；
   逐窗回执 `/var/log/blyb/daily-upload/catchup-2026-11-0*.json`（outcome 应 OK）；
   完成后 `submission` PREPARED 应≈0、四窗+终态以 CONFIRMED 主导、配额≈29.5K/80K。
2. **今夜 01:00 首夜**：receipt `20261109-010029.json` 应 outcome=OK、elapsed<1h；
   明 08:30 catchup timer 首跑零副作用；次晨核对日报行 11-08。
3. **已知缺口（小，后续补丁）**：BLYB_DT_PROFILE 的 stages_ms 在切片（worker）模式
   下未并入 run.summary（只在单窗模式落库）——观测退化为 elapsed/单量；排入 BACKLOG。
4. 提速选项（未做，红线不动 40/min 限速语义）：当前实际 ~10 单/min（发送间节流），
   若需缩短补发窗口须单独评审限速参数。

## 六、运维速查

- 补发进度：`journalctl -u batchfix-catchup -f`；状态 `psql … SELECT state,count(*) FROM submission GROUP BY 1`；配额 `SELECT used,max_bills FROM drug_trace_send_quota`。
- 手动补某窗：`bash /root/batchfix-catchup-run.sh 2026-11-XX`（幂等）。
- purge 手动：`bash /opt/blyb/current/implementation/deploy/linux/purge_terminal_rows.sh`（dry-run）/`--apply`。
