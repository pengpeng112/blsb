# INF-V15-SQL-FIX-REVIEW-20261115

- 用户目标：定位V_INF_EX_LAB INVALID，交付由用户执行的SQL，同时核查程序剩余问题。
- 工具/模型：Codex / unknown；session `20261115T131435Z-87d930f0`；2026-11-15。
- 认领范围：`docs/development/inf-lab-linkage-review-20261115/revision-3-v15-fix/`与本交接。业务目录及共享状态有其他活跃持有人，本轮未覆盖。
- 可恢复基线：交付目录baseline/保存7个源文件和在线三视图DDL文本；baseline-manifest.json记录哈希。

交付：[V15主视图修复SQL和程序复核](../../development/inf-lab-linkage-review-20261115/revision-3-v15-fix/README.md)。

## 查明原因及交付

ODS ALL_ERRORS实查ORA-00904 ACTV.CLINIC_NO无效。V15仅serial/type/patient/time四列，主视图错引TEST_NO/CLINIC_NO并混用act/actv别名。交付完整替换SQL，使用现有V15真实列，在主视图CTE按patient_id+test_no形成唯一候选，不依赖不存在的列。无FORCE/无DROP。

01号SQL由用户/DBA执行；02号校验期望三对象VALID、错误0行。本轮未执行DDL、未改源码/调度/业务数据、未真实发送。

## 实测

- 根目录运行build_repair.py退出0，仅生成SQL。
- Oracle诊断：受控sjzc ods_internal，ALL_ERRORS一行ORA-00904；对象状态与用户提供一致；V15四列已核实。
- verify_repair.py：首次受控通道返回非JSON失败（无ORA错误），保留失败证据；机械移除行注释后退出0，零行解析完整SELECT成功；三单限定预览各1行/type2/活动键各命中1；当前明细38条标准码均空（H/L当前误码已撤，但不证明历史平台字段）。仅SELECT，无DDL。
- SQLite内存合成候选测试：0/1/N分支、重复锚点、同次3报告不放大均符合；不将SQLite测试冒充Oracle对象创建。
- check_runtime.py：只读远端三代码摘要，退出0、远端0、0.76秒；与上轮生产SHA全部相同。程序R2/R3/R4缺陷仍适用，没有重复跑旧12场景或宣称新版修复通过。
- 收尾静态检查退出0：主视图列数37、最终交付SELECT机械去注释后的哈希与Oracle已测文本一致、9个本地链接存在、7个源文件无漂移。verification-summary.json记录UTC13:24:39，ddl_executed=false。

## 未完成与下一步

用户执行01→02；如报错保留错误原文，不继续真实发送。执行后核对本例主3/子38及关联键，程序继续按第二版R2—R6修父版本证据、持久等待、严格manifest并专项验收。已补齐38条不盲目整批重发。

当前明细仍有MAX流水归属，后续需与新父候选集合一致；六张无真实操作人的单据应保持质量等待。平台历史错误字段和待确诊触发原因未在本轮验证。

共享STATE/HANDOFF/BACKLOG和四类业务档案由持有人/接力实施会话更新，本轮独立交接记录交付事实，不另建平行业务状态。

结果：SQL交付与程序复核完成；DDL部署、程序修复、平台更正均未执行。
