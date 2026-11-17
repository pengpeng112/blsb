# DTR-HISTORY-RETRY-20261026 · 历史单重发测试交接

Codex/model unknown，session20261026T135855Z-86d40348。用户明确要求历史重发测试；先读START_HERE/STATE/HANDOFF并登记，无并行代理。实际仅维护脚本、专项测试、共享记录和业务导航，没有修改登记范围内业务服务源码，也没有部署新镜像或访问HIS。

[任务说明和真实证据](../../operations/drug-trace-history-retry-20261026/README.md)。选择9b19170e-5ceb-4da4-99bb-7478c77d9695，原HTTP429/UNKNOWN/1条码；使用同企业/查询A/原单号/日期覆盖先完整查询NOT_FOUND，复核原冻结配置、digest、源版本、授权scope、租户、空在途和共享冷却。

人工一次决策94184837-4fc8-50e8-b9c6-c499c3415e33与审计/预查回执及UNKNOWN+版本3 CAS恢复PREPARED在同一事务提交；然后正常perform_send再次校验并发送一次。22:05:49 HTTP200 ACCEPTED，等待至少120秒，22:07:57正常perform_query查3/51 CONFIRMED并落真实回执/状态。不是手写成功状态。未新建payload/单据号，未重置原12代查询job，未覆盖原attempt1。

实际命令：

```powershell
# implementation目录，本机合成；最终24通过，退出0
python -m pytest tests/integration/test_drug_trace_history_retry.py -q -ra --junitxml=var/history-retry-guards-r2.xml
# 根目录，以下现场命令已执行，send不得重跑
python docs/operations/drug-trace-history-retry-20261026/prepare_trial.py
python docs/operations/drug-trace-history-retry-20261026/check.py app inspect
python docs/operations/drug-trace-history-retry-20261026/check.py app send
python docs/operations/drug-trace-history-retry-20261026/check.py app status
python docs/operations/drug-trace-history-retry-20261026/check.py app query
python docs/operations/drug-trace-history-retry-20261026/check.py app verify
```

本机最初23保护测试通过，但未覆盖实际查询job类型；首次现场临时脚本写错drug_trace.query，断言在授权CAS之前拦截（发送0/决策0）。保存旧源码和绑定，修正为注册常量drug_trace.query_status并增加真实SQLite计数测试，24通过后在新r2目录执行一次成功；原失败证据保留。未把首轮失败隐去，未重复真实发送。

最后只读查库：2577确认/41受理/205未知，2823提交/2824尝试，额度2824/80000，源断点46；投影恢复目标旧状态/版本后，历史247摘要与升级前一致，证明其他246未变。目标digest未变，原429尝试保留，新的200受理尝试及1条正式3/51查询回执存在。六容器healthy、timer active/waiting；普通daily-upload.service上次失败历史仍保留，不能用timer状态声称昨日任务全正常。

下一步：其余205张未知按保留原时间的清单逐单新查、核历史和批准恢复，不批量UPDATE UNKNOWN；41已在平台成功的走受控回写，不能重传。本轮只固定一张一次POST，send/prepare_trial不能拿来做通用恢复工具，正式批量恢复需要独立范围和逐单审计设计。原定时授权与共享冷却保持，首次完整新日批/SLA未观察。真实上传无法用恢复数据库撤销，不倒灌旧备份。
