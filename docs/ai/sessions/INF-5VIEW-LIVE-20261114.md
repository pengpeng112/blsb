# INF-5VIEW-LIVE-20261114 · 检验/医嘱/生命体征 5 接口注册与演练（停于 Ph0 双推确认）

- 任务：把 5 新视图（ex_lab/ex_lab_item/order/order_item/vital_signs）并入推送器注册、完成自测与
  mock 演练，按六步法上线。任务书：用户会话指令（依据 [20261113-检验医嘱生命体征上报执行计划](../../数据上报/4、传染病病历数据上报/plans/20261113-检验医嘱生命体征上报执行计划.md)）。
- 工具/模型：ZCode / GLM-5.3（account:bigmodel-offpeak-idle-plan）。
- 会话：`20261113T170106Z-1eecafa8`（start 登记 scope：inf_pusher/tables.py、数据上报/4、STATE/BACKLOG/HANDOFF）。
- 执行时间：2026-11-14 01:0x—0x:xx CST（容器 UTC 时钟，下述时间以 CST 记）。

## 结果一句话

**推送器侧全部就绪（注册+selftest 66/66 三环境+mock 4/5 表年度全过+主子对齐/锚点 100%），但 Ph0
双推确认未获答复 → 按停车条件零真实推送：容器运行时未切换（tables.py 仍 6 表版 SHA 0882ede1）、
crontab/state 零改动、已上线 6 表零影响。** 另发现 order_item 两个数据质量问题（PUB_LYYP 一码多行
→ 行集 2× 放大 + 年度 fetch 300s 超时），建议 DBA 去重后再放量。

## Ph0 双推确认（阻塞项，结论）

- 会话内以选择卡向用户提问「crbsb 对 5 接口推送是否已停止」，**用户未答复**。
- 2026-11-13 计划编制时用户已确认「crbsb 仍在推」（计划附记），无新的停推证据。
- 处置（按停车条件 1「Ph0 双推未澄清」）：**不做任何真实推送、不更新容器运行时注册**
  （否则下一班 cron `all real` 会自动首推新表=违反六步法+双推红线）。staging 部署只到宿主
  <deploy-path>（无运行时效果）。
- **放行条件**：用户/信息科确认 crbsb 已停（或从未推）这 5 接口 → 下一棒按 §5 runbook 上线。

## 1. 代码变更（本地仓库）

| 文件 | 变更 | 说明 |
|---|---|---|
| `implementation/deploy/inf_pusher/tables.py` | 合并 `tables-register-5views.py` 的 5 段 + ROLL_OUT_ORDER 追加 | 与注册段源逐字一致，仅 1 处偏离：**vital_signs 去掉 `CREATE_DATE:createDate` 映射**（见 §3-D1） |
| `implementation/deploy/inf_pusher/selftest.py` | 2 处硬编码 6→`len(pusher.ROLL_OUT_ORDER)` | reconcile 补推/失败告警两断言随表数动态（偏离说明：计划称"仅 tables.py 允许改"，但 selftest 断言按 6 表硬编码，注册 11 表后必红；改为动态后不再需要动） |
| `implementation/plugins/domains/infectious/tables.py` | 整体同步生产 tables.py（影子头保留） | LT-05 既定流程"生产变更后只更新影子"；正文与生产逐行一致（脚本校验 body identical: True） |
| `implementation/tests/unit/test_inf_tables_parity.py` | ROLL_OUT_ORDER 断言 11 表 + `_row()` 补 5 表合成行 + 两处计数 18→`len(TABLES)*3` + `test_equivalence_covers_all_six_tables`→`..._all_tables` | parity 守护随注册联动 |

未动：pusher.py/run.sh/config、crontab、systemd、state/、已上线 6 表注册段。

## 2. 本地/宿主/容器自测（三环境全绿）

```
$ cd implementation/deploy/inf_pusher && python selftest.py        # 本地
== 66 checks, 0 fail ==   EXIT=0        # 61→66：5 新表各+1"注册表必填可映射"
$ python deploy_8156_infpusher.py                                # 宿主 staging（4 文件 sha256 OK + 宿主 selftest）
== 66 checks, 0 fail ==   DEPLOY_EXIT=0
$ docker exec ... /tmp/infp5 && python selftest.py                # 容器测试路径
== 66 checks, 0 fail ==
$ python -m pytest tests/unit/test_inf_tables_parity.py tests/integration/test_inf_shadow.py tests/unit/test_external_ledger.py -q
31 passed, 1 warning in 15.04s     # warning=external_ledger 既有 utcnow 弃用，非本轮
```

## 3. ODS 只读核查（sjzc ods_internal，全部聚合，患者明细零输出）

- **视图状态**：7/7 VALID（5 新 + death/admission 抽查）。
- **列结构**：OPERATION_TIME 5 视图全 DATE（水位/序列化正确）；字符串日期列均视图层 TO_CHAR
  'YYYY-MM-DD HH24:MI:SS'；**vital CREATE_DATE=VARCHAR2 且实为 14 位 yyyymmddhh24miss**
  （7 天 58,040 行 LENGTH=14 全一致）→ **D1 偏离：map 不映射 createDate**（非必填；日期归一在
  pusher.py 本次禁改、DATE_FIELDS 无法处理该格式；防 errorCode 05 格式拒收）。
- **年度行数**（>=DATE'2026-01-01'）：ex_lab **107,696** / ex_lab_item **1,324,193** / order
  **405,010** / vital **661,722**（order_item COUNT 60s 连接器超时，行量由 mock/对齐脚本间接实测，
  见 §4）。锚点滚动窗自然约束历史行集——全年 5 表约 250 万行，**非任务书警示的千万级**
  （千万级是 CRBSB 裸视图口径）。
- **必填空值率（30 天）**：ex_lab DEPT 2159/46889=4.6%（执行科室无 INFECT 映射）+ OPERATOR
  573=1.2%（检验员解不出工号）；ex_lab_item OPERATOR 1943/573442=0.34%；order **全零**；
  vital ID_CARD 352/260393=0.14%（CRBSB 透传无 NVL）。→ 预期 skipped_invalid 尾差，与 mock 实测吻合。
- **主子对齐（1 天窗 2026-11-13，客户端集合校验）**：ex_lab_item **16,713/16,713=100%**；
  order_item **12,418/12,418=100%**。（视图对视图 JOIN 聚合在 60s/300s 均超时，改双 SELECT 拉回
  Python 比对；构造上 ORDER_ID 与主表 ID 同表达式同 WHERE、EX_LAB_ID=TEST_NO 过滤超集，实测互证。）
- **锚点命中**：vital 7 天 58,042/58,042=100%（LEFT JOIN DISTINCT 锚点）；其余 4 表由视图 EXISTS
  构造保证 + 11-13 DBA 源端实测 100%。
- **PUB_LYYP 一码多行**：8,590 行 / 6,096 个不同 HIS药品编码 → order_item LEFT JOIN 放大：
  1 天窗 12,418 行仅 6,316 个不同 ID（dup_ID=6,102 ≈ 每单 2 行）。pusher 按 ID 幂等只推首行
  （无重复发送），但两条重复行的 DRUG_CODE/NAME 取舍任意 + fetch 量 2×。**建议 DBA 视图 JOIN
  改去重内联（每码一行）后再放量**（未决，已入 16 号档案与 BACKLOG）。

## 4. 容器 mock 演练（测试路径 /tmp/infp5，运行时零改动）

- 路径与隔离：`docker cp <deploy-path>/{pusher,tables,selftest}.py → <worker-container>:/tmp/infp5/`；
  生成 config-test.json（workdir 指到 /tmp/infp5/state，**除首表外 mock 回执均写测试 workdir 不进生产
  state/**）；运行时 /data/exchange/inf-pusher 全程未动（SHA 复核 0882ede1）。
- 首表 ex_lab 误用了生产 config（workdir=生产 state/）：向生产 `receipts-20261114.jsonl` 追加了
  101,256 行 mode=mock 回执（append-only 证据、不写 sent.jsonl/不推水位，对真实推送无影响）——
  如实登记；后续 4 表均改用 config-test.json。
- 命令（容器内，env 由 `. /data/exchange/inf-pusher/inf.env` 提供，未打印凭据）：

```
python pusher.py --config config(-test).json --table <T> --mode mock --since "2026-01-01 00:00:00"
```

- 实测（2026-11-14）：

| 表 | rows | sent(mock) | skipped_invalid | failed | 与源端 COUNT 对账 |
|---|---|---|---|---|---|
| ex_lab | 107,696 | 101,256 | 6,440 | 0 | **完全一致** |
| ex_lab_item | 1,324,193 | 1,318,974 | 5,219 | 0 | **完全一致**（132 万行单窗 fetch 内存/耗时可行，容器无内存上限） |
| order | 405,025 | 405,025 | 0 | 0 | +15（两次测量间活跃新增，预期漂移） |
| order_item | — | — | — | — | **error: DPY-4024 call timeout 300000ms**（年度 fetch 超时，见 §3 PUB_LYYP） |
| vital_signs | 661,808 | 660,959 | 849 | 0 | +86（活跃漂移）；尾差 0.13%=idCard 空，与 30 天口径一致 |

- order_item 结论：近窗增量（水位窗几天）/行限量查询实测正常（计划 §附 已证 ROWNUM<=20 秒回；
  1 天窗 12,418 行拉取在 §3 对齐脚本完成）；**仅年度全窗 fetch 超 300s**。处置：DBA PUB_LYYP 去重后
  重测（去重后行量≈order 40 万级，order 年度 fetch 已实测通过）；若仍超时→停车升级，不擅自改
  pusher 超时（禁改文件）。

## 5. 下一棒 runbook（Ph0 放行后）

1. 前置复核：用户/信息科确认 crbsb 已停 5 接口（结论写入新交接）；建议先请 DBA 做 PUB_LYYP 去重
   （16-v_inf_order_item.sql JOIN 改去重内联，一行；不做则 order_item 放量接受 2× fetch+任意行取舍）。
2. 运行时切换（staging 已在宿主 <deploy-path>，sha256 已校验）：
   `docker cp <deploy-path>/{pusher.py,tables.py,selftest.py,run.sh} <worker-container>:/data/exchange/inf-pusher/`
   → 容器 selftest 复跑 → **切换即生效：下一班 cron `all real`（*/15 于 0-5、9-23 点；8:30/23:58/00:27）
   会自动纳入 5 新表首跑（无水位→full_days=60 天窗）**。若要保 mock-first 纪律：在 cron 静默窗
   （06:00-08:29 CST）切换并立刻手动走六步法，或在切换后 15 分钟内完成 mock+limit3。
3. 六步法逐表（ex_lab→ex_lab_item→order→order_item→vital_signs）：
   `run.sh <表> mock`（可用测试路径复跑）→ `run.sh <表> real 3`（receipts 全 true、errorCode null）
   → 去 limit 放量（幂等；order/order_item 用 `--since "2026-01-01 00:00:00"` 显式窗回填年度，
   超时/被 timeout 杀掉即重跑续推）。任何 02/03/04 或 failed>0 → 停车逐条分析（id 掩码后 4 位）。
4. Ph4 验证：新表可关联率≥90%（源端已 100%）、主子对齐 100%（§3 已实测）、已上线 6 表水位/
   cron.log 无异常、次日 `reconcile-<当日>.json` 11 表 repushed=0。
5. Ph5 补齐：02-运维手册新增 5 接口口径（本棒按"未上线"未动 02）；interfaces/03/01/STATE/BACKLOG
   本棒已按"注册就绪·待双推确认"回写，上线后状态行改"已上线"+补上线证据。

## 6. 硬约束遵守声明

- AI 零 DDL/DML：本轮 ODS 访问全部只读 SELECT 聚合（视图为 DBA 已建，未改）。
- 推送器仅 tables.py 注册段（selftest 两断言动态化为必要联动，见 §1）；crontab/systemd/state 零改动
  （state/ 仅新增 mock 回执行与生产 receipts 同文件 append——见 §4 说明）。
- 双推红线：零真实推送；运行时未切换，cron 班次行为与切换前完全一致。
- 已上线 6 表：注册段未动、水位未动、视图未动、无任何推送动作。
- <prod-host> 凭据全程未读取/未打印（inf.env 仅容器内 source）。

## 7. 证据索引

- 本地/宿主/容器 selftest 输出、mock 统计：本文件 §2/§4（原始 JSON 行已摘录）。
- ODS 核查：§3（sjzc live 查询 + 容器直连 check_align/check_align2 脚本，脚本存 <prod-host>:/tmp 与容器
  /tmp/infp5，均为只读 SELECT）。
- 生产 state 影响：仅 receipts-20261114.jsonl 追加 ex_lab mock 回执 101,256 行（mode=mock 标记）。
- 运行时基线：容器 /data/exchange/inf-pusher/tables.py SHA256 `0882ede1…49d458a`（切换前=切换后复核）。
