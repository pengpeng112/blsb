# ADR-DEV-04：性能测试测服务子进程RSS；定位TestClient内存失真

日期：2026-09-30　状态：已接受

## 背景
A01 要求"流式内存稳定"。初版用 starlette TestClient 压 600MB：RSS 线性涨到 ~690MB。
排查（tracemalloc + ASGITransport 对照 + 子进程采样）确认：
1. 应用（服务端）无泄漏：ASGITransport 直连 30 片内存平稳，tracemalloc 堆顶仅当前分片。
2. starlette TestClient 门户保留请求体 → 客户端侧测量失真。
3. httpx 客户端请求对象存在循环引用，大对象不触发 GC 代际阈值 → 单文件长传输客户端堆增长。
4. Windows 工作集含大块瞬态且不回落，不能代表应用内存。

## 决策
- A01 以**独立子进程真实 uvicorn + 监控线程每秒采样服务进程RSS**为准
  （`tests/performance/_server_proc.py`）。
- Agent 传输循环每 16 片 `gc.collect()` 缓解客户端循环引用。
- 结论：600MB 文件服务进程峰值 RSS ≈ 95MB（见测试执行记录）。

## 后果
- 测量可信并可复现；客户端GC策略为工程缓解，若上游修复可移除。
