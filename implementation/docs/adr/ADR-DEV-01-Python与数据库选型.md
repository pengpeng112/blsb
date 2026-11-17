# ADR-DEV-01：Python 3.14 + SQLAlchemy 双引擎（SQLite开发 / PostgreSQL生产）

日期：2026-09-30　状态：已接受

## 背景
设计默认 Python（建议验证3.12）+ PostgreSQL。本机仅 Python 3.14.4、无 PostgreSQL 实例；
要求"全新开发环境能一条文档命令启动，迁移可重复"。

## 决策
- 统一 SQLAlchemy 2.x ORM；开发/测试默认 SQLite(WAL, synchronous=FULL)；
  生产通过 `BLYB_DATABASE_URL` 指向 PostgreSQL，迁移同一套脚本。
- 任务队列租约：不用 PG 专属 `FOR UPDATE SKIP LOCKED`，改为
  短事务 UPDATE 竞争 + `lease_epoch` fencing（C03 测试验证语义等价）。
- 时间统一 UTC 朴素值，API 层输出 ISO；`size_bytes` 本地 Integer，PG 迁移时换 BigInteger。

## 后果
- 正：本地零依赖可跑全部测试；迁移可重复；生产换 PG 不改业务代码。
- 负：SQLite 并发写吞吐有限（单执行器设计缓解）；无 SKIP LOCKED 的等待语义不同
  （fencing 保证正确性，不保证公平）。生产前须在 PG 上回归 A/C/D 系测试（已列入矩阵）。
- Python 3.14 为本机实测版本；生产锁定版本待厂商SDK兼容验证（07 §2 原文要求）。
