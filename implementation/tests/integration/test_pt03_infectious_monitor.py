"""PT-03 集成测试：传染病全维监控专页后端（importer 三硬伤 + 遥测迁移 +
流式只读同步 + 5 个 monitor 端点 + 13 表完整分类）。

覆盖（任务书 §3.1，≥25 用例）：
- 混合时区零 8h 偏差（receipts=UTC + sent=CST → AUTO_MIXED 解析正确）；
- parent-wait 单列（PENDING/PARENT_WAITING，绝不进 REJECTED）；
- 13 表规范映射（death=17/exam=11/… 权威编码 + 存量迁移修正）；
- 多快照解析（watermark/last-run-real/reconcile/pending-wait/status/daily/alerts
  → 遥测/快照表；不再报 NOT_JSON）；
- 流式尾读（cursor 追加不重读、轮转重同步、源目录零改动 100% O_RDONLY）；
- 5 个 /api/v1/infectious/monitor/* 端点（13 表矩阵/target_scopes 鉴权/未配置温和提示）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from contracts.api import hash_token  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.engine import utcnow  # noqa: E402
from core_db.models import (AlertRule, AuthSubject, Tenant)  # noqa: E402
from plugins.report_targets import seed_default_targets  # noqa: E402

NOW = datetime(2026, 11, 18, 12, 0, 0)


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/pt03.sqlite3"
    for k in ("BLYB_INF_PUSHER_STATE_DIR", "BLYB_INF_WATCHDOG_STATE_DIR",
              "BLYB_ALLOW_EXTERNAL_NOTIFY"):
        os.environ.pop(k, None)
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology", "infectious"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="pt03-h", name="PT-03")
        db.add(t)
        db.flush()
        tid = t.id
        seed_default_targets(db, tid)
        subjects = {}
        for role, code in (("admin", "u-admin"), ("operator", "u-operator"),
                           ("viewer", "u-viewer")):
            roles = {"admin": ["platform_admin"], "operator": ["operator"],
                     "viewer": ["viewer"]}[role]
            db.add(AuthSubject(id=new_uuid(), tenant_id=tid, kind="user",
                               subject_code=code, roles=roles,
                               token_hash=hash_token(f"tok-{role}"),
                               status="ACTIVE", target_scopes=["*"]))
            subjects[role] = f"tok-{role}"
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "tmp": str(tmp_path),
           "subjects": subjects}
    engine.dispose()
    for k in ("BLYB_DATABASE_URL", "BLYB_INF_PUSHER_STATE_DIR",
              "BLYB_INF_WATCHDOG_STATE_DIR"):
        os.environ.pop(k, None)


@pytest.fixture()
def api(env):
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace", "pathology", "infectious"])
    from fastapi.testclient import TestClient
    from apps.core_api.app import create_app
    client = TestClient(create_app(session_factory=env["sf"],
                                   engine=env["engine"], registry=reg))
    env["client"] = client
    return env


def H(env, role):
    return {"Authorization": f"Bearer {env['subjects'][role]}"}


def _jl(objs):
    return ("\n".join(json.dumps(o, ensure_ascii=False) for o in objs)
            + "\n").encode()


def _receipt(rid, table, ts, *, result=True, code="00", stage=""):
    return {"id": rid, "result": result, "errorCode": code, "desc": "合成回执",
            "attempt": 0, "stage": stage, "mode": "", "table": table, "ts": ts}


# ================================================================ 步骤 1：importer
class TestImporterFixes:
    def test_auto_mixed_timezone_no_eight_hour_skew(self, env):
        """混合口径：receipts ts=UTC、sent opTime=CST——8h 偏差根除。"""
        from plugins.external_ledger.importers.inf_pusher import import_files
        with env["sf"]() as db:
            stats = import_files(
                db, env["tid"], "inf_cdc_frontend",
                files=[{"name": "receipts-20261117.jsonl", "content": _jl([
                    _receipt("R1", "ex_lab", "2026-11-17 04:00:00")])},
                       {"name": "sent.jsonl", "content": _jl([
                           {"id": "ex_lab/R1", "opTime": "2026-11-17 12:00:00",
                            "table": "ex_lab", "ts": "2026-11-17 04:00:01"}])}],
                source_instance="tz", source_tz="AUTO_MIXED", actor="t")
            db.commit()
            from core_db.models import ExternalSubmissionRecord
            r = (db.query(ExternalSubmissionRecord)
                 .filter_by(record_key="ex_lab/R1").one())
            # 回执 ts=04:00 UTC → last_attempt_at=04:00（naive UTC 存储）
            assert r.last_attempt_at.strftime("%H:%M") == "04:00"
            # sent opTime=12:00 CST = 04:00 UTC → op_time=04:00（与回执同时刻，
            # 旧单一 CST 口径会把回执解释成 12:00 CST=04:00Z/或单一 UTC 把
            # opTime 解释成 04:00Z 差 8 小时——混合口径两者一致）
            assert r.op_time.strftime("%H:%M") == "04:00"
            assert stats["timezone"] == {"receipts": "UTC", "sent": "CST"}
            assert (r.last_attempt_at - r.op_time).total_seconds() == 0

    def test_legacy_source_tz_back_compat(self, env):
        """旧调用 source_tz=CST 两侧同口径（完全向后兼容）。"""
        from plugins.external_ledger.importers.inf_pusher import import_files
        with env["sf"]() as db:
            import_files(db, env["tid"], "inf_cdc_frontend",
                         files=[{"name": "receipts-20261117.jsonl", "content": _jl([
                             _receipt("R2", "order", "2026-11-17 08:00:00")])}],
                         source_instance="lgc", source_tz="CST", actor="t")
            db.commit()
            from core_db.models import ExternalSubmissionRecord
            r = (db.query(ExternalSubmissionRecord)
                 .filter_by(record_key="order/R2").one())
            assert r.last_attempt_at.strftime("%H:%M") == "00:00"  # 08:00CST=00:00Z

    def test_parent_wait_not_rejected(self, env):
        """parent-wait 单列 PENDING/PARENT_WAITING——绝不进 REJECTED。"""
        from plugins.external_ledger.importers.inf_pusher import classify, import_files
        assert classify({"stage": "parent-wait", "result": False}) == \
            ("PARENT_WAIT", "PARENT_WAIT")
        with env["sf"]() as db:
            import_files(db, env["tid"], "inf_cdc_frontend",
                         files=[{"name": "receipts-20261117.jsonl", "content": _jl([
                             _receipt("W1", "ex_lab_item", "2026-11-17 05:00:00",
                                      result=False, stage="parent-wait"),
                             _receipt("V1", "ex_lab", "2026-11-17 05:00:00",
                                      result=False, code="04")])}],
                         source_instance="pw", source_tz="AUTO_MIXED", actor="t")
            db.commit()
            from core_db.models import ExternalSubmissionRecord
            w = (db.query(ExternalSubmissionRecord)
                 .filter_by(record_key="ex_lab_item/W1").one())
            assert w.bucket == "PENDING" and w.evidence_level == "PARENT_WAITING"
            v = (db.query(ExternalSubmissionRecord)
                 .filter_by(record_key="ex_lab/V1").one())
            assert v.bucket == "REJECTED"      # 04 真退回仍归 REJECTED

    def test_authoritative_doc_type_mapping(self):
        """13 表权威编码（death=17/exam=11/admission=09…；旧错位映射已修）。"""
        from plugins.external_ledger.importers.inf_pusher import (DOC_TYPE_CODES,
                                                                  INF_TABLES)
        assert DOC_TYPE_CODES["death"] == "17"
        assert DOC_TYPE_CODES["admission"] == "09"
        assert DOC_TYPE_CODES["exam"] == "11"
        assert DOC_TYPE_CODES["exam_item"] == "12"
        assert DOC_TYPE_CODES["first_course"] == "07"
        assert DOC_TYPE_CODES["daily_course"] == "08"
        assert DOC_TYPE_CODES["admission_info"] == "06"
        assert DOC_TYPE_CODES["discharge_info"] == "10"
        assert len(INF_TABLES) == 13
        assert INF_TABLES["ex_lab_item"]["parent"] == "ex_lab"
        assert INF_TABLES["order_item"]["endpoint"] == "emrOrderItem"

    def test_migration_fixes_legacy_doc_type_codes(self, env):
        """迁移 023 按 business_type 修正存量行（death 行 07→17）。"""
        from core_db.models import ExternalSubmissionRecord
        with env["sf"]() as db:
            db.add(ExternalSubmissionRecord(          # ORM 插入（模型默认兜底 NOT NULL）
                id=new_uuid(), tenant_id=env["tid"],
                target_code="inf_cdc_frontend", source_instance="legacy",
                record_key="death/X", business_type="death",
                doc_type_code="07",                    # 旧错位映射的存量形态
                first_seen_at=utcnow(), attempts=1,
                last_result="ACCEPTED", bucket="ACCEPTED"))
            db.commit()
            # 重新执行迁移（幂等：再次 UPDATE 后码=17）
            from core_db import migrate
            mig = dict(migrate.available_migrations())
            from core_db.migrate import _MIGRATIONS
            for step, name, fn in _MIGRATIONS:
                if step == 23:
                    fn(db.get_bind())
            row = (db.query(ExternalSubmissionRecord)
                   .filter_by(record_key="death/X").one())
            assert row.doc_type_code == "17"


# ================================================================ 步骤 2：快照解析
class TestSnapshotParsing:
    def _import_snapshots(self, env, extra=None):
        from plugins.external_ledger.importers.inf_pusher import import_files
        files = [
            {"name": "watermark.json", "content": json.dumps({
                "ex_lab": "2026-11-17 10:00:00", "ex_lab_item":
                    "2026-11-17 09:00:00"}).encode()},
            {"name": "last-run-real.json", "content": json.dumps([
                {"table": "ex_lab", "since": "2026-11-17 08:00:00",
                 "reason": "watermark+overlap", "rows": 10, "sent": 8,
                 "parent_wait": 2, "skipped_invalid": 0, "failed": 0},
                {"table": "death", "rows": 0, "sent": 0}]).encode()},
            {"name": "reconcile-20261116.json", "content": json.dumps({
                "day": "20261116", "ran_at": "2026-11-17 00:05:00",
                "total_repushed": 12, "total_failed": 0, "total_parent_wait": 3,
                "total_skipped_invalid": 1,
                "tables": {"ex_lab": {"repushed": 12, "parent_wait": 3,
                                      "failed": 0, "window_rows": 15}}}).encode()},
            {"name": "pending-wait.json", "content": json.dumps({
                "version": 1, "items": {"ex_lab_item/W1": {}, "ex_lab_item/W2":
                    {}}}).encode()},
        ]
        if extra:
            files.extend(extra)
        with env["sf"]() as db:
            stats = import_files(db, env["tid"], "inf_cdc_frontend",
                                 files=files, source_instance="snap",
                                 source_tz="AUTO_MIXED", actor="t")
            db.commit()
        return stats

    def test_snapshots_parsed_not_not_json(self, env):
        """快照文件结构化解析（不再整文件报 NOT_JSON）。"""
        stats = self._import_snapshots(env)
        assert stats["snapshots_parsed"] == 4
        kinds = {s["name"]: s.get("_kind") for s in stats["snapshots"]}
        assert kinds["watermark.json"] == "watermark.json"
        assert kinds["reconcile-20261116.json"] == "reconcile"

    def test_watermark_telemetry_written(self, env):
        """水位遥测写入 telemetry（含 CST 解释的滞后小时）。"""
        self._import_snapshots(env)
        from core_db.models import InfectiousTableTelemetry
        with env["sf"]() as db:
            tel = (db.query(InfectiousTableTelemetry)
                   .filter_by(table_key="ex_lab").first())
            assert tel is not None
            assert tel.watermark_op_time.strftime("%H:%M") == "02:00"  # 10:00CST=02:00Z
            assert tel.watermark_lag_hours is not None
            assert tel.doc_type_code == "13"

    def test_shift_telemetry_written(self, env):
        from core_db.models import InfectiousTableTelemetry
        self._import_snapshots(env)
        with env["sf"]() as db:
            tel = (db.query(InfectiousTableTelemetry)
                   .filter_by(table_key="ex_lab").first())
            assert tel.shift_rows == 10 and tel.shift_sent == 8 \
                and tel.shift_parent_wait == 2

    def test_reconcile_telemetry_written(self, env):
        from core_db.models import InfectiousTableTelemetry
        self._import_snapshots(env)
        with env["sf"]() as db:
            tel = (db.query(InfectiousTableTelemetry)
                   .filter_by(table_key="ex_lab").first())
            assert tel.reconcile_day == "20261116"
            assert tel.reconcile_repushed == 12
            assert tel.reconcile_parent_wait == 3

    def test_watchdog_status_snapshot(self, env):
        """守护 status.json → 快照表（C1–C11 白名单 + relay_reachable）。"""
        from plugins.external_ledger.importers.inf_pusher import import_files
        status = {"ts": "2026-11-17 10:55:00", "host": "<host-label>",
                  "level": "WARN", "exit_code": 1, "relay_reachable": True,
                  "driver_errors": [],
                  "checks": {"c1_freshness": {"level": "OK", "elapsed_ms": 12,
                                              "detail": ""},
                             "c5_push_failures": {"level": "CRITICAL",
                                                  "elapsed_ms": 900,
                                                  "detail": "真实失败 27 行"},
                             "c11_relay_tunnel": {"level": "OK",
                                                  "elapsed_ms": 30}}}
        with env["sf"]() as db:
            stats = import_files(db, env["tid"], "inf_cdc_frontend",
                                 files=[{"name": "status.json",
                                         "content": json.dumps(status).encode()}],
                                 source_instance="snap", source_tz="AUTO_MIXED",
                                 actor="t")
            db.commit()
        assert stats["snapshots_parsed"] == 1
        from core_db.models import InfectiousWatchdogSnapshot
        with env["sf"]() as db:
            snap = (db.query(InfectiousWatchdogSnapshot)
                    .filter_by(source_instance="snap").one())
        assert snap.overall_level == "WARN"
        assert snap.relay_reachable is True
        assert snap.checks_json["c5_push_failures"]["level"] == "CRITICAL"
        assert "c11_relay_tunnel" in snap.checks_json

    def test_daily_and_alerts_snapshot(self, env):
        from plugins.external_ledger.importers.inf_pusher import import_files
        daily = "【晨报】传染病上报·昨日运行总览（2026-11-17）\n检验报告 sent=100"
        alerts = _jl([{"ts": "2026-11-17 10:00:00", "level": "WARN",
                       "subject": "【警告】传染病上报·推送失败突增"}])
        with env["sf"]() as db:
            stats = import_files(db, env["tid"], "inf_cdc_frontend",
                                 files=[{"name": "daily-20261117.txt",
                                         "content": daily.encode()},
                                        {"name": "alerts-20261117.jsonl",
                                         "content": alerts}],
                                 source_instance="snap",
                                 source_tz="AUTO_MIXED", actor="t")
            db.commit()
        assert stats["snapshots_parsed"] == 2
        from core_db.models import InfectiousWatchdogSnapshot
        with env["sf"]() as db:
            snap = (db.query(InfectiousWatchdogSnapshot)
                    .filter_by(source_instance="snap").one())
        assert snap.daily_ymd == "20261117" and "晨报" in snap.daily_text
        assert snap.recent_alerts_json["items"][0]["level"] == "WARN"


# ================================================================ 步骤 2：流式尾读同步
class TestMonitorSync:
    def _mk_state_dir(self, tmp, *, receipts="", sent="", watermark=None):
        d = tmp / "pusher-state"
        d.mkdir(exist_ok=True)
        if receipts:
            (d / "receipts-20261117.jsonl").write_text(receipts, encoding="utf-8")
        if sent:
            (d / "sent.jsonl").write_text(sent, encoding="utf-8")
        if watermark:
            (d / "watermark.json").write_text(watermark, encoding="utf-8")
        return d

    def test_not_configured_mild_hint(self, env):
        from domains.infectious import monitor_sync
        with env["sf"]() as db:
            out = monitor_sync.sync_status(db, env["tid"])
        assert out["configured"] is False
        assert "未配置" in out["reason"]

    def test_streaming_increment_and_cursor(self, env, tmp_path):
        """游标追加：首轮吸收既有行；追加新行只读增量（旧行不重读）。"""
        from domains.infectious import monitor_sync
        d = self._mk_state_dir(tmp_path, receipts=_jl([
            _receipt("A1", "ex_lab", "2026-11-17 01:00:00"),
            _receipt("A2", "ex_lab", "2026-11-17 02:00:00")]).decode())
        os.environ["BLYB_INF_PUSHER_STATE_DIR"] = str(d)
        try:
            with env["sf"]() as db:
                out1 = monitor_sync.sync_status(db, env["tid"])
                db.commit()
            assert out1["configured"] is True
            assert out1["rows_read"] == 2
            # 追加新行（模拟推送器继续写）
            with open(d / "receipts-20261117.jsonl", "a", encoding="utf-8") as fh:
                fh.write(_jl([_receipt("A3", "ex_lab", "2026-11-17 03:00:00")
                              ]).decode())
            with env["sf"]() as db:
                out2 = monitor_sync.sync_status(db, env["tid"])
                db.commit()
                from core_db.models import ExternalSubmissionRecord
                keys = {r.record_key for r in db.query(ExternalSubmissionRecord)
                        .filter_by(source_instance="live").all()}
            assert out2["rows_read"] == 1          # 只读增量 1 行
            assert keys == {"ex_lab/A1", "ex_lab/A2", "ex_lab/A3"}
        finally:
            os.environ.pop("BLYB_INF_PUSHER_STATE_DIR", None)

    def test_rotation_restarts_from_zero(self, env, tmp_path):
        """轮转重建（文件头摘要变化）→ 从 0 重读。"""
        from domains.infectious import monitor_sync
        d = self._mk_state_dir(tmp_path, receipts=_jl([
            _receipt("B1", "ex_lab", "2026-11-17 01:00:00")]).decode())
        os.environ["BLYB_INF_PUSHER_STATE_DIR"] = str(d)
        try:
            with env["sf"]() as db:
                monitor_sync.sync_status(db, env["tid"])
                db.commit()
            # 整文件轮转重建（新内容/更小）
            (d / "receipts-20261117.jsonl").write_text(_jl([
                _receipt("B2", "ex_lab", "2026-11-17 05:00:00")]).decode(),
                encoding="utf-8")
            with env["sf"]() as db:
                out2 = monitor_sync.sync_status(db, env["tid"])
                db.commit()
                from core_db.models import ExternalSubmissionRecord
                keys = {r.record_key for r in db.query(ExternalSubmissionRecord)
                        .filter_by(source_instance="live").all()}
            assert "ex_lab/B2" in keys and out2["rows_read"] >= 1
        finally:
            os.environ.pop("BLYB_INF_PUSHER_STATE_DIR", None)

    def test_source_directory_readonly_zero_side_effect(self, env, tmp_path):
        """源目录 100% 只读：同步前后目录树（文件名+mtime+内容哈希）零变化。"""
        from domains.infectious import monitor_sync
        d = self._mk_state_dir(tmp_path, receipts=_jl([
            _receipt("C1", "ex_lab", "2026-11-17 01:00:00")]).decode(),
            watermark='{"ex_lab": "2026-11-17 09:00:00"}')

        def snapshot():
            out = {}
            for f in sorted(d.iterdir()):
                st = f.stat()
                out[f.name] = (st.st_mtime_ns, hashlib.sha256(
                    f.read_bytes()).hexdigest())
            return out

        os.environ["BLYB_INF_PUSHER_STATE_DIR"] = str(d)
        try:
            before = snapshot()
            with env["sf"]() as db:
                monitor_sync.sync_status(db, env["tid"])
                db.commit()
            assert snapshot() == before
            assert not [p for p in d.iterdir() if p.name not in
                        ("receipts-20261117.jsonl", "watermark.json")]
        finally:
            os.environ.pop("BLYB_INF_PUSHER_STATE_DIR", None)

    def test_watchdog_dir_sync(self, env, tmp_path):
        from domains.infectious import monitor_sync
        wd_dir = tmp_path / "watchdog-state"
        wd_dir.mkdir()
        (wd_dir / "status.json").write_text(json.dumps({
            "ts": "2026-11-17 10:55:00", "host": "<host-label>",
            "level": "OK", "relay_reachable": True, "checks": {}}),
            encoding="utf-8")
        os.environ["BLYB_INF_WATCHDOG_STATE_DIR"] = str(wd_dir)
        try:
            with env["sf"]() as db:
                out = monitor_sync.sync_status(db, env["tid"])
                db.commit()
            assert out["snapshots_parsed"] == 1
        finally:
            os.environ.pop("BLYB_INF_WATCHDOG_STATE_DIR", None)


# ================================================================ 5 个监控端点
class TestMonitorEndpoints:
    def _seed_data(self, env):
        from plugins.external_ledger.importers.inf_pusher import import_files
        with env["sf"]() as db:
            import_files(db, env["tid"], "inf_cdc_frontend", files=[
                {"name": "receipts-20261117.jsonl", "content": _jl([
                    _receipt("D1", "ex_lab", "2026-11-17 04:00:00"),
                    _receipt("D2", "ex_lab_item", "2026-11-17 04:00:00",
                             result=False, stage="parent-wait"),
                    _receipt("D3", "order", "2026-11-17 04:00:00",
                             result=False, code="04"),
                    _receipt("D4", "exam", "2026-11-17 04:00:00",
                             result=False, stage="client-validate")])},
                {"name": "watermark.json", "content": json.dumps({
                    "ex_lab": "2026-11-17 10:00:00"}).encode()},
                {"name": "status.json", "content": json.dumps({
                    "ts": "2026-11-17 10:55:00", "host": "<host-label>",
                    "level": "OK", "relay_reachable": True,
                    "checks": {"c11_relay_tunnel": {"level": "OK",
                                                    "elapsed_ms": 30}}}).encode()},
                {"name": "reconcile-20261116.json", "content": json.dumps({
                    "day": "20261116", "ran_at": "2026-11-17 00:05:00",
                    "total_repushed": 5, "total_failed": 0,
                    "total_parent_wait": 1,
                    "tables": {"ex_lab": {"repushed": 5, "parent_wait": 1,
                                          "failed": 0}}}).encode()},
            ], source_instance="ep", source_tz="AUTO_MIXED", actor="t")
            db.commit()

    def test_overview_endpoint_13_matrix_ready(self, api):
        self._seed_data(api)
        r = api["client"].get("/api/v1/infectious/monitor/overview?days=7",
                              headers=H(api, "admin"))
        assert r.status_code == 200
        body = r.json()["data"]
        assert body["buckets"]["ACCEPTED"] >= 1
        assert body["buckets"]["PENDING"] >= 1      # parent-wait 单列
        assert body["buckets"]["REJECTED"] >= 1
        assert any(e["code"] == "04" for e in body["error_codes"])
        assert "sync_configured" in body

    def test_tables_endpoint_full_13_matrix(self, api):
        self._seed_data(api)
        r = api["client"].get("/api/v1/infectious/monitor/tables",
                              headers=H(api, "admin"))
        assert r.status_code == 200
        data = r.json()["data"]
        all_tables = [t for g in data["groups"] for t in g["tables"]]
        assert data["total_tables"] == 13 and len(all_tables) == 13
        names = {t["doc_type_name"] for t in all_tables}
        for cn in ("住院病案首页", "入院记录", "出院记录", "死亡记录", "首次病程记录",
                   "日常病程记录", "检查报告主表", "检查报告项目", "检验报告主表",
                   "检验报告项目", "医嘱处方主表", "医嘱药品明细", "生命体征记录"):
            assert cn in names, cn
        by_table = {t["table"]: t for t in all_tables}
        assert by_table["ex_lab_item"]["parent_table"] == "ex_lab"
        assert by_table["order_item"]["parent_name"] == "医嘱处方主表"
        assert by_table["ex_lab"]["watermark"] is not None

    def test_watchdog_endpoint(self, api):
        self._seed_data(api)
        r = api["client"].get("/api/v1/infectious/monitor/watchdog",
                              headers=H(api, "admin"))
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["present"] is True
        assert data["overall_level"] == "OK"
        assert data["relay_reachable"] is True

    def test_reconcile_endpoint(self, api):
        self._seed_data(api)
        r = api["client"].get("/api/v1/infectious/monitor/reconcile",
                              headers=H(api, "admin"))
        assert r.status_code == 200
        data = r.json()["data"]
        assert any(row["table"] == "ex_lab" for row in data["reconcile"])

    def test_sync_endpoint_unconfigured_mild(self, api):
        r = api["client"].post("/api/v1/infectious/monitor/sync",
                               headers=H(api, "operator"))
        assert r.status_code == 200
        assert r.json()["data"]["configured"] is False

    def test_sync_endpoint_forbidden_for_viewer(self, api):
        r = api["client"].post("/api/v1/infectious/monitor/sync",
                               headers=H(api, "viewer"))
        assert r.status_code in (403,)

    def test_endpoints_require_scope(self, env):
        """无 inf_cdc_frontend 范围的账号 → 403（R08 对象级范围）。"""
        with env["sf"]() as db:
            db.add(AuthSubject(id=new_uuid(), tenant_id=env["tid"], kind="user",
                               subject_code="u-limited", roles=["viewer"],
                               token_hash=hash_token("tok-limited"),
                               status="ACTIVE",
                               target_scopes=["drug_trace_alihealth"]))
            db.commit()
        import app_runtime
        reg2 = app_runtime.load_registry(["drug_trace", "pathology",
                                          "infectious"])
        from fastapi.testclient import TestClient
        from apps.core_api.app import create_app
        client = TestClient(create_app(session_factory=env["sf"],
                                       engine=env["engine"], registry=reg2))
        r = client.get("/api/v1/infectious/monitor/tables",
                       headers={"Authorization": "Bearer tok-limited"})
        assert r.status_code == 403

    def test_classification_hook_full_13(self, api):
        """hooks.classification 返回全部 13 张在推接口（零值行在）+ 逐表细分。"""
        self._seed_data(api)
        from domains.infectious import hooks
        with api["sf"]() as db:
            out = hooks.classification(db, api["tid"], "inf_cdc_frontend", 7)
        by_code = {t["doc_type_code"]: t for t in out["doc_types"]}
        codes = {t["doc_type_code"] for t in out["doc_types"]}
        assert {"06", "07", "08", "09", "10", "11", "12", "13", "14",
                "15", "16", "17", "18"} <= codes
        ex = by_code["13"]
        assert ex["accepted"] >= 1
        assert ex["parent_table"] == ""
        item = by_code["14"]
        assert item["parent_table"] == "ex_lab"
        assert item["parent_waiting"] >= 1
        other = by_code[""]
        assert other["total"] == 0            # 黑洞消灭：其他/未分类=0

    def test_classification_via_route(self, api):
        """既有 /report-targets/{code}/classification 契约兼容（13 表）。"""
        self._seed_data(api)
        r = api["client"].get(
            "/api/v1/report-targets/inf_cdc_frontend/classification?days=7",
            headers=H(api, "admin"))
        assert r.status_code == 200
        data = r.json()["data"]
        codes = {t["doc_type_code"] for t in data["doc_types"]}
        assert "17" in codes and "13" in codes
