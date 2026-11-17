# V15主视图INVALID修复SQL与程序复核

任务INF-V15-SQL-FIX-REVIEW-20261115；2026-11-15；Codex / unknown。

## 执行文件

由用户/DBA在ODS库按顺序执行，遇错误停止：

1. [01-修复检验主视图-v1.5-r1.sql](01-修复检验主视图-v1.5-r1.sql)：完整替换`ODS.V_INF_EX_LAB`，不修改已经VALID的V15锚点与明细视图，不执行上传。
2. [02-执行后校验.sql](02-执行后校验.sql)：三对象均应VALID，ALL_ERRORS应返回0行。

本轮**没有执行CREATE/ALTER/GRANT、没有部署或发送**；原在线三视图定义文本已备份至baseline/online-views.json，原v1.5及程序文件也已冻结。修复使用CREATE OR REPLACE VIEW，不DROP、不使用FORCE，语法错误时直接报错。

## 原因已实查

Oracle当前编译错误：`ORA-00904: "ACTV"."CLINIC_NO": 标识符无效`。

V15实际只有SERIAL_NUMBER、ACTIVITY_TYPE_CODE、PATIENT_ID、OPERATION_TIME四列；原02号SQL仍按报告绑定视图使用ACTV.TEST_NO、ACTV.CLINIC_NO，还把SELECT中的act别名与FROM里的actv混用。单纯ALTER VIEW COMPILE无法补出缺失列，必须修正定义。[原始诊断](oracle-diagnostics.json)来自本轮ODS只读查询。

新SQL保留当前四列V15，在主视图内部按报告形成候选集合：

- 患者+检验单+就诊日匹配活动候选，活动字段只引用真实存在的列。
- DISTINCT去除重复锚点；按patient_id+test_no计算候选数，唯一时才绑定。候选冲突/姓名冲突等待，不用MIN/MAX任取。
- 急诊类型复用锚点2，门诊起点保持11-14；住院分支保持冻结v1.5原文。
- 保留真实操作人映射，缺人员保持质量等待，不把科室码填入operatorId。

## 已验证及限制

执行`python .../revision-3-v15-fix/build_repair.py`生成本包，退出0，无数据库访问。

执行`python .../revision-3-v15-fix/verify_repair.py`，通过sjzc → <relay-jump>受控连接器 → 已核enabled/readonly的ods_internal，仅SELECT，单次上限10行/120秒：

| 实测 | 结果 |
|---|---|
| Oracle零行解析完整SELECT | 成功，PARSE_ROWS=0，包含完整住院/门诊UNION及37列 |
| 受限三单预览（3处LAB_TEST_MASTER均注入三单限制） | 三张各1行，type均2，完整活动键命中各1，操作人不空 |
| 当前已VALID明细的本例结果码 | 38条标准码均空，原H/L误转耐药的当前视图映射已撤除；不据此断言历史平台数据已更正 |
| 本地合成候选粒度 | 0候选排除、N候选等待、重复锚点去重、同次就诊3报告仍只出3父 |

证据：[select-verification.json](select-verification.json)、[candidate-grain-check.json](candidate-grain-check.json)。SELECT实测通过不等于DDL已经执行；DDL权限及执行后三对象状态由02号SQL确认。

首次SELECT核验未取得有效JSON结果，未返回ORA错误；移除生成SQL的行注释后，最终三条SELECT均ok=true，命令退出0。失败记录保留在select-verification-first-attempt.json，不冒充通过。修复SQL本身可保留注释，预览的执行文本仅机械去注释并经过同一连接器只读门禁。

当前03号明细虽然VALID，仍有独立MAX(CLINIC_NO)归属；本包优先恢复主视图有效及正确的报告级绑定，不宣称已完成全部主子业务验收。后续仍需按第二版计划使子表候选与父表同源，并处理平台实际错误字段。

## 程序仍需修正

本轮执行`python .../revision-3-v15-fix/check_runtime.py`，只读容器代码摘要，退出0、远端0，约0.76秒；未扫描台账或执行pusher。pusher.py、tables.py、run.sh与上轮已复现缺陷的生产版本SHA256全部相同。

[程序比对](program-comparison.json)确认没有收到程序修复版本，因此上轮12个隔离观察仍适用于这一版本；本轮没有重复跑同一套测试或假称新版验收通过。主要未完成项：

1. 父证据未绑定父表/父版本，旧成功或跨表同ID仍可误放行。
2. 跨日父复用、持久等待及超overlap恢复不完整，医嘱新增门禁也有兼容问题。
3. manifest目标/摘要/失效/数量等校验不足，reconcile分支能绕过apply控制，重放缺少持久完成状态。

继续按[第二版计划R2—R6](../revision-2/README.md)修改、专项验收再上线。修复视图不能替代程序修复；当前不执行自动补传或全量real。38条已补齐，不当漏项再次整批重放。

## 交接与范围

业务目录仍由INF-V14-LIVE认领，共享STATE/HANDOFF/BACKLOG由RT-05认领；本轮只写独立SQL交付及[本轮交接](../../../ai/sessions/INF-V15-SQL-FIX-REVIEW-20261115.md)，不覆盖其文件。用户/DBA执行后，实施会话须将实际执行结果及版本同步业务README、01/02/03、接口13/14和共享状态。
