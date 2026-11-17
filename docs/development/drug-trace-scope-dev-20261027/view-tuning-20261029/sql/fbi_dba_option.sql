-- 方案 1（推荐）：函数索引（FBI）——r1 草案 + Codex 审查修订版（review/round-1/检查_codex.md 30 条）
-- DTR-VIEW-TUNING-20261029 / DTR-FBI-REVIEW-20261031；执行者：DBA（低峰窗口）；AI 未执行任何 DDL。
-- 预期（收敛后措辞）：显著降低门诊逐单查库成本，有望使采集回到驱动 4h 等待上限内；
-- 实际整体收益须完整窗口验证（生产回执中约一半耗时未被 query_seconds 覆盖，见 README §七）。

-- ============ 预检（建索引前，全部只读） ============
-- P1 版本/Edition：ONLINE 建索引属 Enterprise Edition 特性——确认目标实例版本与支持：
--   SELECT * FROM v$version;
-- P2 NLS 核对（三方一致：DBA 建索引会话 / 应用会话 / 库默认）：
--   已实证（2026-10-31）：库级 NLS_CALENDAR=GREGORIAN、NLS_NUMERIC_CHARACTERS='.,'、AMERICAN/AMERICA；
--   DRUG_PRESC_MASTER.PRESC_NO 全表 702 万行均为非负整数（无小数→数字分隔符风险实证排除）；
--   日期掩码全数字→NLS_DATE_LANGUAGE 无影响。剩余动作：抓一次应用会话
--   SELECT * FROM nls_session_parameters; 与库默认比对（重点是 NLS_NUMERIC_CHARACTERS / NLS_CALENDAR）。
-- P3 空间预算：键长约 32 字节/行（19 日期+2 分隔+处方号+药房），702 万行键文本 ~215MiB，
--   加条目/块开销估计几百 MB 量级；建议用 DBMS_SPACE.CREATE_INDEX_COST 按真实统计核定，
--   并为 ONLINE 构建预留排序临时表空间。
-- P4 表达式匹配口径：优化器按解析后的表达式树匹配（空白/大小写无关）；不得增删格式参数或
--   CAST——改动即不匹配，需重新验证。

-- ============ ① 创建（建议先 INVISIBLE 验证再公开，见 ③-5） ============
--    ONLINE 允许并发 DML（不等于零影响：有 IO/CPU/临时空间开销）；NOLOGGING 与否由 DBA 按
--    Force Logging/备份恢复策略决定（本方案默认不写 NOLOGGING，与 README 统一）。
CREATE INDEX PHARMACY.IX_DT_BILLKEY ON PHARMACY.DRUG_PRESC_MASTER(
    TO_CHAR(PRESC_DATE,'YYYY-MM-DD HH24:MI:SS') || '|' || TO_CHAR(PRESC_NO) || '|' || DISPENSARY)
  ONLINE TABLESPACE <索引表空间>;

-- ============ ② 统计信息（2026-10-31 实测必做项！） ============
-- 实测：DBA 建完索引后查询仍走全表扫——原因是隐藏虚拟列 SYS_NC00061$ 无统计
-- （num_distinct/density 均 NULL），优化器对该列等值谓词估数失真、不选 FBI；
-- 基表直查靠 11g 基数反馈二次执行纠正，视图查询永不纠正（evidence/plan-check-*）。
-- 修复（只采该列，不重采全表）：
BEGIN
  DBMS_STATS.GATHER_TABLE_STATS(
    ownname       => 'PHARMACY',
    tabname       => 'DRUG_PRESC_MASTER',
    method_opt    => 'FOR COLUMNS SYS_NC00061$ SIZE 254',
    estimate_percent => DBMS_STATS.AUTO_SAMPLE_SIZE,
    cascade       => FALSE,
    no_invalidate => FALSE);   -- 使相关游标失效重解析，新统计立即生效
END;
/
-- 核对：SELECT num_distinct, density FROM all_tab_cols
--   WHERE owner='PHARMACY' AND table_name='DRUG_PRESC_MASTER' AND hidden_column='YES';  -- 应非空

-- ============ ③ 验证（须用【真实模板形态】查询，COUNT 不足以替代） ============
-- 3-1 单头（全列投影，等值键）：替换 :sb/:pc 为真实样本（跨药房多取几张，含 UNKNOWN 单）
-- 3-2 码明细（含 after_record_id 游标 + ORDER BY source_code_record_id + 外层 ROWNUM 分页，
--     取首页/次页/空页三种形态）——r1 版验收脚本遗漏此项，必须补上
-- 3-3 计时对照：建前/建后各跑一遍上述样本（预期：单头 ~1.1s→毫秒级；码明细视计划而定）
-- 3-4 计划核对：优先看真实绑定游标而非仅 EXPLAIN PLAN——
--   DBA 会话：SELECT * FROM TABLE(DBMS_XPLAN.DISPLAY_CURSOR(NULL, NULL, 'ALLSTATS LAST'));
--   重点确认：M 侧出现 IX_DT_BILLKEY 的 INDEX RANGE SCAN（非唯一索引等值范围扫，非唯一扫）；
--   T 侧实际访问方式（NESTED LOOPS 走 med_trace_record_trace_billno 或其他——以实际计划为准）
-- 3-5 稳妥路径（推荐）：先建为 INVISIBLE，
--   验证会话 ALTER SESSION SET optimizer_use_invisible_indexes=TRUE 跑 3-1..3-4，
--   结果符合预期后 ALTER INDEX PHARMACY.IX_DT_BILLKEY VISIBLE; 再观察
-- 3-6 结果一致性：同一窗口（如某日 1 小时）行数 + ORA_HASH 汇总对照建前建后一致；
--     抽 3-5 张多码单逐列比对；覆盖建索引后新增/更新的行
-- 3-7 整体验收口径：以"完整日批窗口采集耗时回到 4h 内"为准，不是"次日 outcome=OK"
--     （运行复用/发送阶段/业务驳回与索引无关；FBI 也不覆盖 r11 住院分支与未解释的其余耗时）

-- ============ ④ 回滚 ============
-- DROP INDEX PHARMACY.IX_DT_BILLKEY;
