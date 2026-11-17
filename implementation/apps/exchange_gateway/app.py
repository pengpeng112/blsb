"""交换网关：对外视图 + 文件下载 + 回执接收（05 §6、03 §7）。

采集器只读已发布行；文件按发布快照授权的固定资产版本映射，不映射“最新文件”。

R02/R04/R26 整改：
- 结构化视图/generation/文件/回执都先解析 scope→Destination(tenant, code)，
  跨租户scope不存在（404，不泄露存在性）；交换库查询带 tenant 维度。
- ACCEPTED 回执只代表传输受理，不推进 Submission CONFIRMED（R01/R02）。
- 下载证据在实际流结束/中断后记录，不在返回 StreamingResponse 前写 COMPLETE。
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from contracts import ApiError, new_uuid, ok
from contracts.api import ROLE_SERVICE, install_error_handlers, make_auth_dependency
from core_db.engine import make_engine, make_session_factory
from core_db.models import (Destination, DownloadEvidence, ReceiptEvent, Submission)
from destinations.view_publication.exchange_db import ExchangeDb
from storage_backend import parse_range_spec

import stat


def create_app(session_factory=None, engine=None, exchange_path: str | None = None) -> FastAPI:
    engine = engine or make_engine()
    sf = session_factory or make_session_factory(engine)
    exchange_path = exchange_path or os.environ.get(
        "BLYB_EXCHANGE_DB",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "var", "exchange.sqlite3"))
    ex = ExchangeDb(exchange_path)
    auth = make_auth_dependency(sf)
    app = FastAPI(title="blyb exchange-gateway", version="0.4.0", docs_url="/docs")
    install_error_handlers(app)

    def _resolve_destination(db, tenant_id: str, scope: str) -> Destination:
        """scope 必须解析为本租户已登记的 Destination；用户传参不代替授权（R04）。"""
        dest = (db.query(Destination)
                .filter_by(tenant_id=tenant_id, code=scope).first())
        if dest is None:
            raise ApiError("NOT_FOUND", "scope not found for this tenant")
        return dest

    @app.get("/health")
    def health():
        return ok({"status": "UP"}, "")

    @app.get("/exchange/v1/generations/current")
    def current_generation(scope: str = "first-hospital-exchange", ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            _resolve_destination(db, p.tenant_id, scope)
        gen = ex.current_generation(scope, tenant_id=p.tenant_id)
        if gen is None:
            return ok(None, ctx.request_id)
        return ok({"batch_id": gen["batch_id"],
                   "generation_id": gen["generation_id"],
                   "published_at": gen["published_at"],
                   "row_count": gen["row_count"],
                   "manifest_digest": gen["manifest_digest"]}, ctx.request_id)

    @app.get("/exchange/v1/datasets/{dataset}")
    def dataset_rows(dataset: str, scope: str = "first-hospital-exchange",
                     cursor: int = 0, limit: int = 100, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            _resolve_destination(db, p.tenant_id, scope)
        rows, next_cursor = ex.view_rows(dataset, scope, limit=min(200, limit),
                                         cursor=cursor, tenant_id=p.tenant_id)
        return ok({"dataset": dataset, "rows": rows, "next_cursor": next_cursor,
                   "has_more": next_cursor != 0}, ctx.request_id)

    @app.post("/exchange/v1/receipts")
    def post_receipt(body: dict, ctx=Depends(auth)):
        """采集器业务确认回执（区别于下载观察）。受理≠业务确认（R01）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_SERVICE,)
        with sf() as db:
            dest = _resolve_destination(db, p.tenant_id,
                                        body.get("scope", "first-hospital-exchange"))
            ext_id = body.get("receipt_id", "")
            existing = (db.query(ReceiptEvent)
                        .filter_by(tenant_id=p.tenant_id, destination_id=dest.id,
                                   external_receipt_id=ext_id).first())
            if existing is not None:
                return ok({"receipt_id": ext_id, "duplicate": True}, ctx.request_id)
            sub = None
            if body.get("submission_key"):
                sub = (db.query(Submission)
                       .filter_by(tenant_id=p.tenant_id, destination_id=dest.id,
                                  idempotency_key=body["submission_key"]).first())
            db.add(ReceiptEvent(id=new_uuid(), tenant_id=p.tenant_id,
                                destination_id=dest.id, external_receipt_id=ext_id,
                                submission_ref=sub.id if sub else None, raw_payload=body,
                                publication_ref=body.get("publication_id"),
                                parsed_result={"result": body.get("result"),
                                               "source": "exchange_collector"}))
            if sub is not None:
                if body.get("result") == "CONFIRMED":
                    # 网关不感知领域状态：发Outbox事件由worker统一落地
                    # （功能测试发现网关直改submission但病例分列不更新的不一致）
                    from core_db.models import OutboxEvent
                    db.add(OutboxEvent(
                        id=new_uuid(), tenant_id=p.tenant_id,
                        aggregate_ref=f"submission:{sub.id}",
                        event_type="submission.confirmed",
                        payload={"submission_id": sub.id,
                                 "receipt_id": ext_id}))
                elif body.get("result") == "ACCEPTED":
                    sub.state = "ACCEPTED"  # 传输受理，非业务成功
            db.commit()
            return ok({"receipt_id": ext_id}, ctx.request_id)

    @app.api_route("/exchange/v1/files/{route_id}", methods=["GET", "HEAD"])
    async def download_file(route_id: str, request: Request, ctx=Depends(auth),
                            range_header: str | None = Header(default=None, alias="Range")):
        p = ctx.require_principal()
        p.require_any_role(ROLE_SERVICE)
        route = ex.asset_route_by_id(route_id)
        if route is None:
            raise ApiError("NOT_FOUND", "file not published")
        # 跨租户路由：route 的发布批次必须属于本租户（R04）
        with sf() as db:
            from core_db.models import Asset, Blob
            asset = db.get(Asset, route["asset_id"])
            if asset is None or asset.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "file not published")
            # Blob 健康隔离：损坏字节对所有引用停止提供（R42）
            if not asset.blob_id:
                raise ApiError("NOT_FOUND", "asset has no blob")
            blob = db.get(Blob, asset.blob_id)
            if blob is None or blob.state != "AVAILABLE":
                raise ApiError("CONFLICT", "blob not healthy; download blocked")
        path = route["physical_key"]
        if not os.path.exists(path):
            raise ApiError("NOT_FOUND", "file not reachable")
        # symlink/越界防护：物理路径来自我方发布器，仅作存在性防御
        st = os.lstat(path)
        if stat.S_ISLNK(st.st_mode):
            raise ApiError("FORBIDDEN", "symlink rejected")
        total = route["size_bytes"]
        etag = f'"{route["sha256"]}"'
        if request.method == "HEAD":
            return Response(status_code=200, headers={
                "Content-Length": str(total), "ETag": etag,
                "Accept-Ranges": "bytes"})
        rng = None
        state = "NONE"
        if range_header:
            state, rng = parse_range_spec(range_header, total)
            if_range = request.headers.get("If-Range")
            if if_range and if_range != etag and state != "NONE":
                state, rng = "NONE", None  # R24：If-Range错配→完整200
        if state in ("UNSATISFIABLE", "INVALID"):
            _record(p, route_id, route, range_header, 0, "ABORTED")
            return Response(status_code=416, headers={
                "Content-Range": f"bytes */{total}", "ETag": etag})
        # MULTI：合法多段按设计忽略（03 §7 首版），回完整200

        async def streaming(gen_factory, length, result_ok):
            """实际流结束才写完成证据；异常/断开记录实际字节（R26/W08）。

            async生成器 + 每块后轮询 request.is_disconnected()：
            uvicorn对被取消的同步生成器不保证终结（实测遗弃不触发finally），
            断开以主动轮询为准，检出即 ABORTED+实际bytes 并停止读文件。
            """
            sent = 0
            recorded = False
            try:
                for b in gen_factory():
                    yield b
                    sent += len(b)
                    if await request.is_disconnected():
                        _record(p, route_id, route, range_header, sent, "ABORTED")
                        recorded = True
                        break
            except BaseException:  # noqa: BLE001
                _record(p, route_id, route, range_header, sent, "ABORTED")
                recorded = True
                raise
            finally:
                if not recorded:
                    _record(p, route_id, route, range_header, sent,
                            result_ok if sent == length else "ABORTED")

        if state != "SINGLE":
            def gen():
                with open(path, "rb") as f:
                    while True:
                        b = f.read(4 * 1024 * 1024)
                        if not b:
                            break
                        yield b
            return StreamingResponse(streaming(gen, total, "COMPLETE"),
                                      media_type=route["media_type"], headers={
                "Content-Length": str(total), "ETag": etag, "Accept-Ranges": "bytes"})
        start, end = rng
        length = end - start + 1

        def gen_range():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    b = f.read(min(4 * 1024 * 1024, remaining))
                    if not b:
                        break
                    remaining -= len(b)
                    yield b
        return StreamingResponse(streaming(gen_range, length, "PARTIAL"), status_code=206,
                                  media_type=route["media_type"], headers={
            "Content-Length": str(length), "ETag": etag, "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{total}"})

    def _record(p, route_id, route, range_header, n, result):
        with sf() as db:
            principal = f"{p.kind}:{p.subject_code}"
            db.add(DownloadEvidence(
                id=new_uuid(), tenant_id=p.tenant_id, asset_id=route["asset_id"],
                publication_item_id=route_id,
                principal=principal,
                range_header=range_header or "", bytes_sent=n, result=result))
            from core_db.models import AuditEvent
            db.add(AuditEvent(id=new_uuid(), tenant_id=p.tenant_id, actor=principal,
                              action="exchange.file.download",
                              target=f"route:{route_id}",
                              reason=f"bytes={n} result={result}"))
            db.commit()

    return app


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app
