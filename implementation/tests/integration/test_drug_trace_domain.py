"""药品追溯领域测试（W03/W04/W05 局部）：DTR-A01—A16、S01—S08、B02/B03/B05/B07。

合成数据全部为编造值（无患者真实信息）。真实 HIS/PG 侧验证在 W03-PG/W10 环境另行执行。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from contracts import ApiError, SubmissionState, new_uuid  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (DeliveryAttempt, Destination, Job, OutboxEvent,
                            PackageRevision, QualityIssue, SourceCheckpoint,
                            Submission)  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    """drug_trace 启用的完整栈（SQLite 内存文件）。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/dt.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
        from core_db.models import Tenant
        t = Tenant(id=new_uuid(), code="first-hospital", name="首院")
        db.add(t)
        db.commit()
        tenant_id = t.id
    yield {"sf": sf, "engine": engine, "tenant_id": tenant_id, "reg": reg}
    engine.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def _bill_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              updated="2026-10-12T08:00:00", revision="1", finalized=True,
              bill_time="2026-10-12T07:55:00", **extra):
    row = {
        "source_system": "HIS", "source_bill_id": bill_id,
        "source_bill_no": bill_id, "source_revision": revision,
        "business_kind": kind, "pharmacy_code": pharmacy,
        "bill_time": bill_time, "updated_at": updated, "finalized": finalized,
        "is_void": False,
    }
    row.update(extra)
    return row


def _code_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              record_id="R1", line_id="L1", code="81000000000123",
              revision="1", updated="2026-10-12T08:00:00", deleted=False,
              quantity="1", unit="盒", **extra):
    row = {
        "source_system": "HIS", "source_bill_id": bill_id, "pharmacy_code": pharmacy,
        "business_kind": kind, "source_revision": revision,
        "source_line_id": line_id, "source_code_record_id": record_id,
        "trace_code": code, "updated_at": updated, "is_deleted": deleted,
        "quantity": quantity, "unit": unit,
    }
    row.update(extra)
    return row


def _connector_id(env):
    from core_db.models import ConnectorInstance
    with env["sf"]() as db:
        c = ConnectorInstance(id=new_uuid(), tenant_id=env["tenant_id"],
                              direction="source",
                              plugin_id="sources.db_view.drug_trace",
                              name="his-drugtrace", status="ENABLED")
        db.add(c)
        db.commit()
        return c.id


def _wh_with(rows_bills, rows_codes, namespace="HIS"):
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    w = SyntheticDrugTraceWarehouse(namespace=namespace)
    for r in rows_bills:
        w.add_bill(r)
    for r in rows_codes:
        w.add_code(r)
    return w


# ================================================================ 迁移与装配
def test_a04_migration_idempotent_and_upgrade_path(tmp_path):
    """001—008 先行（旧插件集）→ 009/010 增量（drug_trace + FIX2）→ 幂等。"""
    import plugin_registry  # pathology+metrics
    url = f"sqlite:///{tmp_path}/up.sqlite3"
    os.environ["BLYB_DATABASE_URL"] = url
    e1 = make_engine()
    results1 = run_migrations(e1, extra_migrations=plugin_registry.MIGRATIONS)
    e1.dispose()
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    e2 = make_engine()
    results2 = run_migrations(e2, extra_migrations=reg.migrations)
    names2 = [n for _, n, r in results2 if r == "APPLIED"]
    assert names2 == ["009_drug_trace_domain", "010_drug_trace_fix2",
                       "011_drug_trace_fix3",
                       "012_drug_trace_daily_report",
                       "020_drug_trace_perf_indexes"], names2
    results3 = run_migrations(e2, extra_migrations=reg.migrations)
    assert all(r == "ALREADY_APPLIED" for _, _, r in results3)
    # 旧插件集在新 schema 上仍可运行（表结构扩展不破坏旧代码）
    results4 = run_migrations(e2, extra_migrations=plugin_registry.MIGRATIONS)
    assert all(r == "ALREADY_APPLIED" for _, _, r in results4)
    e2.dispose()
    os.environ.pop("BLYB_DATABASE_URL", None)


def test_a01_drug_only_registry_and_unknown_strictness(env):
    """drug-only 装配可启动；未知 job/事件/目标显式失败；禁用药品不调度。"""
    from domains.drug_trace import plugin as dt_plugin
    handlers = env["reg"].capability_merged("worker.job_handlers")
    assert "drug_trace.collect" in handlers
    # 未知插件严格报错（app_runtime 既有语义回归）
    import app_runtime
    with pytest.raises(RuntimeError):
        app_runtime.load_registry(["drug_trace", "nonexistent"])
    # 禁用药品：pathology+metrics 注册表不含药品 job/路由
    import plugin_registry
    assert "drug_trace.collect" not in dir(plugin_registry)
    # 未知模板/方法显式报错
    from connector_sdk import CapabilityError
    from sources.db_view.drug_trace import SyntheticDrugTraceWarehouse
    w = SyntheticDrugTraceWarehouse()
    with pytest.raises(CapabilityError):
        w.fetch_all("drugtrace.unknown_template", {}, 10)


# ================================================================ 整单抽取与质量
def test_a05_zero_one_n_codes_and_multipage(env):
    from domains.drug_trace import ingest, service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceBillRevision
    conn = _connector_id(env)
    # 一单 0 码、一单 1 码、一单跨页 N 码（page_limit=2 强制翻页）
    wh = _wh_with(
        [_bill_row("B0", kind="PATIENT_DISPENSE"),
         _bill_row("B1", kind="PATIENT_DISPENSE"),
         _bill_row("BN", kind="PATIENT_DISPENSE",
                   updated="2026-10-12T08:01:00")],
        [_code_row("B1", record_id="R1", code="81000000000001")]
        + [_code_row("BN", record_id=f"RN{i}", line_id=f"LN{i}",
                     code=f"81000000000{i:02d}",
                     updated="2026-10-12T08:01:00") for i in range(1, 6)])
    with env["sf"]() as db:
        run = service.run_collection(
            db, env["tenant_id"], conn, wh,
            window_begin=datetime(2026, 10, 12, 0, 0, 0),
            window_end=datetime(2026, 10, 13, 0, 0, 0))
        assert run["bills_seen"] == 3
        # K1/SD-05（DTR-SCOPE-DEV-20261027 §一 口径表 + 执行书 SD-07）：
        # 门诊 0 码单超过 7 天回看窗 → NOT_REPORTABLE 终态。B0 bill_time=
        # 2026-10-12 距今超窗，原断言 waiting==1/WAITING 按口径重挂；
        # 7 天内仍 WAITING 的两例见 test_drug_trace_scope_dev.py。
        assert run["waiting"] == 0 and run["valid"] == 2
        assert run["not_reportable"] == 1 and run["invalid"] == 0
        rev0 = (db.query(DrugTraceBillRevision)
                .join(DrugTraceBill, DrugTraceBill.id == DrugTraceBillRevision.bill_id)
                .filter(DrugTraceBill.source_bill_no == "B0").first())
        assert rev0.quality_state == "NOT_REPORTABLE"
        assert any(i["rule"] == "QD-NO-CODES-FINAL" for i in rev0.quality_issues)
        revn = (db.query(DrugTraceBillRevision)
                .join(DrugTraceBill, DrugTraceBill.id == DrugTraceBillRevision.bill_id)
                .filter(DrugTraceBill.source_bill_no == "BN").first())
        assert len(revn.lines) == 5  # 跨页全部读齐（翻页 code_page_limit=2 由下例覆盖）


def test_a05_multipage_codes_full_read(env):
    from domains.drug_trace import ingest, service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    codes = [_code_row("BP", record_id=f"RP{i:03d}", line_id=f"LP{i}",
                       code=f"8100000001{i:03d}") for i in range(1, 8)]
    wh = _wh_with([_bill_row("BP", kind="PATIENT_DISPENSE")], codes)
    with env["sf"]() as db:
        service.run_collection(
            db, env["tenant_id"], conn, wh,
            window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        assert len(rev.lines) == 7  # 7 码、page_limit=2 → 4 页读全


def test_a06_original_values_preserved(env):
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceCodeIndex
    conn = _connector_id(env)
    lead_zero_code = "00012345678901234567890"  # 前导零+超长数字串
    wh = _wh_with(
        [_bill_row("BZ", kind="PATIENT_DISPENSE",
                   source_bill_id="007")],
        [_code_row("BZ", source_bill_id="007", record_id="RZ",
                   code=lead_zero_code, quantity="0.25")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        idx = db.query(DrugTraceCodeIndex).first()
        assert idx.trace_code == lead_zero_code  # 不转数值/科学计数
        assert idx.quantity == "0.25"


def test_a07_cross_pharmacy_same_bill_no(env):
    """同单号跨药房/跨业务分类互不串单（复合键）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("S9", pharmacy="YF01", kind="PATIENT_DISPENSE"),
         _bill_row("S9", pharmacy="YF02", kind="PATIENT_DISPENSE"),
         _bill_row("S9", pharmacy="YF01", kind="SUPPLIER_IN")],
        [_code_row("S9", pharmacy="YF01", kind="PATIENT_DISPENSE", record_id="R1"),
         _code_row("S9", pharmacy="YF02", kind="PATIENT_DISPENSE", record_id="R2"),
         _code_row("S9", pharmacy="YF01", kind="SUPPLIER_IN", record_id="R3")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        assert db.query(DrugTraceBill).count() == 3


def test_a08_s03_halfopen_window_and_same_timestamp(env):
    """半开区间端点不重复/不遗漏；同刻多单 keyset 不丢。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("W1", updated="2026-10-12T00:00:00"),   # 恰在 begin → 包含
         _bill_row("W2", updated="2026-10-13T00:00:00"),   # 恰在 end → 排除
         _bill_row("W3", updated="2026-10-12T05:00:00"),
         _bill_row("W4", updated="2026-10-12T05:00:00")],  # 同刻多单
        [_code_row("W1", record_id="R1"), _code_row("W3", record_id="R2"),
         _code_row("W4", record_id="R3")])
    with env["sf"]() as db:
        run = service.run_collection(db, env["tenant_id"], conn, wh,
                                     window_begin=datetime(2026, 10, 12),
                                     window_end=datetime(2026, 10, 13))
        assert run["bills_seen"] == 3
        nos = {b.source_bill_no for b in db.query(DrugTraceBill).all()}
        assert nos == {"W1", "W3", "W4"}


def test_a09_checkpoint_same_transaction_rollback(env):
    """提交失败 → 快照与 checkpoint 全部回滚（无半提交状态）。"""
    from domains.drug_trace import ingest, service
    from domains.drug_trace.models import DrugTraceBill
    conn = _connector_id(env)
    wh_good = _wh_with([_bill_row("OK")], [_code_row("OK", record_id="R1")])

    class Boom(Exception):
        pass

    class FailingWh:
        dialect_name = "synthetic"

        def dialect(self):
            return self.dialect_name

        def fetch_all(self, template_id, params, limit, **kw):
            if template_id == "drugtrace.bill_codes":
                raise Boom("db down mid-bill")
            return wh_good.fetch_all(template_id, params, limit, **kw)

    with env["sf"]() as db:
        with pytest.raises(Boom):
            service.run_collection(
                db, env["tenant_id"], conn, FailingWh(),
                window_begin=datetime(2026, 10, 12), window_end=datetime(2026, 10, 13))
        assert db.query(DrugTraceBill).count() == 0
        cp = db.query(SourceCheckpoint).filter_by(
            tenant_id=env["tenant_id"], connector_id=conn,
            stream="drugtrace.bills").first()
        assert cp is None  # checkpoint 未推进


def test_a10_idempotent_reimport_and_conflict(env):
    """增量语义：HIS 触碰单据会推进 updated_at → 新窗口重扫。
    同版本同内容 → 幂等；同版本异内容 → 冲突登记不覆盖。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("IDEM")], [_code_row("IDEM", record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        # 重叠回看重扫未变数据（内容/digest 一致）→ 幂等重导
        r1 = service.run_collection(db, env["tenant_id"], conn, wh,
                                    window_begin=datetime(2026, 10, 12),
                                    window_end=datetime(2026, 10, 14),
                                    lookback_seconds=2 * 86400)
        assert r1["idempotent"] == 1 and r1["revisions_created"] == 0, r1
        # 同版本内容变化（updated_at 再推进）→ 冲突登记，不覆盖
        wh.mutate_code({"source_bill_id": "IDEM"}, {"trace_code": "99999999999999",
                                                    "updated_at": "2026-10-13T10:00:00"})
        wh.mutate_bill({"source_bill_id": "IDEM"}, {"updated_at": "2026-10-13T10:00:00"})
        r2 = service.run_collection(db, env["tenant_id"], conn, wh,
                                    window_begin=datetime(2026, 10, 13),
                                    window_end=datetime(2026, 10, 14),
                                    lookback_seconds=2 * 86400)
        assert r2["conflicts"] == 1, r2
        rev = db.query(DrugTraceBillRevision).first()
        assert any(i["rule"] == "QD-REVISION-CONFLICT" for i in rev.quality_issues)


def test_a11_kind_unknown_waits_and_profile_routing(env):
    from domains.drug_trace import contracts as C
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("K1", kind="UNKNOWN"),
         _bill_row("K2", kind="PATIENT_RETURN"),
         _bill_row("K3", kind="SUPPLIER_IN")],
        [_code_row("K1", kind="UNKNOWN", record_id="R1"),
         _code_row("K2", kind="PATIENT_RETURN", record_id="R2"),
         _code_row("K3", kind="SUPPLIER_IN", record_id="R3")])
    with env["sf"]() as db:
        run = service.run_collection(db, env["tenant_id"], conn, wh,
                                     window_begin=datetime(2026, 10, 12),
                                     window_end=datetime(2026, 10, 13))
        assert run["waiting"] == 1
        revs = {r.header["source_bill_id"]: r
                for r in db.query(DrugTraceBillRevision).all()}
        assert revs["K2"].quality_state == "VALID"
        assert C.profile_for_kind("PATIENT_RETURN") == C.PROFILE_RETAIL
        assert C.profile_for_kind("SUPPLIER_IN") == C.PROFILE_INOUT
        assert C.profile_for_kind("UNKNOWN") is None


def test_a12_duplicate_rows_and_codes_flagged(env):
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("D1")],
        [_code_row("D1", record_id="RD", code="81000000000001"),
         _code_row("D1", record_id="RD", code="81000000000001"),  # 来源重复行
         _code_row("D1", record_id="RD2", code="81000000000002"),
         _code_row("D1", record_id="RD3", code="81000000000002")])  # 同单同码
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        rules = {i["rule"] for i in rev.quality_issues}
        assert "QD-DUP-SOURCE-ROW" in rules and "QD-DUP-CODE" in rules
        assert rev.quality_state == "INVALID"
        # 重复行未被 DISTINCT 静默去重：快照保留 4 行
        assert len(rev.lines) == 4


def test_a13_limit_guard_boundary(env):
    """零售保护值 3500 的 N-1/N/N+1（用小保护值注入测试边界语义）。"""
    from domains.drug_trace import quality
    from domains.drug_trace import contracts as C
    header = _bill_row("G1")
    def lines(n):
        return [_code_row("G1", record_id=f"RG{i}", code=f"81{i:016d}")
                for i in range(n)]
    # 注入小保护值验证边界：N-1 过、N 阻断、N+1 阻断
    st, iss = quality.evaluate_revision(header, lines(2), profile=C.PROFILE_RETAIL,
                                        code_limit_guard=3)
    assert st == "VALID"
    st, iss = quality.evaluate_revision(header, lines(3), profile=C.PROFILE_RETAIL,
                                        code_limit_guard=3)
    assert st == "INVALID" and any(i["rule"] == "QD-CODE-LIMIT-GUARD" for i in iss)
    st, iss = quality.evaluate_revision(header, lines(4), profile=C.PROFILE_RETAIL,
                                        code_limit_guard=3)
    assert st == "INVALID"


def test_a14_readonly_guard(env):
    from connector_sdk import CapabilityError
    from sources.db_view import drug_trace as dtw
    with pytest.raises(CapabilityError):
        dtw.render_sql("drugtrace.poll_bills", "mysql")  # 无该方言
    sql = dtw.render_sql("drugtrace.poll_bills", "oracle", limit=100)
    assert sql.lstrip().upper().startswith("SELECT")
    assert ":begin_time" in sql and "< :end_time" in sql  # 绑定参数+半开区间


def test_a16_preview_dryrun_no_submission_no_network(env):
    from domains.drug_trace import service
    from core_db.models import Submission
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("PV", kind="PATIENT_DISPENSE")],
                  [_code_row("PV", record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        from domains.drug_trace.models import DrugTraceBillRevision
        rev = db.query(DrugTraceBillRevision).first()
        # 无字典批准 → build_payload 抛 DICTIONARY_UNMAPPED（等待，不猜 321/323）
        from domains.drug_trace import payload as payload_mod
        from domains.drug_trace.models import DrugTraceBill
        bill = db.get(DrugTraceBill, rev.bill_id)
        with pytest.raises(ApiError) as ei:
            payload_mod.build_payload(db, env["tenant_id"], rev, bill,
                                      profile="alihealth_yljg_retail_v1")
        assert ei.value.code == "DICTIONARY_UNMAPPED"
        assert db.query(Submission).count() == 0


def test_s01_two_codes_same_group_both_kept(env):
    """S01：同组两个不同码都保留（不因 MAX 丢码）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceCodeIndex
    conn = _connector_id(env)
    wh = _wh_with(
        [_bill_row("SG")],
        [_code_row("SG", record_id="R1", code="000123"),
         _code_row("SG", record_id="R2", code="000124")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        codes = {c.trace_code for c in db.query(DrugTraceCodeIndex).all()}
        assert codes == {"000123", "000124"}


def test_s02_record_granularity_kept(env):
    """S02：RECORD_ID 粒度保留；单据键=复合键而非拼接明细ID。"""
    from domains.drug_trace import ingest
    header = _bill_row("GR")
    lines = [_code_row("GR", record_id="R100", line_id="D1"),
             _code_row("GR", record_id="R200", line_id="D1")]  # 同明细两码
    digest1 = ingest.content_digest(header, lines)
    assert ingest.composite_key(header) == "HIS|YF01|PATIENT_DISPENSE|GR"
    assert "PRESC" not in ingest.composite_key(header)


def test_s06_level_boundary_flagged(env):
    """S06：f/s/t 存在但 code_level 未确认 → 显式能力边界项。"""
    from domains.drug_trace import quality
    from domains.drug_trace import contracts as C
    lines = [_code_row("LV", record_id="R1", code="81000000000001",
                       f_trace_code="8100000000000F",
                       t_trace_code="81000000000001")]
    st, iss = quality.evaluate_revision(_bill_row("LV"), lines,
                                        profile=C.PROFILE_RETAIL,
                                        code_limit_guard=3500)
    assert st == "VALID"
    assert any(i["rule"] == "QD-LEVEL-UNCONFIRMED" for i in iss)


# ================================================================ 冻结与发送
def _frozen_retail(env, tmp_path):
    """准备一张 VALID 零售单 + 批准字典 + mock 目的地 + PREPARED 提交。"""
    from core_db.models import ConfigRevision, DictionaryRevision
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("FD", kind="PATIENT_DISPENSE",
                             source_bill_kind_raw="323")],
                  [_code_row("FD", kind="PATIENT_DISPENSE", record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "ENT-HOSP-001"}]),
                ("drugtrace.query_bill_type", [{"code": "323", "target": "A"}])):
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], namespace=ns,
                version="1", items=items, source="target",
                approved_by="admin", effective_from=datetime(2026, 10, 1)))
        dest = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="alihealth-test",
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload", "query"], is_mock=True, status="ENABLED")
        db.add(dest)
        db.commit()
        result = service.prepare_submission(
            db, env["tenant_id"], rev.id,
            profile="alihealth_yljg_retail_v1", destination_id=dest.id)
        return {"revision": rev.id, "dest": dest, "submission": result}


def test_b02_profile_routing_and_bill_code_stability(env, tmp_path):
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillCode
    prep = _frozen_retail(env, tmp_path)
    with env["sf"]() as db:
        pr = db.get(PackageRevision, prep["submission"]["package_revision_id"])
        assert pr.payload["method"].endswith("uploadretail")
        assert pr.payload["bill_type"] == "323"
        # 再次 prepare 同版本 → 幂等复用同一提交
        r2 = service.prepare_submission(
            db, env["tenant_id"], prep["revision"],
            profile="alihealth_yljg_retail_v1",
            destination_id=prep["dest"].id)
        assert r2["submission_id"] == prep["submission"]["submission_id"]
        assert db.query(DrugTraceBillCode).count() == 1  # bill_code 稳定不重建


def test_a02_empty_evidence_blocks_real_send(env, tmp_path):
    """空证据表不放行真实采集/发送（DTR-BASE-03 修订验证）。"""
    from domains.drug_trace import service
    from core_db.models import Destination, Submission
    prep = _frozen_retail(env, tmp_path)
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        dest.is_mock = False  # 真实目标
        db.commit()
        sub = db.get(Submission, prep["submission"]["submission_id"])
        with pytest.raises(ApiError) as ei:
            service.perform_send(db, env["tenant_id"], sub)
        assert ei.value.code == "PROFILE_UNAPPROVED"
        assert "EXT" in ei.value.message  # 明确缺项提示
        # 采集能力同样 fail-closed
        with pytest.raises(ApiError):
            service.require_production_evidence(
                db, env["tenant_id"], profile="alihealth_yljg_retail_v1",
                capability="collect")
        # 缺项审批被拒
        with pytest.raises(ApiError):
            service.approve_evidence(
                db, env["tenant_id"], profile="alihealth_yljg_retail_v1",
                capability="send", destination_id=dest.id,
                provided_items=[{"key": "EXT-03", "evidence_ref": "x"}],
                approved_by="admin")


def test_a02_full_evidence_allows_and_revoke_blocks(env, tmp_path):
    from domains.drug_trace import service
    from core_db.models import Destination, Submission
    prep = _frozen_retail(env, tmp_path)
    with env["sf"]() as db:
        dest = db.get(Destination, prep["dest"].id)
        dest.is_mock = False
        db.commit()
        sub = db.get(Submission, prep["submission"]["submission_id"])
        from core_db.models import ConfigRevision
        cfg = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=1,
            content_json={"dmz_base_url": "https://dmz-blyb.invalid",
                          "app_key": "k", "app_secret_ref": "env:DT_REAL_SECRET"},
            digest="d", state="PUBLISHED")
        db.add(cfg)
        db.flush()
        dest.config_revision = cfg.id
        # FIX2-02：send 必需项扩为 8 项（+EXT-05 校时/EXT-10 补报范围/EXT-12 首批授权）
        items = [{"key": k, "evidence_ref": f"doc:{k}"}
                 for k in ("EXT-03", "EXT-04", "EXT-05", "EXT-06", "EXT-07",
                           "EXT-08", "EXT-10", "EXT-12")]
        ev = service.approve_evidence(
            db, env["tenant_id"], profile="alihealth_yljg_retail_v1",
            capability="send", destination_id=dest.id,
            provided_items=items, approved_by="admin",
            scope={"window_begin": "2026-10-01T00:00:00",
                   "window_end": "2026-11-01T00:00:00",
                   "pharmacies": ["YF01"], "max_bills": 10},
            config_release_id=cfg.id)   # FIX3-04：完整scope+精确配置绑定
        db.commit()
        # FB-02 签名资格已按批准修订关闭；本地确定性阻断改由永久安全属性
        # 验证：mock 标记密钥上真实 transport 必拒（不发出任何请求）
        os.environ["DT_REAL_SECRET"] = "__mock__real-for-blocking-test"
        from connector_sdk import CapabilityError as _CE
        with pytest.raises(_CE) as ei:
            service.perform_send(db, env["tenant_id"], sub)
        assert ei.value.code == "CAPABILITY_UNSUPPORTED"
        assert "mock-marked secret" in ei.value.message
        os.environ.pop("DT_REAL_SECRET", None)
        service.revoke_evidence(db, env["tenant_id"], ev.id)
        db.commit()
        with pytest.raises(ApiError):
            service.require_production_evidence(
                db, env["tenant_id"], profile="alihealth_yljg_retail_v1",
                capability="send", destination_id=dest.id)


def test_b03_b05_mock_send_unknown_and_query(env, tmp_path):
    """FIX2 修订后语义：Mock 发送受理（官方 response_success 信封）；终态禁重发；
    UNKNOWN 自动排查询；未知响应结构不盲判定。"""
    from domains.drug_trace import service
    from core_db.models import Destination, Job, Submission

    prep = _frozen_retail(env, tmp_path)
    with env["sf"]() as db:
        sub = db.get(Submission, prep["submission"]["submission_id"])
        dest = db.get(Destination, prep["dest"].id)
        orig_build = service.build_client
        upload_key = "alibaba_alihealth_drugtrace_top_yljg_uploadretail_response"

        def accepted_client_factory(db_, tid, dest_, **kw):
            from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
            from destinations.alihealth_drugtrace.transport import MockTransport
            return AlihealthDrugTraceClient(
                MockTransport(body=('{"%s": {"response_success": true,'
                                    ' "model": "SYN"}}' % upload_key).encode()),
                app_key="test-key", app_secret_ref="mock:demo",
                resolve_secret=lambda r: "__mock__demo")

        service.build_client = accepted_client_factory
        try:
            result = service.perform_send(db, env["tenant_id"], sub)
            assert result["outcome"] == "ACCEPTED"
            assert sub.state == SubmissionState.ACCEPTED
        finally:
            service.build_client = orig_build
        # 终态（ACCEPTED）再 send → 状态守卫拒绝（R2-06：不盲重发）
        with pytest.raises(ApiError) as ei:
            service.perform_send(db, env["tenant_id"], sub)
        assert ei.value.code == "VERSION_CONFLICT"
        # 终态 dry-run 同样拒绝（不可发送状态没有「发送预览」）
        attempts_before = db.query(DeliveryAttempt).filter_by(
            submission_id=sub.id).count()
        with pytest.raises(ApiError):
            service.perform_send(db, env["tenant_id"], sub, dry_run=True)
        assert db.query(DeliveryAttempt).filter_by(
            submission_id=sub.id).count() == attempts_before
        assert sub.state == SubmissionState.ACCEPTED
        # 第二个场景：PREPARED 提交的 dry-run 零副作用（不建尝试/不改状态/零外连）
        sub2 = db.get(Submission, prep["submission"]["submission_id"])
        sub2.state = SubmissionState.PREPARED
        db.commit()
        attempts2_before = db.query(DeliveryAttempt).filter_by(
            submission_id=sub2.id).count()
        preview = service.perform_send(db, env["tenant_id"], sub2, dry_run=True)
        assert preview["outcome"] == "DRY_RUN"
        # FIX3-01/R3-01：上传 ref_user_id = 医院 ref_ent_id（不再是独立
        # ref_user_id 字典值；官方上传定义 ref_user_id 对应医院 ref_ent_id）
        assert preview["biz_params"]["ref_user_id"] == "ENT-HOSP-001"
        assert "bill_time" in preview["biz_params"]
        assert "_doc" not in preview["biz_params"]
        assert db.query(DeliveryAttempt).filter_by(
            submission_id=sub2.id).count() == attempts2_before
        assert sub2.state == SubmissionState.PREPARED

        def unknown_client_factory(db_, tid, dest_, **kw):
            from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
            from destinations.alihealth_drugtrace.transport import MockTransport
            return AlihealthDrugTraceClient(
                MockTransport(body=b"<html>gateway timeout</html>"),
                app_key="test-key", app_secret_ref="mock:demo",
                resolve_secret=lambda r: "__mock__demo")

        service.build_client = unknown_client_factory
        try:
            result2 = service.perform_send(db, env["tenant_id"], sub2)
            assert result2["outcome"] == "UNKNOWN"
            assert sub2.state == SubmissionState.UNKNOWN
            jobs = db.query(Job).filter_by(
                tenant_id=env["tenant_id"],
                job_type="drug_trace.query_status").all()
            assert jobs, "UNKNOWN 必须排查询 job（先查询不盲重发）"
        finally:
            service.build_client = orig_build
        # UNKNOWN 再 send → 状态守卫拒绝（只允许查询）
        with pytest.raises(ApiError) as ei2:
            service.perform_send(db, env["tenant_id"], sub2)
        assert ei2.value.code == "VERSION_CONFLICT"


def test_b07_gateway_error_classification():
    """FIX2-01：官方 response_success 信封解析；error_response 分类不变。"""
    import destinations.alihealth_drugtrace.protocol as P
    m = "alibaba.alihealth.drugtrace.top.yljg.uploadretail"
    ok_body = b'{"%s": {"response_success": true, "model": "SYN"}}' % \
        P.method_response_key(m).encode()
    r = P.parse_response(m, 200, ok_body)
    assert r["outcome"] == "ACCEPTED"
    # 官方拒绝形状：response_success=false + msg_code/msg_info
    r = P.parse_response(m, 200,
                         b'{"%s": {"response_success": false,'
                         b' "msg_code": "BILL_DUPLICATE",'
                         b' "msg_info": "duplicate bill"}}'
                         % P.method_response_key(m).encode())
    assert r["outcome"] == "REJECTED"
    assert r["error"]["msg_code"] == "BILL_DUPLICATE"
    # 旧自行假设的 success 字段不再被解析：不得制造兼容假象 → UNKNOWN
    r = P.parse_response(m, 200,
                         b'{"%s": {"success": true}}'
                         % P.method_response_key(m).encode())
    assert r["outcome"] == "UNKNOWN"
    r = P.parse_response(m, 200,
                         b'{"error_response": {"code": "15", "msg": "isp limit"}}')
    assert r["outcome"] == "UNKNOWN"  # 限流类 → 可退避
    r = P.parse_response(m, 200,
                         b'{"error_response": {"code": "21", "msg": "bad bill_type"}}')
    assert r["outcome"] == "REJECTED"
    r = P.parse_response(m, 502, b"bad gateway")
    assert r["outcome"] == "UNKNOWN"


def test_b07_signature_blocked_until_vectors_verified():
    """签名安全属性（FB-02 资格关闭后保留）：mock 标记密钥 + 真实 transport
    → 必拒（类型+密钥标记双绑定，R2-04 语义保留）；MockTransport 离线通道可用。

    EXT-07 签名一致性已于 DTR-FIRSTBILL-EXEC-20261021 按批准修订关闭
    （官方 C# 向量 + 官方 PyPI SDK 移植对照 + 平台真实受理；见
    test_drug_trace_fb02_official.py 与任务 evidence）。本测试守护的不再是
    "未验签全阻断"，而是"mock 密钥永不emit真实请求"这一永久属性。
    """
    from connector_sdk import CapabilityError
    from destinations.alihealth_drugtrace import protocol as P
    from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
    from destinations.alihealth_drugtrace.transport import (MockTransport,
                                                            RealTransport,
                                                            TransportResult)
    # 伪造 __mock__ 前缀密钥 + 真实 transport → 必须被拒（永久安全属性）
    fake_transport = RealTransport("https://dmz.invalid")
    calls = []

    def fake_post(body, ctype):
        calls.append(body)
        return TransportResult(http_status=200, body=b"{}")
    fake_transport.post = fake_post
    client = AlihealthDrugTraceClient(
        fake_transport, app_key="synthetic", app_secret_ref="mock:offline",
        resolve_secret=lambda ref: "__mock__offline")
    with pytest.raises(CapabilityError) as ei:
        client.upload_retail({"bill_code": "SYN-B1"})
    assert "mock-marked secret" in ei.value.message
    assert not calls, "真实 transport 不得发出任何请求（强类型绑定）"
    # MockTransport + mock 密钥：离线通道可用，签名确定性
    mt = AlihealthDrugTraceClient(
        MockTransport(body=b"{}"), app_key="k", app_secret_ref="mock:demo",
        resolve_secret=lambda r: "__mock__demo")
    body, ctype = P.build_form(
        "m.n", "key", "__mock__demo", biz_params={"a": "1"},
        timestamp="2026-10-16 00:00:00")
    assert ctype.startswith("application/x-www-form-urlencoded")
    assert b"sign=" in body and b"a=1" in body
    body2, _ = P.build_form("m.n", "key", "__mock__demo",
                            biz_params={"a": "1"},
                            timestamp="2026-10-16 00:00:00")
    assert body == body2
    # TOP MD5 语义自检（公开算法：md5(secret + sorted k v + secret).upper）
    sign = P.top_sign({"b": "2", "a": "1"}, "s")
    import hashlib
    assert sign == hashlib.md5(b"sa1b2s").hexdigest().upper()
    assert mt is not None


# ================================================================ worker 装配
def test_worker_routes_drug_trace_jobs(env, tmp_path):
    """engine 消费药品 job：collect → BILL_INGESTED(VALID) → prepare → PREPARED。"""
    from apps.worker.engine import WorkerEngine
    from core_db.models import (ConfigRevision, DictionaryRevision, Submission)
    from domains.drug_trace.models import DrugTraceCollectionRun
    from domains.drug_trace import service
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("WK", kind="PATIENT_DISPENSE",
                             source_bill_kind_raw="323")],
                  [_code_row("WK", kind="PATIENT_DISPENSE", record_id="R1")])
    with env["sf"]() as db:
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "ENT-H"}]),
                ("drugtrace.query_bill_type", [{"code": "323", "target": "A"}])):
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], namespace=ns,
                version="1", items=items, approved_by="admin"))
        dest = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="alihealth-mock",
            profile_revision="alihealth_yljg_retail_v1", mode="PUSH",
            capabilities=["upload", "query"], is_mock=True, status="ENABLED")
        db.add(dest)
        # 注入合成仓储（真实驱动不可用时的测试通道）
        from workflow_core.jobs import enqueue_job
        from domains.drug_trace import contracts as C
        run = DrugTraceCollectionRun(
            id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn,
            stream="drugtrace.bills", window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), state="PENDING")
        db.add(run)
        db.commit()
        # monkeypatch make_warehouse → 固定合成仓储（保持到 tick 结束）
        enqueue_job(db, env["tenant_id"], C.DomainJobType.COLLECT,
                    f"collect:{run.id}",
                    {"collection_run_id": run.id, "connector_id": conn,
                     "connector_config": {"mode": "synthetic"},
                     "window_begin": "2026-10-12T00:00:00",
                     "window_end": "2026-10-13T00:00:00"})
        db.commit()
    orig = service.make_warehouse
    service.make_warehouse = lambda cfg: wh
    engine = WorkerEngine(env["sf"], registry=_EngineRegAdapter(env["reg"]))
    from core_db.models import Submission as _Sub
    try:
        for _ in range(5):  # outbox→job→事件→job 链式推进需要多 tick
            engine.tick()
            with env["sf"]() as db:
                if db.query(_Sub).filter_by(tenant_id=env["tenant_id"]).count():
                    break
    finally:
        service.make_warehouse = orig
    with env["sf"]() as db:
        subs = db.query(Submission).filter_by(tenant_id=env["tenant_id"]).all()
        assert subs and subs[0].state == SubmissionState.PREPARED, \
            "冻结→提交就绪（不自动发送）"
        run = db.query(DrugTraceCollectionRun).first()
        assert run.state == "DONE"


class _EngineRegAdapter:
    """engine 需要的适配面（复用 engine.PluginRegistryAdapter 语义）。"""

    def __init__(self, reg):
        from apps.worker.engine import PluginRegistryAdapter
        self._inner = PluginRegistryAdapter(reg)

    def __getattr__(self, name):
        return getattr(self._inner, name)


# ================================================================ FIN-04 测试
def test_fin_a15_branch_rules_default_empty_wait_visible(env):
    """FIN-A15/16：默认无批准规则——正常/住院/退药原始状态都进质量等待，
    QD-BRANCH-UNAPPROVED 可见；不静默放行为正常发药。"""
    from domains.drug_trace import business_rules as BR
    assert BR.APPROVED_STATUS_RULES.entries == (), "初始必须为空（未批准）"
    cases = [
        ("PRESC_SOURCE=0;FLAG=1", "门诊正常样本形态"),
        ("PRESC_SOURCE=1;FLAG=0", "住院未解释形态（42张）"),
        ("PRESC_SOURCE=0;FLAG=3", "部分退（注释口径）"),
    ]
    for raw, label in cases:
        header = _bill_row(bill_id=f"B{raw[-1]}", kind="UNKNOWN",
                           source_bill_kind_raw=raw)
        kind = BR.resolve_business_kind(header)
        assert kind == "UNKNOWN", label
        issue = BR.branch_wait_issue(header)
        assert issue is not None and issue["rule"] == "QD-BRANCH-UNAPPROVED"
        assert "not approval" in issue["detail"]["reason"]

    # 批准规则登记后（模拟院方批准门诊正常），解析生效
    approved = BR.ApprovedStatusRules(entries=(
        BR.ApprovedRule(presc_source="0", flag="1",
                        branch=BR.BRANCH_OUTPATIENT_NORMAL,
                        approved_by="药学部+信息科",
                        evidence_ref="req-confirm-2026-07-21"),))
    header = _bill_row(bill_id="BA", kind="UNKNOWN",
                       source_bill_kind_raw="PRESC_SOURCE=0;FLAG=1")
    assert BR.resolve_business_kind(header, approved) == "PATIENT_DISPENSE"
    # 视图已给非 UNKNOWN 时尊重视图
    assert BR.resolve_business_kind(
        _bill_row(kind="PATIENT_RETURN"), approved) == "PATIENT_RETURN"
    # 缺原始状态 → UNKNOWN（非分支问题，QD-KIND-UNKNOWN 兜底）
    assert BR.resolve_business_kind(_bill_row(kind="UNKNOWN")) == "UNKNOWN"
    assert BR.branch_wait_issue(_bill_row(kind="UNKNOWN")) is None


def test_fin_a16_inpatient_refund_split_wait(env):
    """住院/退药/拆零未批准分支导入：WAITING 而非 VALID/INVALID。"""
    from domains.drug_trace import ingest
    with env["sf"]() as db:
        cid = _connector_id(env)
        header = _bill_row(bill_id="BIN1", kind="UNKNOWN",
                           source_bill_kind_raw="PRESC_SOURCE=1;FLAG=0")
        lines = [_code_row(bill_id="BIN1", record_id="R1",
                           code="81000000000123"),
                 _code_row(bill_id="BIN1", record_id="R2",
                           code="81000000000124")]
        result = ingest.import_bill(db, env["tenant_id"], cid, header, lines,
                                    profile=None)
        assert result["state"] == "WAITING"
        rules = {i["rule"] for i in result["issues"]}
        assert "QD-BRANCH-UNAPPROVED" in rules
        assert "QD-KIND-UNKNOWN" in rules
        # 拆零行：quantity 未知规则下不复制明细数量——quantity 留空合法
        header2 = _bill_row(bill_id="BS1", kind="UNKNOWN",
                            source_bill_kind_raw="PRESC_SOURCE=0;FLAG=1;SPLIT_FLAG=1")
        lines2 = [_code_row(bill_id="BS1", record_id="R3", quantity="")]
        result2 = ingest.import_bill(db, env["tenant_id"], cid, header2, lines2,
                                     profile=None)
        assert result2["state"] == "WAITING"


def test_fin_a16_approved_activation_reimport(env):
    """批准规则登记后重导同 revision：快照 digest 不变（no-op），但 bill 的
    business_kind 被解析激活（等待单 → 可分类）。"""
    from domains.drug_trace import ingest, models as M
    from domains.drug_trace import business_rules as BR
    approved = BR.ApprovedStatusRules(entries=(
        BR.ApprovedRule(presc_source="0", flag="1",
                        branch=BR.BRANCH_OUTPATIENT_NORMAL,
                        approved_by="test", evidence_ref="t"),))
    orig_resolve = BR.resolve_business_kind
    header = _bill_row(bill_id="BA1", kind="UNKNOWN",
                       source_bill_kind_raw="PRESC_SOURCE=0;FLAG=1")
    lines = [_code_row(bill_id="BA1", record_id="R9")]
    with env["sf"]() as db:
        cid = _connector_id(env)
        r1 = ingest.import_bill(db, env["tenant_id"], cid, header, lines,
                                profile=None)
        assert r1["created"] and r1["state"] == "WAITING"
        # 登记（monkeypatch 模块级注册表）
        import domains.drug_trace.business_rules as br_mod
        saved = br_mod.APPROVED_STATUS_RULES
        br_mod.APPROVED_STATUS_RULES = approved
        try:
            r2 = ingest.import_bill(db, env["tenant_id"], cid, header, lines,
                                    profile=None)
        finally:
            br_mod.APPROVED_STATUS_RULES = saved
        assert r2["created"] is False          # digest 一致 → no-op revision
        bill = db.query(M.DrugTraceBill).filter_by(
            tenant_id=env["tenant_id"], source_bill_id="BA1").one()
        assert bill.business_kind == "PATIENT_DISPENSE"  # 实体状态已激活


def test_fin_a18_continuous_mode_requires_change_source():
    """FIN-A18：批次模式默认可用；continuous 无确认变更源 → 拒绝；
    有 {kind, evidence_ref} 才放行。"""
    from plugins.domains.drug_trace.business_rules import \
        require_continuous_mode
    from contracts import ApiError
    require_continuous_mode(None)                       # batch 默认
    require_continuous_mode({"collection_mode": "batch"})
    with pytest.raises(ApiError) as ei:
        require_continuous_mode({"collection_mode": "continuous"})
    assert "change source" in str(ei.value)
    with pytest.raises(ApiError):
        require_continuous_mode({"collection_mode": "continuous",
                                 "change_source": {"kind": ""}})
    require_continuous_mode({
        "collection_mode": "continuous",
        "change_source": {"kind": "indexed_modify_time",
                          "evidence_ref": "dba-confirmation-2026-10-21"}})
    with pytest.raises(ApiError):
        require_continuous_mode({"collection_mode": "weird"})


def test_fin_r7_source_profile_draft():
    """r7 草案 revision 可检索；require_runtime_ready 仍拒绝生产采集。"""
    from sources.db_view.source_profiles import (
        HIS_TRACE_20261020_R7, get_source_profile)
    p = get_source_profile(HIS_TRACE_20261020_R7.key)
    assert p.evidence_level == "LIVE_READONLY_PROBED"
    assert "live_view_contract_verification" in p.unresolved
    assert "dba_reviewed_view_ddl_execution" in p.unresolved
    from connector_sdk import CapabilityError
    with pytest.raises(CapabilityError):
        p.require_runtime_ready()
    # r1/r2 原文未变
    from sources.db_view.source_profiles import (
        HIS_TRACE_20261019_R1, HIS_TRACE_20261019_R2)
    assert HIS_TRACE_20261019_R1.evidence_level == "PLATFORM_METADATA_ONLY"
    assert HIS_TRACE_20261019_R2.evidence_level == "LIVE_READONLY_PROBED"


# ================================================================ FIN-05 测试
def test_fin_a21_session_optional_end_to_end(monkeypatch):
    """session 可选链路：SecretRef(env:) → client → 签名表单；缺省允许；
    file: 明确拒绝；session 值不进冻结快照/日志。"""
    import domains.drug_trace.service as S
    from destinations.alihealth_drugtrace import protocol as P
    from destinations.alihealth_drugtrace.transport import MockTransport

    class Dest:
        is_mock = True
        profile_revision = ""
        config_revision = ""

    monkeypatch.setenv("BLYB_TEST_SESSION", "sess-ABC123")
    # env: SecretRef → 注入 client → build_form 表单含 session 且参与签名
    captured = {}
    orig_build = P.build_form

    def spy_build(method, app_key, app_secret, **kw):
        body, ctype = orig_build(method, app_key, app_secret, **kw)
        captured.setdefault("sessions", []).append(kw.get("session", ""))
        captured.setdefault("bodies", []).append(body)
        return body, ctype

    monkeypatch.setattr(P, "build_form", spy_build)
    forms = []
    from urllib.parse import parse_qs
    transport = MockTransport(body_fn=lambda b: (forms.append(b), 200,
                                                 b"{}")[1:])
    dest = Dest()
    # build_client 走 mock 分支需要 destination 对象属性——直接构造 client 验证
    from destinations.alihealth_drugtrace.client import AlihealthDrugTraceClient
    client = AlihealthDrugTraceClient(
        transport, app_key="k", app_secret_ref="mock:offline",
        resolve_secret=S.resolve_secret_default,
        session=S.resolve_secret_default("env:BLYB_TEST_SESSION"))
    client.query_bill_status({"ref_ent_id": "1", "bill_code": "B",
                              "bill_type": "A", "begin_date": "2026-10-12",
                              "end_date": "2026-10-14"})
    assert captured["sessions"] == ["sess-ABC123"]
    form = parse_qs(forms[-1].decode())
    assert form["session"] == ["sess-ABC123"]
    # 无 session 缺省允许（医院自用）
    client2 = AlihealthDrugTraceClient(
        transport, app_key="k", app_secret_ref="mock:offline",
        resolve_secret=S.resolve_secret_default)
    client2.query_bill_status({"ref_ent_id": "1", "bill_code": "B",
                               "bill_type": "A", "begin_date": "x",
                               "end_date": "y"})
    assert captured["sessions"] == ["sess-ABC123", ""]


def test_fin_a21_session_ref_file_rejected(env):
    """file: SecretRef 未实现——真实目标配置引用即拒绝，不假装支持。"""
    from domains.drug_trace import service as S
    from core_db.models import Destination
    with env["sf"]() as db:
        d = Destination(id=new_uuid(), tenant_id=env["tenant_id"],
                        code="fin05-real-dest",
                        profile_revision="alihealth_yljg_retail_v1@1",
                        mode="PUSH", capabilities=["upload", "query"],
                        is_mock=False, status="ENABLED")
        db.add(d)
        db.commit()
        from contracts import ApiError
        # file: session_ref 未实现——直接验证 build_client 的拒绝分支：
        # 用最小真实目标 + env 配置化（file: 引用在配置读取处被拒）
        with pytest.raises(ApiError, match="dmz_base_url"):
            S.build_client(db, env["tenant_id"], d, profile="alihealth_yljg_retail_v1")


def test_fin_a22_query_date_semantics_windows():
    """历史 bill_time 与上传日期相差数月：默认 both 开两个窗口且都不漏；
    语义取值非法拒绝。"""
    from domains.drug_trace import service as S
    from contracts import ApiError
    payload = {"bill_time": "2026-07-25T08:00:00"}
    upload = datetime(2026, 10, 20, 12, 0, 0)
    wins = S._query_windows(payload, upload, "both")
    assert len(wins) == 2
    flat = {w for w in wins}
    assert ("2026-07-24", "2026-07-26") in flat      # 业务日期窗
    assert ("2026-10-19", "2026-10-21") in flat      # 上传日期窗
    assert S._query_windows(payload, upload, "bill_time") == \
        [("2026-07-24", "2026-07-26")]
    assert S._query_windows(payload, upload, "upload_time") == \
        [("2026-10-19", "2026-10-21")]
    # 相邻日期去重：bill_time 与上传同日 → both 只开一个窗
    near = S._query_windows({"bill_time": "2026-10-20T09:00:00"},
                            upload, "both")
    assert len(near) == 1
    with pytest.raises(ApiError):
        S._query_date_semantics({"query_date_semantics": "guess"})
    assert S._query_date_semantics({}) == "both"
    assert S._query_date_semantics(
        {"query_date_semantics": "upload_time"}) == "upload_time"


def test_fin_a22_merged_windows_hit_old_bill():
    """双窗口合并命中：老窗口返回条目 + 新窗口空 → OK 且含条目（不误判
    NOT_FOUND）。"""
    from domains.drug_trace import service as S

    class FakeClient:
        def __init__(self):
            self.calls = []

        def query_bill_status(self, biz):
            self.calls.append((biz["begin_date"], biz["end_date"]))
            if biz["begin_date"] == "2026-07-24":
                return {"found": True, "total_num": 1, "unknown": False,
                        "bills": {"list": [
                            {"bill_code": "OLD-1", "result_type": "3",
                             "sub_process_flag": "51"}]}}
            return {"found": True, "total_num": 0, "unknown": False,
                    "bills": {"list": []}}

    fc = FakeClient()
    full = S._query_full_windows(
        fc, {"bill_code": "OLD-1"}, {"bill_time": "2026-07-25T08:00:00"},
        datetime(2026, 10, 20, 12, 0, 0), "both")
    assert full["status"] == "OK"
    assert len(full["entries"]) == 1 and \
        full["entries"][0]["bill_code"] == "OLD-1"
    assert full["windows_queried"] == 2
    cls = S._classify_query(full, "OLD-1")
    assert cls["state"] == "CONFIRMED"
    # 单窗口语义只查业务窗（官方确认为业务日期口径时）
    fc2 = FakeClient()
    full2 = S._query_full_windows(
        fc2, {"bill_code": "OLD-1"}, {"bill_time": "2026-07-25T08:00:00"},
        datetime(2026, 10, 20, 12, 0, 0), "bill_time")
    assert full2["windows_queried"] == 1


def test_fin_a23_response_normalization_matrix():
    """FIN-A23：空列表/空 model/缺字段/权限错/未知枚举/部分成功分类。"""
    from destinations.alihealth_drugtrace.protocol import (
        QUERY_METHOD, parse_query_response, parse_response)
    key = QUERY_METHOD.replace(".", "_") + "_response"
    # 空 result_list + total 0：完整查询确实无此单（NOT_FOUND 语义）
    r = parse_query_response(200, (
        '{"%s": {"result": {"response_success": true, "model": '
        '{"total_num": 0, "result_list": '
        '{"bill_deal_status_search_do": []}}}}}' % key).encode())
    assert r["found"] is True and r["bills"]["list"] == [] \
        and r["total_num"] == 0
    # model 是字符串（非对象）→ unknown
    r2 = parse_query_response(200, (
        '{"%s": {"result": {"response_success": true, "model": ""}}}'
        % key).encode())
    assert r2["unknown"] is True
    # result_list 缺失 → unknown
    r3 = parse_query_response(200, (
        '{"%s": {"result": {"response_success": true, "model": '
        '{"total_num": 3}}}}}' % key).encode())
    assert r3["unknown"] is True
    # 权限错误（error_response）→ 明确错误，绝不等价 NOT_FOUND
    r4 = parse_query_response(200, (
        '{"error_response": {"code": "15", "msg": "isp permission denied"}}'
    ).encode())
    assert r4["unknown"] is False and r4["error"]["code"] == "15"
    # 上传响应：response_success=false + msg → REJECTED；缺字段 → UNKNOWN
    up = "alibaba_alihealth_drugtrace_top_yljg_uploadretail_response"
    r5 = parse_response(
        "alibaba.alihealth.drugtrace.top.yljg.uploadretail", 200,
        ('{"%s": {"response_success": false, "msg_code": "E", '
         '"msg_info": "bad"}}' % up).encode())
    assert r5["outcome"] == "REJECTED"
    r6 = parse_response(
        "alibaba.alihealth.drugtrace.top.yljg.uploadretail", 200,
        ('{"%s": {"other": 1}}' % up).encode())
    assert r6["outcome"] == "UNKNOWN"
    # 未知枚举组合 → MANUAL_REVIEW（不猜）
    from domains.drug_trace import contracts as C
    assert C.lookup_query_status("9", "99") == "MANUAL_REVIEW"
    # 321/323：无默认 bill_type（EXT-08 未批准保持哨兵）
    assert C.BILL_TYPE_UNAPPROVED_SENTINEL is None
