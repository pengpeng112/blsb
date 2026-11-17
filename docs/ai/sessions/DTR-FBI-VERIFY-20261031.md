# FBI 索引上线验证与夜间批次终验交接

任务 DTR-FBI-VERIFY-20261031；session `20261031T122941Z-070ba1b4`；ZCode/GLM-5.3；2026-10-31→11-01。

## 结果（全部实测，证据 view-tuning-20261029/evidence/ 与 timed-report-20261024/evidence/）

1. **索引上线三步闭环**：DBA 建成 IX_DT_BILLKEY（TSP_PHARMACY/VALID/VISIBLE，直接可见未走 INVISIBLE）→
   首测仍全表扫 → 根因=隐藏虚拟列 SYS_NC00061$ 无统计（num_distinct NULL，视图查询不触发基数反馈）→
   DBA 执行 GATHER_TABLE_STATS('FOR COLUMNS SYS_NC00061$ SIZE 254') → **应用真实路径单头/码明细
   1.1s→0.002-0.005s（约300倍）**。等价性：1h 窗 557 行全列哈希建前后一致。
   （r1→r2 修订含 Codex 30 条审查：review/round-1/汇总.md；MV 替代评估 README §八——机制可行但
   陈旧正确性/热表写放大不可接受，维持不推荐。）
2. **夜间批次终验（01:00，窗口 10-31）**：查询层修复铁证（1,479 查询共 1.99s vs 此前 ~500s 同量级）；
   但批次 FAILED collect_progress_stalled（59 分钟）——**瓶颈转移**：应用侧采集管道每单 ~11s 非查询
   开销（入库/质量/调度，Codex 预警的未解释耗时现占 100%）。采集服务端继续推进，08:07 DONE
   （2502 单/valid 1509）。
3. **值守自动补跑成功**：/root/catchup-1101-launch.sh（DONE 即 systemctl start）→ 10:14 触发 →
   **1,509/1,509 零错误、outcome OK**（7611s），终态 CONFIRMED 100%，PREPARED 清零，
   配额 13,180/80,000，日报 10-31 行 OK（report_push ok）。

## 移交待办

- **P1：采集管道非查询开销排查**（每单 ~11s：worker 入库/质量处理/调度；属 scope 会话域，
  勿双头改）。排查前日常模式=01:00 批次（可能停滞失败）→ 值守/白天补跑完成发送（本轮已两次验证）。
- 值守脚本 /root/catchup-1101-launch.sh 为一次性（窗口写死 10-31），如需常态化守夜应参数化另立任务。
- BUSINES_REJECTED 1 单（10-30，重复码）仍待药学复核（前session移交项，未变）。
