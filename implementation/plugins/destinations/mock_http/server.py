"""模拟医保平台（Mock destination server）——明确非真实平台。

提供 05 §7 的全部本地Mock场景：同步成功、异步受理、部分拒绝、超时但受理、
重复回执、乱序回执、格式不支持、长期处理中、无回执能力。
测试用 /exchange-mock/admin/* 控制行为与回执注入。
"""
from __future__ import annotations

import threading
import time
import uuid

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

STATE: dict = {"submissions": {}, "receipts": [], "receipt_seq": 0,
               "default_behavior": "sync_success", "lock": threading.RLock()}


def create_app() -> FastAPI:
    app = FastAPI(title="MOCK insurance platform (not real)", version="0.4.0")

    @app.get("/health")
    def health():
        # 探活端点（run_dev/运维对其宣告为独立服务；此前缺失导致8090无法健康检查）
        return {"data": {"status": "UP", "service": "mock-platform",
                         "note": "MOCK，非真实医保"}, "request_id": ""}

    def _receipt(sub_key: str, result: str, delay_seq: int | None = None):
        with STATE["lock"]:
            STATE["receipt_seq"] += 1
            seq = delay_seq if delay_seq is not None else STATE["receipt_seq"]
            r = {"receipt_id": f"RCPT-{seq:06d}", "submission_key": sub_key,
                 "result": result, "occurred_at": _ts(), "seq": seq}
            STATE["receipts"].append(r)
            return r

    @app.post("/exchange-mock/receive")
    def receive(payload: dict, x_idempotency_key: str = Header(default=""),
                x_mock_behavior: str = Header(default="")):
        key = x_idempotency_key or payload.get("submission_key", str(uuid.uuid4()))
        behavior = x_mock_behavior or STATE["default_behavior"]
        with STATE["lock"]:
            if key in STATE["submissions"]:
                prev = STATE["submissions"][key]
                return JSONResponse(status_code=prev["http_status"], content={
                    "receipt_id": prev.get("receipt_id"), "submission_id": key,
                    "duplicate": True})
        if behavior == "format_unsupported":
            with STATE["lock"]:
                STATE["submissions"][key] = {"http_status": 406}
            raise HTTPException(406, "format unsupported by mock platform")
        if behavior == "timeout_accepted":
            # 服务端先受理再故意超时不回包（客户端视角UNKNOWN；恢复后可查询）
            with STATE["lock"]:
                STATE["submissions"][key] = {"http_status": 200, "accepted": True}
                _receipt(key, "ACCEPTED")
            time.sleep(30)
            return {"submission_id": key}
        if behavior == "partial_reject":
            items = payload.get("items", [])
            accepted = [i for i in items if i.get("item_key", "k") != "bad-item"]
            rejected = [i for i in items if i.get("item_key") == "bad-item"]
            with STATE["lock"]:
                rec = {"http_status": 207, "receipt_id": f"RCPT-P{len(STATE['submissions'])}"}
                STATE["submissions"][key] = rec
                _receipt(key, "PARTIAL")
            return JSONResponse(status_code=207, content={
                "submission_id": key, "receipt_id": rec["receipt_id"],
                "accepted": len(accepted), "rejected": len(rejected)})
        if behavior == "async_pending":
            with STATE["lock"]:
                STATE["submissions"][key] = {"http_status": 202, "accepted": False}
            return JSONResponse(status_code=202, content={"submission_id": key})
        if behavior == "long_processing":
            with STATE["lock"]:
                STATE["submissions"][key] = {"http_status": 202, "accepted": None,
                                              "processing": True}
            return JSONResponse(status_code=202, content={"submission_id": key})
        if behavior == "no_receipt":
            with STATE["lock"]:
                STATE["submissions"][key] = {"http_status": 200}
            return JSONResponse(status_code=200, content={
                "submission_id": key,
                "note": "accepted but this mock never issues receipts"})
        # ---------------- LT-03 inf_frontend 行为档（传染病前置机语义） ----------------
        # 请求体 JSON 含 __mock_code（仅测试用）时按其值返回；不带按 "00"。
        # __mock_fail_times: 按 idempotency key 计数，前 N 次返回服务端异常(01)，
        # 第 N+1 次返回成功——用于退避重试测试。不改既有行为档输出。
        if isinstance(payload, dict) and ("__mock_code" in payload or
                                          "__mock_fail_times" in payload):
            code = str(payload.get("__mock_code", "00"))
            fail_times = int(payload.get("__mock_fail_times", 0) or 0)
            with STATE["lock"]:
                fails = STATE.setdefault("inf_fail_counts", {})
                seen = fails.get(key, 0)
                if fail_times > 0 and seen < fail_times:
                    fails[key] = seen + 1
                    code = "01"
                elif fail_times > 0:
                    code = "00"
            if code == "timeout":
                time.sleep(30)
                return {"submission_id": key}
            if code == "500":
                raise HTTPException(500, "mock internal error")
            if code == "429":
                return JSONResponse(status_code=429, headers={
                    "Retry-After": "3"}, content={"desc": "mock rate limited"})
            if code == "notjson":
                from fastapi import Response as _Resp
                return _Resp(content="This is not JSON at all",
                             media_type="text/plain")
            ok_code = code == "00"
            ec = "00" if ok_code else code
            descs = {"00": "成功", "01": "服务端异常", "99": "服务端异常",
                     "02": "必填项缺失"}
            body_resp = {"result": ok_code, "errorCode": ec,
                         "desc": descs.get(code, "mock")}
            with STATE["lock"]:
                STATE["submissions"][key] = {"http_status": 200,
                                             "inf_error_code": ec}
            return body_resp
        # sync_success / duplicate_receipt / out_of_order_receipt 首包相同
        with STATE["lock"]:
            r = _receipt(key, "ACCEPTED")
            STATE["submissions"][key] = {"http_status": 200,
                                         "receipt_id": r["receipt_id"], "accepted": True}
        return {"submission_id": key, "receipt_id": r["receipt_id"]}

    @app.get("/exchange-mock/submissions/{key}")
    def get_submission(key: str):
        with STATE["lock"]:
            s = STATE["submissions"].get(key)
        if s is None:
            raise HTTPException(404, "not found")
        return {"data": {"submission_id": key, **s}}

    @app.get("/exchange-mock/receipts")
    def receipts(since: str = ""):
        with STATE["lock"]:
            rs = [r for r in STATE["receipts"] if r["occurred_at"] > (since or "")]
        return {"data": {"receipts": rs}}

    # ---------------- 测试控制 ----------------
    @app.post("/exchange-mock/admin/behavior/{behavior}")
    def set_behavior(behavior: str):
        STATE["default_behavior"] = behavior
        return {"data": {"behavior": behavior}}

    @app.post("/exchange-mock/admin/receipts/emit")
    def emit_receipt(submission_key: str, result: str = "CONFIRMED", seq: int | None = None):
        r = _receipt(submission_key, result, delay_seq=seq)
        return {"data": r}

    @app.post("/exchange-mock/admin/async/complete")
    def complete_async(submission_key: str, result: str = "ACCEPTED"):
        with STATE["lock"]:
            s = STATE["submissions"].get(submission_key)
            if s is None:
                raise HTTPException(404, "submission not found")
            s["accepted"] = True
        r = _receipt(submission_key, result)
        return {"data": r}

    @app.post("/exchange-mock/admin/reset")
    def reset():
        with STATE["lock"]:
            STATE["submissions"].clear()
            STATE["receipts"].clear()
            STATE["receipt_seq"] = 0
        return {"data": {"reset": True}}

    return app


def _ts() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
