"""C-22 万级待传队列（设计02 §8 工程验收负载，合成数据）。

Agent 本地 SQLite 插入 1 万条待传事件后：
- discover() 空转（无新清单）耗时可接受；
- 取队查询（register_and_transfer 的 pending 拉取）与心跳队列深度统计耗时可接受。
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from apps.edge_agent.local_db import LocalDb
from apps.edge_agent.service import AgentService

N_EVENTS = 10_000
# 经验阈值（本机SSD实测余量约两个数量级；超阈值视为索引/查询计划回归）
BUDGET_DISCOVER_S = 2.0
BUDGET_QUERY_S = 1.0


def _fill_queue(db: LocalDb, n: int) -> None:
    """批量插入 n 条 PENDING 待传事件（单事务，接近真实批量发现）。"""
    manifest = json.dumps({
        "schema_version": "1.0", "event_id": "ev", "agent_id": "a",
        "source_system_code": "scanner-simulator", "root_id": "t",
        "captured_at": "2026-09-30T08:00:00+08:00",
        "source_scan_id": "s", "source_revision": "1",
        "identity_hints": {"barcode_raw": "900001"},
        "completion_evidence": {}, "files": []})
    rows = [(str(uuid.uuid4()), f"ev-{i}", "1", manifest, "{}", "DISCOVERED")
            for i in range(n)]
    db.conn.executemany(
        "INSERT INTO acquisition_event (event_id, source_event_id, source_revision,"
        " manifest_json, completion_evidence, state) VALUES (?,?,?,?,?,?)", rows)
    db.conn.commit()


def test_C22_queue_10k_discover_and_fetch(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    qpath = tmp_path / "agent-queue.sqlite3"
    svc = AgentService(watch_root=str(watch), core_url="http://127.0.0.1:9",
                       storage_url="http://127.0.0.1:9", token="t",
                       agent_id="agent-c22", local_db_path=str(qpath))
    t_fill0 = time.perf_counter()
    _fill_queue(svc.db, N_EVENTS)
    t_fill = time.perf_counter() - t_fill0
    assert svc.db.one("SELECT COUNT(*) c FROM acquisition_event")["c"] == N_EVENTS

    # discover 空转：无新清单时只做目录遍历，不应随队列长度退化
    t0 = time.perf_counter()
    found = svc.discover()
    t_discover = time.perf_counter() - t0
    assert found == 0
    assert t_discover < BUDGET_DISCOVER_S, \
        f"discover空转{t_discover:.3f}s 超预算{BUDGET_DISCOVER_S}s"

    # 取队查询：register_and_transfer 每批拉取的 pending 查询
    sql = ("SELECT event_id, manifest_json FROM acquisition_event "
           "WHERE state IN ('DISCOVERED','REGISTERED','TRANSFERRING') LIMIT 5")
    t1 = time.perf_counter()
    batch = svc.db.q(sql)
    t_fetch = time.perf_counter() - t1
    assert len(batch) == 5
    assert t_fetch < BUDGET_QUERY_S, f"取队查询{t_fetch:.3f}s 超预算{BUDGET_QUERY_S}s"

    # 心跳队列深度统计（每轮心跳都会执行）
    t2 = time.perf_counter()
    pending = svc.db.one(
        "SELECT COUNT(*) c FROM acquisition_event "
        "WHERE state NOT IN ('UPLOADED')")["c"]
    t_count = time.perf_counter() - t2
    assert pending == N_EVENTS
    assert t_count < BUDGET_QUERY_S, f"队列深度统计{t_count:.3f}s 超预算{BUDGET_QUERY_S}s"

    print(f"\n[C-22] fill={t_fill:.3f}s discover_idle={t_discover:.3f}s "
          f"fetch_batch5={t_fetch*1000:.1f}ms count_pending={t_count*1000:.1f}ms")


def test_C22_queue_state_index_used(tmp_path):
    """取队走 state 索引：EXPLAIN QUERY PLAN 不出现全表扫描。"""
    qpath = tmp_path / "agent-queue.sqlite3"
    db = LocalDb(str(qpath))
    plan = db.q("EXPLAIN QUERY PLAN SELECT event_id FROM acquisition_event "
                "WHERE state IN ('DISCOVERED','REGISTERED','TRANSFERRING')")
    detail = " ".join(r["detail"] for r in plan)
    assert "SCAN" not in detail.upper() or "INDEX" in detail.upper(), \
        f"取队查询应使用索引（plan: {detail}）"
