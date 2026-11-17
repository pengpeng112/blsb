# 历史追溯单据重新发送测试

任务DTR-HISTORY-RETRY-20261026，Codex/model unknown。用户明确要求历史重发测试；本轮范围是一张原HTTP429、平台完整查询未找到的历史单，最多一次新的真实POST。不把已成功的41张历史单重新发送，不启动206张批量补报。

## 选择与真实发送

只读核查当前206 UNKNOWN，205张原HTTP429且冻结配置/源版本/授权仍匹配；另1张原运行异常未选。选择仅1条追溯码的submission `9b19170e-5ceb-4da4-99bb-7478c77d9695`，原版本3、原尝试1；单据号仅保留SHA256，未在报告保存追溯码/患者/Secret。

2026-10-26 22:05:49 GMT+8再次按原ref_ent_id、零售查询A、原单号及业务/上传日期窗口完整查询：OK、1页1窗、NOT_FOUND。随后逐项检查租户、冻结digest、源revision、当前配置、发送证据/scope、空在途队列和共享冷却，均通过。

本次使用独立维护操作：新建ManualDecision `94184837-4fc8-50e8-b9c6-c499c3415e33`、审计和预查ReceiptEvent，并以UNKNOWN/revision3/digest/tenant为条件CAS恢复PREPARED/revision4；它们同事务提交。然后调用现有perform_send，由原授权/范围/额度/配置/来源/签名/传输校验再复核后发送。不是直接把状态写成成功，不新建业务单号或payload。

真实路径：<prod-host>现有worker → <relay-net-host>专用mTLS HTTPS → eco.taobao.com/router/rest，方法uploadretail，冻结bill_type321/query_bill_typeA。本次真实POST **1次**，HTTP200、ACCEPTED。原HTTP429尝试保留为attempt1，新受理为attempt2。原12代查询job保留不删除、不重置；因此受理后的终态由本轮有限人工查询验证，而不是假称自动轮询预算恢复。

## 最终处理结果

**本次真实历史重发测试成功。** 22:05:49受理，等待约128秒后，22:07:57平台返回result_type=3/sub_process_flag=51，全部处理成功；原业务服务将本地状态更新CONFIRMED并保存正式ReceiptEvent。22:08:32只读查库确认该单CONFIRMED、正式3/51回执1条。

| 项目 | 结果 |
| --- | --- |
| 范围 | 1张历史单、1条追溯码 |
| 新上传次数 | 1次，无自动重发 |
| 平台结果 | HTTP200受理→3/51全部处理成功 |
| 本地状态 | UNKNOWN→人工审计恢复→ACCEPTED→CONFIRMED |
| 全库 | 2577确认、41受理、205未知 |
| 历史其他246张 | 状态/版本不变 |
| 额度/源断点 | 2824/80000；断点46不变 |
| 运行服务 | 原flow发行、六healthy、timer active/waiting |

实际证据：[正式查询](evidence/app-query-20261026T140757635856Z.json)、[最终查库与保护核验](evidence/app-verify-20261026T140832734419Z.json)、[本机保护专项XML](../../../implementation/var/history-retry-guards-r2.xml)。这次闭环证明所选历史单可恢复并成功上传；未测试其他205张，不保证不同历史内容均能通过。


## 保护措施与证据

- 发送脚本绑定一个submission、一个单据哈希、1条码和一次uploadretail网络调用；原单已ACCEPTED/CONFIRMED、配置/源变更、查询失败/限流/不完整、其他在途job或新鲜度超过120秒均拒绝恢复。
- 一次性维护目录与固定Decision ID阻止重复执行。即使网络断开也不重发；发送失败需先核结果，不能直接重跑send脚本。
- 实际维护目录：`/root/blyb-maintenance-backups/history-retry-20261026-one-r2/`；worker持久标记`/data/exchange/history-retry-20261026-one-r2/`。不改现有应用镜像、业务源码、HIS/视图、已批准字典、payload和定时范围。没有HIS查询或源库写入。
- 22:06只读账本核验：提交数2823；尝试总数2824；额度2823→2824/80000；源断点46不变；历史其他246张状态/版本摘要一致，目标digest一致；六容器healthy、timer active/waiting。

证据在[evidence](evidence/)：inspect、send、status、query、verify各次采集；[受控脚本绑定](evidence/trial-binding.json)。状态以时间最新的progress和verify为准，runner.final仅为发送结束时的快照，不随后续终态查询修改。

## 验证与首次中断

本机合成保护测试`python -m pytest tests/integration/test_drug_trace_history_retry.py -q -ra --junitxml=var/history-retry-guards-r2.xml`在implementation目录执行，**24通过，退出0**。包含已受理/确认不得重发、错误租户/配置/源/digest/身份/在途拒绝、平台错误不得视为NOT_FOUND、证据过期、网络结果不明也只调用一次、拒绝其他上传方法，以及SQLite实际注册查询job类型计数。原应用未改，不重复全量回归，不把前一任务522项当本轮新跑结果。

首轮临时脚本将查询job类型误写为drug_trace.query，保护断言在授权CAS前中止，上传0、决策0、目标仍UNKNOWN。修正为注册常量drug_trace.query_status、加实际SQLite回归，并换r2不可变目录重试。首轮源码/绑定保留baselines和trial-binding-r1，首轮远端目录也保留，没有覆盖或删除失败证据。

## 后续历史补报

这次测试只代表这一张历史单的结果。其余205张UNKNOWN仍需按原身份先查、逐单核是否满足恢复条件，有限授权后排队执行；1张原异常记录单独检查。41张旧ACCEPTED已经另有平台3/51证据，应走核查回写，不能重传。原人工核查CSV保留历史事实，新结果用本任务证据追加，不能覆盖原报告伪造当时结果。

现有普通send接口仍PREPARED-only，UNKNOWN不提供直接重发；本轮是一张固定目标的用户授权维护例外，不是开放通用绕过入口。之后做批量恢复应复用正式服务校验、逐单新鲜查询、记录决策和原payload、共享冷却与有限失败停止，不能批量UPDATE UNKNOWN或删除历史attempt/job/额度。已真实发送不能通过数据库回退“撤销”平台事实。
