# INF-LAB-ACCEPTANCE-REVIEW-20261115

用户目标：复核按计划修订后的结果，判断是否可以验收。
工具/模型：Codex / unknown；session 20261115T140633Z-14a7a0e9。

结论：视图部分已通过本例核验，程序仍未达到整体验收条件。本轮仅只读生产及隔离行为验证，不修改发送器、SQL或生产配置。

交付：[修订完成后验收复核](../../development/inf-lab-linkage-review-20261115/revision-4-acceptance/README.md)。

## 实际范围与证据

- 根目录ai_session认领独立revision-4-acceptance、本文件、STATE/HANDOFF/BACKLOG、上报总索引、传染病README和01过程记录；当前没有其他活跃认领。原文件恢复副本在`.ai/baselines/INF-LAB-ACCEPTANCE-REVIEW-20261115/`。
- 发送器4文件冻结在revision-4-acceptance/baseline/，摘要在baseline-manifest.json；未改implementation任何文件。
- `python .../revision-4-acceptance/check_runtime.py`退出0、远端0、0.81秒：只读容器pusher/tables/run.sh，三文件SHA与R2问题版本一致。
- 通过导入既有check_case_ods并仅重定向本轮输出路径核对三单：退出0、ok=true、3行、2.57秒。受控源ods_internal已核enabled/readonly，主3/子38、完整活动键每张命中1。单条SELECT按三单业务键限定，输出仅标签/计数。
- 同一sjzc通道查询ALL_OBJECTS和ALL_ERRORS：退出0/ok=true；三视图VALID、错误0行，证据object-check.json。
- `python .../revision-4-acceptance/collect_behavior.py`退出0：本次冻结代码12场景，fetch/post全合成拦截、urlopen强制拒绝、自动清理临时目录，无真实HTTP。收集脚本不预设缺陷结果。
- `python .../revision-4-acceptance/evaluate_acceptance.py`退出1（明确表示未达安全要求）：2通过/10未通过，详见acceptance-results.json。不是把观察收集成功冒充验收通过。
- 未重复跑同SHA旧68项自测，历史68/0不能替代未通过专项；未重新查前置机业务状态、未真实发送或执行DDL。

## 发现及接续

对方交接末尾R2-A01～A12全部通过与本轮实际不一致。主要仍存在旧父成功/跨表同ID误放行当前失败子项、跨日有效父被卡、等待超overlap丢项、清单参数约束缺失、reconcile+manifest无apply仍调用发送、同force清单二跑重复、缺失目标静默成功；新增医嘱门禁也受跨日问题影响。

下一步按第二版R2/R3/R4实际修改程序并新增能在当前缺陷代码上失败的专项测试，再做R5/R6。视图已修先保留，38条已补齐不盲目整批重放；平台实际错误字段和业务确认另有界核对。

共享STATE/HANDOFF/BACKLOG、业务README/总索引及01维护记录已同步新证据入口，保留历史报告与旧交接正文。机器目录和局部链接/摘要核验在收尾执行并记录于verification-summary.json。

收尾实测：`python scripts/project_docs.py inventory`退出0，机器目录543份文档；局部检查here-string → python退出0，18个新增/变更链接存在、脚本语法/视图证据/2通过10未通过行为结论一致、生产与冻结R2摘要一致、共享历史正文保留。verification-summary.json记录UTC14:13:20。未运行全仓已知历史断链检查来替代本次局部检查。

本轮结果：验收复核和后续范围交付完成；业务整体验收未通过，程序修复/部署/平台确认未执行。

维护记录：机器目录实际写入路径为docs/catalog.json；误登记但未使用的.ai/document-inventory.json范围已取消，实际目录文件单独登记后更新。该误登记未产生文件修改。
