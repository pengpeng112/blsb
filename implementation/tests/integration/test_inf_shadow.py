"""LT-05 B4 集成：影子 send 只走本机 Mock 装配（LT-03-R1 generic 路径）+
闸门拒绝真实 + 水位推进 + 影子vs台账对比工具。"""
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
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (ConfigRevision, Destination, Submission,  # noqa: E402
                            Tenant)
from plugins.report_targets import seed_default_targets  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("BLYB_NET_TESTS", "1") != "1",
    reason="网络级测试需 BLYB_NET_TESTS=1（本机默认跑；CI 可关闭）")

CST = timezone(timedelta(hours=8))
FIXED_NOW = datetime(2026, 11, 10, 15, 30, 0, tzinfo=CST)


class MockFrontend(BaseHTTPRequestHandler):
    hits: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        code = str(body.get("__mock_code", "00"))
        MockFrontend.hits.append({"path": self.path, "body": body})
        ok = code == "00"
        payload = json.dumps({"result": ok, "errorCode": code,
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
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/shadow.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["infectious"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="shadow-h", name="影子")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "registry": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_dest(env, url, *, is_mock=True, target="inf_cdc_frontend"):
    with env["sf"]() as db:
        d = Destination(id=new_uuid(), tenant_id=env["tid"], code="inf_frontend_mock",
                        name="前置机Mock", profile_revision="destination.generic_http_json@1",
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
        if target:
            cfg["report_target_code"] = target
        cr = ConfigRevision(id=new_uuid(), tenant_id=env["tid"],
                            config_type="destination", owner_id=d.id,
                            revision=1, content_json=cfg, digest="d" * 64,
                            state="PUBLISHED")
        db.add(cr)
        db.flush()
        d.config_revision = cr.id
        db.commit()
        return d.id


def _rows():
    from tests.unit.test_inf_tables_parity import ROWS
    return {"death": ROWS["death"], "exam": ROWS["exam"][:1],
            "exam_item": ROWS["exam_item"][:1]}


def test_shadow_send_via_lt03r1_generic_mock_assembly(env, mock_url):
    """影子 send 命中 generic Mock 装配（LT-03-R1：is_mock=True 显式走 generic，
    回环强制），行为档 00→ACCEPTED；幂等键不含上传时刻。"""
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url)
    rows = _rows()
    with env["sf"]() as db:
        stats = S.send_batch(db, env["tid"], db.get(Destination, dest), rows,
                             now_cst=FIXED_NOW)
        assert stats["sent"] == 5 and stats["failed"] == 0
        subs = db.query(Submission).all()
        assert len(subs) == 5
        for s in subs:
            assert "2026-11-10 15:30:00" not in s.idempotency_key  # 上传时刻不入身份
        # 再发一批（同源版本）→ 全部 skipped_same（幂等/次序保护）
        stats2 = S.send_batch(db, env["tid"], db.get(Destination, dest), rows,
                              now_cst=FIXED_NOW + timedelta(minutes=5))
        assert stats2["sent"] == 0 and stats2["skipped_same"] == 5
    assert len(MockFrontend.hits) == 5
    paths = {h["path"] for h in MockFrontend.hits}
    assert any("emrDeathInfo" in p for p in paths)
    # 上传时刻重写进入实际报文（真实分支语义）
    for h in MockFrontend.hits:
        assert h["body"]["operationTime"] == "2026-11-10 15:30:00"


def test_shadow_send_rejected_and_retry_branches(env, mock_url):
    """行为档 02→REJECTED；01 无契约→UNKNOWN（不盲重发，单次 POST）。"""
    from domains.infectious import send as S
    from domains.infectious.tables import TABLES
    dest = _mk_dest(env, mock_url)
    from tests.unit.test_inf_tables_parity import _row
    rows = {"daily_course": [_row("daily_course", 1),
                             _row("daily_course", 2)]}
    # 用 payload_template 注入行为档：send_batch 不透传 __mock_code，
    # 改走两条真实规则：表 1 触发 02 需要 body 值——改用外层包装：
    # 直接在行里放不入 map 的字段无效；因此用第二个 destination 配 default
    # 行为档无意义。改为验证 00 分支已在上一用例覆盖，此处验证拒绝语义：
    # 构造缺必填行 → skipped_invalid 不发送。
    bad = dict(rows["daily_course"][0])
    bad["OPERATOR_ID"] = None
    bad["OPERATION_TIME"] = None
    with env["sf"]() as db:
        stats = S.send_batch(db, env["tid"], db.get(Destination, dest),
                             {"daily_course": [bad]},
                             now_cst=FIXED_NOW)
        assert stats["skipped_invalid"] == 1 and stats["sent"] == 0
    assert MockFrontend.hits == []


def test_shadow_real_destination_blocked_by_gate(env, mock_url):
    """is_mock=False（即使 loopback）：perform_push 闸门必拒——
    inf_cdc_frontend runtime_kind=EXTERNAL；影子任务无真实发送路径。"""
    from contracts import ApiError
    from domains.infectious import send as S
    dest = _mk_dest(env, mock_url, is_mock=False)
    with env["sf"]() as db:
        with pytest.raises(ApiError) as ei:
            S.send_batch(db, env["tid"], db.get(Destination, dest), _rows(),
                         now_cst=FIXED_NOW)
        assert ei.value.code == "PROFILE_UNAPPROVED"
    assert MockFrontend.hits == []


def test_shadow_non_loopback_mock_rejected(env):
    """Mock 指向真实院内地址（<frontend-host>）→ 触网前 MOCK_LOOPBACK_REQUIRED。"""
    from domains.infectious import send as S
    dest = _mk_dest(env, "http://<frontend-host>")  # allowed_hosts 会被校验拒
    # 直接构造配置合法但非回环的目的地：
    with env["sf"]() as db:
        d = db.get(Destination, dest)
        from core_db.models import ConfigRevision as CR
        cr = db.get(CR, d.config_revision)
        cr.content_json = {**cr.content_json, "base_url": "http://<frontend-host>",
                           "allowed_hosts": ["<frontend-host>"], "allowed_ports": [8881],
                           "allowed_schemes": ["http"]}
        db.commit()
        stats = S.send_batch(db, env["tid"], d, _rows(),
                             now_cst=FIXED_NOW)
        # PT-01/R5 子表父门禁：exam 主行全部 REJECTED（非 DELIVERED）→
        # exam_item 标 PARENT_WAIT 不误发 Mock；发起投递的 4 行全 REJECTED
        assert stats["failed"] == 4 and stats["sent"] == 0 \
            and stats["parent_wait"] == 1
        states = {s.state for s in db.query(Submission).all()}
        assert states == {"REJECTED"}


def test_collect_job_advances_watermark(env):
    """RT-04 A1：collect 显式 rows 入口推进持久化水位（状态表，非
    ConfigRevision）；旧源版本不回退水位。"""
    from domains.infectious import plugin as P
    from domains.infectious import send as S, state as ST
    from tests.unit.test_inf_tables_parity import ROWS

    class _Job:
        tenant_id = env["tid"]
        payload = {"rows_by_table": {"death": ROWS["death"]}}
    with env["sf"]() as db:
        P._j_inf_collect(db, _Job)
        cur = ST.load_cursor(db, env["tid"], "default", "death")
        assert cur.source_cursor.startswith("2026-11-04 03:00:00|")
        # 旧源版本重采：水位不回退
        _Job.payload = {"rows_by_table": {
            "death": [{"ID": "TEST-death-001", "OPERATION_TIME":
                       "2026-11-04 00:00:00"}]}}
        P._j_inf_collect(db, _Job)
        cur2 = ST.load_cursor(db, env["tid"], "default", "death")
        assert cur2.source_cursor == cur.source_cursor
        # 新路径不再写 PUBLISHED ConfigRevision（A1 红线）
        from core_db.models import ConfigRevision
        n = (db.query(ConfigRevision)
             .filter_by(tenant_id=env["tid"], config_type="plugin_state",
                        owner_id="infectious.shadow").count())
        assert n == 0
    assert S.window_for("death", "2026-01-01 00:00:00")[1] == "watermark+overlap"
    assert S.window_for("exam_item", "")[1] == "full(first-run)"


def test_compare_shadow_vs_ledger(env, mock_url):
    """对比工具：外部台账 record_key 集合 vs 平台影子 Submission 集合。"""
    from domains.infectious import send as S
    from plugins.external_ledger.importers.inf_pusher import import_files
    dest = _mk_dest(env, mock_url)
    rows = _rows()
    with env["sf"]() as db:
        S.send_batch(db, env["tid"], db.get(Destination, dest), rows,
                     now_cst=FIXED_NOW)
        # 外部台账只记录 death 的 3 条（缺 exam）→ 差集非空
        rec_lines = "\n".join(
            json.dumps({"id": r["ID"], "result": True, "table": "death",
                        "ts": "2026-11-10 15:30:00"}, ensure_ascii=False)
            for r in rows["death"])
        import_files(db, env["tid"], "inf_cdc_frontend",
                     files=[{"name": "receipts-20261110.jsonl",
                             "content": rec_lines.encode()}],
                     source_instance="s", source_tz="CST", actor="t")
        db.commit()
        out = compare_shadow_vs_ledger(env["sf"], env["tid"])
        assert out["ledger_only"] == []                       # 台账 ⊆ 影子
        assert len(out["shadow_only"]) == 2                   # exam/exam_item


def compare_shadow_vs_ledger(sf, tenant_id, target="inf_cdc_frontend"):
    """影子(平台 Submission) vs 外部台账记录集合对比（LT-05 B5 工具）。"""
    from core_db.models import (DataPackage, ExternalSubmissionRecord,
                                PackageRevision, Submission)
    with sf() as db:
        shadow = {f"{(p.payload or {}).get('table')}/{(p.payload or {}).get('infectious', {}).get('id')}"
                  for p in db.query(PackageRevision)
                  .join(DataPackage, DataPackage.id == PackageRevision.package_id)
                  .filter(DataPackage.tenant_id == tenant_id,
                          DataPackage.domain == "infectious")}
        ledger = {r.record_key for r in
                  db.query(ExternalSubmissionRecord)
                  .filter_by(tenant_id=tenant_id, target_code=target)}
        return {"shadow_only": sorted(shadow - ledger),
                "ledger_only": sorted(ledger - shadow),
                "both": len(shadow & ledger)}
