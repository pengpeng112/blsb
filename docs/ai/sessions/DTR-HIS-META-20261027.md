# DTR-HIS-META-20261027 交接（HIS 元数据探查：住院/退药口径与原厂家体系）

- 执行：ZCode / GLM-5.3，2026-10-27 深夜，session `20261027T123355Z-9f9bbdb9`。
- 触发：用户授权连 hisuser 库查表备注，找退药关联与住院扫码线索；方案A（K3 回看补报）获批。
- 结果：[README「HIS 元数据探查」节](../../operations/drug-trace-sjzc-verify-20261027/README.md)。

## 四轮探查（全部只读：数据字典+索引化样本查询，约 20 条）

1. explore_meta.py：两 schema 表/列注释清单 → 发现原厂家四类追溯视图、上传日志表。
2. explore_meta2.py：视图 DDL + 关键表结构（ALL_VIEWS/ALL_TAB_COLUMNS）。
3. explore_meta3.py：DRUG_PRESC_TRACE_CODE 样本（0 行）、CLI_TRACE_BILLNO（无索引跳过）、
   TRACE_UPLOAD_TIME_LOG/DOCUMENT_UPLOAD_RECORD 最近 30 条。
4. explore_meta4.py：PHA_CLI_REQUEST_DRUG 中 9 样本状态（索引化查询）。

## 核心结论

- 住院上报可行：原厂家住院视图（PHA_INP_TRACE_DRUG_SEND_VIEW）基于住院发药申请表；
  样本 send_st='Y'。我们 r10 从门诊处方主表取住院分支，键体系不同所以看不到码。
- 退药：申请表 send+return 成对（样本数量全对冲）；原厂家口径=全退剔除；K2 冲正=
  return 行+IN 方向码（IN_OUT_TYPE 值域已由列注释确认）。
- 205：TRACE_UPLOAD_TIME_LOG=原厂家上传日志且仍在每小时写入——比对材料不依赖人工 Excel；
  ⚠️ 需核实原厂家是否仍向平台传数（双上报风险，BUSINESS_ID 3503/3505 含义未确认不猜）。
- 260 缺码：申请表发药确认=Y 但 MED_TRACE_RECORD 无码行、备用码表也零行——
  收窄到"扫码环节未写码"；下一步对比申请表标志位（PDA/扫码相关列）或药学部核对。

## 下一步

1. 住院/退药上报开发（K2+住院）：按原厂家视图口径设计新来源 revision（申请表体系），
   需先验证住院码在 MED_TRACE_RECORD 的真实键（拿 REQUEST_NO/DIS_RECORDNO 样本查）。
2. 205 比对：按时间窗聚合 TRACE_UPLOAD_TIME_LOG vs 我们 205 张清单。
3. 双上报核实：用户/平台确认原厂家程序现状（biz=3503 每小时 rec>0）。
4. K3 方案A开发：每日回看近 7 天无码/部分码单重扫（已获批）。
5. 260 深挖：申请表标志位对比 260 vs 1995。
