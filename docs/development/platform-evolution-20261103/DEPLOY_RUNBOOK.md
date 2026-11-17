# 平台演进 LT-03R1—LT-09 部署 Runbook（2026-11-11 编制；**未执行**）

> 本文档是部署准备，不是执行指令。执行须用户在场并按窗口步骤确认。

## 1. 变更概览

- **迁移**：015 schedule/schedule_run、016 告警三表+alert_event 扩列（可空）、
  017 外部台账三表、018 auth_subject.target_scopes（既有 user 兼容 ["*"]）、
  019 debug_exchange。014/020 不触碰（执行器按已执行集合逐项判断）。
- **新插件**：infectious（影子+外部台账）、medins_emr（联调台）、
  medins_imaging（脚手架骨架）——**均无生产采集/发送 job**。
- **新路由**：report-center（台账/导入）、schedules/alerts2/rules/channels、
  system/users、audit 扩展/详情/导出、medins-emr 联调 7 端点。
- **新前端**：Dashboard 等 9 页（/dashboard 为新首页）。
- **新系统调度**：system.alert_evaluate / alert_digest / run_guard——
  **种子默认 enabled=False；部署后保持停用**（它们写库写文件占 worker）。
  影像调度 drug_trace.daily_mirror / inf.poll_mirror 为 observe_only 仅观测。
- **生产链路零变化**：药品宿主 timer、传染病 crontab、inf_watchdog 未改。

## 2. BLYB_PLUGINS 结论（R10 必答；RT-02 修订）

compose 现状：docker-compose.yml 默认 `pathology,metrics`；
drug-trace.override.yml（RT-02 起）为 **core/worker/migrate/bootstrap 四个服务**
统一覆盖为 `${BLYB_PLUGINS_DRUGTRACE:-pathology,metrics,drug_trace,infectious,medins_emr,medins_imaging}`。

**RT-02 修复要点**：
- 旧 override 只覆盖 core/worker：`migrate` 会漏跑插件迁移（009—020）、
  `bootstrap` 会因不认识 job_type 跳过镜像调度种子——现已四服务一致，
  迁移/种子与运行时用同一插件集；
- 新默认集纳入 infectious/medins_emr/medins_imaging（均无自启生产任务、
  无真实外发权限）：升级后 medins 联调台与传染病外部台账面板直接可用；
- 运维可随时经 `BLYB_PLUGINS_DRUGTRACE` 收缩（如退回
  `pathology,metrics,drug_trace`）——**须在 upgrade 前设置**；四服务引用
  同一变量，不会再出现只改一半的断层；
- 应用装配已单轨化（RT-02）：未设 `BLYB_PLUGINS` 的进程也持有真实
  PluginRegistry（默认集 pathology+metrics），ops 调度校验不再丢失
  领域 job 类型。

## 3. 前置检查

1. `deploy/linux/backup.sh` 一致性备份（留存回滚点）。
2. 磁盘余量；当前发行版本号记录。
3. 窗口避让（**以部署日现场证据为准，不沿用旧固定窗口**）：药品 01:00 批次
   与白天补发/catchup timer、传染病 */15 班次与 23:58/00:05/00:27 迟班、
   DBA 维护窗。建议窗口：药品日批完成且当班传染病队列空档。

## 4. 步骤

1. 核实离线材料：发行包 SHA256SUMS、基础镜像 tar（构建器会 docker pull——
   **本机离线未证实则不得调用并声称离线构建**；缺材料登记阻断，见 RESULT）。
2. `build_release.py --out <dir>`（有 Docker 且可离线时）→ 校验 SHA。
3. 拷贝到宿主 staging；**先设置 BLYB_PLUGINS 与新调度停用配置**（见 §2）。
4. `upgrade.sh`（含迁移 015—019 + bootstrap 种子）。
5. 健康检查：六容器 healthy；`/api/v1/report-targets` 返回 5 条；
   `GET /api/v1/schedules` 镜像与系统调度 `enabled=false`；/ui/ 新首页可达。
6. 前端验证：驾驶舱药品卡片有数；告警中心 file_default 通道测试发送成功；
   联调台/台账/用户页可打开。

## 5. 验证清单

- [ ] 六容器 healthy
- [ ] 迁移 015—019 全 APPLIED、幂等复跑 ALREADY_APPLIED
- [ ] /api/v1/report-targets 5 条；驾驶舱可见药品数据
- [ ] 种子后：3 系统调度+2 镜像 enabled=False（贴查询结果）
- [ ] 告警 file 通道有输出（var/alerts/*.jsonl）
- [ ] **药品与传染病生产链路零变化**：timer/crontab 未动、当日推送正常
      （日报行/inf receipts 对照）

## 6. 回退

1. **先停新调度、核实在途 Job**（schedule_run 非终态）再回旧发行。
2. **保留新产生的业务台账/回执**（debug_exchange/external_submission_record/
   notification_log）——不恢复旧库覆盖新账。
3. 验证扩列兼容：旧版本对 alert_event 新列、auth_subject.target_scopes 可读
   （018 列可空；旧代码 SELECT * 不受影响）。
4. 回退后新表残留无害（旧代码不引用）；如需清理按 runbook 附录逐表 DROP。

## 7. 部署后观察 24h

- worker 健康（无 scheduling 相关异常日志）；
- 药品日批/传染病班次正常（生产链路金标准）；
- 迁移 018 后登录正常（既有账号 ["*"] 范围）；
- 探针/日报输出正常。


## RT-03～06 追加（2026-11-16 编制；本地终验完成、**未执行**）

### 新迁移（按序追加；编号迁移执行器按已执行集合判断）
- **021** infectious_shadow_cursor / infectious_shadow_record（传染病影子状态；
  只新增表；回退按 runbook DROP 并清 schema_migrations）
- **022** external_submission_record 补 source_instance 列 + 唯一键
  uq_esr_key → uq_esr_key_src（跨实例不合并；PG 分支幂等已修；SQLite 分支
  表重建保留全部数据）

### 新 Profile revision（运行时已指向；部署无需动作，回退见下）
- medins_emr_sd：revisions/index.json active=r2-23txn（23 交易树形 schema）；
  回退=index 改回 r1（root 平面契约只读保留）
- medins_imaging_jn：revisions/r2-csv16（16 数据集/288 字段；
  EXTRACTED_NOT_APPROVED——**不构成真实传输授权**，仅本地校验/示范投影）

### 新路由/端点
- POST /config-releases/{id}/rollback（**reason 必填**，破坏性契约）
- POST /config-releases/{id}/restore（恢复到此版本；新发布承载历史闭包；
  幂等；CLOSURE_DRIFTED 409）
- GET /api/v1/plugins、GET /api/v1/schedules/meta、
  GET /api/v1/report-targets/{code}/classification、
  GET+POST /api/v1/medins-imaging/datasets[/validate|project]
- PUT /report-targets/{code}：lifecycle 升级 PILOT/PRODUCTION 必填 reason

### 备份保账
- 备份须含 schema_migrations（新表由 021/022 建；恢复旧代码时新账保留——
  旧代码忽略未知表，无破坏）；外部台账/影子状态表均为可再生数据但随备份走。

### 只读验证（部署后）
1. `GET /api/v1/plugins` 含 pathology/metrics/drug_trace/infectious/
   medins_emr/medins_imaging；
2. `GET /api/v1/schedules/meta` job_types 含域任务类型；
3. `GET /api/v1/medins-imaging/datasets` 返回 16；
4. `GET /api/v1/report-targets` 五业务在册。

### 镜像/系统调度
- 系统三任务与两镜像**保持种子默认停用**；启用走 enable 专用端点+理由审计；
  镜像 observe_only 五路拒绝不变。

### 通知
- 通道 file-only（本批 smtp/webhook 服务端强制停用 NOT_ALLOWED_THIS_BATCH）；
  启用须单独授权。

### 回退步骤
- 代码回退到 RT-02 版本：新账保留（021/022 表未知即忽略）；profile 回退=
  revisions/index.json active 改 r1；restore 端点可把发布闭包回置历史版本
  （创建新发布，不改历史）。

### 验证材料
- 终验报告：docs/development/platform-rectification-20261111/
  FINAL_ACCEPTANCE_REPORT.md（含 RT-03～06 证据矩阵、外部阻断清单）；
- 路由核对：implementation/var/rt06_route_audit.json（missing 0/unresolved 0）；
- 最终回归 JUnit：implementation/var/RT-06-final-regression.xml。
