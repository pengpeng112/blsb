"""进程内令牌桶限速（LT-03）：每个 destination_id 一桶。

注意：**跨进程限速未实现**——单 worker 部署下有效；多 worker 需 LT-04 后续增强
（例如复用 alihealth rate_control 的共享文件预算方案）。
"""
from __future__ import annotations

import threading
import time

_BUCKETS: dict[str, "_TokenBucket"] = {}
_LOCK = threading.Lock()


class _TokenBucket:
    def __init__(self, per_minute: int):
        self.capacity = max(1, int(per_minute))
        self.tokens = float(self.capacity)
        self.rate = self.capacity / 60.0     # tokens per second
        self.updated = time.monotonic()

    def take_delay(self, sleeper) -> None:
        """取一枚令牌；不足时按需等待（经注入的 sleeper，可测试）。"""
        while True:
            with _LOCK:
                now = time.monotonic()
                self.tokens = min(self.capacity,
                                  self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                wait = (1 - self.tokens) / self.rate
            sleeper(max(wait, 0.001))


def bucket_for(destination_id: str, per_minute: int) -> "_TokenBucket":
    with _LOCK:
        b = _BUCKETS.get(destination_id)
        if b is None or b.capacity != int(per_minute):
            b = _TokenBucket(per_minute)
            _BUCKETS[destination_id] = b
        return b
