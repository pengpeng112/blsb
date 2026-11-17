# DTR-SCOPE-PLAN-20261027 交接（口径扩展开发执行书编制）

- 执行：ZCode / GLM-5.3，2026-10-27 深夜，session `20261027T124505Z-97c297be`。
- 触发：用户要求写详细计划交其他 AI 一次性完成剩余开发。
- 交付：[EXECUTION_TASK.md](../../development/drug-trace-scope-dev-20261027/EXECUTION_TASK.md)
  （SD-00—08、验收 A01—08、§五硬边界、§六风险回滚、入口提示词含 HIS 探查授权句）。
- 本轮纯计划，零代码、零查询、零生产访问。

## 执行书要点（下一棒先读执行书本身）

- 任务号 DTR-SCOPE-DEV-20261027；范围=drug_trace 域+db_view 源+UI+tests+docs。
- 四条已批口径 K1—K4 写死在 §一（无码不报/退药冲正/迟到码7天回看/住院要报）。
- 关键设计：r11 视图列契约与 r10 逐列一致（reader/ingest/质量零改动复用）；
  住院 kind=INPATIENT_DISPENSE、退药复用 PATIENT_RETURN；bill_type 一律 DRAFT
  **fail-closed**（平台确认前不真实发送）；rescan 定向复核 job+驱动v3 回看阶段
  （已 CONFIRMED 不重报）；NOT_REPORTABLE 终态（7 天回看窗）。
- SD-01 只读探查四问：住院码真实键、退药 IN 码、BILL_TYPE 证据、260 共同特征
  （通道模式与坑：拒注释/GROUP BY 别名/无索引不扫——均写明）。
- 205 只做宏观对照（日志无单号明细，如实声明），零状态改动。
- 部署不在本任务：DBA 建 r11 视图先行→build→upgrade（避 06:00–08:30）→驱动v3。

## 编制依据（全部本会话已实测）

- 口径确认三轮对话（K1/K2/K3+K4、方案A批准）。
- HIS 元数据四轮探查（原厂家视图 DDL/申请表样本实测/上传日志活着）。
- sjzc-verify 任务全部证据（缺码 260 归因、关联无误证明、050102/050121 覆盖对照）。
