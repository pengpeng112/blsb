# HIS 追溯视图调优分析交接

任务 DTR-VIEW-TUNING-20261029；session `20261029T152157Z-f2b7db8c`；ZCode/GLM-5.3；2026-10-29。
用户授权："连接数据库看看给出优化视图，但是不要影响结构"。全程经 <relay-jump> 受控连接器
（his_source_<his_host_id>，平台只读账号）单条 SELECT——**零 DDL/DML，未改任何对象**。

## 交付

[分析报告](../../development/drug-trace-scope-dev-20261027/view-tuning-20261029/README.md) +
两个 SQL 文件（fbi_dba_option.sql 方案1 / r10_views_v2_hinted.sql 方案2 含原版回滚文本）。

## 结论摘要

- 根因（实测）：应用按拼接串 source_bill_id 过滤（任何普通索引无法服务）→ 每单全表扫
  DRUG_PRESC_MASTER（702万行）；`T.TRACE_BILLNO=TO_CHAR(M.PRESC_NO)` 函数在索引列侧加剧。
- 计时矩阵：poll≈0s（走 IDX_DISPENSING_DATETIME）；单头 ~1.1s / 码明细 ~1.0s（全扫）；
  分解谓词毫秒级（T 侧 med_trace_record_trace_billno / M 侧 PK 均已存在）。
- 方案1（推荐，DBA）：FBI on source_bill_id 表达式（ONLINE，索引级非表结构）→ 日批采集
  ~6h→分钟级；方案2（零 schema）：视图 v2+hint 只救码明细且未实测（连接器拦注释，须 DBA
  EXPLAIN）；方案3：应用模板分解列（长期）。
- r11 住院草案 v3 同病两处（TO_CHAR(DIS_RECORDNO)、TO_CHAR(DATE)=VARCHAR）——创建前修，归 scope 会话。

## 下一步

DBA 低峰执行 fbi_dba_option.sql 并跑验证 → 观察次日凌晨 01:00 日批（应 DONE）；
FBI 不批则走方案2。下一棒从 BACKLOG 顶部条目进入。
