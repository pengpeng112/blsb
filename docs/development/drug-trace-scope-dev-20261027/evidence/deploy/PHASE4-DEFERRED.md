# Phase 4 记录（住院冒烟）——按手册降级中止（2026-10-29 02:00–03:10 CST）

## 结论

住院采集**性能不达标**，按手册硬边界（03:30 前完成、不撞 06:10 日批）中止：
run `a33cca7d-562b-4422-999c-2539344ac343` 已 CANCEL（200；12 张已入库住院单为无害新增，
75 seen/12 revisions）。**系统终态安全**：升级+驱动v3+配置+字典全部生效，06:10 日批将正常
以 v3 首跑（门诊主流程+回看），住院真实冒烟改白天优化视图后重试。

## 过程与两次修正（如实）

1. v1/v2 的"按窗口复用采集"误匹配了今晨 06:10 r10 门诊日批的运行（窗口同为 10-27，
   列表接口无连接器字段）——v1/v2 输出 json 里的 summary（3205/1792 valid）是**门诊数据**，
   非住院采集结果，以本文件为准。修正后 v3 总是新建采集。
2. v3（真住院采集）实测：3 slices/259 查询/1003 查询秒 → **平均 3.9 秒/查询**，全程外推
   ~5 小时。根因诊断：`V_DRUG_TRACE_CODE_INP` 第二 UNION 分支
   `T.TRACE_BILLNO = TO_CHAR(d2.DIS_RECORDNO)` 的函数侧连接使 MED_TRACE_RECORD 的
   TRACE_BILLNO 索引失效（每单据码查询退化为全表扫）。首 75 张（00:00–06:00 发药）全部
   WAITING（0 码）符合预期（住院码仅 050121 稀疏覆盖）。

## 白天待办（住院冒烟重试的前置）

1. 视图调优（DBA，建议三选一）：
   a. 分支二改为 `T.TRACE_BILLNO IN (SELECT ... FROM PHA_INP_DISPMASTER ...)` 半连接；
   b. 建函数索引 `CREATE INDEX ON PHA_INP_DISPMASTER (TO_CHAR(DIS_RECORDNO))`？——不，
      需要的是 MED_TRACE_RECORD 侧可走索引：把分支二改写为先按 d2 主键取 DIS_RECORDNO
      列表再等值 IN；
   c. 简化：分支二暂删（当前两键全零命中的是 REQUEST_NO 与 DIS_RECORDNO 均零——但 47k
      行码是经视图 JOIN 出的，说明分支一/二有命中；调优时先 EXPLAIN 确认哪分支慢）。
   **先 EXPLAIN 再改**（DBA 窗口执行；本地不再盲试）。
2. 调优后重试小窗采集（建议 10-27 08:00–16:00 含 050121 码密集时段）→ DRY_RUN → 单张真实发送。
3. `bills` 列表接口建议后续补 connector 维度返回（本轮踩坑点，纯产品改进）。

## 证据文件

- phase1-transfer.log / phase2-upgrade-*.json / phase3-config-*.json / phase4-smoke-*.json（v3 版含真实住院采集+取消）
- 终态快照（03:0x）：release=scope-20261028、driver SHA=2f33c026…、timer active、6 容器 healthy
