"""Storage Service API（03 §4.2 请求序列 + 04 §5.1）。"""
from __future__ import annotations

import logging
import os
import threading

from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from contracts import ApiError, ok
from contracts.api import (ROLE_AUDITOR, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN,
                           ROLE_SERVICE, audit, install_error_handlers,
                           make_auth_dependency)
from core_db.engine import make_engine, make_session_factory, utcnow
from core_db.models import Job, Operation, StorageVolume, UploadChunk, UploadSession
from storage_backend import (VolumeHandle, atomic_write_chunk_async,
                             parse_range_spec, sha256_of_file)

from . import service
from .schemas import AcquisitionManifest, CreateUploadSession, RegisterVolume

log = logging.getLogger("storage_service")


def create_app(session_factory=None, engine=None, *, embedded_worker: bool = True) -> FastAPI:
    engine = engine or make_engine()
    sf = session_factory or make_session_factory(engine)
    auth = make_auth_dependency(sf)
    app = FastAPI(title="blyb storage-service", version="0.4.0", docs_url="/docs")
    install_error_handlers(app)
    # R36：跨源访问仅限显式允许列表（默认核心API同源地址）；推荐生产走同源反代。
    from starlette.middleware.cors import CORSMiddleware
    allowed = [o for o in os.environ.get(
        "BLYB_ALLOWED_ORIGINS",
        "http://127.0.0.1:8000,http://localhost:8000").split(",") if o]
    app.add_middleware(
        CORSMiddleware, allow_origins=allowed,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key",
                       "X-Chunk-SHA256", "X-Session-Epoch", "If-Match",
                       "If-Range", "Range"],
        expose_headers=["ETag", "Content-Range", "Accept-Ranges"],
        allow_credentials=False)

    # -------------------------------------------------- 健康与卷
    @app.get("/health")
    def health():
        with sf() as db:
            db.execute(text("SELECT 1"))
        return ok({"status": "UP"}, "")

    @app.post("/api/v1/storage/volumes")
    def register_volume(body: RegisterVolume, ctx=Depends(auth)):
        ctx.require_principal().require_any_role(ROLE_PLATFORM_ADMIN)
        root = os.path.abspath(body.root_ref)
        if not os.path.isdir(root):
            raise ApiError("VALIDATION_FAILED", f"root_ref is not a directory: {root}")
        with sf() as db:
            if db.query(StorageVolume).filter_by(tenant_id=ctx.principal.tenant_id,
                                                 name=body.name).first():
                raise ApiError("CONFLICT", "volume name exists")
            vol = StorageVolume(id=str(__import__("uuid").uuid4()),
                                tenant_id=ctx.principal.tenant_id, backend="filesystem",
                                name=body.name, root_ref=root,
                                capacity_bytes=body.capacity_bytes,
                                safety_margin_bytes=body.safety_margin_bytes,
                                state="ACTIVE", health="OK")
            db.add(vol)
            db.flush()
            handle = VolumeHandle(vol.id, root, body.safety_margin_bytes)
            handle.ensure_layout()
            handle.ensure_sentinel()  # W07：注册即写挂载哨兵
            db.commit()
            audit(db, ctx.principal, "storage.volume.register", f"volume:{vol.id}",
                  new_ref={"name": body.name, "root": root})
            db.commit()
            return ok({"volume_id": vol.id, "name": vol.name, "root_ref": root},
                      ctx.request_id)

    @app.get("/api/v1/storage/volumes")
    def list_volumes(ctx=Depends(auth)):
        with sf() as db:
            vols = db.query(StorageVolume).filter_by(tenant_id=ctx.principal.tenant_id).all()
            out = []
            for v in vols:
                h = VolumeHandle(v.id, v.root_ref, v.safety_margin_bytes)
                out.append({"id": v.id, "name": v.name, "state": v.state, "backend": v.backend,
                            "root_ref": v.root_ref, "reserved_bytes": v.reserved_bytes,
                            "free_bytes": h.free_bytes() if os.path.isdir(v.root_ref) else None,
                            "safety_margin_bytes": v.safety_margin_bytes})
            return ok(out, ctx.request_id)

    # -------------------------------------------------- 采集登记
    @app.post("/api/v1/acquisitions")
    async def register_acquisition(body: dict, ctx=Depends(auth),
                                   idempotency_key: str | None = Header(default=None)):
        p = ctx.require_principal()
        manifest = AcquisitionManifest.model_validate(body).model_dump()
        if manifest["agent_id"] and p.agent_id and manifest["agent_id"] != p.agent_id:
            raise ApiError("FORBIDDEN", "manifest agent_id does not match authenticated agent")
        with sf() as db:
            acq, created = service.register_acquisition(db, p.tenant_id, p.agent_id, manifest)
            db.commit()
            audit(db, p, "acquisition.register", f"acquisition:{acq.id}",
                  new_ref={"external_event_id": acq.external_event_id, "created": created})
            db.commit()
            from core_db.models import Asset
            rows = db.query(Asset).filter_by(acquisition_id=acq.id).all()
            by_client = {r.client_asset_id: r.id for r in rows}
            return ok({
                "acquisition_id": acq.id, "created": created,
                "manifest_digest": acq.manifest_digest,
                "assets": [{"client_asset_id": f["client_asset_id"],
                            "asset_id": by_client.get(f["client_asset_id"]),
                            "role": f["role"]} for f in manifest["files"]],
            }, ctx.request_id)

    # -------------------------------------------------- 上传会话
    @app.post("/api/v1/upload-sessions")
    def create_session(body: CreateUploadSession, ctx=Depends(auth),
                       idempotency_key: str = Header(default="")):
        p = ctx.require_principal()
        if not idempotency_key:
            raise ApiError("VALIDATION_FAILED", "Idempotency-Key header required")
        with sf() as db:
            s, created = service.create_upload_session(
                db, p.tenant_id, p.agent_id, body.acquisition_id, body.asset_id,
                body.size_bytes, body.sha256, body.chunk_size, idempotency_key,
                body.allow_empty)
            if s is None:
                # R16：资产已 AVAILABLE → 返回终态证明（含历史完成会话），不新建
                from core_db.models import UploadSession as _US
                done = (db.query(_US)
                        .filter_by(asset_id=body.asset_id,
                                   state="COMPLETED")
                        .order_by(_US.created_at.desc()).first())
                return ok({
                    "session_id": done.id if done else None,
                    "asset_id": body.asset_id, "created": False,
                    "state": "COMPLETED",
                    "note": "asset already AVAILABLE; terminal proof returned, "
                            "no new session created",
                }, ctx.request_id)
            done = [c.chunk_index for c in db.query(UploadChunk)
                    .filter_by(session_id=s.id).order_by(UploadChunk.chunk_index)]
            db.commit()
            return ok({
                "session_id": s.id, "asset_id": s.asset_id, "created": created,
                "chunk_size": s.chunk_size, "chunk_count": s.chunk_count,
                "expires_at": s.expires_at.isoformat(), "epoch": s.epoch,
                "state": s.state, "confirmed_chunk_indexes": done,
            }, ctx.request_id)

    @app.get("/api/v1/upload-sessions/{session_id}")
    def get_session(session_id: str, ctx=Depends(auth)):
        with sf() as db:
            s = db.get(UploadSession, session_id)
            if s is None or s.tenant_id != ctx.principal.tenant_id:
                raise ApiError("NOT_FOUND", "session not found")
            chunks = (db.query(UploadChunk).filter_by(session_id=s.id)
                      .order_by(UploadChunk.chunk_index).all())
            have = {c.chunk_index for c in chunks}
            return ok({
                "session_id": s.id, "state": s.state, "epoch": s.epoch,
                "asset_id": s.asset_id, "chunk_size": s.chunk_size,
                "chunk_count": s.chunk_count,
                "confirmed_chunk_indexes": sorted(have),
                "missing_chunk_indexes": [i for i in range(s.chunk_count) if i not in have],
                "expires_at": s.expires_at.isoformat(),
            }, ctx.request_id)

    @app.put("/api/v1/upload-sessions/{session_id}/chunks/{index}")
    async def put_chunk(session_id: str, index: int, request: Request, ctx=Depends(auth),
                        content_length: int = Header(default=-1),
                        x_chunk_sha256: str = Header(default=""),
                        x_session_epoch: int = Header(default=1)):
        p = ctx.require_principal()
        if not x_chunk_sha256:
            raise ApiError("VALIDATION_FAILED", "X-Chunk-SHA256 header required")
        with sf() as db:
            s = db.get(UploadSession, session_id)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "session not found")
            from contracts import UploadSessionState
            if s.state not in (UploadSessionState.CREATED, UploadSessionState.RECEIVING,
                               UploadSessionState.VERIFYING):
                if s.state == UploadSessionState.COMPLETED:
                    existing = (db.query(UploadChunk)
                                .filter_by(session_id=s.id, chunk_index=index).first())
                    if existing and existing.sha256 == x_chunk_sha256:
                        return Response(status_code=204)
                raise ApiError("CONFLICT", f"session state {s.state} rejects chunks")
            if x_session_epoch != s.epoch:
                raise ApiError("VERSION_CONFLICT", "stale session epoch")
            service.ensure_session_active(db, s)  # 410 + 释放预留
            expected_len = service.expected_chunk_len(s, index)
            if content_length != expected_len:
                raise ApiError("VALIDATION_FAILED",
                               f"Content-Length {content_length} != expected {expected_len}")
            existing = (db.query(UploadChunk)
                        .filter_by(session_id=s.id, chunk_index=index).first())
            if existing is not None:
                if existing.sha256 != x_chunk_sha256:
                    raise ApiError("CONFLICT", "chunk index confirmed with different hash")
                return Response(status_code=204)

            vol_row = db.get(StorageVolume, s.volume_id)
            vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
            # W07挂载丢失：哨兵缺失时拒绝写入（防卸载后同名目录顶替导致脑裂）
            if not vol.mount_ok():
                db.rollback()
                raise ApiError("VOLUME_OFFLINE",
                               "volume mount sentinel missing — write rejected")
            db.commit()
            # 流式接收（事务外），失败时不留 .tmp
            tmp_path = None
            try:
                tmp_path, (digest, size) = await atomic_write_chunk_async(
                    vol, s.id, index, request.stream())
            except Exception as exc:  # noqa: BLE001
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise ApiError("VALIDATION_FAILED", f"chunk write failed: {exc}") from exc
            if digest != x_chunk_sha256:
                os.remove(tmp_path)
                raise ApiError("CHUNK_HASH_MISMATCH",
                               f"chunk sha256 {digest} != header {x_chunk_sha256}")
            if size != expected_len:
                os.remove(tmp_path)
                raise ApiError("VALIDATION_FAILED",
                               f"chunk size {size} != expected {expected_len}")
            with sf() as db2:
                s2 = db2.get(UploadSession, s.id)
                service.commit_chunk(db2, s2, index, digest, size, tmp_path, vol)
                db2.commit()
            return Response(status_code=204)

    @app.post("/api/v1/upload-sessions/{session_id}/complete")
    def complete(session_id: str, ctx=Depends(auth), x_session_epoch: int = Header(default=1)):
        p = ctx.require_principal()
        with sf() as db:
            s = db.get(UploadSession, session_id)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "session not found")
            if x_session_epoch != s.epoch:
                raise ApiError("VERSION_CONFLICT", "stale session epoch")
            service.ensure_session_active(db, s)
            op = service.complete_session(db, s)
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"operation_id": op.id, "state": op.state}, ctx.request_id))

    @app.delete("/api/v1/upload-sessions/{session_id}")
    def cancel(session_id: str, ctx=Depends(auth), reason: str = Query(default="")):
        p = ctx.require_principal()
        import shutil
        with sf() as db:
            s = db.get(UploadSession, session_id)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "session not found")
            if s.state == "COMPLETED":
                raise ApiError("CONFLICT", "completed session cannot be cancelled")
            if s.state in ("ABORTED", "EXPIRED", "FAILED"):
                # R15：重复取消幂等返回，预留已在首次转换时释放，不得重复扣减
                return JSONResponse(status_code=202, content=ok(
                    {"state": s.state, "idempotent": True}, ctx.request_id))
            service.abort_session(db, s, reason=reason)
            db.commit()
            audit(db, p, "upload_session.cancel", f"session:{s.id}", reason=reason)
            db.commit()
            return JSONResponse(status_code=202, content=ok({"state": "ABORTED"}, ctx.request_id))

    # -------------------------------------------------- 操作
    @app.get("/api/v1/operations/{operation_id}")
    def get_operation(operation_id: str, ctx=Depends(auth)):
        with sf() as db:
            op = db.get(Operation, operation_id)
            if op is None or op.tenant_id != ctx.principal.tenant_id:
                raise ApiError("NOT_FOUND", "operation not found")
            return ok({"operation_id": op.id, "type": op.op_type, "state": op.state,
                       "subject_ref": op.subject_ref, "result": op.result,
                       "error": op.error}, ctx.request_id)

    # -------------------------------------------------- 下载
    @app.api_route("/api/v1/assets/{asset_id}/content", methods=["GET", "HEAD"])
    async def asset_content(asset_id: str, request: Request, ctx=Depends(auth),
                            range_header: str | None = Header(default=None, alias="Range")):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_AUDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            asset, blob, path = service.asset_content(db, p.tenant_id, asset_id)
            total = blob.size_bytes
            etag = f'"{blob.sha256}"'
            method = request.method

        def finish_record(bytes_sent: int, result: str):
            # 流式响应期间原会话已关闭；用新会话落证据（R26：流结束才写COMPLETE）
            with sf() as db2:
                if method == "GET":
                    service.record_download(db2, p.tenant_id, asset_id, None,
                                            f"{p.kind}:{p.subject_code}",
                                            range_header or "", bytes_sent, result)

        rng = None
        state = "NONE"
        if range_header:
            # R24：If-Range 错配（非当前强ETag）→ 忽略Range，返回完整200
            state, rng = parse_range_spec(range_header, total)
            if_range = request.headers.get("If-Range")
            if if_range and if_range != etag and state != "NONE":
                state, rng = "NONE", None
        if state in ("UNSATISFIABLE", "INVALID"):
            finish_record(0, "ABORTED")
            return Response(status_code=416, headers={
                "Content-Range": f"bytes */{total}", "ETag": etag})
        # MULTI：合法多段按设计忽略（03 §7 首版），回完整200
        if method == "HEAD":
            return Response(status_code=200, headers={
                "Content-Length": str(total), "ETag": etag, "Accept-Ranges": "bytes"})

        async def streaming(gen_factory, length, result_ok):
            """W08：async生成器 + 每块后轮询客户端断开。

            同步生成器在uvicorn取消时不保证终结（实测被遗弃不触发finally），
            断开检测以 request.is_disconnected() 为准：检出即记 ABORTED+实际bytes
            并停止继续读文件；正常耗尽/异常路径在finally统一落证。
            """
            sent = 0
            recorded = False
            try:
                for b in gen_factory():
                    yield b
                    sent += len(b)
                    if await request.is_disconnected():
                        finish_record(sent, "ABORTED")
                        recorded = True
                        break
            except BaseException:  # noqa: BLE001
                finish_record(sent, "ABORTED")
                recorded = True
                raise
            finally:
                if not recorded:
                    finish_record(sent, result_ok if sent == length else "ABORTED")

        if state != "SINGLE":
            def gen():
                with open(path, "rb") as f:
                    while True:
                        b = f.read(4 * 1024 * 1024)
                        if not b:
                            break
                        yield b
            return StreamingResponse(streaming(gen, total, "COMPLETE"),
                                      media_type=asset.media_type or
                                      "application/octet-stream", headers={
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
        return StreamingResponse(streaming(gen_range, length, "PARTIAL"),
                                  status_code=206,
                                  media_type=asset.media_type or
                                  "application/octet-stream", headers={
            "Content-Length": str(length), "ETag": etag, "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{total}"})

    @app.post("/api/v1/assets/{asset_id}/verify")
    def verify_asset(asset_id: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        from core_db.models import Asset
        from contracts import new_uuid
        with sf() as db:
            asset = db.get(Asset, asset_id)
            if asset is None or asset.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "asset not found")
            op = Operation(id=new_uuid(), tenant_id=p.tenant_id,
                           op_type="storage.verify_asset", state="PENDING",
                           subject_ref=asset_id)
            db.add(op)
            db.flush()
            db.add(Job(id=new_uuid(), tenant_id=p.tenant_id, job_type="storage.verify_asset",
                       dedup_key=f"{asset_id}:{asset.blob_id}", payload={
                           "asset_id": asset_id, "operation_id": op.id}))
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"operation_id": op.id}, ctx.request_id))

    # -------------------------------------------------- 存储迁移（T5：预览→执行）
    @app.post("/api/v1/storage/migrations")
    def storage_migrations(body: dict, ctx=Depends(auth)):
        """mode=preview 返回计划与可行性；mode=execute 建单一 Operation +
        每资产一个 Job（dedup=migrate:{asset}:{target_volume}，幂等），
        由 worker 执行同卷族拷贝+hash校验+replica位置+CAS释放预留；
        原 location 保留不动（孤儿/回收另立流程）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        mode = (body or {}).get("mode", "preview")
        asset_ids = (body or {}).get("asset_ids") or []
        target_volume_id = (body or {}).get("target_volume_id") or ""
        if mode not in ("preview", "execute") or not asset_ids \
                or not target_volume_id:
            raise ApiError("VALIDATION_FAILED",
                           "mode(preview|execute), asset_ids, target_volume_id"
                           " required")
        if not isinstance(asset_ids, list):
            raise ApiError("VALIDATION_FAILED", "asset_ids must be a list")
        from core_db.models import Asset
        from contracts import new_uuid as _uuid
        with sf() as db:
            plan = service.plan_migration(db, p.tenant_id, asset_ids,
                                          target_volume_id)
            if mode == "preview":
                return ok(plan, ctx.request_id)
            if not plan["feasible"]:
                # 507语义：低空间/哨兵缺失时执行被拒（计划随错误信封返回）
                code = "VOLUME_OFFLINE" if "sentinel" in plan["reason"] \
                    else "STORAGE_CAPACITY_LOW"
                raise ApiError(code, plan["reason"], details={"plan": plan})
            # 幂等结算：目标副本已 AVAILABLE 的资产计为已完成，不再建Job
            from core_db.models import AssetLocation, Asset as _Asset
            already = 0
            pending_items = []
            for item in plan["items"]:
                asset = db.get(_Asset, item["asset_id"])
                done = False
                if asset.blob_id:
                    loc_done = (db.query(AssetLocation)
                                .filter_by(blob_id=asset.blob_id,
                                           volume_id=target_volume_id,
                                           state="AVAILABLE").first())
                    done = loc_done is not None
                if done:
                    already += 1
                else:
                    pending_items.append(item)
            op = Operation(id=_uuid(), tenant_id=p.tenant_id,
                           op_type="storage.migrate_assets",
                           state="SUCCEEDED" if not pending_items else "RUNNING",
                           subject_ref=target_volume_id,
                           # 计划快照随 Operation 持久化：迁移的计划/进度/补偿
                           # 全部可查证（进度=Job计数结算、补偿=预留账本行、
                           # 计划=此处快照；worker 结算只增量改 migrated 键）
                           result={"total": len(plan["items"]),
                                   "migrated": already,
                                   "plan": plan})
            db.add(op)
            db.flush()
            from workflow_core.jobs import enqueue_job
            for item in pending_items:
                enqueue_job(db, p.tenant_id, "storage.migrate_asset",
                            f"migrate:{item['asset_id']}:{target_volume_id}",
                            {"asset_id": item["asset_id"],
                             "target_volume_id": target_volume_id,
                             "operation_id": op.id,
                             "multi_job_operation": True})
            audit(db, p, "storage.migration.create", f"operation:{op.id}",
                  new_ref={"assets": len(plan["items"]),
                           "already_migrated": already,
                           "target_volume": target_volume_id,
                           "total_bytes": plan["total_bytes"]})
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"operation_id": op.id, "assets": len(plan["items"]),
                 "already_migrated": already,
                 "state": op.state,
                 "total_bytes": plan["total_bytes"]}, ctx.request_id))

    try:
        recovered = service.recover_orphans(sf)
        if recovered:
            log.info("startup orphan chunk recovery: %d chunk(s)", recovered)
    except Exception:  # noqa: BLE001
        log.exception("startup orphan recovery failed (non-fatal)")
    # W07：启动即恢复未决提交意图 + 挂载丢失清扫（幂等，可重复）
    try:
        stats = service.recover_commit_intents(sf)
        if stats.get("resumed") or stats.get("retried") or stats.get("unrecoverable"):
            log.info("startup commit-intent recovery: %s", stats)
    except Exception:  # noqa: BLE001
        log.exception("startup commit-intent recovery failed (non-fatal)")
    try:
        with sf() as db:
            swept = service.sweep_lost_mounts(db)
        for e in swept:
            log.warning("volume health change: %s", e)
    except Exception:  # noqa: BLE001
        log.exception("startup mount sweep failed (non-fatal)")

    # -------------------------------------------------- 内嵌 worker（独立部署时可关）
    if embedded_worker:
        start_embedded_worker(app, sf)
    return app


def start_embedded_worker(app: FastAPI, sf, interval: float = 0.3):
    state = {"stop": False, "thread": None}

    def loop():
        import time
        from contracts import JobState
        from core_db.models import Operation, UploadSession
        while not state["stop"]:
            try:
                with sf() as db:
                    job = (db.query(Job)
                           .filter(Job.job_type.in_(["storage.verify_session", "storage.verify_asset"]),
                                   Job.state == JobState.PENDING,
                                   Job.available_at <= utcnow())
                           .order_by(Job.priority, Job.available_at).first())
                    if job is None:
                        time.sleep(interval)
                        continue
                    job.state = JobState.LEASED
                    job.lease_until = utcnow()
                    job.attempts += 1
                    job.lease_owner = f"storage-worker"
                    db.commit()
                    op = None
                    try:
                        if job.job_type == "storage.verify_session":
                            session = db.get(UploadSession, job.payload["session_id"])
                            result = service.verify_session(db, session)
                            op_result = result
                            err = {}
                        else:
                            from core_db.models import Asset, AssetLocation, Blob
                            asset = db.get(Asset, job.payload["asset_id"])
                            loc = (db.query(AssetLocation)
                                   .filter_by(blob_id=asset.blob_id, state="AVAILABLE").first())
                            digest, size = sha256_of_file(loc.physical_key)
                            blob = db.get(Blob, asset.blob_id)
                            if digest != blob.sha256 or size != blob.size_bytes:
                                asset.state = "QUARANTINED"
                                blob.state = "QUARANTINED"
                                db.commit()
                                raise ApiError("FILE_HASH_MISMATCH",
                                               f"verify failed for asset {asset.id}")
                            op_result = {"asset_id": asset.id, "sha256": digest, "verified": True}
                            err = {}
                        op = db.get(Operation, job.payload.get("operation_id"))
                        if op is not None:
                            op.state = "SUCCEEDED"
                            op.result = op_result
                        job.state = JobState.SUCCEEDED
                        job.finished_at = utcnow()
                        db.commit()
                    except Exception as exc:  # noqa: BLE001
                        db.rollback()
                        log.exception("storage job failed: %s", job.id)
                        with sf() as db2:
                            j2 = db2.get(Job, job.id)
                            j2.state = JobState.RETRY_WAIT
                            j2.available_at = utcnow()
                            j2.last_error = {"message": str(exc)[:500]}
                            op2 = db2.get(Operation, job.payload.get("operation_id"))
                            if op2 is not None and j2.attempts >= j2.max_attempts:
                                op2.state = "FAILED"
                                op2.error = {"message": str(exc)[:500]}
                                j2.state = JobState.FAILED
                            db2.commit()
            except Exception:  # noqa: BLE001
                import time
                log.exception("storage worker loop error")
                time.sleep(1)

    t = threading.Thread(target=loop, daemon=True, name="storage-verify-worker")
    t.start()
    app.state.storage_worker = state

    @app.on_event("shutdown")
    def _stop():
        state["stop"] = True


app = None


def get_app():
    global app
    if app is None:
        # W14：BLYB_STORAGE_EMBEDDED_WORKER=0 时独立部署形态（compose默认），
        # worker 由专门服务承担，storage 进程不再内嵌执行器（R43单执行器语义）。
        embedded = os.environ.get("BLYB_STORAGE_EMBEDDED_WORKER", "1") != "0"
        app = create_app(embedded_worker=embedded)
    return app
