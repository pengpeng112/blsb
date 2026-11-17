# SD-01 只读探查结论（DTR-SCOPE-DEV-20261027）

授权：用户在任务入口提示词中授权本任务范围内的 HIS 只读探查（经 sjzc 受控通道：
<relay-jump> 容器只读连接器、本机不读凭据、单条 30s 超时、无索引列不做范围扫、不猜值域）
+ <prod-host> 应用库只读事务读取样本键。除此之外未连任何生产系统。

证据：
- `evidence/sd01-appdb-20261027T135459Z.json`（应用库只读事务：住院 WAITING 样本/退药3张/
  260 缺码键+300 VALID 对照键/205 时间分布）
- `evidence/sd01-his-20261027T135850Z.json`（HIS 四问首轮，身份 READY_HIS@his，12.15s）
- `evidence/sd01-his-r2-20261027T1402xxZ.json`（Q1 去多样本补强，4.76s）
- 完整键值清单（260+300 处方号）：`.ai/private/drug-trace-scope-dev-20261027/sd01-keys-*.json`
  （ACL 当前用户+SYSTEM；共享 evidence 只含 ≤10 样本与聚合）
- 可复跑脚本：`../sd01_pull_appdb.py`、`../sd01_probe_his.py`、`../sd01_probe_his_r2.py`

全部只读 SELECT（HIS 约 40 条 + 应用库 6 条只读事务），源写入 0、上传 0、生产改动 0。

## Q1 住院码真实键（SD-02 前提）——定论：申请体系两候选键在 MED_TRACE_RECORD 均零命中

- 通道/身份：his_source_<his_host_id> → READY_HIS@his（同 10-23/10-27 先例）。
- `PHA_INP_REQUEST_DRUG`：105 列；索引 23 条，**DISP_EXECUTE_TIME 有前导索引**
  （走窗口采样，未全表扫）。
- 采样：10-26 窗 DISP_FLAG='send' AND DISP_QUANTITY>0、住院药房
  （050103/050107/050121/050110/050109/050101/050102）**去重 REQUEST_NO 10 个**
  （050103×9、050107×1；格式 20 位 '02010420261026091911'），逐一关联
  `PHA_INP_DISPMASTER` 得 10 个 DIS_RECORDNO（19 位 '2026102609235738110'，1:1 无重复）。
- **MED_TRACE_RECORD 按 TRACE_BILLNO IN (10 REQUEST_NO) → 0 行；
  TRACE_BILLNO IN (10 DIS_RECORDNO) → 0 行**（无日期/药房过滤，GROUP BY 键分布）。
- 原厂家住院视图本身可读且行存在：`PHA_INP_TRACE_DRUG_SEND_VIEW` 按 REQUEST_NO 点查命中
  （首列修正后；视图不暴露 DISP_EXECUTE_TIME 列）。
- `PHA_INP_DISPDETAIL`：同 REQUEST_NO 的发药明细 PDA_STATE 全部 = 0（10/10 组）。
- 判读：**住院发药在申请表体系完成（send/Y/数量>0），但追溯码未写入 MED_TRACE_RECORD
  ——不是"键没找对"，而是住院分支当前没有扫码落库**（与 10-27 晚核查"住院 7 样本三键零行"、
  10-23 "住院主单零 OUT 码"互相印证）。两个候选键都保留进 r11 code 视图 UNION 分支，
  HIS 将来开始写住院码时（无论挂哪个键）即可流入。
- → SD-02 按 §六降级路径交付：bill 侧+采集可用、code 视图当前自然 0 行（整单 WAITING），
  码源恢复属药学部/HIS 厂家事项（另案）。

## Q2 退药 IN 码存在性（SD-03 前提）——定论：退药码未写入，冲正按 return 行+原发药码实现

- 门诊退药 3 张（57370/57371/57372，050121，原 0/2 分支）在
  MED_TRACE_RECORD 全日期按 TRACE_BILLNO 查询：**7 组行全部 IN_OUT_TYPE='OUT'**，
  时间 2025-07-22 ~ 2026-09-09（处方号复用的历史行，含 050121/050102 两药房、
  非拆零为主 1 条拆零）；**CREATE_TIME >= 2026-10-24 的行 = 0**（近窗零行）。
- **无任何 IN 方向行**（值域注释：IN=入库（退药）），退药执行（10-26 15:31）对应的
  IN 码尚未写入。
- → SD-03 按执行书既定分支：return 行建冲正单、code 视图按三键+IN 方向建（当前自然 0 行），
  冲正 payload 用**原发药侧已 CONFIRMED 冻结 payload 的码列表**（只读复用，不改冻结件）。

## Q3 BILL_TYPE 证据检索（只收集，不决定）

- ALL_TAB_COLUMNS（OWNER=DRUG_USER，%BILL_TYPE%/%BUSINESS%，≤100）：12 列，
  **没有任何名为 BILL_TYPE 的列**；相关仅 DOCK_CALL_LOG.BUSINESS_*、
  DOCUMENT_UPLOAD_RECORD.BUSINESS_TYPE（表 0 行）、TRACE_UPLOAD_TIME_LOG.BUSINESS_ID
  （值 3503/3505，含义仍未确认——不猜）、MED_DICT_FACTORY.BUSINESSSCOPE（经营范围）。
- PUB_CONFIG（20 列，键列=CONFIG_CODE）：UPPER(CONFIG_CODE) LIKE %TRACE%/%UPLOAD% → **0 行**；
  SYSTEM_CONFIG trace 键点查 → **0 行**。
- 结论：HIS 侧**无原厂家 bill_type 线索**；住院/退药的 bill_type 只能由用户/平台确认
  （321=卖出已批；住院/退货待确认）。字典维持 DRAFT、发送 fail-closed 是唯一正确姿势。

## Q4 260 缺码共同特征（线索输出给药学部）

对比组（PHA_CLI_REQUEST_DRUG，PRESC_NO IN + PRESC_CREATE_TIME>=2026-10-20，
CLI_FLAG='send' 行）：
- 缺码组 260 处方 → 1154 行（**平均 4.44 行/处方**）；VALID 对照 300 处方 → 439 行
  （**平均 1.46 行/处方**）——缺码集中于**多行大处方**。
- WINDOW_NO（发药窗口号）：缺码组 97.4% 空（1124/1154）；VALID 组 86.6% 空（380/439）
  ——缺码组**窗口号缺失比例更高**。
- SEND_DRUG_STATUS：两组全部 'Y'（发药流程都完成）。
- RETURN 行：缺码组含 2 条 return 行；VALID 对照 0 条。
- SPLIT_FLAG 拆零占比：缺码 16.6% vs VALID 17.5%——**无差异，排除拆零因素**。
- TRACE_CODE_NUM：两组全 NULL，无信息量。
- 申请表内无 scan/pda/upload 类专用标志列（127 列已核对；住院侧 PHA_INP_DISPDETAIL 才有
  PDA_STATE，门诊申请表无）。住院发药明细 PDA_STATE=0（未扫码态）供对照。
- 结论：申请表标志位无单点开关；"多行大处方 + 窗口号缺失"组合是给药学部/050102 药房
  核对的首选线索（是否某通道/批次发药不走扫码），佐证"扫码环节未写码"归因。

## SD-06 前置：205 张发送时间分布 + 原厂家上传日志对照（详见 ../SD-06-report.md）

- 应用库：205 UNKNOWN 提交 created_at 分布 = **2026-10-23 1 张（17:12:30）+
  2026-10-25 204 张（04:28:57–04:36:54）**（GMT+8）。
- TRACE_UPLOAD_TIME_LOG（按 START_TIME 索引聚合，10-18..10-28）：
  biz 3503 每小时跑：10-23 全天 26 条（08–16 时），**10-24/10-25 两天 REC=0**；
  10-26=37、10-27=78 条成功——**原厂家程序仍在每天上传**（双上报风险持续，待用户核实）。
  biz 3505：10-18..21 每天 2102~2899 条成功，**10-22 起仅失败重试行（138 条 status=0）
  后再无成功**。
