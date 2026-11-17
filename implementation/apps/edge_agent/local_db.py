"""Agent 本地 SQLite 队列（03 §2 表结构）。WAL；恢复时做完整性检查。"""
from __future__ import annotations

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_identity (
  agent_id TEXT PRIMARY KEY, tenant_id TEXT, cert_fingerprint TEXT DEFAULT '',
  enrolled_at TEXT, config_version INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS watched_root (
  root_id TEXT PRIMARY KEY, normalized_path TEXT UNIQUE, recursive INTEGER DEFAULT 1,
  file_filter TEXT DEFAULT '', enabled INTEGER DEFAULT 1,
  last_full_scan TEXT);
CREATE TABLE IF NOT EXISTS discovery_entry (
  entry_id TEXT PRIMARY KEY, root_id TEXT, relative_path TEXT,
  size_bytes INTEGER, mtime TEXT, first_seen TEXT, scan_batch TEXT,
  generation INTEGER DEFAULT 1,
  UNIQUE (root_id, relative_path, generation));
CREATE TABLE IF NOT EXISTS acquisition_event (
  event_id TEXT PRIMARY KEY, source_event_id TEXT, source_revision TEXT,
  manifest_json TEXT, completion_evidence TEXT, state TEXT DEFAULT 'PENDING',
  UNIQUE (source_event_id, source_revision));
CREATE INDEX IF NOT EXISTS idx_acquisition_event_state ON acquisition_event(state);
CREATE TABLE IF NOT EXISTS transfer_local (
  transfer_id TEXT PRIMARY KEY, asset_id TEXT, session_id TEXT,
  session_epoch INTEGER DEFAULT 1, file_snapshot TEXT,
  total_chunks INTEGER DEFAULT 0, state TEXT DEFAULT 'PENDING',
  next_retry_at TEXT, attempts INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chunk_local (
  transfer_id TEXT, chunk_index INTEGER, size_bytes INTEGER, hash TEXT,
  acked_at TEXT, UNIQUE (transfer_id, chunk_index));
CREATE TABLE IF NOT EXISTS command_inbox (
  command_id TEXT PRIMARY KEY, type TEXT, config_version INTEGER,
  received_at TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS local_issue (
  issue_id TEXT PRIMARY KEY, category TEXT, object_ref TEXT,
  error_summary TEXT, first_seen TEXT, last_seen TEXT, next_action TEXT);
CREATE TABLE IF NOT EXISTS dir_candidate (
  candidate_id TEXT PRIMARY KEY, root_id TEXT, relative_path TEXT,
  size_bytes INTEGER, mtime TEXT, sha256 TEXT, snapshot_version INTEGER DEFAULT 1,
  state TEXT DEFAULT 'CANDIDATE',
  evidence_json TEXT DEFAULT '{}', created_at TEXT, decided_at TEXT,
  decided_by TEXT, reason TEXT,
  UNIQUE (root_id, relative_path, snapshot_version));
"""


class LocalDb:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.recovered = False
        self.path = path
        self.corrupt_backup = None
        if not self._open_and_check():
            self._recover_from_corruption()

    def _open_and_check(self) -> bool:
        """打开并建表；文件不可读或完整性检查失败返回 False（由恢复路径接手）。"""
        try:
            self.conn = sqlite3.connect(self.path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._configure()
            return self.integrity_check()
        except sqlite3.DatabaseError:
            # 损坏文件在 PRAGMA 阶段即抛 DatabaseError（"file is not a database"）
            conn = getattr(self, "conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            return False

    def _recover_from_corruption(self):
        """设计03§2：不得清空重传；保留故障副本供排查，换新库继续。

        已传字节靠服务端会话幂等键恢复，不重传已确认分片。
        副本保留后删除原文件（含WAL边车）重建空库——损坏库可能连
        DROP TABLE 都无法执行，不能走"原连接上删表重建"的路径。
        """
        import shutil
        import time as _time
        conn = getattr(self, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        backup = f"{self.path}.corrupt-{int(_time.time())}"
        shutil.copy2(self.path, backup)
        self.corrupt_backup = backup
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.path + suffix)
            except OSError:
                pass
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._configure()
        self.recovered = True

    def _configure(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def integrity_check(self) -> bool:
        try:
            row = self.conn.execute("PRAGMA quick_check").fetchone()
            return row is not None and row[0] == "ok"
        except sqlite3.DatabaseError:
            return False

    def q(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def x(self, sql: str, params: tuple = ()) -> int:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.rowcount

    def one(self, sql: str, params: tuple = ()):
        return self.conn.execute(sql, params).fetchone()
