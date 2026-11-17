"""Storage Service 业务逻辑：采集登记、会话、分片、校验合并、恢复、下载。

实现 03 §4—§7：固定分片、幂等ACK、提交意图、崩溃恢复、卷配额、Range。
"""
from __future__ import annotations

import logging
import os
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from contracts import (ApiError, AssetState, BlobState, JobState, OperationState,
                       UploadSessionState, new_uuid)
from contracts.api import digest_of
from core_db.engine import utcnow
from core_db.models import (Acquisition, AlertEvent, Asset, AssetBundle, AssetLocation,
                            AssetReference, Blob, BundleMember, Job, Operation,
                            OutboxEvent, SourceSystem, StorageCommitIntent,
                            StorageReservation, StorageVolume, UploadChunk,
                            UploadSession, DownloadEvidence)
from storage_backend import PathPolicy, VolumeHandle, parse_range, sha256_of_file
import storage_backend  # 经模块属性调用可注入的崩溃点（kill/观察测试依赖）

log = logging.getLogger("storage_service")

DEFAULT_CHUNK_SIZE = 64 * 1024 * 1024
SESSION_TTL = timedelta(hours=24)


def _crash_point(name: str) -> None:
    """测试崩溃注入点（W07 kill矩阵）：环境变量 BLYB_TEST_CRASH_AT 命中即硬退出。

    os._exit 绕过一切清理，等价进程被 kill -9；生产不设置该变量即无操作。
    """
    import os
    if os.environ.get("BLYB_TEST_CRASH_AT", "") == name:
        log.warning("BLYB_TEST_CRASH_AT=%s hit — hard exit for crash matrix", name)
        os._exit(137)


def reserve_for_session(db: Session, vol_row: StorageVolume, session: UploadSession,
                        need: int, handle: VolumeHandle) -> None:
    """原子预留（W07）：账本行 + 卷 reserved_bytes 递增同一事务。

    超额判定：capacity_bytes>0 时由条件UPDATE在库内仲裁；磁盘可用空间
    （free - safety_margin）为入口快检与事后校验，超限回滚。
    """
    free = handle.free_bytes()
    usable_free = free - vol_row.safety_margin_bytes
    cap = vol_row.capacity_bytes or 0
    stmt = ("UPDATE storage_volume SET reserved_bytes = reserved_bytes + :need "
            "WHERE id = :vid")
    params: dict = {"need": need, "vid": vol_row.id}
    if cap > 0:
        stmt += " AND reserved_bytes + :need <= :cap"
        params["cap"] = cap
    res = db.execute(text(stmt), params)
    if res.rowcount != 1:
        raise ApiError("STORAGE_CAPACITY_LOW",
                       f"need reserve {need} bytes exceeds capacity {cap}")
    # 读同事务内UPDATE后的真实值（不经身份映射缓存）
    row_after = db.execute(text(
        "SELECT reserved_bytes FROM storage_volume WHERE id = :i"),
        {"i": vol_row.id}).fetchone()
    if row_after is not None and row_after[0] > usable_free:
        db.rollback()
        raise ApiError("STORAGE_CAPACITY_LOW",
                       f"need reserve {need} bytes, usable {usable_free}")
    db.add(StorageReservation(
        id=new_uuid(), tenant_id=session.tenant_id, volume_id=vol_row.id,
        session_id=session.id, bytes_amount=need, state="ACTIVE"))


def release_reservation(db: Session, session_id: str, reason: str) -> bool:
    """CAS 释放（W07）：账本行 ACTIVE→RELEASED 条件UPDATE，影响行=1 才扣减。

    返回 True=本次释放；False=已释放过（幂等，重复 cancel/expire/fail/complete
    与恢复流程并发时恰好释放一次）。
    """
    res = db.execute(text(
        "UPDATE storage_reservation SET state = 'RELEASED', reason = :r, "
        "released_at = :t WHERE session_id = :s AND state = 'ACTIVE'"),
        {"r": reason, "t": utcnow(), "s": session_id})
    if res.rowcount != 1:
        return False
    row = (db.query(StorageReservation)
           .filter_by(session_id=session_id).first())
    if row is not None and row.volume_id:
        db.execute(text(
            "UPDATE storage_volume SET reserved_bytes = "
            "CASE WHEN reserved_bytes - :b < 0 THEN 0 ELSE reserved_bytes - :b END "
            "WHERE id = :v"), {"b": row.bytes_amount, "v": row.volume_id})
    return True


def get_volume(db: Session, tenant_id: str) -> StorageVolume:
    vol = (db.query(StorageVolume)
           .filter_by(tenant_id=tenant_id, state="ACTIVE", backend="filesystem")
           .order_by(StorageVolume.created_at).first())
    if vol is None:
        raise ApiError("VOLUME_OFFLINE", "no ACTIVE storage volume registered")
    return vol


def register_acquisition(db: Session, tenant_id: str, agent_id: str | None,
                         manifest: dict) -> tuple[Acquisition, bool]:
    """登记清单；重复事件返回同一资源（幂等）。返回 (acquisition, created)。"""
    src = (db.query(SourceSystem)
           .filter_by(tenant_id=tenant_id, code=manifest["source_system_code"]).first())
    if src is None:
        raise ApiError("NOT_FOUND", f"source_system {manifest['source_system_code']} not registered")
    existing = (db.query(Acquisition)
                .filter_by(tenant_id=tenant_id, source_system_id=src.id,
                           external_event_id=manifest["event_id"],
                           source_revision=manifest.get("source_revision", "1")).first())
    mdigest = digest_of(manifest)
    if existing is not None:
        if existing.manifest_digest != mdigest:
            raise ApiError("IDEMPOTENCY_CONFLICT",
                           "same acquisition event with different manifest content")
        return existing, False

    acq = Acquisition(
        id=new_uuid(), tenant_id=tenant_id, agent_id=agent_id, source_system_id=src.id,
        external_event_id=manifest["event_id"],
        source_revision=manifest.get("source_revision", "1"),
        source_scan_id=manifest.get("source_scan_id", ""),
        manifest=manifest, manifest_digest=mdigest,
        completion_evidence=manifest.get("completion_evidence", {}),
        state="REGISTERED")
    db.add(acq)
    db.flush()

    seen_case_keys: set[str] = set()
    seen_client_ids: set[str] = set()
    bundle = AssetBundle(id=new_uuid(), tenant_id=tenant_id, acquisition_id=acq.id,
                         revision=1, manifest_digest=mdigest, state="PENDING")
    db.add(bundle)
    db.flush()
    for f in manifest["files"]:
        if f["client_asset_id"] in seen_client_ids:
            raise ApiError("VALIDATION_FAILED", "duplicate client_asset_id in manifest")
        seen_client_ids.add(f["client_asset_id"])
        try:
            normalized = PathPolicy.normalize(f["relative_path"])
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED", str(exc)) from exc
        case_key = PathPolicy.case_key(normalized)
        if case_key in seen_case_keys:
            raise ApiError("VALIDATION_FAILED",
                           f"relative path case collision: {f['relative_path']}")
        seen_case_keys.add(case_key)
        asset = Asset(
            id=new_uuid(), tenant_id=tenant_id, acquisition_id=acq.id,
            client_asset_id=f["client_asset_id"], role=f.get("role", "primary"),
            original_name=normalized.split("/")[-1], relative_path=f["relative_path"],
            media_type=f.get("media_type", "application/octet-stream"),
            format_hint=f.get("format_hint", ""),
            expected_size=f["size_bytes"], expected_sha256=f["sha256"],
            state=AssetState.STAGING)
        db.add(asset)
        db.flush()
        db.add(BundleMember(id=new_uuid(), tenant_id=tenant_id, bundle_id=bundle.id,
                            asset_id=asset.id, relative_path=f["relative_path"],
                            relative_path_normalized=normalized,
                            role=f.get("role", "primary")))
    db.flush()
    return acq, True


def create_upload_session(db: Session, tenant_id: str, agent_id: str | None,
                          acquisition_id: str, asset_id: str, size_bytes: int,
                          sha256: str, chunk_size: int | None, idem_key: str,
                          allow_empty: bool = False) -> tuple[UploadSession | None, bool]:
    """返回 (session, created)。

    R16：会话幂等与资产终态证明的顺序——
    1. 同 Idempotency-Key 先恢复既有合法结果（含其当前状态）；
    2. 资产已 AVAILABLE（终态）→ 返回 (None, False)，调用方以终态证明应答
       （ACK 丢失/队列重建后可对账恢复，不要求重传旧会话）；
    3. 其余才创建新会话。
    """
    asset = db.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "asset not found")
    if asset.acquisition_id != acquisition_id:
        raise ApiError("VALIDATION_FAILED", "asset does not belong to acquisition")
    if asset.expected_sha256 and asset.expected_sha256 != sha256:
        raise ApiError("IDEMPOTENCY_CONFLICT", "sha256 differs from registered manifest")
    if asset.expected_size and asset.expected_size != size_bytes:
        raise ApiError("IDEMPOTENCY_CONFLICT", "size differs from registered manifest")
    existing = (db.query(UploadSession)
                .filter_by(tenant_id=tenant_id, idempotency_key=idem_key).first())
    if existing is not None:
        if (existing.asset_id != asset_id or existing.expected_size != size_bytes
                or existing.expected_sha256 != sha256):
            raise ApiError("IDEMPOTENCY_CONFLICT",
                           "idempotency key bound to different session content")
        return existing, False
    if asset.state in (AssetState.AVAILABLE,):
        # 终态证明：资产已可用，重放请求不新建会话（R16）
        return None, False
    if size_bytes == 0 and not allow_empty:
        raise ApiError("VALIDATION_FAILED", "empty file rejected unless allow_empty")

    vol = get_volume(db, tenant_id)
    handle = VolumeHandle(vol.id, vol.root_ref, vol.safety_margin_bytes)
    # W07挂载丢失探测：根存在但哨兵缺失=卸载后被同名目录顶替，拒绝新写入
    if not handle.mount_ok():
        raise ApiError("VOLUME_OFFLINE",
                       "volume mount sentinel missing — mount lost or volume "
                       "root recreated; run sweep / remount before writing")
    handle.ensure_layout()
    # 峰值占用：分片合计 + 合并副本（03 §5）；账本CAS预留（W07）
    need = 2 * size_bytes
    cs = chunk_size or DEFAULT_CHUNK_SIZE
    count = max(1, (size_bytes + cs - 1) // cs)
    session = UploadSession(
        id=new_uuid(), tenant_id=tenant_id, agent_id=agent_id,
        acquisition_id=acquisition_id, asset_id=asset_id, volume_id=vol.id,
        expected_size=size_bytes, expected_sha256=sha256,
        chunk_size=cs, chunk_count=count,
        expires_at=utcnow() + SESSION_TTL, epoch=1,
        state=UploadSessionState.CREATED, idempotency_key=idem_key)
    db.add(session)
    db.flush()
    reserve_for_session(db, vol, session, need, handle)
    return session, True


def _expire_session(db: Session, session: UploadSession) -> None:
    """会话过期：置EXPIRED、CAS释放卷预留、清理暂存目录（03 §4.1 410语义）。"""
    import shutil
    session.state = UploadSessionState.EXPIRED
    vol_row = db.get(StorageVolume, session.volume_id)
    release_reservation(db, session.id, "EXPIRE")
    db.flush()
    if vol_row is not None:
        handle = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
        shutil.rmtree(handle.session_dir(session.id), ignore_errors=True)


def abort_session(db: Session, session: UploadSession, reason: str = "") -> None:
    """取消：置ABORTED、CAS释放预留（恰好一次）、清理暂存（W07账本统一入口）。"""
    import shutil
    session.state = UploadSessionState.ABORTED
    vol_row = db.get(StorageVolume, session.volume_id)
    release_reservation(db, session.id, "CANCEL")
    if vol_row is not None:
        handle = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
        shutil.rmtree(handle.session_dir(session.id), ignore_errors=True)


def ensure_session_active(db: Session, session: UploadSession) -> None:
    if session.expires_at is not None and session.expires_at < utcnow():
        if session.state not in (UploadSessionState.EXPIRED,
                                 UploadSessionState.COMPLETED,
                                 UploadSessionState.ABORTED):
            _expire_session(db, session)
            db.commit()
        raise ApiError("SESSION_EXPIRED",
                       "upload session expired; re-create via same Idempotency-Key "
                       "after verifying server-side asset state")


def recover_orphans(session_factory) -> int:
    """启动恢复：登记"已落盘未登记"分片（03 §5 崩溃点1）。返回恢复数。"""
    total = 0
    with session_factory() as db:
        vols = db.query(StorageVolume).filter_by(state="ACTIVE", backend="filesystem").all()
        for vol_row in vols:
            handle = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
            if not os.path.isdir(handle.root):
                continue
            total += recover_chunk_files(db, handle)
        db.commit()
    return total



def expected_chunk_len(session: UploadSession, index: int) -> int:
    if index < 0 or index >= session.chunk_count:
        raise ApiError("VALIDATION_FAILED", f"chunk index out of range: {index}")
    remaining = session.expected_size - index * session.chunk_size
    return max(0, min(session.chunk_size, remaining))


def commit_chunk(db: Session, session: UploadSession, index: int,
                 sha256: str, size: int, tmp_path: str | None,
                 vol: VolumeHandle) -> bool:
    """登记分片ACK。返回 True=新登记，False=幂等重放。

    W07同index并发写：先以唯一约束占DB行（仲裁并发），成功者才发布文件；
    落败方按已登记hash判定幂等(同hash)或冲突(异hash)，绝不覆盖他人字节。
    """
    existing = (db.query(UploadChunk)
                .filter_by(session_id=session.id, chunk_index=index).first())
    if existing is not None:
        if existing.sha256 != sha256:
            raise ApiError("CONFLICT",
                           f"chunk {index} already confirmed with different bytes")
        return False
    final = vol.chunk_path(session.id, index)
    if session.state == UploadSessionState.CREATED:
        session.state = UploadSessionState.RECEIVING
    db.add(UploadChunk(id=new_uuid(), tenant_id=session.tenant_id, session_id=session.id,
                       chunk_index=index, size_bytes=size, sha256=sha256,
                       storage_key=final, committed_at=utcnow()))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        winner = (db.query(UploadChunk)
                  .filter_by(session_id=session.id, chunk_index=index).first())
        if winner is not None:
            if winner.sha256 != sha256:
                raise ApiError("CONFLICT",
                               f"chunk {index} concurrently confirmed with "
                               "different bytes") from None
            return False
        raise
    if tmp_path is not None:
        from storage_backend import publish_chunk
        publish_chunk(tmp_path, final)
    return True


def recover_chunk_files(db: Session, vol: VolumeHandle) -> int:
    """启动恢复：已落盘未登记的分片补登记（03 §5 崩溃点1）。"""
    recovered = 0
    sessions = db.query(UploadSession).filter(
        UploadSession.state.in_([UploadSessionState.CREATED, UploadSessionState.RECEIVING,
                                 UploadSessionState.VERIFYING])).all()
    for s in sessions:
        sdir = vol.session_dir(s.id)
        chunks_dir = os.path.join(sdir, "chunks") if os.path.isdir(sdir) else None
        if not chunks_dir:
            continue
        for name in os.listdir(chunks_dir):
            if not name.endswith(".chunk"):
                continue
            try:
                index = int(name[:-len(".chunk")])
            except ValueError:
                continue
            if db.query(UploadChunk).filter_by(session_id=s.id, chunk_index=index).first():
                continue
            path = os.path.join(chunks_dir, name)
            digest, size = sha256_of_file(path)
            try:
                expected = expected_chunk_len(s, index)
            except ApiError:
                os.replace(path, vol.quarantine_path(f"{s.id}-{index}"))
                continue
            if size != expected:
                os.replace(path, vol.quarantine_path(f"{s.id}-{index}"))
                continue
            db.add(UploadChunk(id=new_uuid(), tenant_id=s.tenant_id, session_id=s.id,
                               chunk_index=index, size_bytes=size, sha256=digest,
                               storage_key=path, committed_at=utcnow()))
            recovered += 1
    db.flush()
    return recovered


def complete_session(db: Session, session: UploadSession) -> Operation:
    """complete 的单一持久 Operation（R14）：VERIFYING/COMPLETED 期间重放返回
    同一 operation；分片缺失仍显式 409（参数不变不产生新 attempt）。"""
    chunks = (db.query(UploadChunk).filter_by(session_id=session.id)
              .order_by(UploadChunk.chunk_index).all())
    have = {c.chunk_index for c in chunks}
    missing = [i for i in range(session.chunk_count) if i not in have]
    if missing:
        raise ApiError("CONFLICT", "missing chunks", details={"missing_indexes": missing[:50]})
    if session.state in (UploadSessionState.VERIFYING, UploadSessionState.COMPLETED):
        op = _find_operation(db, session.id, "storage.verify_session")
        if op is not None:
            return op
    session.state = UploadSessionState.VERIFYING
    op = Operation(id=new_uuid(), tenant_id=session.tenant_id,
                   op_type="storage.verify_session", state=OperationState.PENDING,
                   subject_ref=session.id)
    db.add(op)
    db.flush()
    db.add(Job(id=new_uuid(), tenant_id=session.tenant_id, job_type="storage.verify_session",
               dedup_key=session.id, payload={"session_id": session.id, "operation_id": op.id},
               priority=50))
    db.flush()
    return op


def _find_operation(db: Session, subject: str, op_type: str) -> Operation | None:
    return (db.query(Operation)
            .filter_by(subject_ref=subject, op_type=op_type)
            .order_by(Operation.created_at.desc()).first())


def verify_session(db: Session, session: UploadSession) -> dict:
    """合并校验并提交 blob/asset（worker 执行；幂等，dedup=session_id）。

    W07崩溃矩阵：阶段推进均先持久化意图（CREATED→ASSEMBLED→RENAMED→
    DB_COMMITTED），每个提交边界带 _crash_point 注入名；进程被kill后
    recover_commit_intents 按意图阶段幂等恢复。
    """
    op = _find_operation(db, session.id, "storage.verify_session")
    if op is not None and op.state == OperationState.SUCCEEDED:
        return op.result
    if op is not None:
        op.state = OperationState.RUNNING
    db.commit()

    vol_row = db.get(StorageVolume, session.volume_id)
    vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
    asset = db.get(Asset, session.asset_id)
    error: dict = {}
    try:
        chunks = (db.query(UploadChunk).filter_by(session_id=session.id)
                  .order_by(UploadChunk.chunk_index).all())
        # W07：重入复用未决意图（kill后重试不再堆叠新意图行）
        intent = (db.query(StorageCommitIntent)
                  .filter_by(session_id=session.id,
                             resolved_at=None)
                  .order_by(StorageCommitIntent.created_at.desc()).first())
        if intent is None:
            intent = StorageCommitIntent(
                id=new_uuid(), tenant_id=session.tenant_id,
                session_id=session.id, blob_id=None,
                target_key=vol.object_path(f"pending-{session.id}"),
                digest=session.expected_sha256, phase="CREATED")
            db.add(intent)
            db.flush()
        _crash_point("before_intent_commit")
        db.commit()  # R40：意图先于任何文件操作独立提交，崩溃后可查证恢复
        _crash_point("intent_committed")

        assembling = storage_backend.assemble_file(
            vol, session.id, [c.storage_key for c in chunks], asset.id,
            session.expected_sha256, session.expected_size)
        intent.phase = "ASSEMBLED"
        db.commit()
        _crash_point("assembled")

        blob = (db.query(Blob).filter_by(tenant_id=session.tenant_id,
                                         sha256=session.expected_sha256,
                                         size_bytes=session.expected_size).first())
        if blob is None:
            blob = Blob(id=new_uuid(), tenant_id=session.tenant_id,
                        size_bytes=session.expected_size, sha256=session.expected_sha256,
                        state=BlobState.VERIFYING)
            db.add(blob)
            db.flush()
        obj_path = vol.object_path(blob.id)
        if not os.path.exists(obj_path):
            storage_backend.commit_object(vol, assembling, blob.id)
        # R41：内容去重复用既有对象时，本会话的 assembling 临时文件必须清理
        if os.path.exists(assembling):
            os.remove(assembling)
        intent.blob_id = blob.id
        intent.target_key = obj_path
        intent.phase = "RENAMED"
        db.commit()
        _crash_point("renamed")

        result = _finalize_commit(db, session, asset, blob, intent, vol_row, vol, op)
        return result
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("verify_session failed for %s", session.id)
        error = {"code": "FILE_HASH_MISMATCH" if "mismatch" in str(exc) else "STORAGE_FAILURE",
                 "message": str(exc)}
        session = db.get(UploadSession, session.id)
        asset = db.get(Asset, session.asset_id) if session is not None else None
        if session is not None:
            session.state = UploadSessionState.FAILED
            # 失败也必须释放卷预留，否则配额泄漏（H-02；W07经账本CAS恰好一次）
            vol_row = db.get(StorageVolume, session.volume_id)
            release_reservation(db, session.id, "FAIL")
            if vol_row is not None:
                import shutil
                shutil.rmtree(VolumeHandle(
                    vol_row.id, vol_row.root_ref,
                    vol_row.safety_margin_bytes).session_dir(session.id),
                    ignore_errors=True)
        if asset is not None and asset.state != AssetState.AVAILABLE:
            asset.state = AssetState.QUARANTINED
        op = _find_operation(db, session.id if session else "", "storage.verify_session")
        if op is not None:
            op.state = OperationState.FAILED
            op.error = error
        db.commit()
        raise ApiError(error.get("code", "STORAGE_CAPACITY_LOW"), error.get("message", "verify failed"))


def _finalize_commit(db: Session, session: UploadSession, asset: Asset, blob: Blob,
                     intent: StorageCommitIntent, vol_row: StorageVolume,
                     vol: VolumeHandle, op: Operation | None) -> dict:
    """核心提交（W07抽取，verify_session 与崩溃恢复共用）：

    资产/位置/会话终态 → CAS释放预留 → 清staging → Outbox/Operation → 提交。
    各边界带 _crash_point：core_pre_commit / staging_cleared / core_committed。
    """
    import shutil
    loc = (db.query(AssetLocation)
           .filter_by(volume_id=vol.volume_id, physical_key=intent.target_key).first())
    if loc is None:
        db.add(AssetLocation(id=new_uuid(), tenant_id=session.tenant_id, blob_id=blob.id,
                             volume_id=vol.volume_id, physical_key=intent.target_key,
                             role="primary", state="AVAILABLE", verified_at=utcnow()))
    blob.state = BlobState.AVAILABLE
    blob.verified_at = utcnow()
    asset.blob_id = blob.id
    asset.state = AssetState.AVAILABLE
    session.state = UploadSessionState.COMPLETED
    session.completed_at = utcnow()
    intent.phase = "DB_COMMITTED"
    intent.resolved_at = utcnow()
    db.flush()
    # 释放配额（W07账本CAS：恢复路径重复调用也恰好释放一次）
    release_reservation(db, session.id, "COMPLETE")
    _crash_point("core_pre_commit")
    # 清理分片暂存
    shutil.rmtree(vol.session_dir(session.id), ignore_errors=True)
    _crash_point("staging_cleared")
    db.add(OutboxEvent(id=new_uuid(), tenant_id=session.tenant_id,
                       aggregate_ref=f"asset:{asset.id}", event_type="asset.available",
                       payload={"asset_id": asset.id, "blob_id": blob.id,
                                "acquisition_id": session.acquisition_id,
                                "sha256": blob.sha256}))
    _maybe_complete_bundle(db, session.acquisition_id)
    if op is not None:
        op.state = OperationState.SUCCEEDED
        op.result = {"asset_id": asset.id, "blob_id": blob.id,
                     "sha256": blob.sha256, "size_bytes": blob.size_bytes}
    db.commit()
    _crash_point("core_committed")
    return {"asset_id": asset.id, "blob_id": blob.id, "sha256": blob.sha256,
            "size_bytes": blob.size_bytes}


def recover_commit_intents(session_factory) -> dict:
    """重启恢复（W07崩溃矩阵）：按意图阶段幂等恢复未决提交。

    - VERIFYING 且分片齐但无意图（意图提交前被kill）→ 重跑 verify_session；
    - 意图未决（resolved_at IS NULL）→ 按阶段续走：对象已在则直接核心提交，
      对象不在则重合并分片；分片与对象都缺 → 会话FAILED+释放预留（不可恢复）；
    - 会话已 COMPLETED 但意图未决/预留未释放 → 补齐释放与暂存清理。
    可重复执行：二次运行为no-op。
    """
    stats = {"retried": 0, "resumed": 0, "unrecoverable": 0, "closed": 0}
    with session_factory() as db:
        verifying = (db.query(UploadSession)
                     .filter(UploadSession.state == UploadSessionState.VERIFYING).all())
        for s in verifying:
            has_intent = (db.query(StorageCommitIntent)
                          .filter_by(session_id=s.id).first())
            if has_intent is not None:
                continue
            have = {c.chunk_index for c in db.query(UploadChunk)
                    .filter_by(session_id=s.id).all()}
            if len(have) >= s.chunk_count:
                try:
                    verify_session(db, s)
                    stats["retried"] += 1
                except ApiError:
                    stats["unrecoverable"] += 1
        db.rollback()
        intents = (db.query(StorageCommitIntent)
                   .filter(StorageCommitIntent.resolved_at.is_(None)).all())
        for intent in intents:
            session = db.get(UploadSession, intent.session_id)
            if session is None:
                intent.resolved_at = utcnow()
                stats["closed"] += 1
                continue
            vol_row = db.get(StorageVolume, session.volume_id)
            vol = VolumeHandle(vol_row.id, vol_row.root_ref, vol_row.safety_margin_bytes)
            asset = db.get(Asset, session.asset_id)
            op = _find_operation(db, session.id, "storage.verify_session")
            import shutil
            if session.state in (UploadSessionState.FAILED, UploadSessionState.ABORTED,
                                 UploadSessionState.EXPIRED):
                # 已失败/取消/过期：只补齐释放与清理，不尝试续走
                release_reservation(db, session.id, "RECOVERY")
                intent.resolved_at = utcnow()
                shutil.rmtree(vol.session_dir(session.id), ignore_errors=True)
                stats["closed"] += 1
                continue
            if session.state == UploadSessionState.COMPLETED:
                shutil.rmtree(vol.session_dir(session.id), ignore_errors=True)
                release_reservation(db, session.id, "RECOVERY")
                if op is not None and op.state != OperationState.SUCCEEDED:
                    op.state = OperationState.SUCCEEDED
                    op.result = {"asset_id": asset.id, "blob_id": asset.blob_id,
                                 "recovered": True}
                intent.resolved_at = utcnow()
                stats["closed"] += 1
                db.commit()
                continue
            try:
                blob = None
                if intent.blob_id:
                    blob = db.get(Blob, intent.blob_id)
                if blob is None:
                    blob = (db.query(Blob)
                            .filter_by(tenant_id=session.tenant_id,
                                       sha256=session.expected_sha256,
                                       size_bytes=session.expected_size).first())
                obj_path = (vol.object_path(blob.id) if blob is not None
                            else intent.target_key)
                if blob is not None and os.path.exists(obj_path):
                    intent.blob_id = blob.id
                    intent.target_key = obj_path
                    intent.phase = "RENAMED"
                    db.flush()
                else:
                    chunks = (db.query(UploadChunk)
                              .filter_by(session_id=session.id)
                              .order_by(UploadChunk.chunk_index).all())
                    have = {c.chunk_index for c in chunks}
                    if len(have) < session.chunk_count or not chunks:
                        # 分片已清且对象不在：唯一恢复材料丢失，显式失败
                        session.state = UploadSessionState.FAILED
                        release_reservation(db, session.id, "RECOVERY")
                        intent.resolved_at = utcnow()
                        if op is not None:
                            op.state = OperationState.FAILED
                            op.error = {"code": "STORAGE_FAILURE",
                                        "message": "unrecoverable: staging cleared "
                                                   "before object commit"}
                        stats["unrecoverable"] += 1
                        db.commit()
                        continue
                    if blob is None:
                        blob = Blob(id=new_uuid(), tenant_id=session.tenant_id,
                                    size_bytes=session.expected_size,
                                    sha256=session.expected_sha256,
                                    state=BlobState.VERIFYING)
                        db.add(blob)
                        db.flush()
                    assembling = storage_backend.assemble_file(
                        vol, session.id, [c.storage_key for c in chunks], asset.id,
                        session.expected_sha256, session.expected_size)
                    obj_path = vol.object_path(blob.id)
                    if not os.path.exists(obj_path):
                        storage_backend.commit_object(vol, assembling, blob.id)
                    if os.path.exists(assembling):
                        os.remove(assembling)
                    intent.blob_id = blob.id
                    intent.target_key = obj_path
                    intent.phase = "RENAMED"
                    db.flush()
                _finalize_commit(db, session, asset, blob, intent, vol_row, vol, op)
                stats["resumed"] += 1
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                log.exception("recover intent %s failed", intent.id)
                session = db.get(UploadSession, intent.session_id)
                if session is not None and session.state != UploadSessionState.COMPLETED:
                    session.state = UploadSessionState.FAILED
                    release_reservation(db, session.id, "RECOVERY")
                    intent = db.get(StorageCommitIntent, intent.id)
                    if intent is not None:
                        intent.resolved_at = utcnow()
                    op = _find_operation(db, session.id, "storage.verify_session")
                    if op is not None:
                        op.state = OperationState.FAILED
                        op.error = {"code": "STORAGE_FAILURE", "message": str(exc)[:500]}
                    stats["unrecoverable"] += 1
                    db.commit()
        db.commit()
    return stats


def sweep_lost_mounts(db: Session) -> list[dict]:
    """挂载丢失清扫（W07）：根缺失或哨兵缺失 → health=LOST；恢复 → OK。

    只改 health 观测字段与告警，不删数据、不改 state；写路径以
    VolumeHandle.mount_ok() 实时拦截为准。
    """
    events: list[dict] = []
    for v in db.query(StorageVolume).filter_by(backend="filesystem").all():
        h = VolumeHandle(v.id, v.root_ref, v.safety_margin_bytes)
        ok = os.path.isdir(h.root) and h.mount_ok()
        want = "OK" if ok else "LOST"
        if v.health != want:
            v.health = want
            events.append({"volume_id": v.id, "name": v.name, "health": want})
            db.add(AlertEvent(id=new_uuid(), tenant_id=v.tenant_id,
                              alert_type="VOLUME_MOUNT_LOST" if want == "LOST"
                              else "VOLUME_MOUNT_RESTORED",
                              severity="warning",
                              message=f"volume {v.name} root={v.root_ref} "
                                      f"mount_ok={ok}",
                              target_ref=f"volume:{v.id}"))
    db.commit()
    return events


def _maybe_complete_bundle(db: Session, acquisition_id: str) -> None:
    bundle = (db.query(AssetBundle).filter_by(acquisition_id=acquisition_id)
              .order_by(AssetBundle.revision.desc()).first())
    if bundle is None:
        return
    assets = (db.query(Asset).filter_by(acquisition_id=acquisition_id).all())
    if all(a.state == AssetState.AVAILABLE for a in assets) and assets:
        bundle.state = "COMPLETE"
        acq = db.get(Acquisition, acquisition_id)
        if acq is not None:
            acq.state = "FILES_COMPLETE"


def asset_content(db: Session, tenant_id: str, asset_id: str):
    asset = db.get(Asset, asset_id)
    if asset is None or asset.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "asset not found")
    if asset.state != AssetState.AVAILABLE or not asset.blob_id:
        raise ApiError("CONFLICT", f"asset not AVAILABLE (state={asset.state})")
    blob = db.get(Blob, asset.blob_id)
    # R42：Blob 健康隔离覆盖全部引用——共享Blob被隔离后任何Asset都不可下载
    if blob is None or blob.state != BlobState.AVAILABLE:
        raise ApiError("CONFLICT",
                       f"blob state {blob.state if blob else 'MISSING'}; "
                       f"download blocked pending quarantine review")
    loc = (db.query(AssetLocation)
           .filter_by(blob_id=blob.id, state="AVAILABLE").first())
    if loc is None or not os.path.exists(loc.physical_key):
        asset.state = AssetState.UNAVAILABLE
        db.commit()
        raise ApiError("VOLUME_OFFLINE", "asset location missing on volume")
    return asset, blob, loc.physical_key


def record_download(db: Session, tenant_id: str, asset_id: str | None,
                    publication_item_id: str | None, principal: str,
                    range_header: str, bytes_sent: int, result: str) -> None:
    db.add(DownloadEvidence(id=new_uuid(), tenant_id=tenant_id, asset_id=asset_id,
                            publication_item_id=publication_item_id, principal=principal,
                            range_header=range_header or "", bytes_sent=bytes_sent,
                            result=result))
    # E01：文件（含可能带患者信息的label/缩略图）访问需审计留痕
    from core_db.models import AuditEvent
    db.add(AuditEvent(id=new_uuid(), tenant_id=tenant_id, actor=principal,
                      action="asset.download",
                      target=f"asset:{asset_id}" if asset_id else f"route:{publication_item_id}",
                      reason=f"range={range_header or '-'} bytes={bytes_sent} result={result}"))
    db.commit()


def verify_asset_job(db, job) -> dict:
    from storage_backend import sha256_of_file
    from core_db.models import Asset, AssetLocation, Blob, Operation
    asset = db.get(Asset, job.payload["asset_id"])
    loc = (db.query(AssetLocation)
           .filter_by(blob_id=asset.blob_id, state="AVAILABLE").first())
    digest, size = sha256_of_file(loc.physical_key)
    blob = db.get(Blob, asset.blob_id)
    ok = digest == blob.sha256 and size == blob.size_bytes
    if not ok:
        asset.state = "QUARANTINED"
        blob.state = "QUARANTINED"
    op = db.get(Operation, job.payload.get("operation_id"))
    if op is not None:
        op.state = "SUCCEEDED" if ok else "FAILED"
        op.result = {"asset_id": asset.id, "sha256": digest, "verified": ok}
        if not ok:
            op.error = {"code": "FILE_HASH_MISMATCH", "message": "verify failed"}
    db.commit()
    if not ok:
        raise ApiError("FILE_HASH_MISMATCH", f"verify failed for asset {asset.id}")
    return {"asset_id": asset.id, "sha256": digest, "verified": True}


# ================================================================ T5 存储迁移
def migration_reservation_key(asset_id: str, target_volume_id: str) -> str:
    """迁移预留账本键：确定性派生（同资产同目标卷稳定，幂等重入可识别）。"""
    import uuid as _uuid
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL,
                           f"blyb:migrate:{asset_id}:{target_volume_id}"))


def plan_migration(db: Session, tenant_id: str, asset_ids: list,
                   target_volume_id: str) -> dict:
    """迁移预览（T5）：逐资产计划 + 总量 + 目标卷可行性
    （free - safety_margin，且扣除预留账本 reserved_bytes）。"""
    target = db.get(StorageVolume, target_volume_id)
    if target is None or target.tenant_id != tenant_id:
        raise ApiError("NOT_FOUND", "target volume not found")
    th = VolumeHandle(target.id, target.root_ref, target.safety_margin_bytes)
    mount_ok = th.mount_ok()
    items = []
    for aid in asset_ids:
        asset = db.get(Asset, aid)
        if asset is None or asset.tenant_id != tenant_id:
            raise ApiError("NOT_FOUND", f"asset not found: {aid}")
        blob = db.get(Blob, asset.blob_id) if asset.blob_id else None
        loc = None
        if asset.blob_id:
            loc = (db.query(AssetLocation)
                   .filter_by(blob_id=asset.blob_id, state="AVAILABLE")
                   .order_by(AssetLocation.id).first())
        items.append({
            "asset_id": asset.id,
            "size_bytes": blob.size_bytes if blob is not None
            else asset.expected_size,
            "from_volume": loc.volume_id if loc is not None else None,
            "to_volume": target.id})
    total = sum(i["size_bytes"] for i in items)
    free = th.free_bytes() if os.path.isdir(target.root_ref) else 0
    usable = free - target.safety_margin_bytes
    if not mount_ok:
        feasible, reason = False, "target volume mount sentinel missing (VOLUME_OFFLINE)"
    elif usable - (target.reserved_bytes or 0) < total:
        feasible = False
        reason = (f"STORAGE_CAPACITY_LOW: need {total} bytes, usable "
                  f"{usable - (target.reserved_bytes or 0)} "
                  f"(free {free} - margin {target.safety_margin_bytes}"
                  f" - reserved {target.reserved_bytes or 0})")
    else:
        feasible, reason = True, ""
    return {"items": items, "total_bytes": total, "feasible": feasible,
            "reason": reason, "target_volume_id": target.id}


def reserve_for_migration(db: Session, vol_row: StorageVolume, tenant_id: str,
                          subject_key: str, need: int, handle: VolumeHandle) -> None:
    """迁移预留（T5）：复用预留CAS账本（reserve_for_session 的同构实现，
    subject 为确定性派生键而非上传会话）。

    - 无账本行：条件UPDATE递增卷 reserved_bytes + 插入 ACTIVE 行；
    - 已有 ACTIVE 行（上次中断遗留）：沿用，不重复递增；
    - 已有 RELEASED 行（失败已释放后重试）：重新激活并再次递增。
    任一时刻卷计数与 ACTIVE 行合计一致；释放仍走 release_reservation（CAS恰好一次）。
    """
    existing = (db.query(StorageReservation)
                .filter_by(session_id=subject_key).first())
    cap = vol_row.capacity_bytes or 0
    if existing is not None and existing.state == "ACTIVE":
        return  # 预留已在账上（中断重入）
    free = handle.free_bytes()
    usable_free = free - vol_row.safety_margin_bytes
    stmt = ("UPDATE storage_volume SET reserved_bytes = reserved_bytes + :need "
            "WHERE id = :vid")
    params: dict = {"need": need, "vid": vol_row.id}
    if cap > 0:
        stmt += " AND reserved_bytes + :need <= :cap"
        params["cap"] = cap
    res = db.execute(text(stmt), params)
    if res.rowcount != 1:
        raise ApiError("STORAGE_CAPACITY_LOW",
                       f"need reserve {need} bytes exceeds capacity {cap}")
    row_after = db.execute(text(
        "SELECT reserved_bytes FROM storage_volume WHERE id = :i"),
        {"i": vol_row.id}).fetchone()
    if row_after is not None and row_after[0] > usable_free:
        db.rollback()
        raise ApiError("STORAGE_CAPACITY_LOW",
                       f"need reserve {need} bytes, usable {usable_free}")
    if existing is not None:  # RELEASED → 重新激活（同键唯一约束限制单行）
        existing.state = "ACTIVE"
        existing.reason = ""
        existing.released_at = None
        existing.bytes_amount = need
        existing.volume_id = vol_row.id
    else:
        db.add(StorageReservation(
            id=new_uuid(), tenant_id=tenant_id, volume_id=vol_row.id,
            session_id=subject_key, bytes_amount=need, state="ACTIVE"))


def migrate_asset(db: Session, job) -> dict:
    """单资产迁移执行（T5，worker Job：dedup=migrate:{asset}:{target_volume}）。

    同卷族拷贝到目标卷 objects 路径 → 全量 hash 校验相等 → 新增
    AssetLocation(role=replica, state=AVAILABLE) → 原 location 保留不动
    （孤儿/回收另立流程，只登记不删）→ CAS 释放预留。目标卷哨兵缺失即拒绝。
    """
    import hashlib
    from core_db.models import Asset, Blob, Operation
    payload = job.payload or {}
    asset = db.get(Asset, payload.get("asset_id"))
    if asset is None or asset.tenant_id != job.tenant_id:
        raise ApiError("NOT_FOUND", "asset not found")
    if not asset.blob_id:
        raise ApiError("CONFLICT", "asset has no blob; nothing to migrate")
    blob = db.get(Blob, asset.blob_id)
    if blob is None or blob.state != BlobState.AVAILABLE:
        raise ApiError("CONFLICT", f"blob state {getattr(blob, 'state', 'MISSING')}"
                                   " not migratable")
    target_row = db.get(StorageVolume, payload.get("target_volume_id"))
    if target_row is None or target_row.tenant_id != job.tenant_id:
        raise ApiError("NOT_FOUND", "target volume not found")
    th = VolumeHandle(target_row.id, target_row.root_ref,
                      target_row.safety_margin_bytes)
    if not th.mount_ok():
        raise ApiError("VOLUME_OFFLINE",
                       "target volume mount sentinel missing — migration rejected")
    th.ensure_layout()
    target_path = th.object_path(blob.id)
    # 幂等：目标位置已可用即视为已迁移（源对象始终不动）
    done = (db.query(AssetLocation)
            .filter_by(volume_id=target_row.id, physical_key=target_path).first())
    if done is not None and done.state == "AVAILABLE":
        release_reservation(db, migration_reservation_key(asset.id, target_row.id),
                            "COMPLETE")
        db.commit()
        return {"asset_id": asset.id, "volume_id": target_row.id,
                "physical_key": target_path, "idempotent": True}
    key = migration_reservation_key(asset.id, target_row.id)
    reserve_for_migration(db, target_row, job.tenant_id, key,
                          blob.size_bytes, th)
    db.commit()
    # 拷贝期间预留 size_bytes（峰值单副本）；流式拷贝 + 边读边算hash。
    # 临时名含 job.id：并发同资产迁移（LG-09）互不共用 assembling 文件，
    # 落位经 os.replace 原子完成 + 二次查重，后到者幂等成功。
    tmp = th.assembling_path(f"migrate-{blob.id}-{job.id}")
    h = hashlib.sha256()
    size = 0
    try:
        src = (db.query(AssetLocation)
               .filter_by(blob_id=blob.id, state="AVAILABLE")
               .order_by(AssetLocation.id).first())
        if src is None or not os.path.exists(src.physical_key):
            raise ApiError("VOLUME_OFFLINE",
                           "source object unreachable; migration aborted")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(src.physical_key, "rb") as fin, open(tmp, "wb") as fout:
            while True:
                block = fin.read(4 * 1024 * 1024)
                if not block:
                    break
                fout.write(block)
                h.update(block)
                size += len(block)
            fout.flush()
            os.fsync(fout.fileno())
        if h.hexdigest() != blob.sha256 or size != blob.size_bytes:
            raise ApiError(
                "FILE_HASH_MISMATCH",
                f"migrated copy digest/size mismatch ({h.hexdigest()[:12]}, {size}) "
                f"vs blob ({blob.sha256[:12]}, {blob.size_bytes})")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        os.replace(tmp, target_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                log.warning("migrate tmp cleanup failed: %s", tmp)
    loc = (db.query(AssetLocation)
           .filter_by(volume_id=target_row.id, physical_key=target_path).first())
    if loc is None:
        db.add(AssetLocation(
            id=new_uuid(), tenant_id=job.tenant_id, blob_id=blob.id,
            volume_id=target_row.id, physical_key=target_path,
            role="replica", state="AVAILABLE", verified_at=utcnow()))
    else:
        loc.state = "AVAILABLE"
        loc.verified_at = utcnow()
    db.flush()
    # CAS 释放预留（恰好一次）
    release_reservation(db, key, "COMPLETE")
    db.commit()
    return {"asset_id": asset.id, "volume_id": target_row.id,
            "physical_key": target_path, "bytes": size}
