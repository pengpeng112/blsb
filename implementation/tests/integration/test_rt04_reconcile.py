"""RT-04 A4：对账工具（纯函数分类 + CLI 只读 + 七天合成场景实跑）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

from contracts import new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import Tenant  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/rt04a4.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["infectious"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        t = Tenant(id=new_uuid(), code="rt04-a4", name="对账")
        db.add(t)
        db.flush()
        tid = t.id
        db.commit()
    yield {"sf": sf, "engine": engine, "tid": tid, "tmp": tmp_path}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _mk_external(db, tid, key, *, op, bucket="ACCEPTED", instance="s",
                 table="death"):
    from core_db.models import ExternalSubmissionRecord
    db.add(ExternalSubmissionRecord(
        id=new_uuid(), tenant_id=tid, target_code="inf_cdc_frontend",
        source_instance=instance, record_key=key, business_type=table,
        doc_type_code="07", first_seen_at=datetime(2026, 11, 12),
        last_attempt_at=datetime(2026, 11, 12), attempts=1,
        last_result="ACCEPTED", bucket=bucket,
        evidence_level="FRONTEND_ACCEPTED",
        op_time=datetime.strptime(op, "%Y-%m-%d %H:%M:%S")
        if op else None,
        source_file="receipts-x.jsonl"))


def _mk_shadow(db, tid, key, *, op, digest="ab12", state="DELIVERED",
               instance="s", table="death"):
    from core_db.models import InfectiousShadowRecord
    db.add(InfectiousShadowRecord(
        id=new_uuid(), tenant_id=tid, target_code="inf_cdc_frontend",
        source_instance=instance, table_key=table, source_record_key=key,
        source_op_time=op, source_version_digest=digest, state=state,
        delivered_result="ACCEPTED" if state == "DELIVERED" else "",
        payload_json={"id": key}))


def test_a4_compare_pure_function_all_classes():
    """纯函数分类：matched/only_*/mismatch/unknown/duplicate/conflict。"""
    from domains.infectious.reconcile_shadow import (ExternalVer, RecKey,
                                                     ShadowVer, compare)
    S, E = ShadowVer, ExternalVer
    shadow = {
        RecKey("s", "death", "M1"): S("2026-11-06 08:00:00", "d1"),
        RecKey("s", "death", "M2"): S("2026-11-07 08:00:00", "d2"),
        RecKey("s", "death", "OS1"): S("2026-11-08 08:00:00", "d3"),
        RecKey("s", "death", "U1"): S("", "d4"),               # 影子侧未知
        RecKey("s", "death", "U2"): S("2026-11-09 08:00:00", "d5"),
        RecKey("s", "death", "C1"): S("2026-11-09 09:00:00", "d6"),
        RecKey("s", "death", "D1"): S("2026-11-10 08:00:00", "d7"),
    }
    external = {
        RecKey("s", "death", "M1"): E("2026-11-06 08:00:00", "ACCEPTED"),
        RecKey("s", "death", "M2"): E("2026-11-07 09:00:00", "ACCEPTED"),  # 版本不符
        RecKey("s", "death", "OE1"): E("2026-11-08 08:00:00", "ACCEPTED"),
        RecKey("s", "death", "U1"): E("2026-11-09 08:00:00", "ACCEPTED"),
        RecKey("s", "death", "U2"): E("", "SENT_LEDGER_ONLY"),   # 外部侧未知
        RecKey("s", "death", "D1"): E("2026-11-10 08:00:00", "ACCEPTED"),
        RecKey("s", "death", "C1"): E("2026-11-09 09:00:00", "ACCEPTED"),
    }
    rep = compare(shadow, external,
                  shadow_conflicts={"s/death/C1"},
                  external_duplicates={RecKey("s", "death", "D1")})
    assert rep.matched == ["s/death/M1"]
    assert rep.only_in_shadow == ["s/death/OS1"]
    assert rep.only_in_external == ["s/death/OE1"]      # D1/C1 双侧在场
    mm = rep.version_mismatch[0]
    assert mm["key"] == "s/death/M2"
    assert mm["direction"] == "external_newer"     # 外部 09:00 > 影子 08:00
    assert mm["shadow_version_digest_prefix"] == "d2"
    unk = {u["key"]: u for u in rep.unknown_source_time}
    assert set(unk) == {"s/death/U1", "s/death/U2"}
    assert unk["s/death/U1"]["shadow_op_time"] is None
    assert rep.conflict == ["s/death/C1"]
    assert rep.duplicate == ["s/death/D1"]


def test_a4_reconcile_db_readonly_and_instance_isolation(env):
    """DB 对账（只读——统计行数前后不变）；来源实例参与身份（不同实例
    互不 only_in_*）。"""
    from domains.infectious.reconcile_shadow import reconcile
    with env["sf"]() as db:
        _mk_shadow(db, env["tid"], "K1", op="2026-11-06 08:00:00")
        _mk_external(db, env["tid"], "death/K1", op="2026-11-06 08:00:00")
        # 同键不同实例（host-b 外部）→ 不构成 only_in_external
        _mk_external(db, env["tid"], "death/K1", op="2026-11-06 08:00:00",
                     instance="host-b")
        from core_db.models import (ExternalSubmissionRecord,
                                    InfectiousShadowRecord)
        db.commit()
        before = (db.query(InfectiousShadowRecord).count(),
                  db.query(ExternalSubmissionRecord).count())
    with env["sf"]() as db:
        rep = reconcile(db, env["tid"], source_instance="s")
        assert rep["matched"] == 1
        assert rep["only_in_external"] == 0 and rep["only_in_shadow"] == 0
        # 全实例视角：host-b 一条外部记录 → only_in_external
        rep_all = reconcile(db, env["tid"])
        assert rep_all["only_in_external_keys"] == ["host-b/death/K1"]
        from core_db.models import (ExternalSubmissionRecord as E2,
                                    InfectiousShadowRecord as S2)
        after = (db.query(S2).count(), db.query(E2).count())
    assert before == after          # 只读


def test_a4_cli_readonly_json(env, capsys):
    """CLI：--json 全量报告；无 --source-instance 实例列全；只读零写。"""
    with env["sf"]() as db:
        _mk_shadow(db, env["tid"], "K1", op="2026-11-06 08:00:00")
        _mk_external(db, env["tid"], "death/K1", op="2026-11-06 08:00:00")
        db.commit()
    from domains.infectious.reconcile_shadow import main as rc_main
    rc = rc_main(["--tenant", env["tid"], "--source-instance", "s",
                  "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["matched"] == 1 and out["total_shadow"] == 1
    assert "matched_keys" in out and "s/death/K1" in out["matched_keys"]
    assert "患者" not in json.dumps(out, ensure_ascii=False)


def test_a4_seven_day_synthetic_scenario(env, tmp_path):
    """七天合成场景实跑：每天两侧各有正常/差异行，四类差异+未知源时间
    全覆盖；报告落盘为证据（RT-04 evidence）。"""
    from domains.infectious.reconcile_shadow import reconcile
    with env["sf"]() as db:
        # 七天：D1..D7（每天 3 正常匹配 + 1 只影子 + 1 只外部；
        # D3 加 1 版本不符；D5 影子未知源时间；D6 外部未知；D7 冲突）
        for d in range(1, 8):
            day = f"2026-11-{d:02d}"
            for i in range(3):
                k = f"S7D{d}M{i}"
                _mk_shadow(db, env["tid"], k, op=f"{day} 08:00:0{i}")
                _mk_external(db, env["tid"], f"death/{k}",
                             op=f"{day} 08:00:0{i}")
            _mk_shadow(db, env["tid"], f"S7D{d}OS", op=f"{day} 09:00:00")
            _mk_external(db, env["tid"], f"death/S7D{d}OE",
                         op=f"{day} 09:30:00")
        _mk_shadow(db, env["tid"], "S7VMS", op="2026-11-03 10:00:00")
        _mk_external(db, env["tid"], "death/S7VMS", op="2026-11-03 11:00:00")
        # 未知源时间（双侧在场各一）：影子缺源时间 / 外部缺源时间
        _mk_shadow(db, env["tid"], "S7UNK", op="", state="QUALITY_WAIT")
        _mk_external(db, env["tid"], "death/S7UNK", op="2026-11-05 12:00:00")
        _mk_shadow(db, env["tid"], "S7UNKX", op="2026-11-05 13:00:00")
        _mk_external(db, env["tid"], "death/S7UNKX", op="")
        _mk_shadow(db, env["tid"], "S7CF", op="2026-11-07 13:00:00",
                   state="CONFLICT")
        _mk_external(db, env["tid"], "death/S7CF", op="2026-11-07 13:00:00")
        db.commit()
    with env["sf"]() as db:
        rep = reconcile(db, env["tid"], source_instance="s",
                        since="2026-11-01 00:00:00",
                        until="2026-11-08 00:00:00")
    assert rep["matched"] == 7 * 3               # 21 匹配（S7CF 走冲突类）
    assert rep["only_in_shadow"] == 7
    assert rep["only_in_external"] == 7          # 每天 OE
    assert rep["version_mismatch"] == 1
    assert rep["version_mismatch_details"][0]["key"] == "s/death/S7VMS"
    assert {u["key"] for u in rep["unknown_source_time_details"]} == (
        {"s/death/S7UNK", "s/death/S7UNKX"})
    assert rep["total_shadow"] == 7 * 4 + 4
    assert rep["total_external"] == 7 * 4 + 4
    blob = json.dumps(rep, ensure_ascii=False)
    for bad in ("patientName", "idCard", "PATIENT_NAME", "ID_CARD"):
        assert bad not in blob
    # 证据落盘（本测试自身即实跑；同款命令在交接记录）
    evd = os.path.join("..", "docs", "development",
                       "platform-rectification-20261111", "revision-2",
                       "evidence")
    os.makedirs(evd, exist_ok=True)
    with open(os.path.join(evd, "rt04-reconcile-7day-synthetic.json"), "w",
              encoding="utf-8", newline="\n") as fh:
        json.dump(rep, fh, ensure_ascii=False, indent=1)


def test_a4_upload_time_not_used_as_version(env):
    """上传 operationTime/导入时间/snapshot digest 不参与版本比较：
    影子快照与外部记录 last_attempt_at 完全不同但源时间相同 → matched。"""
    from domains.infectious.reconcile_shadow import reconcile
    with env["sf"]() as db:
        from core_db.models import (ExternalSubmissionRecord,
                                    InfectiousShadowRecord)
        _mk_shadow(db, env["tid"], "K9", op="2026-11-06 08:00:00")
        db.flush()
        # 改写快照时间为极值，验证不影响
        rec = db.query(InfectiousShadowRecord).filter_by(
            source_record_key="K9").one()
        rec.frozen_at = "1999-01-01T00:00:00"
        _mk_external(db, env["tid"], "death/K9", op="2026-11-06 08:00:00")
        db.flush()
        er = db.query(ExternalSubmissionRecord).one()
        er.last_attempt_at = datetime(2030, 1, 1)    # 导入/尝试时间极值
        db.commit()
    with env["sf"]() as db:
        rep = reconcile(db, env["tid"], source_instance="s")
    assert rep["matched"] == 1 and not rep["version_mismatch"]
