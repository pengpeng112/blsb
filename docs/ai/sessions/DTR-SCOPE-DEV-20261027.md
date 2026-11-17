# DTR-SCOPE-DEV-20261027 交接（药品追溯·口径扩展开发）

- 工具/模型：ZCode / GLM-5.3；session `20261027T134424Z-45575e89`
- 时间：2026-10-27 21:44–23:5x（本地）；唯一执行依据 [EXECUTION_TASK.md](../../development/drug-trace-scope-dev-20261027/EXECUTION_TASK.md)
- 结果主文档：[RESULT.md](../../development/drug-trace-scope-dev-20261027/RESULT.md)
（实测/未测/阻断分开 + 部署清单 + 待确认清单）；本文件只记过程与接手续节。

## 改了什么

1. **SD-01 只读探查**（用户入口授权；sjzc <relay-jump> 通道 READY_HIS + <prod-host> 只读事务）：
   - 应用库：住院 WAITING 样本/退药 3 张键/260 缺码全量键+300 VALID 对照键/205 时间分布
     （evidence/sd01-appdb-*.json；键值明细在 .ai/private/drug-trace-scope-dev-20261027/）。
   - HIS 四问定论（evidence/SD-01-findings.md）：住院 REQUEST_NO/DIS_RECORDNO
     10+10 键在 MED_TRACE_RECORD 全零命中（PDA_STATE 全 0）=住院码未扫码落库；
     退药 57370/71/72 无任何 IN 行（全为历史复用 OUT）；HIS 无 bill_type 列/
     配置线索；260 缺码组人均药行 4.44 vs VALID 1.46、WINDOW_NO 缺失 97.4% vs 86.6%、
     拆零无差异。
   - 可复跑脚本 sd01_pull_appdb.py / sd01_probe_his.py / sd01_probe_his_r2.py。
2. **代码**（11 文件，见 RESULT 表）：contracts（INPATIENT_DISPENSE、NOT_REPORTABLE、
   RESCAN、BILL_TYPE_KEY_OVERRIDES）、quality（7 天终态+退药原单判定+迁移判定）、
   payload（resolve_return_original 只读复用原 CONFIRMED 冻结码+reversal_of）、
   ingest（原单解析、同 digest 幂等迁移、not_reportable 计数）、service
   （run_rescan/_rescan_one）、routes（collection-runs mode=rescan、overview 计数）、
   plugin（_j_rescan）、source_profiles（两个 r11 profile，unresolved 阻断）。
3. **r11 视图 DDL**：sql/r11_inpatient_views.sql（4 视图+授权；列契约与 r10
   逐列一致；DBA 执行，本任务不连生产建）。
4. **驱动 v3**：docs/development/drug-trace-timed-report-20261024/daily_upload_driver.py
   原文件升级（回看阶段 safe_run_lookback；主阶段退出码语义与 v2 一致）。
5. **UI**：kinds+住院发药、NOT_REPORTABLE 标签+置灰（DrugTrace.vue/
   drugTraceDisplay.ts/drugTrace.css）。
6. **SD-06**：sd06_pull_205.py（205 逐单清单入受限目录）+ sd06_report.py（纯函数）
   → SD-06-report.md。原厂家 10-25 rec=0、我方 204 张 04:28-04:36 不重叠；
   3503 程序 10-26/27 仍在上传（双上报风险持续）。
7. **测试**：新增 test_drug_trace_scope_dev.py 13 项；test_drug_trace_domain::test_a05
   按 K1 口径重挂断言（注明依据，未删）。
8. **文档**：任务目录 RESULT/SD-01-findings/SD-06-report/evidence/*；四类实施文档
   各前置 DTR-SCOPE-DEV-20261027 节；STATE/HANDOFF/BACKLOG 更新。

## 真实命令/结果（关键）

- 基线：`python -m pytest tests -q -ra --junitxml=var/sd00-baseline.xml`
  → 595 passed / 7 skipped / 0 failed，exit 0；`npm run build` exit 0。
- HIS 探查：sd01_probe_his.py 12.15s、r2 4.76s，run_completed=true，零 ORA 错误；
  连接器拒注释/别名规则遵守（SQL 无注释、GROUP BY 用表达式）。
- 新测试：`python -m pytest tests/integration/test_drug_trace_scope_dev.py -q`
  → 首轮 3 失败（边界日期算错/job 执行键/字典版本冲突+`_submissions_for_bill`
  传 id bug——后者为真实产品 bug 已修），终 13 passed。
- 相关局部：drug_trace 13 文件 250 passed→修 a05 重挂+payload kind_key 残留后全绿。
- 全量回归：var/sd-full-regression.xml（数字见 RESULT，≥基线/0 failed）。
- 遇到的坑：VALIDATION_FAILED 在 API 层映射 HTTP 422（非 400）；enqueue_job
  把 dedup_key 变为执行键（测试须用返回 job_id）。

## 未完成 / 阻断（如实）

- 住院/退药**发送** BLOCKED（设计内）：bill_type 待用户/平台确认；字典 DRAFT
  载荷已写进 RESULT；双上报未核实前不放开。
- r11 视图未建（DBA 项）；真实 Oracle r11 采集未测；驱动 v3 未部署未跑首个生产日批。
- 205 处置仍待人工平台核查（清单在受限目录；SD-06 报告给了口径）。

## 下一棒建议

1. 用户/平台：确认住院与退货 bill_type（及退货接口语义）→ 建字典 approve；
   核实原厂家是否仍传数（3503 10-26/27 仍有成功上传）。
2. DBA：执行 r11 DDL+授权 → BLYB_DRUGTRACE 限量验证 → 关闭 r11 profile
   unresolved（新 revision）→ 新建 r11 连接器配置发布。
3. 部署窗口（避开 06:00–08:30、用户在场）：build_release → upgrade.sh →
   替换驱动 v3 → 观察 lookback 回执与 rescan 手动 API。
4. 药学部：住院不写码原因（PDA_STATE=0）、退药 IN 码写入计划、260 缺码
   「多行大处方+窗口号缺失」核查（050102 优先）。
