# 10-29/10-30 积压补发执行交接

任务 DTR-TIMED-CATCHUP-20261031；session `20261031T015638Z-f9951d30`；ZCode/GLM-5.3；2026-10-31。
用户授权"请你处理下"。同日另完成视图-only 可行性实测（见 DTR-VIEW-TUNING 附录，本文不重复）。

## 结果（全部实测，证据在 drug-trace-timed-report-20261024/evidence/）

1. **10-30 窗口（1,566/1,566）**：
   - 首轮 09:56 systemctl start：325 单受理后遇 1 次 SEND_RESULT_UNKNOWN（HTTP200 结果未定）→
     驱动防重复安全停机（PARTIAL_FAILURE，无错发）；1 单 BUSINESS_REJECTED（见下）。
   - 续跑 11:14：sent 1,241 + skipped 325（首轮已发正确跳过）、零错误、outcome OK（8063s）。
   - 终态：CONFIRMED 1564 + REJECTED 1 + UNKNOWN 1。
2. **10-29 窗口（1,692/1,692）**：13:52 定向补发 main('2026-10-29')（BLYB_DAILY_REPORT_ENABLED=0
   保留 FAILED 历史行；采集 DONE 秒匹配直发）——零错误、outcome OK（12363s）；
   终态 ACCEPTED 1434 + CONFIRMED 258（查询自动推进中）。
3. **合计**：配额 8413→11671/80000（+3,258 = 两日积压全量，无重发）；**PREPARED 清零**。

## 移交待办

- **人工复核，P1**：BUSINESS_REJECTED 1 单（submission 3df545b7）：平台"单据上传失败！存在已出售的码
  84280100170422892624/83714790431804204430 共2个码"——跨单重复码，与历史双扫同模式，终态；
  建议药学确认处理规则（E06' 同类）。
- **自然恢复，P2**：UNKNOWN 1 单（4ded59cb）——今夜 01:00 批次查询补进（7 天窗口内）覆盖。
- **明晨预期**：FBI 未建 → 11-01 01:00 批次大概率仍 collect_timeout（白天按 README recipe 补跑即可）。
- 回执文件：20261031-095653/111456.json、20261031-135256-backfill-1029.json（宿主 /var/log/blyb/daily-upload/）。
