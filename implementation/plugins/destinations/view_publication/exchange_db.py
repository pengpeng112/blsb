"""交换发布库（独立数据库；对外视图只读已发布行）。

实现 05 §6：release_batch / projection_row / asset_route / tombstone /
publication_evidence。核心库与交换库之间不用假想原子事务，靠 generation 查证恢复。

R03/R05/R06/R07 整改后的版本模型：
- scope 身份 = (tenant_id, dataset_scope)。scope 唯一性不再依赖 destination.code 单值。
- 可见集 = dataset_current 业务键当前指针（增量模型）：发布某批次只替换该批次
  覆盖的业务键，不清除同 scope 其他病例的指针；更正病例 = 同键新指针。
- release_batch 保留 generation 记录；旧批次 SUPERSEDED 后其 asset_route 在
  保留窗口内仍可读（URL 稳定、字节固定），REVOKED/损坏才停止提供。
- route_id 由 uuid5(batch_id, asset_id) 派生，跨进程/重启稳定（R07）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS release_batch (
  batch_id TEXT PRIMARY KEY,
  publication_id TEXT NOT NULL,
  generation_id TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT '',
  dataset_scope TEXT NOT NULL DEFAULT '',
  state TEXT NOT NULL,             -- PREPARING / PUBLISHED / SUPERSEDED / REVOKED
  row_count INTEGER NOT NULL DEFAULT 0,
  manifest_digest TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  published_at TEXT,
  superseded_at TEXT
);
CREATE TABLE IF NOT EXISTS projection_row (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL REFERENCES release_batch(batch_id),
  dataset TEXT NOT NULL,           -- 视图名：chk_info / rpt_info / asset_info / metrics ...
  external_key TEXT NOT NULL,
  payload TEXT NOT NULL,           -- JSON
  UNIQUE (batch_id, dataset, external_key)
);
CREATE TABLE IF NOT EXISTS dataset_current (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT '',
  dataset_scope TEXT NOT NULL,
  dataset TEXT NOT NULL,
  external_key TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (tenant_id, dataset_scope, dataset, external_key)
);
CREATE TABLE IF NOT EXISTS asset_route (
  route_id TEXT PRIMARY KEY,
  batch_id TEXT NOT NULL REFERENCES release_batch(batch_id),
  asset_id TEXT NOT NULL,
  physical_key TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  sha256 TEXT NOT NULL,
  media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
  state TEXT NOT NULL DEFAULT 'PENDING'   -- PENDING / REACHABLE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_route_batch_asset ON asset_route(batch_id, asset_id);
CREATE TABLE IF NOT EXISTS tombstone (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  dataset TEXT NOT NULL,
  external_key TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publication_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  publication_id TEXT NOT NULL,
  kind TEXT NOT NULL,              -- visible / downloaded / receipt
  detail TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_proj_dataset ON projection_row(dataset, external_key);
"""


def _upgrade(conn: sqlite3.Connection) -> None:
    """旧交换库升级（编号迁移的等价实现，连接时幂等执行）。

    - exchange.002：补 tenant_id 列。
    - exchange.003：tombstone 唯一索引 (batch_id, dataset, external_key)
      （LG-05：并发/重复撤回不双写墓碑）。存量库若已有重复墓碑导致索引
      建不起来，记录告警并跳过——只读盘点交人工确认，不默删历史。
    """
    import logging
    log = logging.getLogger(__name__)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(release_batch)").fetchall()}
    if cols and "tenant_id" not in cols:
        conn.execute(
            "ALTER TABLE release_batch ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    idx = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND name='ux_tombstone_key'").fetchall()]
    if not idx:
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_tombstone_key"
                " ON tombstone(batch_id, dataset, external_key)")
            conn.commit()
        except sqlite3.IntegrityError:
            log.warning(
                "exchange.003 skipped: legacy duplicate tombstones exist; "
                "manual review required before the unique index can be created")


class ExchangeDb:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        _upgrade(self.conn)
        self._lock = threading.RLock()

    # ------------------------------------------------ 写侧（发布器）
    def begin_batch(self, publication_id: str, generation_id: str, dataset_scope: str,
                    manifest_digest: str, tenant_id: str = "") -> str:
        batch_id = f"batch-{generation_id}"
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO release_batch (batch_id, publication_id, generation_id,"
                " tenant_id, dataset_scope, state, manifest_digest, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (batch_id, publication_id, generation_id, tenant_id, dataset_scope,
                 "PREPARING", manifest_digest, _now()))
            self.conn.commit()
        return batch_id

    def upsert_projection(self, batch_id: str, dataset: str, external_key: str, payload: dict):
        with self._lock:
            self.conn.execute(
                "INSERT INTO projection_row (batch_id, dataset, external_key, payload)"
                " VALUES (?,?,?,?) ON CONFLICT(batch_id, dataset, external_key)"
                " DO UPDATE SET payload=excluded.payload",
                (batch_id, dataset, external_key, json.dumps(payload, ensure_ascii=False)))
            self.conn.commit()

    def add_asset_route(self, batch_id: str, asset_id: str, physical_key: str,
                        size_bytes: int, sha256: str, media_type: str) -> str:
        # uuid5 确定性派生：同 (batch, asset) 跨进程/重启同值（R07），不再用 hash()
        route_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"blyb:route:{batch_id}:{asset_id}"))
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO asset_route (route_id, batch_id, asset_id,"
                " physical_key, size_bytes, sha256, media_type, state)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (route_id, batch_id, asset_id, physical_key, size_bytes, sha256,
                 media_type, "PENDING"))
            self.conn.commit()
        return route_id

    def mark_asset_reachable(self, route_id: str):
        with self._lock:
            self.conn.execute("UPDATE asset_route SET state='REACHABLE' WHERE route_id=?",
                              (route_id,))
            self.conn.commit()

    def revoke_route(self, route_id: str):
        """安全隔离：单路由停止提供（保留记录）。"""
        with self._lock:
            self.conn.execute("UPDATE asset_route SET state='REVOKED' WHERE route_id=?",
                              (route_id,))
            self.conn.commit()

    def add_tombstone(self, batch_id: str, dataset: str, external_key: str, reason: str):
        """墓碑写入幂等（LG-05：exchange.003 唯一索引兜底并发双写）。"""
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO tombstone"
                " (batch_id, dataset, external_key, reason, created_at)"
                " VALUES (?,?,?,?,?)", (batch_id, dataset, external_key, reason, _now()))
            self.conn.commit()

    def revoke_batch(self, batch_id: str) -> dict:
        """撤回的结构化可见性处理（LG-13，单事务）：

        - release_batch 置 REVOKED（文件路由读侧 asset_route_by_id 因
          batch_state 不在 (PUBLISHED, SUPERSEDED) 而整体停供）；
        - 删除 dataset_current 中仍指向本批次的当前指针：这些业务键从
          结构化当前视图消失；同 scope 其他批次拥有的键不受影响
          （增量模型：dataset_current 指针归属哪个批次就随哪个批次撤回）。
        历史payload（projection_row/release_batch/路由记录）全部保留。
        """
        with self._lock:
            cur = self.conn
            n = cur.execute(
                "UPDATE release_batch SET state='REVOKED'"
                " WHERE batch_id=? AND state<>'REVOKED'", (batch_id,)).rowcount
            removed = cur.execute(
                "DELETE FROM dataset_current WHERE batch_id=?", (batch_id,)).rowcount
            cur.commit()
            return {"batch_state_updated": n, "current_pointers_removed": removed}

    def publish_batch(self, batch_id: str) -> int:
        """单事务：置PUBLISHED、旧代际转SUPERSEDED（仅账面，不清可见集）、
        刷新 dataset_current 当前指针（只覆盖本批次含有的业务键）。"""
        with self._lock:
            cur = self.conn
            row = cur.execute("SELECT generation_id, dataset_scope, tenant_id"
                              " FROM release_batch WHERE batch_id=?", (batch_id,)).fetchone()
            if row is None:
                raise ValueError("batch not found")
            count = cur.execute("SELECT COUNT(*) c FROM projection_row WHERE batch_id=?",
                                (batch_id,)).fetchone()["c"]
            cur.execute(
                "UPDATE release_batch SET state='SUPERSEDED', superseded_at=?"
                " WHERE tenant_id=? AND dataset_scope=? AND state='PUBLISHED' AND batch_id<>?",
                (_now(), row["tenant_id"], row["dataset_scope"], batch_id))
            for pr in cur.execute(
                    "SELECT dataset, external_key, payload FROM projection_row"
                    " WHERE batch_id=?", (batch_id,)).fetchall():
                cur.execute(
                    "INSERT INTO dataset_current (tenant_id, dataset_scope, dataset,"
                    " external_key, batch_id, payload, updated_at)"
                    " VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(tenant_id, dataset_scope, dataset, external_key)"
                    " DO UPDATE SET batch_id=excluded.batch_id, payload=excluded.payload,"
                    " updated_at=excluded.updated_at",
                    (row["tenant_id"], row["dataset_scope"], pr["dataset"],
                     pr["external_key"], batch_id, pr["payload"], _now()))
            n = cur.execute("UPDATE release_batch SET state='PUBLISHED', published_at=?,"
                            " row_count=? WHERE batch_id=? AND state='PREPARING'",
                            (_now(), count, batch_id)).rowcount
            cur.commit()
            return n

    def add_evidence(self, publication_id: str, kind: str, detail: dict):
        with self._lock:
            self.conn.execute(
                "INSERT INTO publication_evidence (publication_id, kind, detail, at)"
                " VALUES (?,?,?,?)",
                (publication_id, kind, json.dumps(detail, ensure_ascii=False), _now()))
            self.conn.commit()

    # ------------------------------------------------ 读侧（采集器）
    def current_generation(self, dataset_scope: str, tenant_id: str = "") -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM release_batch WHERE tenant_id=? AND dataset_scope=?"
                " AND state='PUBLISHED'"
                " ORDER BY published_at DESC LIMIT 1", (tenant_id, dataset_scope)).fetchone()
        return dict(row) if row else None

    def view_rows(self, dataset: str, dataset_scope: str, limit: int = 200,
                  cursor: int = 0, tenant_id: str = "") -> tuple[list[dict], int]:
        """读业务键当前指针集合（增量可见集），不再随最新generation整体切换。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, external_key, payload, batch_id FROM dataset_current"
                " WHERE tenant_id=? AND dataset_scope=? AND dataset=? AND id>?"
                " ORDER BY id LIMIT ?",
                (tenant_id, dataset_scope, dataset, cursor, limit)).fetchall()
        out = [{"external_key": r["external_key"], "payload": json.loads(r["payload"]),
                "cursor": r["id"], "batch_id": r["batch_id"]} for r in rows]
        return out, (rows[-1]["id"] if rows else 0)

    def asset_route_by_id(self, route_id: str) -> dict | None:
        """SUPERSEDED 批次路由在保留窗口内仍可读（R06）；REVOKED 不提供。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT ar.*, rb.state AS batch_state FROM asset_route ar"
                " JOIN release_batch rb ON rb.batch_id=ar.batch_id"
                " WHERE ar.route_id=?", (route_id,)).fetchone()
        if row is None or row["batch_state"] not in ("PUBLISHED", "SUPERSEDED"):
            return None
        if row["state"] not in ("REACHABLE",):
            return None
        return dict(row)

    def close(self):
        self.conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
