"""Agent 采集端故障注入（深度核查 H-10，设计03§2 损坏队列恢复）。"""
from __future__ import annotations

import glob
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from apps.edge_agent.local_db import LocalDb
from apps.edge_agent.service import AgentService


def _corrupt_bytes() -> bytes:
    return b"this is definitely not a sqlite database file " * 64


def test_H10_corrupt_queue_preserves_backup_and_recovers(tmp_path):
    """本地队列损坏：初始化成功、保留 .corrupt-* 副本、local_issue 记 queue_corrupted。

    修复前：损坏文件在 PRAGMA journal_mode 阶段即抛 DatabaseError，
    恢复路径不可达（AgentService 构造直接失败，与"不能拒绝启动"矛盾）。
    """
    qdir = tmp_path / "queue"
    qdir.mkdir()
    qpath = qdir / "agent-queue.sqlite3"
    qpath.write_bytes(_corrupt_bytes())
    svc = AgentService(
        watch_root=str(tmp_path / "watch"), core_url="http://127.0.0.1:9",
        storage_url="http://127.0.0.1:9", token="t", agent_id="agent-h10",
        local_db_path=str(qpath))
    # 初始化成功且换新库
    assert svc.db.recovered is True
    assert svc.db.one("SELECT COUNT(*) c FROM watched_root")["c"] == 1
    # 故障副本保留（供排查，不清空不覆盖）
    backups = glob.glob(str(qpath) + ".corrupt-*")
    assert len(backups) == 1
    with open(backups[0], "rb") as f:
        assert f.read() == _corrupt_bytes(), "副本必须是损坏原文件的完整拷贝"
    # local_issue 记录 queue_corrupted
    rows = svc.db.q("SELECT * FROM local_issue WHERE category='queue_corrupted'")
    assert rows and "corrupt" in rows[0]["error_summary"]
    # 新库可用：插入+查询往返
    svc.db.x("INSERT INTO acquisition_event (event_id, source_event_id,"
             " source_revision, state) VALUES ('e1','s1','1','DISCOVERED')")
    assert svc.db.one("SELECT COUNT(*) c FROM acquisition_event")["c"] == 1


def test_H10_corrupt_midlife_schema_loss(tmp_path):
    """数据页损坏（文件可读但quick_check失败）：同样走保留副本+重建。"""
    qdir = tmp_path / "q2"
    qdir.mkdir()
    qpath = qdir / "agent-queue.sqlite3"
    db = LocalDb(str(qpath))
    db.x("INSERT INTO acquisition_event (event_id, source_event_id, source_revision,"
         " state, manifest_json) VALUES ('e1','s1','1','PENDING',?)",
         ("x" * 20000,))
    db.conn.close()  # WAL checkpoint 回写主文件
    # 结构性损坏：定位插入内容所在的页，破坏其页头（页类型/指针非法）。
    # SQLite 无页校验和，破坏纯内容不会让 quick_check 失败，必须破坏页结构。
    raw = bytearray(qpath.read_bytes())
    off = raw.find(b"xxxxxxxx")
    assert off > 0, "应能定位插入的溢出内容"
    page_start = (off // 4096) * 4096
    raw[page_start:page_start + 8] = b"\x05\xff\xff\xff\xff\xff\xff\xff"
    qpath.write_bytes(bytes(raw))
    db2 = LocalDb(str(qpath))
    assert db2.recovered is True
    assert glob.glob(str(qpath) + ".corrupt-*"), "必须保留故障副本"
    assert db2.one("SELECT COUNT(*) c FROM acquisition_event")["c"] == 0, \
        "新库从空队列开始；已传字节靠服务端幂等恢复，不本地复活"


def test_H10_healthy_queue_untouched(tmp_path):
    """健康队列：不触发恢复、无副本、无 queue_corrupted。"""
    qpath = tmp_path / "q3" / "agent-queue.sqlite3"
    svc = AgentService(
        watch_root=str(tmp_path / "watch3"), core_url="http://127.0.0.1:9",
        storage_url="http://127.0.0.1:9", token="t", agent_id="agent-h10b",
        local_db_path=str(qpath))
    assert svc.db.recovered is False
    assert svc.db.corrupt_backup is None
    assert svc.db.q("SELECT * FROM local_issue WHERE category='queue_corrupted'") == []
