"""HTTP Mock 目的地客户端（05 §7 本地Mock场景全覆盖）。

行为由 destination 配置选择；Mock 成功不得打开真实生产Profile。
"""
from __future__ import annotations

import httpx

BEHAVIORS = ["sync_success", "async_pending", "timeout_accepted", "partial_reject",
             "duplicate_receipt", "out_of_order_receipt", "format_unsupported",
             "long_processing", "no_receipt"]


class MockHttpClient:
    plugin_id = "destination.mock_http"

    def __init__(self, base_url: str, behavior: str = "sync_success", *,
                 timeout_seconds: float = 10.0, token: str = "mock-token"):
        if behavior not in BEHAVIORS:
            raise ValueError(f"unknown mock behavior {behavior}")
        self.base_url = base_url.rstrip("/")
        self.behavior = behavior
        self.timeout = timeout_seconds
        self.token = token

    def submit(self, payload: dict, idempotency_key: str) -> dict:
        """返回 {outcome, http_status, remote_ref, retryable}。outcome ∈
        ACCEPTED / REJECTED / PARTIAL / UNKNOWN / UNSUPPORTED / TIMEOUT_ACCEPTED"""
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/exchange-mock/receive",
                    json=payload,
                    headers={"X-Idempotency-Key": idempotency_key,
                             "Authorization": f"Bearer {self.token}",
                             "X-Mock-Behavior": self.behavior})
        except httpx.TimeoutException:
            # 超时但对方可能已受理 → UNKNOWN（D01：不新建重复提交，走查询）
            return {"outcome": "UNKNOWN", "http_status": None, "remote_ref": None,
                    "retryable": True}
        except httpx.TransportError as exc:
            return {"outcome": "UNKNOWN", "http_status": None, "remote_ref": None,
                    "retryable": True, "error": str(exc)}
        return self._interpret(resp)

    def query(self, idempotency_key: str) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/exchange-mock/submissions/{idempotency_key}",
                              headers={"Authorization": f"Bearer {self.token}"})
            if resp.status_code == 404:
                return {"found": False}
            resp.raise_for_status()
            return {"found": True, **resp.json()["data"]}

    def poll_receipts(self, since: str | None = None) -> list[dict]:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(f"{self.base_url}/exchange-mock/receipts",
                              params={"since": since or ""},
                              headers={"Authorization": f"Bearer {self.token}"})
            resp.raise_for_status()
            return resp.json()["data"]["receipts"]

    def _interpret(self, resp: httpx.Response) -> dict:
        code = resp.status_code
        try:
            body = resp.json()
        except ValueError:
            body = {}
        remote_ref = body.get("receipt_id") or body.get("submission_id")
        if code == 200 or code == 201:
            return {"outcome": "ACCEPTED", "http_status": code,
                    "remote_ref": remote_ref, "retryable": False}
        if code == 202:
            return {"outcome": "ACCEPTED", "http_status": code,
                    "remote_ref": remote_ref, "retryable": False,
                    "async": True}
        if code == 406:
            return {"outcome": "UNSUPPORTED", "http_status": code,
                    "remote_ref": remote_ref, "retryable": False}
        if code == 207:
            return {"outcome": "PARTIAL", "http_status": code,
                    "remote_ref": remote_ref, "retryable": False}
        if code >= 500:
            return {"outcome": "UNKNOWN", "http_status": code,
                    "remote_ref": remote_ref, "retryable": True}
        return {"outcome": "REJECTED", "http_status": code,
                "remote_ref": remote_ref, "retryable": False,
                "error_code": body.get("error", {}).get("code")}
