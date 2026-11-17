# ADR-DEV-06：轮询链代次dedup + 请求体模型必须模块级

日期：2026-09-30　状态：已接受（深度核查轮）

## 背景

深度核查专项测试（H-06/H-07/H-09/H-12）暴露两个结构性坑：

1. **回执轮询链断裂**：`_j_poll_receipts` 无回执时重排自身。若复用原 dedup 键
   （`submission_id`），`enqueue_job` 的去重对 PENDING/LEASED/SUCCEEDED 均提前返回
   原行——payload（含 `poll_count`）不更新；且当前作业执行完会被通用 runner 的
   `finish_job` 置 SUCCEEDED——"重排"的那一行就是被完结的那一行，轮询第一轮即停。
2. **core_api 五个 POST 端点全数 422**：`app.py` 顶部 `from __future__ import annotations`
   使端点注解成为字符串；定义在 `create_app` 内的局部 Pydantic 模型无法被 FastAPI
   从模块全局解析，参数被静默降级为 query 参数。既有测试全部直写 DB 或用 `body: dict`
   端点，未覆盖到这批接口——缺陷长期潜伏。

## 决策

1. **周期性自续作业一律以"业务键#代次"作 dedup 开新行**：
   `destination.poll_receipts` 用 `{submission_id}#poll{N}`（N=poll_count 递增）。
   新行天然 PENDING、携带新 payload；旧行正常 SUCCEEDED 作为历史。同模式适用于
   未来任何"执行完再排下一轮"的作业（当前仅回执轮询）。副作用：每轮留一行
   SUCCEEDED（60s 封顶下 ≤1440 行/天/提交），生产前可加清理作业。
2. **请求体模型只允许定义在模块级**（core_api 及未来任何 FastAPI 应用模块）。
   已在 `apps/core_api/app.py` 模型定义处加注释说明原因；新增端点 code review
   时检查此项。

## 后果

- 正：轮询链在真实 tick 流程下可续（H-09 两轮断言：poll_count 递增、退避 1s→2s）；
  五个 POST 端点恢复可用（H-06/H-07/H-12 直接验证）。
- 负：轮询历史行累积（见上，可清理）；局部模型限制了对 create_app 闭包的可用性
  （模型本就无需闭包状态，无实际损失）。
