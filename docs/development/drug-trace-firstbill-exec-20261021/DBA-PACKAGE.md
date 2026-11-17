# FB-03 DBA 交付包：HIS r7 视图 + 生产受限只读（DTR-FIRSTBILL-EXEC-20261021）

**状态：待 DBA 审阅执行。本 AI 不在 HIS 执行任何 CREATE/GRANT/DDL（任务书 FB-03 §2）。**

## 交付物（既有 r7 原包，未改动）

| 文件 | 用途 | 执行者 |
|---|---|---|
| [10-create-views.sql](../../deployment/reporting-20261015/sql-r7/10-create-views.sql) | 两视图 DDL（V_DRUG_TRACE_BILL / V_DRUG_TRACE_CODE；未批准语义列 NULL，不猜业务） | DBA |
| [20-grants.sql](../../deployment/reporting-20261015/sql-r7/20-grants.sql) | 最小授权：角色仅 SELECT 两视图；生产账号 BLYB_DRUGTRACE（占位名，DBA 可调整）；口令受控交付 | DBA |
| [30-verify.sql](../../deployment/reporting-20261015/sql-r7/30-verify.sql) | 限量只读核验（列契约+整单一致性+脱敏形态；无患者/真实码输出） | DBA + 后续 AI（经 sjzc 限量执行） |
| [40-optional-index.sql](../../deployment/reporting-20261015/sql-r7/40-optional-index.sql) | 可选索引（变更轴优化；不作为首张前置） | DBA 自行评估 |
| [视图契约 r7](../../deployment/reporting-20261015/view-contract-r7.md) | 字段/键/不变量契约与设计依据 | 审阅参考 |

## 本轮核查事实（2026-10-21）

- 平台元数据快照中无 V_DRUG_TRACE_*（`sjzc tables HIS V_DRUG_TRACE` 0 行）。
  快照无行≠活库不存在，但**无任何证据表明 DBA 已建视图或已批只读账号**。
- 源登记 `his_source_<his_host_id>` write_policy 仍为 medical_dict_push
  （10-19 核查值）；live 通道按规则阻断，本轮 HIS 活库查询 0 次。
- <prod-host>→HIS 1521 TCP 通、Oracle Thick 19.32 就绪、`BLYB_DRUGTRACE_ORACLE_DSN`
  为空（采集按设计阻断）——链路只差视图+账号+DSN 注入。

## 请 DBA 确认/执行的清单

1. 审阅并按需调整 10-create-views.sql（BLYB_RO schema 名、表空间等）后执行；
   记录执行人/时间/对象版本。
2. 审阅 20-grants.sql：确认生产采集账号名、口令受控交付方式（不进共享文档）；
   会话只读加固方案（A/B）二选一。
3. 执行 30-verify.sql SQL-1（列契约）并回传结果；SQL-2/3 由后续 AI 经 sjzc
   限量执行（届时需当次授权）。
4. 回答：MED_TRACE_RECORD 是否存在可靠变更轴（晚到补码/删码/退药）；
   40-optional-index 是否采纳。
5. 回退路径（DBA 落档）：DROP VIEW BLYB_RO.V_DRUG_TRACE_BILL /
   V_DRUG_TRACE_CODE；REVOKE 角色/回收账号。基表无任何变更，回退无损。

## 解锁后本侧动作（不在本轮执行）

- 经 sjzc 当次授权限量核验（30-verify SQL-2/3）→ 新建 SourceProfile r3
  （LIVE_VERIFIED）→ 连接器 active config + worker DSN 注入（维护窗口）→
  EXT-01/02 证据登记 → 小窗口采集 1 张整单（零发送）。

## 阻断结论

FB-03 生产取数 **BLOCKED**（缺：视图 DDL 执行、只读账号批准、凭据受控交付、
业务规则批准）。所有不依赖 HIS 的开发/联调工作继续。
