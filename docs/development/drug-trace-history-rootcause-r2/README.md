# 码上放心历史核查 rootcause-r2（2026-10-23）

任务 DTR-HISTORY-ROOTCAUSE-R2（ZCode/GLM-5.3）。承接 [r1 实测](../drug-trace-history-live-compare-20261023/README.md)
与[执行任务书](../drug-trace-history-live-compare-20261023/EXECUTION_TASK.md) Q00—Q06。
**当晚用户授权后追加了源表核查（r2b），原表根因已闭环**，见[RESULT.md 源表核查一节](RESULT.md#源表核查r2b2026-10-23-晚用户明确授权授权你执行后完成)。

**最终结论**：正常门诊 r10 保留可用；**5077 条未匹配全部 U02＝药房 050121 的拆零（SPLIT_FLAG=1）OUT 扫码流**，
其 20 位单号不在门诊处方主单编号空间（主单 PRESC_NO 仅 1—5 位），三键 JOIN 按设计不可命中——
非正常门诊漏报，是否覆盖待院方确认后另立来源 revision；**31 组重复＝1 小时内真实双扫**（SPLIT_FLAG=0），
11 张整单保持 INVALID/QD-DUP-CODE；1396 条 UNKNOWN 三态零迁移拦截保留；逐日覆盖 81 天与 r1 视图
交叉一致（484,567 vs 484,539 行）；T 侧 CREATE_TIME 反向仍未做（无索引，SQL06 实证）；自动推送不启用。

两轮在线查询：r2 37 条 SELECT 仅两视图（BLYB_DRUGTRACE）；r2b 约 181 条 SELECT 源表/主单/字典
（平台托管只读账号 READY_HIS，用户授权，走既有索引、限量分批）。源写入 0、上传 0、生产改动 0。
原始码/单号仅在受限目录；共享证据经泄漏扫描。

| 文件 | 内容 |
|---|---|
| [RESULT.md](RESULT.md) | 结论、五问答案、CHK 验收、**源表核查（r2b）根因闭环** |
| [CANDIDATE_CHANGES.md](CANDIDATE_CHANGES.md) | C01—C07 证据→修订→回退；A01—A05 当前代码状态表 |
| [EXTERNAL_INPUTS.md](EXTERNAL_INPUTS.md) | 剩余外部缺项（r2b 后已大幅收敛） |
| [evidence/classification-final.json](evidence/classification-final.json) | 最终分类：U02×5077、拆零流根因、双扫成因、逐日覆盖 |
| [evidence/](evidence/) | 两轮全部运行回执（含失败尝试与修正记录） |
| [validation.json](validation.json) | 离线校验（r2 轮） |

边界：5077 条已解释但不自动补报；既有 1 单 4 码成功不撤销不重发；平台历史终态未查（无授权即不查）。
