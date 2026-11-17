# ADR-DEV-02：单Worker执行器 + 可选内嵌storage worker

日期：2026-09-30　状态：已接受

## 背景
storage_service 设计为可独立部署（自带校验worker）；run_dev 与测试中也运行通用 worker
引擎（处理 storage.verify_* 与业务任务）。实测双执行器在 SQLite 上出现会话暂存目录
竞争（一次 verify 成功清理分片后另一执行器重复执行导致 FileNotFoundError，资产误标
QUARANTINED）。

## 决策
- 一个部署实例只启用一个任务执行器：`run_dev.py` 与测试装配 storage 时
  `embedded_worker=False`，统一由 WorkerEngine 处理全部 job 类型
  （`storage.verify_session/verify_asset` 已注册进引擎 handler 表）。
- 独立部署形态仍可只起 storage_service（`embedded_worker=True` 默认）承载校验。

## 后果
- 正：消除双写竞争；执行语义确定（测试可同步 tick）。
- 负：单执行器吞吐上限（本地MVP可接受）；多worker横向扩展需先在 PG 上引入
  SKIP LOCKED 与分区租约（生产前整改项）。

## 补充（2026-09-30 核查修订）
E2E 测试最初保留 storage 内嵌 worker（双执行器）碰巧可用；改为 `embedded_worker=False`
后暴露真实依赖：Agent 上传期间的 `storage.verify_*` 任务必须有执行器在跑。
修正为测试夹具内以**后台 ticker 线程驱动同一 WorkerEngine**（仍单执行器），37s 通过。
结论不变：任何部署/测试形态都必须恰好有一个活跃执行器，且在 Agent 活动期间保持运行。
