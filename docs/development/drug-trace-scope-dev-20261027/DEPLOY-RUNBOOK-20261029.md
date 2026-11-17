# 部署手册：scope-20261028 凌晨升级（DTR-SCOPE-DEPLOY-20261028，定时 2026-10-29 02:00）

用户授权（2026-10-28 会话原话）：「请你都记录好，并开始升级，你自行选择个时间，可以凌晨2点左右」。
执行窗口 **2026-10-29 02:00–03:30（本地）**，06:10 日批前完成。全程会话=本对话（完整历史可用）。

## 已完成的准备（2026-10-28 白天）

- r11a profile 关闭版入库（source_profiles.py：住院 `blyb_drugtrace_inp_views@20261028-r11a`
  unresolved=()；退药 `blyb_drugtrace_return_views@20261028-r11a` 留
  `return_first_bill_smoke_pending` 闸）；闸门测试 test_r11a_profiles_gate；
  局部 48 passed（scope-dev 14 + domain 34）。
- 发行包预构建：`implementation/var/rel-scope/scope-20261028`（构建完成后核对 SHA256SUMS
  与文件数；构建若失败→修复后重建，绝不带病部署）。
- bill_type=321 字典载荷（RESULT「bill_type 已确认」节）。

## 执行步骤（每步留证到本目录 evidence/deploy/）

### Phase 1 传输与staging（~02:00）

1. 本地校验发行包：SHA256SUMS 逐文件、含前端 dist。
2. 打包 tar.gz → 经 deploy_fb05 jump+nested 通道传 <prod-host>:<prod-path>-staging/scope-20261028
   （分块 base64；参考 recovery-stability prepare_phase_deployment.py 的做法）→ 服务器端
   解包+SHA 复验（不符即中止）。

### Phase 2 升级（~02:10）

3. 维护文件夹 /root/blyb-maintenance-backups/upgrade-scope-20261028/：记 baseline.json
   （当前 release 名=stability-20261027T064000Z-r3、driver SHA、提交状态摘要、quota、
   checkpoint、timer 状态）。
4. `bash <prod-path>-staging/scope-20261028/implementation/deploy/linux/upgrade.sh
   <prod-path>-staging/scope-20261028` → exit 0；失败走内置回退并停止整晚流程。
5. 健康验证：六容器 healthy、API /ui 200、readiness 200。
6. 驱动 v3 替换：仓库 daily_upload_driver.py（含 v3 回看）→ /opt/blyb/tools/
   blyb-daily-upload.py（先备份旧件、写后 SHA 复验、0755）。
7. 确认 blyb-daily-upload.timer active（下次 06:10）。

### Phase 3 配置（API，platform_admin；~02:30）

8. 读 /etc/blyb/runtime.env 取 bootstrap token（容器内 coreGet，模式同 inspect_remote.py）。
9. r11 连接器×2：ConnectorInstance（direction=source，plugin=sources.db_view.drug_trace）
   + source config revision（content 按 source_config.validate 实际要求填：profile=r11a key、
   objects、dsn_env=BLYB_DRUGTRACE_ORACLE_DSN、collection_mode=batch）。
   **住院：publish+enable；退药：publish 但不 enable（profile 闸未开，双重保险）。**
10. 字典：`drugtrace.bill_type` r11-20261028（INPATIENT_DISPENSE→321，approve=true）；
    `drugtrace.query_bill_type`（→A，approve=true）；PATIENT_RETURN 两项建 DRAFT 不 approve。
11. 验证：readiness 200；新连接器出现在 /connectors。

### Phase 4 住院冒烟（~02:45）

12. 小窗采集：POST collection-runs（住院连接器，window=10-27 全天，mode=oracle，
    profile=alihealth_yljg_retail_v1，checkpoint_partition=r11-inp-smoke）→ 等 DONE；
    记 summary（单据数、VALID/WAITING 分布；预期 050121 部分单有码 VALID，其余 WAITING）。
13. DRY_RUN 预览一张 VALID 住院单（POST previews）→ 确认 bill_type=321、ref_ent_id、码数。
14. **单张真实发送**（住院 321 语义成立）：prepare + send(confirm) 一张 050121 VALID 单
    → 观察平台回执终态（standing scope 覆盖 050121）。任何一步异常 → 停止、保留状态、报告。
15. 退药今晚零操作（profile 闸；明晚单张试传后再开）。

### Phase 5 记录（~03:00）

16. result.json + 本目录 evidence/deploy/*；STATE/HANDOFF/BACKLOG 更新；session checkpoint。
17. 明晨 06:10 日批复核项：v3 首跑（回执应含 lookback 字段）、NOT_REPORTABLE 迁移计数、
    门诊行为与 r3 基线一致。

## 回滚（任一 Phase 失败）

- 代码：deploy/linux/rollback.sh（upgrade.sh 备份链）。
- 驱动：恢复 Phase 2 备份的旧驱动文件。
- 配置：r11 连接器 disable / 删除；字典 DRAFT 无需撤销（未 approve 不生效）。
- 数据：住院冒烟采集的单据为新增数据，回滚不删（无害）；真实发送若已发生保留回执如实记录。

## 硬边界

- 不动 r10 视图/字典/冻结 payload；已 CONFIRMED 不重发；退药今晚不发。
- 任何 Phase 失败即停（不硬闯 06:10）；全程只读 HIS（采集走既有 BLYB_DRUGTRACE 通道）。
- 证据落 docs/development/drug-trace-scope-dev-20261027/evidence/deploy/。
