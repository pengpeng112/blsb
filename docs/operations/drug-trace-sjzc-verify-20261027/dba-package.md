# DBA 执行单：10-26 批 WAITING 缺码/分支源表核查（DTR-SJZC-VERIFY-20261027）

用途：在合规只读身份下执行已备好的 3 条核查 SQL，回传结果由 AI 按
[SOURCE-REVIEW 处置表](../drug-trace-recovery-stability-20261027/SOURCE-REVIEW.md)归因。
本单不含任何单据键明文；样本参数在受限文件（由使用方转交）：
`.ai/private/drug-trace-sjzc-verify-20261027/sample-keys-20261027T111347Z.json`。

## SQL 位置（已备好，无需改写）

`docs/operations/drug-trace-recovery-stability-20261027/sql/`

| 文件 | 用途 | 何时用 |
|---|---|---|
| `01-waiting-master.sql` | 主单三键确认（唯一性 + 10-26 发药日 + 原始状态 PRESC_SOURCE/FLAG/REFUND_FLAG） | 每样本先跑 |
| `02-waiting-trace-exact.sql` | 原扫码行按三键精确核查（方向/删除/拆零原值分组 + T/F/S 非空数 + 采集后新增/修改数） | 每样本 |
| `03-waiting-trace-nearby.sql` | 同单号 ±48h 附近候选（找日期/药房不匹配） | 仅当 02 疑似关联错时 |

## 参数绑定（每样本一行）

| 参数 | 来源字段 | 说明 |
|---|---|---|
| `:presc_date_s` | `presc_date_s` | 'YYYY-MM-DD HH24:MI:SS'，**保留秒** |
| `:presc_no` | `presc_no_text` 转 NUMBER | SQL01 主单用 |
| `:trace_billno` | `trace_billno`（=TO_CHAR(PRESC_NO)） | SQL02/03 码表用 |
| `:store_code` | `store_code`（=DISPENSARY） | 保留前导零 |
| `:snapshot_time_gmt8` | `snapshot_time_gmt8` | 该单采集时点（已换算 GMT+8） |
| `:begin_time`/`:end_time` | snapshot_time_gmt8 ±24h | 仅 SQL03 |

样本结构：`grp`（normal_no_code 20 张 / orig_1_0 7 张 / orig_0_2 3 张）+ `tile`（样本编号）。

## 执行规则（与上一棒约定一致）

- 只读会话/账号；单条 15 秒超时；累计 ≤5 分钟；单查询 ≤50 结果行；超限即停并记录未完成。
- 不使用 EXPLAIN PLAN/PLAN_TABLE；不建索引；依赖既有索引（TRACE_BILLNO、主单 PK，
  2026-10-19 活库证据）。
- 全部结果按**原值**返回（IN_OUT_TYPE/IS_DELETED/SPLIT_FLAG/PRESC_SOURCE/FLAG），
  平台无 confirmed 值域，不猜含义。
- 03 的 `MAY_BE_TRUNCATED=1` 时不得断言"不存在或已完整统计"。

## 回传格式（按样本编号 JSON，每样本一条）

```json
{"grp":"normal_no_code","tile":3,"sql01":{"MASTER_ROWS":1,"IN_AUTHORIZED_DAY":1},
 "sql02":[{"DIRECTION_RAW":"...","TRACE_ROWS":2,"NONEMPTY_T_ROWS":2,
           "CREATED_AFTER_SNAPSHOT":0,"MODIFIED_AFTER_SNAPSHOT":0}]}
```

聚合数字即可，无需原始码值/患者字段。回传后由 AI 归因：
迟到码候选 / 关联不一致候选 / 仅空 T 或删除原值 / 样本内无扫码记录——
四类处置见 SOURCE-REVIEW 表，不改生产 JOIN、不新增上报范围。
