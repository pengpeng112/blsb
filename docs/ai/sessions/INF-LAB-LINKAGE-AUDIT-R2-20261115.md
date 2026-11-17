# INF-LAB-LINKAGE-AUDIT-R2-20261115 · 实施后独立复核

- 用户目标：核查其他会话按原计划完成的结果，有问题则交付下一版修改计划。
- 工具/模型：Codex / unknown；session `20261115T122559Z-00083db2`；2026-11-15。
- 修改范围：`docs/development/inf-lab-linkage-review-20261115/revision-2/`及本文件。未修改业务代码/SQL/生产。
- 接手冲突：业务目录INF-V14-LIVE仍running；STATE/BACKLOG/HANDOFF由RT-05认领。本轮读取其记录，选择独立文档范围，未抢占或覆盖；共享入口追加待相应持有人接力。
- 可恢复基线：revision-2/baseline-manifest.json及9份冻结文件，测试使用冻结代码；部署三文件与本地摘要一致。

交付：[实施复核与第二版修订计划](../../development/inf-lab-linkage-review-20261115/revision-2/README.md)。内含F01—F09、R0—R6及R2-A01—A12；保留原v1计划和全部旧证据。

## 已确认

1. 日期起点已对齐11-14，本例视图明细恢复38；真实回执与sent有38条，源与受理ID集合4/7/27三组摘要完全一致。北京时间19:34:17受理，不再作为漏项全量重放。
2. 当前线上主视图再次固定type1、未用V13，三张急诊完整活动键命中仍为0。当前13号v1.4沿用旧门诊分支，覆盖了此前type2修复。
3. 6单operatorId兜底用的PERFORMED_BY在ODS是科室：6单部门字典命中6、员工号命中0，不能当检验师工号。
4. 当前14号H/L→12/10仍在；本例当前标准码3条12/2条10。38条已受理，需读平台真实字段判断哪些要更正，不把当前视图内容冒充历史payload。
5. 父门禁有旧当日成功/跨表裸ID/reconcile历史账绕过；又存在跨日有效父被误拦、等待项落出overlap丢失。order_item也被加门禁，跨日兼容缺口已复现。
6. manifest错目标/过期/摘要错仍发送，同force清单二跑仍重复发，目标缺失静默0；reconcile+manifest无apply仍进入真实分支（本轮HTTP全部拦截）。
7. V13输出丢报告键、分组内候选数恒1，本例回连每张3行，直接恢复v1.3可能3父放大到9；当前线上未用该视图，不混同为现状9行。

## 真实验证

- sjzc受控源ods_internal已核enabled/readonly，SELECT均有三单或六单业务键/视图对象限定；返回聚合、结构标志或服务器内集合摘要，未输出患者原始结果/身份字段。
- ODS病例脚本重定向独立输出：退出0，ok=true，3行；线上DDL四对象结构标志ok=true。
- 回执完整只读扫描：退出0，34.88秒；本轮只读受限代码及三台账文件，未执行pusher real。第二次为核对准确ID集合只读11-15文件约1秒，没有重复扫描大历史文件。
- `python docs/development/inf-lab-linkage-review-20261115/revision-2/check_view_regressions.py`：初次人员查询列别名超过Oracle11g限制，ORA-00972；保留失败证据，缩短别名后仅定向复跑人员SQL，最终三条均ok=true。
- `python docs/development/inf-lab-linkage-review-20261115/revision-2/check_implementation.py`：退出0；12个当前行为观察，HTTP/fetch全部合成替换、urlopen拒绝、临时目录自动回收；发现缺陷不算修复验收。
- 冻结selftest日志68 checks/0 fail；收尾为明确保留退出码，用subprocess捕获同一冻结自测的返回0，证据selftest-result.json。不足以覆盖上述缺陷。未扩大无关全量回归、未安装依赖。
- 精确源/受理ID集合：38候选，仅服务端计算分组摘要，三组一致。
- 收尾here-string → python，退出0：审计脚本语法通过，13个本地链接有效，证据断言通过；冻结代码与生产摘要一致，9个原文件收尾无漂移。结果保存在revision-2/verification-summary.json（UTC12:40:19）。

## 剩余与交接

下一实施按新计划R0—R6：先协调活跃范围→组合修视图type/人员/结果语义/候选粒度→统一父版本证据→持久等待→严格manifest→新增失败路径验收→有界更正及平台核验。原38已补齐，不盲目整批重放。实际部署/发送/状态处理本轮均未执行。

共享STATE/BACKLOG/HANDOFF及业务README/01/02/03/13/14因活跃范围不覆盖，本次发现需由持有人或接力会话同步。当前独立交接即本轮维护记录，不另起平行业务状态。

结果：实施后复核与第二版计划完成；整改未实施、平台业务确认未完成。
