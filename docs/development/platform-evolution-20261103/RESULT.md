# 平台演进 LT-01—LT-09 总结果（2026-11-11 收官，ZCode/GLM-5.3 执行 LT-03R1—09）

图例：✅实测通过（附命令/数）｜🔶实现未测（原因）｜⛔外部阻断（等谁）｜🎯待用户决策

## 各任务交付 × 验证状态

| 任务 | 交付 | 验证 | 后端全量（当日终跑） |
|---|---|---|---|
| LT-01 上报业务目录 | ReportTarget/014/6API/种子5条/闸门 | ✅ 交接 2026-11-08（56 新测试） | 673/0/1env |
| LT-02 前端外壳 | 组件库21/类型化API/8页改造 | ✅ 28/28+build；🔶未目视（无浏览器） | — |
| LT-03 统一推送台账 | generic适配器/台账/3API | ✅ 交接 11-08（61 新测试） | 734/0/1env |
| LT-03-R1 复用补强 | Mock→generic装配/不盲重发/重定向禁/闸门CONFIRMED/summary口径/游标绑定 | ✅ 11-10（34 新测试） | **768/0/1env** |
| LT-04 调度治理告警 | 015/016/cron/scheduler/run_guard/告警中心/晨报/种子 | ✅ 11-10（62 新测试；双worker SQLite 并发单触发；PG🔶未测） | **830/0/1env** |
| LT-05 外部台账+影子 | 017/导入器/CLI/API/infectious影子/payload真实分支等价 | ✅ 11-11（31 新测试；泄漏夹具全合成） | **861/0/1env** |
| LT-06A 安全后端 | 018/范围鉴权21处/system 5端点/审计扩展+CSV导出 | ✅ 11-11（20 新测试） | **881/0/1env** |
| LT-06B 运营页面 | 9页+ops API层+utils(mjs事实源) | ✅ 前端51/51+build exit0；🔶逐页目视未做 | 881 复核 |
| LT-07 医保联调台 | Profile 23交易/校验器/msgid/签名UNCONFIRMED/019/7API/前端tab | ✅ 11-11（27 新测试；泄漏检查命中0） | **905/0/1env** |
| LT-09 脚手架收尾 | new_report_target.py+模板/影像骨架+CSV Profile 16数据集288字段/接入指南/清理/runbook/本文件 | ✅ 脚手架3测试+骨架3测试；dry-run零写+实跑幂等 | 见下（终跑） |
| LT-08 采集性能（独立） | 4项等价优化 | ✅ 交接 11-04；部署侧动作待用户 | — |

## 环境类未测（如实）

- 隔离 PostgreSQL 并发/崩溃回滚（LT-04/05 调度并发）：BLYB_PG_URL 未配置，
  显式 skip；SQLite WAL 双线程已证单触发。**未宣称 PG 并发已验证**。
- 浏览器目视（LT-02/06B 九页）：本机无 chromium（离线禁下载）；组件与逻辑层
  全测试覆盖，部署前建议 `npm run dev` 逐页过一遍。
- 探针 42/43：R22（Oracle stub vs Thick 初始化）既有差异，三轮登记一致，
  与本系列无关。
- PG 环境错误 1（test_drug_trace_pg，端口被占）：六轮全量一致，同基线。

## 外部阻断（等谁）

| 项 | 等待方 |
|---|---|
| 医保真实联调（网关地址/签名算法与向量/证书/授权） | 院方/医保 |
| 传染病真实 state 副本导入（正式执行须用户确认） | 用户 |
| 传染病影子七天对比（迁移前提） | 时间+用户确认 |
| 真实外发通知通道（smtp/webhook 参数；本批服务端强制停用） | 用户 |
| 发行构建的离线 Docker 材料与镜像（build_release 含 docker pull） | 运维环境 |
| 生产部署窗口与执行 | 用户在场 |

## 仍待用户决策

1. 传染病生产从 pusher 切到平台的时机（前提清单见 infectious README）。
2. 药品调度是否迁入平台调度（镜像已就位，切换须用户在场）。
3. 唯一推送方确认（码上放心 CONFLICT / 传染病 UNCONFIRMED → CONFIRMED）。
4. 告警 smtp/webhook 通道参数（启用+测试外发才放行）。
5. 系统调度（告警评估/晨报/run_guard）生产启用时机与资源预算。
6. 影像云需求排期（骨架/契约就绪，传输鉴权待规范）。
7. medins_emr_sd 存量库 lifecycle 经 API 更新为 DEVELOPING（种子只对新库）。

## BLYB_PLUGINS 与新插件（R10 结论）

生产 compose override 生效值 `pathology,metrics,drug_trace`——**新插件默认
不加载**（路由不注册；core 级迁移 015—019 仍执行）。启用须在 upgrade **前**
设置 override（runbook §2）。部署 runbook：[DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md)。

## 全量验证汇总（LT-09 终验已跑）

- 后端：`python -m pytest tests -q -ra --junitxml=var/LT-09-regression2.xml`
  → **911 passed, 8 skipped, 0 failed, 1 error**（PG 环境错误同基线）1018s
  （=LT-07 后 905 + LT-09 净新增 6：脚手架 3+影像骨架 3；3 处期望更新断言
  未削弱——manifest 集合+medins_imaging、种子 plugin_key 补填、fixture 租户
  code 撞名改 uuid）。
- 探针：42/43（R22 既有差异）。
- 前端：`npm run build` exit 0（3.2s）；node --test **51/51**。
- 文档：project_docs inventory（497 篇）+check 新增断链 0。
