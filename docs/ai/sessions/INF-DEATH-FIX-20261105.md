# INF-DEATH-FIX-20261105 · 死亡信息 T+0 修复执行（视图驱动切换 + 历史补传）

- 会话：`20261105T131812Z-ea110a7e`。ZCode/GLM-5.3，2026-11-05。
- 权威指令：[plans/20261105-死亡T0修复执行计划.md](../../数据上报/4、传染病病历数据上报/plans/20261105-死亡T0修复执行计划.md)（§0.3 七条红线全程遵守：无 DDL/DML、ODS 只读聚合、推送器/crontab/state 零改动、补传三步走、停车即停）。
- 查询通道：`python ~/.zcode/skills/sjzc/scripts/sjzc_query.py live ods_internal --sql "..." --max-rows N`（全部聚合，患者明细未进输出）。
- 接手时 ai_session status 复核：无范围重叠活跃会话（仅 1 条 cancelled 旧记录）。

## Ph1 口径实测定稿（2026-11-05 实测，五组验证全部完成，无停车条件触发）

### Ph1-1 文书驱动量级（近 90 天，CREATE/DISCHARGE >= TRUNC(SYSDATE)-90）

| 月 | 文书就诊数（去重） | PAT_VISIT 死亡离院 |
|---|---|---|
| 2026-08 | 26 | 24 |
| 2026-09 | 33 | 36 |
| 2026-10 | 35 | 28 |
| 2026-11 | 6 | 0（未编码） |

总量 100 vs 88，偏差 **+13.6%**（<20%，停车条件 2 未触发）。筛选：`TOPIC IN ('死亡记录','24小时内入院死亡记录') OR TOPIC LIKE '死亡记录模板%'`（排除讨论记录）。

### Ph1-2 必填非空率（近 30 天文书去重就诊 37 例）

| 项 | 命中 | 率 |
|---|---|---|
| 关联键 NULL（PATIENT_ID/VISIT_ID） | 0 | - |
| CREATOR_ID 非空 | 37/37 | 100% |
| PATIENT_CUR_DEPT_CODE 非空 | 37/37 | 100% |
| join PAT_VISIT 命中 | 37/37 | 100% |
| DISCHARGE_DATE_TIME 非空 | 37/37 | 100% |
| PAT_MASTER_INDEX 命中 | 37/37 | 100% |
| pmi.ID_NO 非空 | 37/37 | 100% |
| 出院主诊断（type=3,no=1）存在 | 37/37 | 100% |

全部 ≥80%，停车条件 1 未触发。

### Ph1-3 双驱动并集/交集矩阵

- 近 90 天：文书 100 / 编码死亡 88 / 交集 86 / **文书独有 14**（未编码漏网=T+0 修复目标）/ 编码独有 2（无文书历史病例，v0.5 分支兜住）。
- 11 月（文书 vs 10 月以来编码死亡）：文书 6 例、**全部未编码**（上线即新增 6 行）。
- **口径修正（相对计划 E4 的"9 例"）**：按 §2.1 定稿筛选，11 月（11-01 起）死亡记录类文书=6 例（11-02×1、11-03×1、11-04×4，按日×TOPIC 明细已核）；E4 的 9 为编制时估计（含其他归类）。Ph3 验收"11 月行数"预期据此定为 **≥6（部署时刻含当日新增 +1~2）**。

### Ph1-4 与 v0.5 行集 diff 预演（2026-01-01 起）

| 项 | 值 |
|---|---|
| 文书去重就诊 | 340 |
| v0.5 驱动（=当前视图行数，E2 复现一致） | 329 |
| 交集 | 327（PAT_VISIT 死亡病例 99.4% 有死亡文书） |
| **v0.6 并集行数** | **342** |
| **新增行（文书独有）** | **13** |

新增行 OPERATION_TIME（=文书 LAST_MODIFY_DATE_TIME）按月：2026-01×1（01-09）、2026-04×1（04-15）、2026-10×5（10-19/10-23/10-29×2/10-31）、2026-11×6。1~4 月及 10 月多数低于推送水位 → 须 `--since 2026-01-01` 补传覆盖。

**补传预期实测定稿**：sent ≈ **134~140**（1~4 月 PAT_VISIT 行 ~127 + 文书独有历史 2 + 10 月文书独有 5 + 11 月 6 若未被自动班先推）——计划 §4 基线 127±10，实测略高 6~13 条（文书独有行在编制计划时未计入明细），Ph4 以实测记录，超差不构成停车（停车仅 errorCode 02/03/04 或 failed>0）。

### Ph1-5 死亡诊断兜底有效性（近 90 天死亡病例 88 例）

出院主诊断存在 88/88=100%；院标→国标映射命中 **88/88=100%**（CDA_DICTIONARY ICD-10）→ DEATH_DIAGNOSIS 兜底链有效，填充率预期 ≥80% 达标（远超 50% 停车线）。

### Ph1 结论

五组验证全部通过，停车条件 1/2 均未触发，口径定稿=计划 §2.1/§2.2 原案 + 11 月预期数修正（9→6 实测）+ 补传预期修正（127→134~140）。进入 Ph2。

## Ph2 视图 v0.6 脚本编写（完成，未执行——AI 禁 DDL 红线遵守）

产出：`docs/development/inf-report-20261027/sql/04-v_inf_death_info_v0.6.sql`（单文件自包含）：

- v0.6 全文：`CREATE OR REPLACE FORCE VIEW "ODS"."V_INF_DEATH_INFO"`，WITH dw CTE（TOPIC 筛选+ROW_NUMBER 取每就诊首份死亡文书，ROWID tie-breaker）→ 分支1 文书行（LEFT JOIN PAT_VISIT 补充字段不丢行、PAT_MASTER_INDEX 经文书键关联）UNION ALL 分支2 v0.5 原文 + NOT EXISTS 去重；`WITH READ ONLY`；GRANT 为注释模板（账号 DBA 侧确认，未读 inf.env）。
- 字段决策按计划 §2.2 + Ph1 实测落地：OPERATION_TIME 文书行=LAST_MODIFY_DATE_TIME；DEAD_DATE CDA→DISCHARGE→CREATE 三链；DEATH_DIAGNOSIS 新增主诊断映射兜底（两分支，唯一口径增强）；DEPT 文书在院科室 INFECT 优先→出院科室链兜底；OPERATOR/CHIEF=CREATOR_ID 兜底主治。
- 5 条 DBA 验证 SQL（行数≈342/按月分布/MAX op≥11-04/必填空值率/11 月行数≥6）附文件内。
- 回滚段：v0.5 全文整体行注释保留于文件尾部（回滚方式二选一：去注释执行或直接用基线文件）。

自查（脚本机械验证，2026-11-05）：

| 项 | 结果 |
|---|---|
| 视图列清单（名称+顺序）v0.5 vs v0.6 | 22 列 diff=**NONE** |
| 回滚段（规范化去行内注释）vs v0.5 正文 | 77 行逐行 diff=**NONE** |
| 分支1 SELECT 别名 | 22 个齐全、顺序=列清单 ✅ |
| 分支2 SELECT 别名 | 22 个齐全、顺序=列清单 ✅（首轮脚本误把回滚段计入，修正后验证） |
| LIKE '死亡记录模板%' 误伤检查 | 不误伤'死亡病例讨论%'（前缀第 3 字分叉），注释已说明 |
| DEATH_DATE 停更 VARCHAR 字段 | 无 TO_DATE 强转，NVL 隐式行为与 v0.5 一致 |

Ph2 完成。进入 Ph3（DBA 门，唯一外部依赖）。

## Ph3 DBA 门（交付就绪；等待 DBA 排期——本会话在此中断，标 handed_off）

- 交付物（单文件自包含）：`docs/development/inf-report-20261027/sql/04-v_inf_death_info_v0.6.sql` = v0.6 全文 + 5 条验证 SQL + v0.5 回滚段。
- 中断原因：Ph3 是唯一外部依赖（红线 1：AI 禁止 DDL/DML，视图重建必须 DBA 执行）。2026-11-05 会话内向用户询问 DBA 安排未获答复，按计划默认路径（§3.Ph3 / 用户指令）停在 Ph3、标 handed_off 上报，不即兴发挥。
- 会话内完成的偏离记录：①11 月预期 9→6 例（Ph1-3 实测口径修正，E4 的 9 为计划编制时估计）；②补传预期 127→134~140（Ph1-4 预演：1~4 月 PAT_VISIT 行 ~127 + 文书独有历史 2 + 10 月 5 + 11 月 6 视自动班先推与否）；两者均非停车条件，已在档案与 STATE/HANDOFF 注明以实测为准。

### 下一棒接手指令（DBA 执行后继续）

1. 用户/协调人向 DBA 转交 `04-v_inf_death_info_v0.6.sql`；DBA 在 ODS 执行 v0.6 CREATE（推送器现有授权不受影响，GRANT 注释模板仅供新账号）。
2. DBA 回传文件内 5 条验证 SQL 结果；执行 AI 复核判定：总行数≈342（±部署时刻当日文书新增）、按月分布对齐 Ph1 预演、MAX(OPERATION_TIME)≥2026-11-04、11 月行≥6、必填空值率异常 → 任一不达标即要求 DBA 执行回滚段，任务终止于 handed_off 并记录。
3. 复核通过 → **Ph4（计划 §3.Ph4，<prod-host>，真实推送，三步走纪律）**：
   a. 部署后首个 */15 班自动观察：death 行 sent>0、failed=0、无新增 skipped_invalid 模式；当班未到可手动 `run.sh all real` 前台一班（timeout 300）；
   b. 11 月行落位：`grep '"table": "death"' receipts-当日.jsonl` true 条数≈Ph3 复核数；sent.jsonl 增量对应；
   c. 补传：mock 预演（容器内 `python pusher.py --config config.json --table death --mode mock --since "2026-01-01 00:00:00"`，预期 rows≈342）→ 小批 real 同命令 `--mode real --limit 3`（receipts 全 true）→ 放量去掉 limit（预期 sent≈134~140、failed=0、其余 skipped_same/skipped_invalid 已知尾差）；**任何 errorCode 02/03/04 或 failed>0 = 停车条件 4，停下逐条分析（id 掩码后 4 位）**；
   d. 次日 reconcile-<当日>.json：death repushed=0。
4. Ph5 平台复核（死亡当日上传>0 连续≥2 天；质控"死亡诊断代码"收敛；仍 0 人=停车条件 5 → 公卫科澄清，不猜不改）。
5. Ph6 按计划 §6 六项铁律回写（01 时间线已有本行，执行完成后更新结果；02 手册死亡表口径；interfaces/04；STATE/BACKLOG；本交接补 Ph4-Ph6；`python scripts/project_docs.py inventory`）。

## Ph4 推送验证 + 历史补传（待下一棒）

## Ph4 推送验证 + 历史补传（待补）

## Ph5 平台复核（待补）

## Ph6 铁律回写（待补）
