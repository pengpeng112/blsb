"""RT-04 A1/A2 集成：影子状态持久化、digest 幂等、六视图真实 SQL 采集。

- 合成源：SQLite 六张 **CREATE VIEW** 视图（视图名/列名=TABLES 注册表白名单，
  全 TEXT 列——前导零/日期串/空值原样保留）；
- 投递：本机回环 Mock 前置（generic 装配强制回环；真实发送被闸门拒绝）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (ConfigRevision, Destination, Submission,  # noqa: E402
                            Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("BLYB_NET_TESTS", "1") != "1",
    reason="网络级测试需 BLYB_NET_TESTS=1（本机默认跑；CI 可关闭）")

CST = timezone(timedelta(hours=8))
FIXED_NOW = datetime(2026, 11, 12, 15, 30, 0, tzinfo=CST)
SIX_VIEWS = ("death", "admission", "first_course", "daily_course",
             "exam", "exam_item")


class MockFrontend(BaseHTTPRequestHandler):
    hits: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        MockFrontend.hits.append({"path": self.path, "body": body})
        payload = json.dumps({"result": True, "errorCode": "00",
                              "desc": "mock"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def mock_url():
    MockFrontend.hits.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), MockFrontend)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt04st.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["infectious"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt04-st", name="影子状态")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "tmp": tmp_path}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_dest(env, url, *, is_mock=True):
    with env["sf"]() as db:
        d = Destination(id=new_uuid(), tenant_id=env["tid"],
                        code="inf_frontend_mock", name="前置机Mock",
                        profile_revision="destination.generic_http_json@1",
                        mode="PUSH", is_mock=is_mock, status="ENABLED")
        db.add(d)
        db.flush()
        cfg = {"base_url": url, "allowed_hosts": ["127.0.0.1"],
               "allowed_schemes": ["http"],
               "allowed_ports": [int(url.rsplit(":", 1)[-1])],
               "timeout_seconds": 5, "max_attempts": 3,
               "backoff_seconds": [0.01] * 3, "rate_limit_per_minute": 6000,
               "path_template": "/hclient/emr/receive/{__endpoint}",
               "receipt_rules": [
                   {"json_path": "result", "op": "eq", "value": True,
                    "outcome": "ACCEPTED"},
                   {"json_path": "errorCode", "op": "in",
                    "value": ["01", "99"], "outcome": "RETRYABLE"},
                   {"json_path": "errorCode", "op": "exists",
                    "outcome": "REJECTED"}],
               "default_outcome": "UNKNOWN"}
        cr = ConfigRevision(id=new_uuid(), tenant_id=env["tid"],
                            config_type="destination", owner_id=d.id,
                            revision=1, content_json=cfg, digest="d" * 64,
                            state="PUBLISHED")
        db.add(cr)
        db.flush()
        d.config_revision = cr.id
        db.commit()
        return d.id


# ---------------------------------------------------------------- 合成源视图
def make_source_db(path: str, *, extra_rows: dict | None = None):
    """合成 SQLite 视图源：为**全部注册表**建底表+CREATE VIEW（六表重点
    装数，5 新表建空视图——采集默认全表顺序不因缺视图报错）；全 TEXT 列
    ——前导零/日期串/空值原样保留。"""
    from sqlalchemy import create_engine, text
    from domains.infectious.tables import TABLES
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as c:
        for name in TABLES:
            cols = sorted(TABLES[name]["map"].keys())
            col_defs = ", ".join(f'"{col}" TEXT' for col in cols)
            base = f"base_{name}"
            c.execute(text(f'DROP TABLE IF EXISTS "{base}"'))
            c.execute(text(f'CREATE TABLE "{base}" ({col_defs})'))
            c.execute(text(f'DROP VIEW IF EXISTS "{TABLES[name]["view"]}"'))
            c.execute(text(
                f'CREATE VIEW "{TABLES[name]["view"]}" '
                f'AS SELECT * FROM "{base}"'))
        for name, rows in (extra_rows or {}).items():
            for row in rows:
                keys = ", ".join(f'"{k}"' for k in row)
                vals = ", ".join(f':{k}' for k in row)
                c.execute(text(
                    f'INSERT INTO "base_{name}" ({keys}) VALUES ({vals})'),
                    row)
    return engine


def _death_row(rid, op, *, idcard="0000"):
    from domains.infectious.tables import TABLES
    cols = TABLES["death"]["map"].keys()
    base = {c: None for c in cols}
    base.update({
        "ID": rid, "PATIENT_ID": "P" + rid, "SERIAL_NUMBER": "0000123",
        "PATIENT_NAME": "测试患者", "ID_CARD_TYPE_CODE": "01",
        "ID_CARD_TYPE_NAME": "居民身份证", "ID_CARD": idcard,
        "ORG_CODE": "ORG1", "ORG_NAME": "测试医院", "DEPT_CODE": "D1",
        "DEPT_NAME": "测试科", "OPERATOR_ID": "DOC001",
        "OPERATION_TIME": op, "ACTIVITY_TYPE_CODE": "05",
        "ACTIVITY_TYPE_NAME": "死亡", "DEAD_DATE": "2026-11-01",
        "DIRECT_CAUSE_CODE": "A00", "DIRECT_CAUSE_NAME": "测试病因",
    })
    return base


def _exam_row(rid, op):
    from domains.infectious.tables import TABLES
    cols = TABLES["exam"]["map"].keys()
    base = {c: None for c in cols}
    base.update({
        "ID": rid, "PATIENT_ID": "P" + rid, "SERIAL_NUMBER": "0000123",
        "PATIENT_NAME": "测试患者", "ID_CARD_TYPE_CODE": "01",
        "ID_CARD_TYPE_NAME": "居民身份证", "ID_CARD": "0000",
        "ORG_CODE": "ORG1", "ORG_NAME": "测试医院", "DEPT_CODE": "D1",
        "DEPT_NAME": "测试科", "OPERATOR_ID": "DOC001",
        "OPERATION_TIME": op, "ACTIVITY_TYPE_CODE": "04",
        "ACTIVITY_TYPE_NAME": "检查",
        "APPLY_DEPT_CODE": "D1", "APPLY_DEPT_NAME": "测试科",
        "EXAMINATION_TYPE_CODE": "CT01", "EXAMINATION_TYPE_NAME": "CT",
    })
    return base


def _exam_item_row(rid, op, parent, *, with_operator=True):
    from domains.infectious.tables import TABLES
    cols = TABLES["exam_item"]["map"].keys()
    base = {c: None for c in cols}
    base.update({
        "ID": rid, "EX_CLINICAL_ID": parent, "ITEM_CODE": "X01",
        "ITEM_NAME": "测试项目", "OPERATION_TIME": op,
    })
    if with_operator:
        base["OPERATOR_ID"] = "DOC001"
    return base


# ================================================================ A1
def test_a1_real_digest_and_snapshot_consistency(env, mock_url):
    """真64位 digest=canonical SHA256；Submission.snapshot_digest 与其一致。"""
    from contracts.api import canonical_json, digest_of
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    rows = {"death": [_death_row("D001", "2026-11-10 08:00:00")]}
    with env["sf"]() as db:
        S.send_batch(db, env["tid"], db.get(Destination, dest), rows,
                     now_cst=FIXED_NOW)
        subs = db.query(Submission).all()
        assert len(subs) == 1
        pr_id = subs[0].package_revision_id
        from core_db.models import PackageRevision
        pr = db.get(PackageRevision, pr_id)
        assert pr.digest == digest_of(pr.payload)
        assert len(pr.digest) == 64 and pr.digest == subs[0].snapshot_digest
        # payload 里有实际冻结内容（非空、非仅源ID哈希）
        assert pr.payload["infectious"]["id"] == "D001"


def test_a1_same_source_version_across_upload_times(env, mock_url):
    """同一源版本跨不同上传时刻重采 → 只产生一笔投递（幂等键不含上传时刻）。"""
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    rows = {"death": [_death_row("D001", "2026-11-10 08:00:00")]}
    with env["sf"]() as db:
        s1 = S.send_batch(db, env["tid"], db.get(Destination, dest), rows,
                          now_cst=FIXED_NOW)
        s2 = S.send_batch(db, env["tid"], db.get(Destination, dest), rows,
                          now_cst=FIXED_NOW + timedelta(days=3))
        assert s1["sent"] == 1
        assert s2["sent"] == 0 and s2["skipped_same"] == 1
        assert db.query(Submission).count() == 1
    assert len(MockFrontend.hits) == 1


def test_a1_new_source_version_new_snapshot(env, mock_url):
    """新源版本 → 新不可变快照 + 新 Submission（旧投递结果清空重发）。"""
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    r1 = _death_row("D001", "2026-11-10 08:00:00")
    r2 = _death_row("D001", "2026-11-11 09:00:00")
    with env["sf"]() as db:
        S.send_batch(db, env["tid"], db.get(Destination, dest),
                     {"death": [r1]}, now_cst=FIXED_NOW)
        S.send_batch(db, env["tid"], db.get(Destination, dest),
                     {"death": [r2]}, now_cst=FIXED_NOW)
        assert db.query(Submission).count() == 2      # 两个源版本各一笔
    assert len(MockFrontend.hits) == 2


def test_a1_same_time_different_content_conflict(env, mock_url):
    """同源时间不同内容 → 显式 CONFLICT，不静默覆盖、不新 Submission。"""
    from core_db.models import InfectiousShadowRecord
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    r1 = _death_row("D001", "2026-11-10 08:00:00", idcard="0001")
    r2 = _death_row("D001", "2026-11-10 08:00:00", idcard="0002")
    with env["sf"]() as db:
        s1 = S.send_batch(db, env["tid"], db.get(Destination, dest),
                          {"death": [r1]}, now_cst=FIXED_NOW)
        s2 = S.send_batch(db, env["tid"], db.get(Destination, dest),
                          {"death": [r2]}, now_cst=FIXED_NOW)
        assert s1["sent"] == 1
        assert s2["skipped_conflict"] == 1 and s2["sent"] == 0
        rec = (db.query(InfectiousShadowRecord)
               .filter_by(source_record_key="D001").one())
        assert rec.state == "CONFLICT"
        assert db.query(Submission).count() == 1


def test_a1_old_version_blocked_after_restart(env, mock_url):
    """旧源版本拦截依赖持久化状态：新 Session（进程重启模拟）后仍有效。"""
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    with env["sf"]() as db:
        S.send_batch(db, env["tid"], db.get(Destination, dest),
                     {"death": [_death_row("D001", "2026-11-11 09:00:00")]},
                     now_cst=FIXED_NOW)
    # 新 Session（旧内存 sent_state 不复存在）
    with env["sf"]() as db:
        s2 = S.send_batch(db, env["tid"], db.get(Destination, dest),
                          {"death": [_death_row("D001", "2026-11-10 08:00:00")]},
                          now_cst=FIXED_NOW)
        assert s2["skipped_old_version"] == 1 and s2["sent"] == 0
        assert db.query(Submission).count() == 1


def test_a1_no_published_config_revision_and_legacy_import(env):
    """新路径零写 PUBLISHED ConfigRevision；旧配置只读一次性迁入（幂等、
    字节语义不变）。"""
    from domains.infectious import state as ST
    with env["sf"]() as db:
        # 造旧状态（模拟 LT-05 存量）
        cr = ConfigRevision(
            id=new_uuid(), tenant_id=env["tid"], config_type="plugin_state",
            owner_id="infectious.shadow", revision=7,
            content_json={"watermarks": {"death": "2026-11-01 00:00:00"},
                          "sent": {"death/D900": "2026-10-31 08:00:00"}},
            digest="legacy-digest", state="PUBLISHED")
        db.add(cr)
        db.commit()
        before = (cr.revision, json.dumps(cr.content_json, sort_keys=True),
                  cr.digest, cr.state)
        out = ST.migrate_legacy_config(db, env["tid"])
        db.commit()
        assert out["imported_cursors"] == 1 and out["imported_records"] == 1
        cur = ST.load_cursor(db, env["tid"], "default", "death")
        assert cur.source_cursor == "2026-11-01 00:00:00"
        assert cur.legacy_imported is True
        rec = ST.get_record(db, env["tid"], "default", "death", "D900")
        assert rec.state == "DELIVERED" and rec.source_op_time == \
            "2026-10-31 08:00:00"
        # 旧配置字节语义不变
        after = (cr.revision, json.dumps(cr.content_json, sort_keys=True),
                 cr.digest, cr.state)
        assert before == after
        # 幂等：重复迁入零动作
        out2 = ST.migrate_legacy_config(db, env["tid"])
        assert out2["skipped"] is True and out2["imported_cursors"] == 0
        # 新路径写入不新增 plugin_state 配置
        from domains.infectious import collect as C
        C.collect_rows(db, env["tid"], rows_by_table={
            "death": [_death_row("D001", "2026-11-10 08:00:00")]})
        n = (db.query(ConfigRevision)
             .filter_by(tenant_id=env["tid"], config_type="plugin_state",
                        owner_id="infectious.shadow").count())
        assert n == 1                    # 只有旧的那一条，未被修改/新增


def test_a1_missing_source_op_time_is_quality_failure(env, mock_url):
    """缺 source_op_time → 显式质量失败（不得用上传时刻伪造源版本）。"""
    from core_db.models import InfectiousShadowRecord
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    row = _death_row("D001", "")
    with env["sf"]() as db:
        stats = S.send_batch(db, env["tid"], db.get(Destination, dest),
                             {"death": [row]}, now_cst=FIXED_NOW)
        assert stats["skipped_invalid"] == 1 and stats["sent"] == 0
        rec = db.query(InfectiousShadowRecord).one()
        assert rec.state == "QUALITY_WAIT"
        assert "MISSING_SOURCE_OP_TIME" in rec.last_error
    assert MockFrontend.hits == []


# ================================================================ A2
def test_a2_real_view_collect_map_send_chain(env, mock_url, tmp_path):
    """真实六 SQLite 视图 → SQL keyset 采集 → 映射 → 冻结排队 →
    send_pending → generic 回环 Mock 实际命中 → Submission 结果账。"""
    from domains.infectious import collect as C, send as S
    src = make_source_db(str(tmp_path / "src.sqlite3"), extra_rows={
        "death": [_death_row("D001", "2026-11-12 09:00:00")],
        "exam": [_exam_row("E001", "2026-11-12 10:00:00")],
        "exam_item": [_exam_item_row("I001", "2026-11-12 10:30:00", "E001",
                                     with_operator=False)],
    })
    dest = _mk_dest(env, mock_url)
    try:
        with src.connect() as conn, env["sf"]() as db:
            out = C.collect_from_connection(conn, db, env["tid"], now=FIXED_NOW)
            t = out["tables"]
            assert t["death"]["frozen"] == 1
            assert t["exam"]["frozen"] == 1
            # 子表操作人来自父（安全补查或当前窗 parent_ops）
            assert t["exam_item"]["frozen"] == 1
            stats = S.send_pending(db, env["tid"], db.get(Destination, dest),
                                   now_cst=FIXED_NOW)
            assert stats["sent"] == 3 and stats["failed"] == 0
            subs = db.query(Submission).all()
            assert len(subs) == 3
            for s in subs:
                assert s.state == "ACCEPTED"
        # 回环实际命中（真实 HTTP 到 Mock 前置）+ 端点寻址正确
        assert len(MockFrontend.hits) == 3
        paths = {h["path"] for h in MockFrontend.hits}
        assert any("emrDeathInfo" in p for p in paths)
        assert any("emrExClinical" in p for p in paths)
        assert any("emrExClinicalItem" in p for p in paths)
        # 上传时间重写=接口上传时刻（真实分支语义）
        for h in MockFrontend.hits:
            assert h["body"]["operationTime"] == "2026-11-12 15:30:00"
    finally:
        src.dispose()


def test_a2_same_timestamp_over_500_rows_no_loss(env, tmp_path):
    """同时间戳 >500 条：keyset 分页 (op,id) 配对——全量采集零丢失。"""
    from domains.infectious import collect as C
    rows = [_exam_row(f"E{n:04d}", "2026-11-12 10:00:00") for n in range(600)]
    src = make_source_db(str(tmp_path / "src2.sqlite3"),
                         extra_rows={"exam": rows})
    try:
        with src.connect() as conn, env["sf"]() as db:
            out = C.collect_from_connection(conn, db, env["tid"],
                                            tables=["exam"],
                                            limit_per_table=500, now=FIXED_NOW)
            t = out["tables"]["exam"]
            assert t["rows_scanned"] == 600 and t["frozen"] == 600
            assert t["pages"] >= 2                       # 确实分页
            from core_db.models import InfectiousShadowRecord
            n = (db.query(InfectiousShadowRecord)
                 .filter_by(table_key="exam").count())
            assert n == 600
    finally:
        src.dispose()


def test_a2_cross_page_parent_and_missing_parent_quality_wait(env, tmp_path):
    """跨页父子：父在窗外按父键安全补查；父缺失→可见质量等待（不编造/
    不丢子项）。"""
    from domains.infectious import collect as C
    from core_db.models import InfectiousShadowRecord
    rows = ([_exam_row("EOLD", "2026-11-12 10:00:00")]
            + [_exam_item_row(f"I{n:03d}", "2026-11-12 11:00:00", "EOLD",
                              with_operator=False) for n in range(3)]
            + [_exam_item_row("I999", "2026-11-12 11:00:00", "E MISSING",
                              with_operator=False)])
    src = make_source_db(str(tmp_path / "src3.sqlite3"), extra_rows={
        "exam": rows[:1], "exam_item": rows[1:]})
    try:
        with src.connect() as conn, env["sf"]() as db:
            out = C.collect_from_connection(conn, db, env["tid"],
                                            tables=["exam", "exam_item"],
                                            limit_per_table=500, now=FIXED_NOW)
            assert out["tables"]["exam"]["frozen"] == 1
            ti = out["tables"]["exam_item"]
            assert ti["frozen"] == 3          # 父补查成功（EOLD 在当前窗）
            assert ti["quality_wait"] == 1    # E MISSING 无父 → 质量等待
            rec = (db.query(InfectiousShadowRecord)
                   .filter_by(source_record_key="I999").one())
            assert rec.state == "QUALITY_WAIT"
            assert any("operatorId" in q for q in rec.quality_issues)
    finally:
        src.dispose()


def test_a2_parent_outside_window_backfill(env, tmp_path):
    """父记录不在当前时间窗（增量窗起点之后）→ 按父键补查仍可关联。"""
    from domains.infectious import collect as C, state as ST
    rows = {"exam": [_exam_row("EOLD", "2026-11-01 08:00:00")],
            "exam_item": [_exam_item_row("I001", "2026-11-12 11:00:00",
                                         "EOLD", with_operator=False)]}
    src = make_source_db(str(tmp_path / "src4.sqlite3"), extra_rows=rows)
    try:
        with src.connect() as conn, env["sf"]() as db:
            # 先采 exam（水位到 11-01），再单独采 exam_item（增量窗不含父）
            C.collect_from_connection(conn, db, env["tid"],
                                      tables=["exam"], now=FIXED_NOW)
            out = C.collect_from_connection(conn, db, env["tid"],
                                            tables=["exam_item"],
                                            now=FIXED_NOW)
            assert out["tables"]["exam_item"]["frozen"] == 1
            with env["sf"]() as db2:
                rec = (db2.query(
                    __import__("core_db.models", fromlist=[
                        "InfectiousShadowRecord"]).InfectiousShadowRecord)
                    .filter_by(source_record_key="I001").one())
                assert rec.state == "PENDING"          # 补查成功非质量等待
                assert rec.payload_json.get("operatorId") == "DOC001"
    finally:
        src.dispose()


def test_a2_processing_failure_does_not_advance_cursor(env, tmp_path,
                                                       monkeypatch):
    """行级处理失败 → 该行 FAILED、水位停在其前（失败不越水位）；下一班
    从重叠窗重扫补上。"""
    from domains.infectious import collect as C, mapping, state as ST
    rows = [_death_row(f"D{n:03d}", f"2026-11-12 09:{n:02d}:00")
            for n in range(1, 4)]
    src = make_source_db(str(tmp_path / "src5.sqlite3"),
                         extra_rows={"death": rows})
    orig = mapping.build_payload
    calls = {"n": 0}

    def flaky(table, row, parent_ops):
        if row.get("ID") == "D002":
            raise RuntimeError("synthetic processing failure")
        return orig(table, row, parent_ops)

    try:
        with src.connect() as conn, env["sf"]() as db:
            monkeypatch.setattr(mapping, "build_payload", flaky)
            out = C.collect_from_connection(conn, db, env["tid"],
                                            tables=["death"], now=FIXED_NOW)
            t = out["tables"]["death"]
            assert t["failed"] == 1 and t["frozen"] == 1
            cur = ST.load_cursor(db, env["tid"], "default", "death")
            # 水位停在 D001（失败行 D002 之前），不越过
            assert cur.source_cursor == "2026-11-12 09:01:00|D001"
        with src.connect() as conn, env["sf"]() as db:
            monkeypatch.undo()
            out2 = C.collect_from_connection(conn, db, env["tid"],
                                             tables=["death"], now=FIXED_NOW)
            t2 = out2["tables"]["death"]
            assert t2["frozen"] >= 1          # D002/D003 从重叠窗补上
            cur2 = ST.load_cursor(db, env["tid"], "default", "death")
            assert cur2.source_cursor == "2026-11-12 09:03:00|D003"
    finally:
        src.dispose()
    _ = calls


def test_a2_restart_recollection_idempotent(env, mock_url, tmp_path):
    """断点恢复/并发重复采集幂等：重复 collect 零新快照；再 send 零新投递。"""
    from domains.infectious import collect as C, send as S
    src = make_source_db(str(tmp_path / "src6.sqlite3"), extra_rows={
        "death": [_death_row("D001", "2026-11-12 09:00:00")]})
    dest = _mk_dest(env, mock_url)
    try:
        with src.connect() as conn, env["sf"]() as db:
            C.collect_from_connection(conn, db, env["tid"], now=FIXED_NOW)
        with src.connect() as conn, env["sf"]() as db:
            out = C.collect_from_connection(conn, db, env["tid"], now=FIXED_NOW)
            assert out["tables"]["death"]["duplicate_version"] == 1
            assert out["tables"]["death"]["frozen"] == 0
        with env["sf"]() as db:
            s1 = S.send_pending(db, env["tid"], db.get(Destination, dest),
                                now_cst=FIXED_NOW)
            s2 = S.send_pending(db, env["tid"], db.get(Destination, dest),
                                now_cst=FIXED_NOW)
            assert s1["sent"] == 1
            assert s2["sent"] == 0            # 已发不重发（含新会话）
            assert db.query(Submission).count() == 1
        assert len(MockFrontend.hits) == 1
    finally:
        src.dispose()


def test_a2_collect_job_requires_source_config(env):
    """inf.collect 主入口：无连接配置 → 显式能力不可用（不静默、不落内存
    假采集）。"""
    from connector_sdk import CapabilityError
    from domains.infectious.plugin import _j_inf_collect

    class _Job:
        tenant_id = env["tid"]
        payload = {}
    os.environ.pop("BLYB_INF_SHADOW_SOURCE_URL", None)
    with env["sf"]() as db:
        with pytest.raises(CapabilityError) as ei:
            _j_inf_collect(db, _Job)
        assert ei.value.code == "INF_SOURCE_UNCONFIGURED"


def test_a2_collect_job_real_sql_path(env, mock_url, tmp_path):
    """inf.collect job 走真实 SQL（payload.database_url 指向合成源库）。"""
    from domains.infectious.plugin import _j_inf_collect
    src = make_source_db(str(tmp_path / "src7.sqlite3"), extra_rows={
        "death": [_death_row("D001", "2026-11-12 09:00:00")]})
    try:
        class _Job:
            tenant_id = env["tid"]
            payload = {"database_url": f"sqlite:///{tmp_path}/src7.sqlite3"}
        with env["sf"]() as db:
            _j_inf_collect(db, _Job)
            assert _Job.payload["tables"]["death"]["frozen"] == 1
    finally:
        src.dispose()
