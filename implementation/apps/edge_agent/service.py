"""采集 Agent 服务：发现→清单→分片传输→心跳/命令（03 §2—§4）。

- 本地SQLite持久队列；重启不漏件、不生成新事件ID冒充新扫描。
- 分片ACK以服务端为准；重传幂等；指数退避。
- 目录模式仅产生候选（稳定窗口不能证明扫描完成），需人工确认。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone

import httpx

from .local_db import LocalDb


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentService:
    def __init__(self, *, watch_root: str, core_url: str, storage_url: str,
                 token: str, agent_id: str, local_db_path: str,
                 chunk_size: int = 4 * 1024 * 1024, stability_window: int = 60,
                 device_code: str = "SCAN-WS-01"):
        self.watch_root = os.path.abspath(watch_root)
        self.core_url = core_url.rstrip("/")
        self.storage_url = storage_url.rstrip("/")
        self.token = token
        self.agent_id = agent_id
        self.chunk_size = chunk_size
        self.stability_window = stability_window
        self.device_code = device_code
        os.makedirs(self.watch_root, exist_ok=True)
        self.db = LocalDb(local_db_path)
        if self.db.recovered:
            self._issue("queue_corrupted", "local_db",
                        f"local queue was corrupted; backup kept at "
                        f"{self.db.corrupt_backup}; fresh queue started, "
                        f"server-side session idempotency prevents re-upload")
        self.db.x("INSERT OR IGNORE INTO watched_root (root_id, normalized_path) "
                  "VALUES (?,?)", (str(uuid.uuid4()), self.watch_root))
        self.db.x("INSERT OR REPLACE INTO agent_identity (agent_id) VALUES (?)",
                  (self.agent_id,))

    # ------------------------------------------------------------ HTTP 基础
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    # ------------------------------------------------------------ 发现
    def discover(self) -> int:
        """厂家manifest模式：发现已原子发布的完成清单。"""
        found = 0
        for dirpath, _dirs, names in os.walk(self.watch_root):
            if "manifest.json" not in names:
                continue
            path = os.path.join(dirpath, "manifest.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    manifest = json.load(f)
            except (OSError, ValueError):
                continue
            src_id = manifest.get("source_scan_id", "")
            rev = str(manifest.get("source_revision", "1"))
            # manifest 由采集端 ManifestBuilder 负责：agent_id 以本机注册身份为准
            manifest["agent_id"] = self.agent_id
            row = self.db.one("SELECT event_id FROM acquisition_event "
                              "WHERE source_event_id=? AND source_revision=?",
                              (src_id, rev))
            if row is not None:
                continue
            # 校验成员文件仍与清单一致（A06：源变化→不沿用旧快照）
            ok_files = []
            changed = False
            for f in manifest.get("files", []):
                rel = f["relative_path"]
                local = os.path.join(self.watch_root, rel.replace("/", os.sep))
                if not os.path.exists(local):
                    changed = True
                    continue
                st = os.stat(local)
                if st.st_size != f["size_bytes"]:
                    changed = True
                    continue
                ok_files.append(f)
            if changed or not ok_files:
                self._issue("source_changed", f"manifest:{src_id}",
                            "manifest members missing/changed on disk; skip")
                continue
            self.db.x(
                "INSERT INTO acquisition_event (event_id, source_event_id,"
                " source_revision, manifest_json, completion_evidence, state)"
                " VALUES (?,?,?,?,?,?)",
                (manifest["event_id"], src_id, rev, json.dumps(manifest),
                 json.dumps(manifest.get("completion_evidence", {})), "DISCOVERED"))
            for f in ok_files:
                local = os.path.join(self.watch_root,
                                     f["relative_path"].replace("/", os.sep))
                st = os.stat(local)
                self.db.x("INSERT OR IGNORE INTO discovery_entry "
                          "(entry_id, root_id, relative_path, size_bytes, mtime,"
                          " first_seen, scan_batch, generation) VALUES (?,?,?,?,?,?,?,1)",
                          (str(uuid.uuid4()), self.watch_root, f["relative_path"],
                           st.st_size, datetime.fromtimestamp(
                               st.st_mtime, timezone.utc).isoformat(), _now(), src_id))
            found += 1
        return found

    def discover_directory_candidates(self) -> list[dict]:
        """目录模式：稳定窗口仅产生候选，不放行（03 §3.2）。"""
        candidates = []
        now = time.time()
        for dirpath, _dirs, names in os.walk(self.watch_root):
            for name in names:
                if name.endswith(".json") or name.endswith(".tmp"):
                    continue
                path = os.path.join(dirpath, name)
                st = os.stat(path)
                if now - st.st_mtime < self.stability_window:
                    continue
                rel = os.path.relpath(path, self.watch_root).replace(os.sep, "/")
                candidates.append({"relative_path": rel, "size": st.st_size,
                                   "mtime": st.st_mtime,
                                   "note": "候选：稳定窗口不证明扫描完成，需人工确认"})
        return candidates

    # ------------------------------------------------------------ 目录候选（W13）
    def _root_id(self) -> str:
        return self.db.one("SELECT root_id FROM watched_root")["root_id"]

    def _path_in_root(self, path: str) -> bool:
        """junction/symlink越根防护：解析后必须仍在监视根内（S11）。"""
        root_real = os.path.realpath(self.watch_root)
        real = os.path.realpath(path)
        return real == root_real or real.startswith(root_real + os.sep)

    def _hash_file(self, path: str, size: int) -> str:
        """受控本地快照流式hash（缺hash时计算，不猜完成）。"""
        h = hashlib.sha256()
        read = 0
        with open(path, "rb") as f:
            while True:
                block = f.read(4 * 1024 * 1024)
                if not block:
                    break
                h.update(block)
                read += len(block)
        if read != size:
            raise RetryLater(f"file changed during hashing: {read} != {size}")
        return h.hexdigest()

    def scan_directory_candidates(self, *, compute_hash: bool = True) -> list[dict]:
        """扫描目录候选并持久化（W13）。

        - 稳定窗口外的文件登记 CANDIDATE（含流式hash快照）；
        - junction/symlink 越根的路径拒绝登记并记 issue；
        - 同路径内容变化（size/mtime/hash）→ 旧候选置 CHANGED，生成新
          snapshot_version 候选；已 RELEASED 的不受影响（历史证据不变）。
        """
        root_id = self._root_id()
        out = []
        for cand in self.discover_directory_candidates():
            rel = cand["relative_path"]
            path = os.path.join(self.watch_root, rel.replace("/", os.sep))
            if not self._path_in_root(path):
                self._issue("path_escape", f"dir:{rel}",
                            "symlink/junction escapes watch root; rejected")
                continue
            sha = self._hash_file(path, cand["size"]) if compute_hash else ""
            prior = self.db.one(
                "SELECT * FROM dir_candidate WHERE root_id=? AND relative_path=? "
                "ORDER BY snapshot_version DESC LIMIT 1", (root_id, rel))
            if prior is not None:
                if (prior["state"] in ("CANDIDATE",) and prior["size_bytes"] == cand["size"]
                        and prior["mtime"] and float(prior["mtime"]) == cand["mtime"]):
                    out.append(dict(prior))
                    continue
                if prior["state"] == "CANDIDATE":
                    self.db.x("UPDATE dir_candidate SET state='CHANGED', "
                              "decided_at=?, reason='content changed; superseded' "
                              "WHERE candidate_id=?", (_now(), prior["candidate_id"]))
                version = (prior["snapshot_version"] or 0) + 1
            else:
                version = 1
            cid = str(uuid.uuid4())
            self.db.x(
                "INSERT INTO dir_candidate (candidate_id, root_id, relative_path,"
                " size_bytes, mtime, sha256, snapshot_version, state, created_at)"
                " VALUES (?,?,?,?,?,?,?,'CANDIDATE',?)",
                (cid, root_id, rel, cand["size"], str(cand["mtime"]), sha, version,
                 _now()))
            row = self.db.one("SELECT * FROM dir_candidate WHERE candidate_id=?", (cid,))
            out.append(dict(row))
        return out

    def release_directory_candidate(self, candidate_id: str, *, decided_by: str,
                                    reason: str) -> dict:
        """人工放行（W13）：复核快照→生成acquisition_event进同一归集队列。

        证据保存：放行者/理由/内容hash/size/mtime进 completion_evidence；
        内容与登记快照不一致→拒绝（CONTENT_CHANGED），等新版本候选。
        """
        cand = self.db.one("SELECT * FROM dir_candidate WHERE candidate_id=?",
                           (candidate_id,))
        if cand is None:
            return {"ok": False, "code": "NOT_FOUND"}
        if cand["state"] != "CANDIDATE":
            return {"ok": False, "code": "INVALID_STATE",
                    "detail": f"candidate state={cand['state']}"}
        path = os.path.join(self.watch_root,
                            cand["relative_path"].replace("/", os.sep))
        if not self._path_in_root(path):
            return {"ok": False, "code": "PATH_ESCAPE"}
        st = os.stat(path)
        if st.st_size != cand["size_bytes"]:
            self._refuse_changed(candidate_id)
            return {"ok": False, "code": "CONTENT_CHANGED",
                    "detail": f"size {st.st_size} != snapshot {cand['size_bytes']}"}
        sha = self._hash_file(path, st.st_size)
        if sha != cand["sha256"]:
            self._refuse_changed(candidate_id)
            return {"ok": False, "code": "CONTENT_CHANGED",
                    "detail": "content hash differs from registered snapshot"}
        event_id = str(uuid.uuid4())
        manifest = {
            "schema_version": "1.0", "event_id": event_id, "agent_id": self.agent_id,
            "source_system_code": "directory-manual-release", "root_id": self._root_id(),
            "captured_at": _now(),
            "source_scan_id": f"dir:{cand['relative_path']}:v{cand['snapshot_version']}",
            "source_revision": str(cand["snapshot_version"]),
            "identity_hints": {},
            "completion_evidence": {
                "method": "manual_release",
                "confirmed_at": _now(),
                "evidence_ref": f"dir_candidate:{candidate_id}",
                "decided_by": decided_by, "reason": reason,
                "snapshot_sha256": sha, "snapshot_size": st.st_size,
                "snapshot_mtime": cand["mtime"],
                "source_immutable_assertion": False},
            "files": [{"client_asset_id": str(uuid.uuid4()),
                       "relative_path": cand["relative_path"], "role": "primary",
                       "size_bytes": st.st_size, "sha256": sha}]}
        self.db.x(
            "INSERT INTO acquisition_event (event_id, source_event_id, "
            "source_revision, manifest_json, completion_evidence, state)"
            " VALUES (?,?,?,?,?,'DISCOVERED')",
            (event_id, manifest["source_scan_id"], manifest["source_revision"],
             json.dumps(manifest, ensure_ascii=False),
             json.dumps(manifest["completion_evidence"])))
        self.db.x("UPDATE dir_candidate SET state='RELEASED', decided_at=?, "
                  "decided_by=?, reason=?, evidence_json=? WHERE candidate_id=?",
                  (_now(), decided_by, reason,
                   json.dumps(manifest["completion_evidence"]), candidate_id))
        return {"ok": True, "event_id": event_id}

    def reject_directory_candidate(self, candidate_id: str, *, decided_by: str,
                                   reason: str) -> dict:
        row = self.db.x("UPDATE dir_candidate SET state='REJECTED', decided_at=?, "
                        "decided_by=?, reason=? WHERE candidate_id=? AND "
                        "state='CANDIDATE'",
                        (_now(), decided_by, reason, candidate_id))
        return {"ok": row == 1}

    def _refuse_changed(self, candidate_id: str) -> None:
        self.db.x("UPDATE dir_candidate SET state='CHANGED', decided_at=?, "
                  "reason='content changed at release; new candidate required'"
                  " WHERE candidate_id=?", (_now(), candidate_id))
        self._issue("dir_candidate_changed", f"candidate:{candidate_id}",
                    "content changed between registration and manual release")

    # ------------------------------------------------------------ 注册与传输
    def register_and_transfer(self, max_events: int = 5) -> int:
        rows = self.db.q("SELECT event_id, manifest_json FROM acquisition_event "
                         "WHERE state IN ('DISCOVERED','REGISTERED','TRANSFERRING')"
                         " LIMIT ?", (max_events,))
        done = 0
        for r in rows:
            manifest = json.loads(r["manifest_json"])
            try:
                self._process_event(manifest)
                done += 1
            except RetryLater:
                continue
        return done

    def _process_event(self, manifest: dict) -> None:
        reg = self._register_manifest(manifest)
        acquisition_id = reg["acquisition_id"]
        asset_map = {a["client_asset_id"]: a["asset_id"] for a in reg["assets"]}
        all_done = True
        for f in manifest["files"]:
            asset_id = asset_map.get(f["client_asset_id"])
            if not asset_id:
                continue
            ok = self._transfer_file(manifest, f, asset_id, acquisition_id)
            all_done = all_done and ok
        if all_done:
            self.db.x("UPDATE acquisition_event SET state='UPLOADED' WHERE event_id=?",
                      (manifest["event_id"],))

    def _register_manifest(self, manifest: dict) -> dict:
        resp = httpx.post(f"{self.storage_url}/api/v1/acquisitions",
                          json=manifest, headers=self._headers(), timeout=30)
        if resp.status_code in (200, 201):
            return resp.json()["data"]
        if resp.status_code == 409:
            raise RetryLater(f"register conflict: {resp.text[:200]}")
        raise RetryLater(f"register failed {resp.status_code}")

    def _transfer_file(self, manifest: dict, f: dict, asset_id: str,
                       acquisition_id: str) -> bool:
        event_key = f"{manifest['event_id']}:{f['relative_path']}"
        local = os.path.join(self.watch_root,
                             f["relative_path"].replace("/", os.sep))
        resp = httpx.post(
            f"{self.storage_url}/api/v1/upload-sessions",
            json={"acquisition_id": acquisition_id,
                  "asset_id": asset_id, "size_bytes": f["size_bytes"],
                  "sha256": f["sha256"], "chunk_size": self.chunk_size},
            headers={**self._headers(), "Idempotency-Key": event_key}, timeout=30)
        if resp.status_code not in (200, 201):
            raise RetryLater(f"session create failed {resp.status_code} {resp.text[:200]}")
        sess = resp.json()["data"]
        session_id, epoch = sess["session_id"], sess["epoch"]
        confirmed = set(sess.get("confirmed_chunk_indexes") or [])
        # 已完成资产：跳过（F02：不重传已AVAILABLE字节）
        if sess.get("state") == "COMPLETED":
            return True
        row = self.db.one("SELECT transfer_id FROM transfer_local WHERE session_id=?",
                          (session_id,))
        transfer_id = row["transfer_id"] if row else str(uuid.uuid4())
        self.db.x("INSERT OR IGNORE INTO transfer_local (transfer_id, asset_id,"
                  " session_id, session_epoch, file_snapshot, total_chunks, state)"
                  " VALUES (?,?,?,?,?,?,?)",
                  (transfer_id, asset_id, session_id, epoch,
                   json.dumps({"path": local, "sha256": f["sha256"]}),
                   sess["chunk_count"], "RUNNING"))
        size, chunk_size = f["size_bytes"], sess["chunk_size"]
        for index in range(sess["chunk_count"]):
            if index % 16 == 0:
                import gc
                gc.collect()  # 长传输周期回收httpx请求对象的循环引用
            if index in confirmed:
                continue
            offset = index * chunk_size
            length = min(chunk_size, size - offset)
            digest, data = _read_chunk(local, offset, length)
            header_hash = hashlib.sha256(data).hexdigest()
            put = httpx.put(
                f"{self.storage_url}/api/v1/upload-sessions/{session_id}"
                f"/chunks/{index}",
                content=data,
                headers={**self._headers(), "Content-Length": str(length),
                         "X-Chunk-SHA256": header_hash,
                         "X-Session-Epoch": str(epoch)}, timeout=120)
            if put.status_code == 204:
                confirmed.add(index)
                self.db.x("INSERT OR REPLACE INTO chunk_local (transfer_id,"
                          " chunk_index, size_bytes, hash, acked_at)"
                          " VALUES (?,?,?,?,?)",
                          (transfer_id, index, length, header_hash, _now()))
                continue
            if put.status_code == 409:
                # 服务端已有不同字节：源被改写（A03/A06）
                self._issue("chunk_conflict", f"session:{session_id}#{index}",
                            "server holds different bytes for this index")
                raise RetryLater("chunk conflict")
            raise RetryLater(f"chunk {index} failed {put.status_code}")
        comp = httpx.post(
            f"{self.storage_url}/api/v1/upload-sessions/{session_id}/complete",
            headers={**self._headers(), "X-Session-Epoch": str(epoch)}, timeout=30)
        if comp.status_code not in (200, 202):
            raise RetryLater(f"complete failed {comp.status_code} {comp.text[:200]}")
        op_id = comp.json()["data"]["operation_id"]
        for _ in range(120):
            op = httpx.get(f"{self.storage_url}/api/v1/operations/{op_id}",
                           headers=self._headers(), timeout=30)
            state = op.json()["data"]["state"]
            if state == "SUCCEEDED":
                self.db.x("UPDATE transfer_local SET state='DONE' WHERE transfer_id=?",
                          (transfer_id,))
                return True
            if state == "FAILED":
                self._issue("verify_failed", f"asset:{asset_id}",
                            "server-side verification failed")
                raise RetryLater("verify failed")
            time.sleep(0.3)
        raise RetryLater("operation poll timeout")

    # ------------------------------------------------------------ 心跳/命令
    def heartbeat(self) -> dict:
        pending = self.db.q("SELECT COUNT(*) c FROM acquisition_event "
                            "WHERE state NOT IN ('UPLOADED')")[0]["c"]
        body = {"applied_config_revision": None,
                "queue": {"pending_events": pending},
                "disk": {"watch_free_bytes": _disk_free(self.watch_root)}}
        resp = httpx.post(f"{self.core_url}/api/v1/agents/{self.agent_id}/heartbeat",
                          json=body, headers=self._headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()["data"]
        for cmd in data.get("commands", []):
            self._handle_command(cmd)
        return data

    def _handle_command(self, cmd: dict) -> None:
        """命令处理（R17/W13）：已实现的命令真实生效；未实现能力明确拒绝并ACK
        success=False，不得对未执行的动作假报成功。

        W13：command_id 幂等（重发不重复执行）；带 expires_at 的命令超期
        显式 EXPIRED；apply_config 校验 digest 后原子落地、失败保留旧配置；
        upgrade 先做签名验证（框架），安装本身仍未实现→显式拒绝。
        """
        cid = cmd["command_id"]
        if self.db.one("SELECT command_id FROM command_inbox WHERE command_id=?",
                       (cid,)):
            return  # 幂等：重发不重复执行
        result = {"ok": True}
        try:
            expires_at = cmd.get("expires_at")
            if expires_at:
                exp = datetime.fromisoformat(str(expires_at))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) > exp:
                    result = {"ok": False, "code": "COMMAND_EXPIRED",
                              "error": f"command expired at {expires_at}"}
                    self._record_command(cid, cmd, result)
                    return
            if cmd["type"] == "rescan":
                self.discover()
            elif cmd["type"] == "pause":
                # 暂停新任务（在途传输按约定完成，不中断）
                self.paused = True
                result = {"ok": True, "note": "discovery paused; in-flight transfers "
                                              "complete per contract"}
            elif cmd["type"] == "resume":
                self.paused = False
                result = {"ok": True}
            elif cmd["type"] == "apply_config":
                result = self._apply_config(cmd.get("payload") or {})
            elif cmd["type"] == "upgrade":
                result = self._verify_upgrade_payload(cmd.get("payload") or {})
            elif cmd["type"] == "diagnostic":
                result = {"ok": True, "diagnostics": {
                    "watch_root": self.watch_root,
                    "disk_free_bytes": _disk_free(self.watch_root),
                    "queue_pending": self.db.one(
                        "SELECT COUNT(*) c FROM acquisition_event "
                        "WHERE state NOT IN ('UPLOADED')")["c"],
                    "open_issues": self.db.one(
                        "SELECT COUNT(*) c FROM local_issue")["c"]}}
            else:
                result = {"ok": False, "code": "CAPABILITY_UNSUPPORTED",
                          "error": "unsupported command (whitelist)"}
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)[:200]}
        self._record_command(cid, cmd, result)

    def _record_command(self, cid: str, cmd: dict, result: dict) -> None:
        self.db.x("INSERT INTO command_inbox (command_id, type, received_at, result)"
                  " VALUES (?,?,?,?)",
                  (cid, cmd["type"], _now(), json.dumps(result)))
        httpx.post(
            f"{self.core_url}/api/v1/agents/{self.agent_id}/commands/{cid}/ack",
            json={"success": result.get("ok", True), "result": result},
            headers=self._headers(), timeout=15)

    def _config_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(self.db.path)),
                            "agent-config-pending.json")

    def _apply_config(self, payload: dict) -> dict:
        """apply_config（W13）：digest校验→临时文件→原子替换；失败保留旧配置。"""
        import tempfile
        config = payload.get("config")
        digest = payload.get("digest", "")
        if not isinstance(config, dict) or not digest:
            return {"ok": False, "code": "VALIDATION_FAILED",
                    "error": "apply_config requires config dict + digest"}
        from contracts.api import digest_of
        actual = digest_of(config)
        if actual != digest:
            return {"ok": False, "code": "DIGEST_MISMATCH",
                    "error": f"config digest {actual} != declared {digest}"}
        # 校验路径字段：watch_root 必须是绝对路径且存在（不存在则拒绝）
        wr = config.get("watch_root")
        if wr:
            if not os.path.isabs(wr):
                return {"ok": False, "code": "VALIDATION_FAILED",
                        "error": "watch_root must be absolute"}
            if not os.path.isdir(wr):
                return {"ok": False, "code": "VALIDATION_FAILED",
                        "error": f"watch_root does not exist: {wr}"}
        # 原子落地：先写同目录tmp，校验JSON可解析后os.replace；旧配置留.bak
        cfg_dir = os.path.dirname(self.db.path)
        final = os.path.join(cfg_dir, "agent-config-applied.json")
        bak = final + ".bak"
        tmp = os.path.join(cfg_dir, "agent-config.tmp.json")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"config": config, "digest": digest,
                           "applied_at": _now()}, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            json.load(open(tmp, encoding="utf-8"))  # 可解析性校验
            if os.path.exists(final):
                import shutil
                shutil.copy2(final, bak)
            os.replace(tmp, final)
        except Exception as exc:  # noqa: BLE001
            for p in (tmp,):
                if os.path.exists(p):
                    os.remove(p)
            return {"ok": False, "code": "CONFIG_APPLY_FAILED",
                    "error": f"atomic apply failed; old config kept: {exc}"}
        return {"ok": True, "applied_digest": digest, "backup": bak
                if os.path.exists(bak) else None}

    def _verify_upgrade_payload(self, payload: dict) -> dict:
        """upgrade（W13签名验证框架）：验签通过后仍显式拒绝安装（未实现），
        不假ACK成功；验签失败直接拒绝。"""
        from .upgrade_verify import verify_upgrade_bundle
        manifest = payload.get("manifest")
        signature = payload.get("signature", "")
        key_path = payload.get("public_key_path") or os.environ.get(
            "BLYB_UPGRADE_KEY", "")
        if not manifest or not signature or not key_path:
            return {"ok": False, "code": "CAPABILITY_UNSUPPORTED",
                    "error": "upgrade install not implemented; provide "
                             "manifest+signature+public_key_path for staged "
                             "verification"}
        verified, detail = verify_upgrade_bundle(manifest, signature, key_path)
        if not verified:
            return {"ok": False, "code": "SIGNATURE_INVALID", "error": detail}
        return {"ok": False, "code": "CAPABILITY_UNSUPPORTED",
                "signature_verified": True,
                "error": "signature verified but installer not implemented on this "
                         "build; operator must install upgrade package manually"}

    def _issue(self, category: str, obj: str, summary: str) -> None:
        row = self.db.one("SELECT issue_id FROM local_issue WHERE category=? AND"
                          " object_ref=?", (category, obj))
        if row:
            self.db.x("UPDATE local_issue SET last_seen=?, error_summary=? WHERE"
                      " issue_id=?", (_now(), summary, row["issue_id"]))
        else:
            self.db.x("INSERT INTO local_issue (issue_id, category, object_ref,"
                      " error_summary, first_seen, last_seen, next_action)"
                      " VALUES (?,?,?,?,?,?,?)",
                      (str(uuid.uuid4()), category, obj, summary, _now(), _now(),
                       "见设备采集页/人工处理"))

    # ------------------------------------------------------------ 主循环
    def run_once(self) -> dict:
        if getattr(self, "paused", False):
            # 暂停期间仍心跳（命令通道必须可用），但不发现/传输新任务
            stats = {"discovered": 0, "transferred": 0, "heartbeat": None,
                     "paused": True}
            try:
                stats["heartbeat"] = self.heartbeat()
            except Exception as exc:  # noqa: BLE001
                self._issue("heartbeat_failed", "core", str(exc)[:200])
            return stats
        stats = {"discovered": self.discover(), "transferred": 0, "heartbeat": None}
        try:
            stats["transferred"] = self.register_and_transfer()
        except RetryLater as exc:
            self._issue("transfer_retry", "batch", str(exc))
        try:
            stats["heartbeat"] = self.heartbeat()
        except Exception as exc:  # noqa: BLE001
            self._issue("heartbeat_failed", "core", str(exc)[:200])
        return stats

    def run_forever(self, interval: float = 2.0):
        while True:
            self.run_once()
            time.sleep(interval)


class RetryLater(Exception):
    pass


def _read_chunk(path: str, offset: int, length: int):
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read(length)
    if len(data) != length:
        raise RetryLater(f"short read {len(data)} != {length}")
    return hashlib.sha256(data).hexdigest(), data


def _disk_free(path: str) -> int:
    import shutil
    return shutil.disk_usage(path).free
