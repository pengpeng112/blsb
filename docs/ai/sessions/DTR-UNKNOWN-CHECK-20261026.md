# DTR-UNKNOWN-CHECK-20261026 交接

Codex/model unknown，会话20261026T070804Z-b37f25ba。仅本次运维核验文档、工具与共享状态；未改应用源码/镜像/服务/配置/视图。

## 最新：平台人工查询完成，41受理已在平台成功（2026-10-26 18:18:41）

[实际核查与边界](../../operations/drug-trace-unknown-check-20261026/README.md)：用户要求看平台是否收到，按冻结身份/类型/业务和上传日期覆盖查询旧247笔。去重结果{'UNKNOWN->NOT_FOUND': 206, 'ACCEPTED->CONFIRMED': 41}，已知成功单作正对照正常；不是mock。
旧206 UNKNOWN原发送205次HTTP429、1次FileNotFoundError；历史查询126仍429/80未找到，41受理历史查询也429且均12轮耗尽。下午r1—r3遇TOP7/调用次数限制已停并保留143未找到/63未核实的部分报告；用户随后要求继续，18:12单笔实测额度恢复且NOT_FOUND，r4以4.5秒间隔完成剩余62笔，未重复核已查清记录。未猜平台精确额度或恢复周期。
本次只读独立证据，不改业务状态/轮询预算、不发新单、不读HIS。生产本地仍2576确认/41受理/206未知，额度2823/80000、断点46不变；**41笔平台确认不等于看板已经回写**。逐笔UUID及结果CSV在任务目录，无患者/原始码/凭据。
下一步：受控人工核查回写41笔并保留平台证据；206按NOT_FOUND名单比对历史人工上报及身份后制定恢复清单，禁止盲重发；平台TOP7退避/子错误保存、轮询耗尽人工入口、明细分页仍待完善。[交接](DTR-UNKNOWN-CHECK-20261026.md)。


实际命令、初始失败与恢复见[实录](../../operations/drug-trace-unknown-check-20261026/README.md)。r1—r3因平台TOP7退出1，15:42部分报告未核完；18:12用户继续后单笔查明已恢复，r4实际查询exit0且ledger_unchanged=true。inspect/sample/diagnose/recheck_limit/evening/progress4通道exit0。本轮无应用代码改动，未跑应用回归也不冒充通过。

复核工具all/resume/finalpass/evening均一次性，禁止重跑；progress4/inspect只读。原始平台与诊断证据私有服务器目录见实录。最终CSV247行：41已在平台成功、206平台未找到；全部仅单据级结果，不排除其他身份/单号人工上报。尚未实现状态回写与未找到后的补报流程，不直接UPDATE或重发UNKNOWN。原部分报告JSON/CSV保留，最终另存-final及最终版CSV。
