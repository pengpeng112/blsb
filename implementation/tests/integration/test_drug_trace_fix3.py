"""药品追溯第三轮整改测试（FIX3；R3-01—07 反例 → G3-01—19 正确行为）。

把 review-3-20261017 的 14 个离线反例逐条转换为正确业务断言，并覆盖
NEXT_AI_PROMPT 的 G3-01—G3-17 验收（G3-18 真实 PG 恢复见
test_drug_trace_pg_fix3.py；G3-19 慢流 deadline 在本文件，浏览器禁发闭环
证据在 fix3-20261017/evidence）。全部离线：临时 SQLite、fake DBAPI、本地
TLS 慢流服务；零外连、零真实数据、零凭据读取。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: F401,E402

from contracts import ApiError, SubmissionState, new_uuid  # noqa: E402
from connector_sdk import CapabilityError  # noqa: E402
from core_db import make_engine, make_session_factory, run_migrations  # noqa: E402
from core_db.models import (ConfigRevision, Destination, DictionaryRevision, Job,
                            PackageRevision, Submission, Tenant)  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{tmp_path}/fix3.sqlite3"
    import app_runtime
    reg = app_runtime.load_registry(["drug_trace"])
    engine = make_engine()
    run_migrations(engine, extra_migrations=reg.migrations)
    sf = make_session_factory(engine)
    with sf() as db:
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
    row = {"source_system": "HIS", "source_bill_id": bill_id,
           "source_bill_no": bill_id, "source_revision": revision,
           "business_kind": kind, "pharmacy_code": pharmacy,
           "bill_time": bill_time, "updated_at": updated,
           "finalized": finalized, "is_void": False}
    row.update(extra)
    return row


def _code_row(bill_id="B0001", pharmacy="YF01", kind="PATIENT_DISPENSE",
              record_id="R1", line_id="L1", code="81000000000123",
              revision="1", updated="2026-10-12T08:00:00", deleted=False,
              quantity="1", unit="盒", **extra):
    row = {"source_system": "HIS", "source_bill_id": bill_id,
           "pharmacy_code": pharmacy, "business_kind": kind,
           "source_revision": revision, "source_line_id": line_id,
           "source_code_record_id": record_id, "trace_code": code,
           "updated_at": updated, "is_deleted": deleted,
           "quantity": quantity, "unit": unit}
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


def _seed_dicts(env, extra=()):
    """FIX3-01 字典集：ref_ent_id 与 ent_id 值刻意完全不同（防同值掩盖串用）。"""
    with env["sf"]() as db:
        have = {r.namespace for r in db.query(DictionaryRevision)
                .filter_by(tenant_id=env["tenant_id"]).all()}
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"},
                                         {"code": "101", "target": "101"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "HOSP-REF"},
                                          {"code": "CK01", "target": "HOSP-REF2"}]),
                ("drugtrace.query_bill_type",
                 [{"code": "323", "target": "A"}, {"code": "101", "target": "AI"}]),
                ("drugtrace.ent_id", [{"code": "YF01", "target": "HOSP-ENT"},
                                      {"code": "CK01", "target": "HOSP-ENT2"}]),
                ("drugtrace.supplier_ent_id",
                 [{"code": "S1", "target": "SUP-ENT"}]),
                ("drugtrace.physic_type", [{"code": "101", "target": "3"}]),
                ("drugtrace.client_type",
                 [{"code": "SUPPLIER_IN", "target": "2"},
                  {"code": "SUPPLIER_RETURN", "target": "2"}]),
                *extra):
            if ns in have:            # 幂等：同命名空间不重复播种
                continue
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], namespace=ns,
                version="1", items=items, source="target", approved_by="admin"))
        db.commit()


def _dest(env, profile="alihealth_yljg_retail_v1", mock=True):
    with env["sf"]() as db:
        d = Destination(
            id=new_uuid(), tenant_id=env["tenant_id"], code="fix3-dest",
            profile_revision=profile, mode="PUSH",
            capabilities=["upload", "query"], is_mock=mock, status="ENABLED")
        db.add(d)
        db.commit()
        return d.id


def _collect_one(env, bill_id, kind="PATIENT_DISPENSE", pharmacy="YF01",
                 raw="323", supplier=None):
    """采集一单（合成视图）并返回 (connector_id, revision_id)。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import (DrugTraceBill,
                                           DrugTraceBillRevision)
    conn = _connector_id(env)
    extra = {"supplier_code": supplier} if supplier else {}
    wh = _wh_with(
        [_bill_row(bill_id, pharmacy=pharmacy, kind=kind,
                   source_bill_kind_raw=raw, **extra)],
        [_code_row(bill_id, pharmacy=pharmacy, kind=kind, record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        bill = (db.query(DrugTraceBill)
                .filter_by(tenant_id=env["tenant_id"],
                           source_bill_id=bill_id).one())
        rev = (db.query(DrugTraceBillRevision)
               .filter_by(bill_id=bill.id)
               .order_by(DrugTraceBillRevision.created_at.desc()).first())
        return conn, rev.id


QUERY_KEY = "alibaba_alihealth_drugtrace_top_yljg_query_billstatus_response"


def _frozen(env, bill_id="FD", kind="PATIENT_DISPENSE", pharmacy="YF01",
            raw="323", supplier=None, dest_profile="alihealth_yljg_retail_v1",
            config_release_id=None):
    """采集 → 字典 → 目的地 → PREPARED 提交；返回 submission 相关句柄。"""
    from domains.drug_trace import service
    conn, rev_id = _collect_one(env, bill_id, kind=kind, pharmacy=pharmacy,
                                raw=raw, supplier=supplier)
    _seed_dicts(env)
    dest_id = _dest(env, profile=dest_profile)
    with env["sf"]() as db:
        result = service.prepare_submission(
            db, env["tenant_id"], rev_id, profile=dest_profile,
            destination_id=dest_id, config_release_id=config_release_id)
        payload = db.get(PackageRevision,
                         result["package_revision_id"]).payload
        return {"conn": conn, "revision": rev_id, "dest": dest_id,
                "submission": result, "payload": payload,
                "bill_code": payload["bill_code"]}


class FakeQueryClient:
    """平台查询替身：按页返回官方嵌套形状（经真实 parse_query_response）。"""
    def __init__(self, pages, bill_code="FD", total=None, error_from=None,
                 error=None, delay=0.0):
        self.pages = pages            # list[list[entry]]（或 None=错误页）
        self.calls: list[dict] = []
        self.bill_code = bill_code
        self.total = total
        self.error_from = error_from  # 从该页起返回平台错误
        self.error = error or {"code": "SYN-AUTH",
                               "msg": "synthetic permission denied"}
        self.delay = delay

    def query_bill_status(self, biz):
        if self.delay:
            time.sleep(self.delay)
        self.calls.append(dict(biz))
        page_no = int(biz.get("page", "1"))
        if self.error_from is not None and page_no >= self.error_from:
            return {"found": None, "unknown": False,
                    "bills": {"list": []}, "total_num": None,
                    "error": dict(self.error)}
        entries = self.pages[page_no - 1] if page_no <= len(self.pages) else []
        total = self.total if self.total is not None else \
            sum(len(p) for p in self.pages)
        body = json.dumps({QUERY_KEY: {"result": {
            "response_success": True, "model": {"total_num": total,
            "result_list": {"bill_deal_status_search_do": entries}}}}}).encode()
        from destinations.alihealth_drugtrace.protocol import parse_query_response
        return parse_query_response(200, body)


def _entry(bill_code, rt="3", spf="51"):
    return {"bill_code": bill_code, "result_type": rt, "sub_process_flag": spf}


# ================================================================ G3-01 双ID
def test_g3_01_dual_entity_ids_both_directions(env):
    """R3-01/G3-01：ref_ent_id 与 ent_id 分离；两方向 from/to 用 ent_id。

    字典值刻意完全不同（HOSP-REF vs HOSP-ENT），防同值掩盖串用。"""
    from domains.drug_trace import contracts as C
    # 采购入库：to=医院 ent_id；退供应商：from=医院 ent_id
    for kind, expect_from, expect_to in (
            ("SUPPLIER_IN", "SUP-ENT", "HOSP-ENT2"),
            ("SUPPLIER_RETURN", "HOSP-ENT2", "SUP-ENT")):
        f = _frozen(env, bill_id=f"INV-{kind}", kind=kind, pharmacy="CK01",
                    raw="101", supplier="S1",
                    dest_profile=C.PROFILE_INOUT)
        assert f["payload"]["ref_user_id"] == "HOSP-REF2", \
            "上传 ref_user_id 必须来自 ref_ent_id 字典（不是 ent_id）"
        assert f["payload"]["ref_ent_id"] == "HOSP-REF2"
        assert f["payload"]["from_user_id"] == expect_from
        assert f["payload"]["to_user_id"] == expect_to
    # 零售：上传 ref_user_id 同样 = 医院 ref_ent_id（不再读独立 ref_user_id 字典）
    r = _frozen(env, bill_id="RET-1")
    assert r["payload"]["ref_user_id"] == "HOSP-REF"
    assert r["payload"]["ref_ent_id"] == "HOSP-REF"


# ================================================================ G3-02 查询契约
def test_g3_02_query_selector_and_dates_independent(env):
    """R3-01/G3-02：query.bill_type 用独立选择器（A/AI/AO），不复用上传 323；
    日期按冻结 bill_time（GMT+8）±1 天，时区口径显式。"""
    from domains.drug_trace import service as S
    f = _frozen(env, bill_id="QD-1", raw="323")
    assert f["payload"]["query_bill_type"] == "A"
    assert f["payload"]["bill_type"] == "323"
    with env["sf"]() as db:
        sub = db.get(Submission, f["submission"]["submission_id"])
        client = FakeQueryClient([[_entry(f["bill_code"])]])
        S.perform_query(db, env["tenant_id"], sub, client=client)
        req = client.calls[0]
        assert req["bill_type"] == "A"          # 独立选择器，绝不传 323
        assert req["ref_ent_id"] == "HOSP-REF"
        # bill_time=2026-10-12T07:55:00（GMT+8 墙钟）→ 创建日窗口 ±1 天
        assert req["begin_date"] == "2026-10-11"
        assert req["end_date"] == "2026-10-13"
        assert sub.state == SubmissionState.CONFIRMED


def test_g3_02b_query_selector_outside_documented_set_rejected(env):
    """选择器字典映射到文档外值（非 A/AI/AO）→ 拒绝冻结（不猜平台支持其他值）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBillRevision
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("BADSEL", source_bill_kind_raw="323")],
                  [_code_row("BADSEL", record_id="R1")])
    with env["sf"]() as db:
        service.run_collection(db, env["tenant_id"], conn, wh,
                               window_begin=datetime(2026, 10, 12),
                               window_end=datetime(2026, 10, 13))
        rev = db.query(DrugTraceBillRevision).first()
        for ns, items in (
                ("drugtrace.bill_type", [{"code": "323", "target": "323"}]),
                ("drugtrace.ref_ent_id", [{"code": "YF01", "target": "HOSP-REF"}]),
                # 选择器映射到文档外值（323 不是 A/AI/AO）→ 冻结必须拒绝
                ("drugtrace.query_bill_type", [{"code": "323", "target": "323"}])):
            db.add(DictionaryRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], namespace=ns,
                version="1", items=items, source="target", approved_by="admin"))
        db.commit()
        dest_id = _dest(env)
        with pytest.raises(ApiError) as ei:
            service.prepare_submission(
                db, env["tenant_id"], rev.id,
                profile="alihealth_yljg_retail_v1", destination_id=dest_id)
        assert ei.value.code == "DICTIONARY_UNMAPPED"
        assert "selector" in ei.value.message


# ================================================================ G3-03 keyset
def test_g3_03_fieldwise_keyset_no_skip():
    """R3-02/G3-03：逐字段 keyset——YF1/YF10、含分隔符键值下元组序与拼接序
    相反的行不再被跳过（旧拼接谓词会整页丢失）。"""
    from sources.db_view.drug_trace import render_sql, SyntheticDrugTraceWarehouse
    sql = render_sql("drugtrace.poll_bills", "oracle", limit=10)
    assert "source_system > :since_ss" in sql
    assert "source_bill_id > :since_sb" in sql
    assert "|| '|'" not in sql
    wh = SyntheticDrugTraceWarehouse()
    same_ts = "2026-10-12T08:00:00"
    for bid in ("1",):   # 游标锚点：YF1
        wh.add_bill(_bill_row(bid, updated=same_ts))
    wh.add_bill(_bill_row("10", updated=same_ts))          # YF10：元组序在 YF1 后
    wh.add_bill(_bill_row("1", pharmacy="YF1|X", updated=same_ts))  # 含分隔符
    rows = wh.fetch_all("drugtrace.poll_bills", {
        "begin_time": same_ts, "end_time": "2026-10-13T00:00:00",
        "since_time": same_ts,
        "since_ss": "HIS", "since_pc": "YF01",
        "since_bk": "PATIENT_DISPENSE", "since_sb": "1"}, 10)
    ids = sorted(r["source_bill_id"] for r in rows)
    # 逐字段序：('HIS','YF01',K,'10') 与 ('HIS','YF1|X',K,'1') 都 > 锚点
    assert ids == ["1", "10"]


def test_g3_03b_same_timestamp_multipage_keyset(env):
    """同刻多页 + 不同键长混合：5 单（含 YF1/YF10 药房）全量读入不漏。"""
    from domains.drug_trace import service
    conn = _connector_id(env)
    rows, codes = [], []
    for i, (pharm, bid) in enumerate([("YF1", "A1"), ("YF10", "A2"),
                                      ("YF1", "A3"), ("YF10", "A4"),
                                      ("YF1|X", "A5")]):
        rows.append(_bill_row(bid, pharmacy=pharm,
                              updated="2026-10-12T08:00:00"))
        codes.append(_code_row(bid, pharmacy=pharm, record_id="R1"))
    wh = _wh_with(rows, codes)
    with env["sf"]() as db:
        out = service.run_collection(db, env["tenant_id"], conn, wh,
                                     window_begin=datetime(2026, 10, 12),
                                     window_end=datetime(2026, 10, 13),
                                     page_limit=2)
        assert out["bills_seen"] == 5 and out["revisions_created"] == 5


# ================================================================ G3-04 独立会话暂停
def test_g3_04_pause_from_independent_session_stops(env):
    """R3-05/G3-04：另一会话置 PAUSED → 安全点（独立只读会话）实际停止；
    不再被 expire_on_commit=False 缓存掩盖（旧反例读了 2 单还置 DONE）。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceCollectionRun
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("P1"), _bill_row("P2")],
                  [_code_row("P1", record_id="R1"),
                   _code_row("P2", record_id="R2")])
    with env["sf"]() as db:
        run = DrugTraceCollectionRun(
            id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn,
            stream="drugtrace.bills", state="PENDING",
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13))
        db.add(run)
        db.commit()
        rid = run.id
        fetch = wh.fetch_all
        paused = []

        def interrupt(template, params, limit, **kw):
            result = fetch(template, params, limit, **kw)
            if template == "drugtrace.poll_bills" and not paused:
                with env["sf"]() as other:      # 独立会话暂停（另一事务）
                    other.get(DrugTraceCollectionRun, rid).state = "PAUSED"
                    other.commit()
                paused.append(True)
            return result
        wh.fetch_all = interrupt
        summary = service.run_collection(
            db, env["tenant_id"], conn, wh, run_id=rid,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), page_limit=1)
        assert summary["bills_seen"] == 1        # 只处理第一页即停
        db.refresh(run)
        assert run.state == "PAUSED"             # 不再被置 DONE


# ================================================================ G3-05 页级原子
def test_g3_05_second_page_failure_atomic_and_resumable(env):
    """R3-05/G3-05：第二页异常 → 已保存快照与 checkpoint 原子一致（同事务），
    run FAILED；重启续跑从 checkpoint 起不漏不重。"""
    from domains.drug_trace import service
    from domains.drug_trace.models import DrugTraceBill, DrugTraceCollectionRun
    from core_db.models import SourceCheckpoint
    conn = _connector_id(env)
    wh = _wh_with([_bill_row("A1")], [_code_row("A1", record_id="R1")])
    fetch2 = wh.fetch_all
    seen = []

    def fail_after_page(template, params, limit, **kw):
        if template == "drugtrace.poll_bills":
            seen.append(True)
            if len(seen) > 1:
                raise RuntimeError("synthetic second page failure")
        return fetch2(template, params, limit, **kw)
    wh.fetch_all = fail_after_page
    with env["sf"]() as db:
        with pytest.raises(RuntimeError):
            service.run_collection(db, env["tenant_id"], conn, wh,
                                   window_begin=datetime(2026, 10, 12),
                                   window_end=datetime(2026, 10, 13),
                                   page_limit=1)
        run = db.query(DrugTraceCollectionRun).filter_by(connector_id=conn).one()
        assert run.state == "FAILED"
    with env["sf"]() as db:
        # 原子一致：第一页快照与推进到本页尾的 checkpoint 同事务提交
        assert db.query(DrugTraceBill).filter_by(connector_id=conn).count() == 1
        cp = db.query(SourceCheckpoint).filter_by(connector_id=conn).one()
        assert cp.cursor_json["completed_window"] is False
        assert cp.cursor_json["since_key"] == ["HIS", "YF01",
                                               "PATIENT_DISPENSE", "A1"]
    # 重启续跑：无漏（A1 不重复建版本；窗口顺利完成）
    wh2 = _wh_with([_bill_row("A1")], [_code_row("A1", record_id="R1")])
    with env["sf"]() as db:
        out = service.run_collection(db, env["tenant_id"], conn, wh2,
                                     window_begin=datetime(2026, 10, 12),
                                     window_end=datetime(2026, 10, 13),
                                     page_limit=1)
        assert out["revisions_created"] == 0 and out["idempotent"] >= 1
        assert out.get("stopped") is None


# ================================================================ G3-06 连接绑定
def test_g3_06_connector_binding_and_partitions(env):
    """R3-05/G3-06：worker 按批准连接器配置重建绑定（不丢 namespace/DSN/
    对象白名单）；历史补报与增量 checkpoint 分区独立。"""
    from domains.drug_trace import plugin, service
    from core_db.models import ConnectorInstance, SourceCheckpoint
    captured = {}
    orig = service.make_warehouse
    service.make_warehouse = lambda cfg: captured.update(cfg=cfg) or _wh_with([], [])
    try:
        with env["sf"]() as db:
            conn = ConnectorInstance(
                id=new_uuid(), tenant_id=env["tenant_id"], direction="source",
                plugin_id="sources.db_view.drug_trace", name="c", status="ENABLED")
            db.add(conn)
            db.flush()
            cfg = ConfigRevision(
                id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
                owner_id=conn.id, revision=1,
                content_json={"mode": "synthetic", "namespace": "NSX"},
                digest="d", state="PUBLISHED")
            db.add(cfg)
            db.flush()
            conn.active_config_revision = cfg.id
            from domains.drug_trace.models import DrugTraceCollectionRun
            run = DrugTraceCollectionRun(
                id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn.id,
                stream="drugtrace.bills", state="PENDING",
                window_begin=datetime(2026, 10, 12),
                window_end=datetime(2026, 10, 13))
            db.add(run)
            db.commit()
            from workflow_core.jobs import enqueue_job
            from domains.drug_trace import contracts as C
            job = enqueue_job(db, env["tenant_id"], C.DomainJobType.COLLECT,
                              f"collect:{run.id}",
                              {"collection_run_id": run.id,
                               "connector_id": conn.id,
                               "connector_config": {"mode": "synthetic"},
                               "window_begin": "2026-10-12T00:00:00",
                               "window_end": "2026-10-13T00:00:00",
                               "checkpoint_partition": "backfill"})
            db.commit()
            plugin._j_collect(db, job)
            assert captured["cfg"].get("namespace") == "NSX", \
                "必须使用连接器批准配置的 namespace（API 入队 mode 提示被覆盖）"
            # 分区独立：backfill 分区 checkpoint 已建，增量分区（0）尚未存在
            parts = {r.partition for r in db.query(SourceCheckpoint)
                     .filter_by(connector_id=conn.id).all()}
            assert parts == {"backfill"}
            # owner 绑定不符 → 跳过（不跨连接器/租户复用配置）
            conn.active_config_revision = new_uuid()   # 指向他 owner 的配置 id
            run3 = DrugTraceCollectionRun(
                id=new_uuid(), tenant_id=env["tenant_id"], connector_id=conn.id,
                stream="drugtrace.bills", state="PENDING",
                window_begin=datetime(2026, 10, 12),
                window_end=datetime(2026, 10, 13))
            db.add(run3)
            db.commit()
            job2 = enqueue_job(db, env["tenant_id"], C.DomainJobType.COLLECT,
                               "collect:second",
                               {"collection_run_id": run3.id,
                                "connector_id": conn.id,
                                "connector_config": {"mode": "synthetic"},
                                "window_begin": "2026-10-12T00:00:00",
                                "window_end": "2026-10-13T00:00:00"})
            db.commit()
            out2 = plugin._j_collect(db, job2)
            assert "skipped" in out2 and "binding" in out2["skipped"]
    finally:
        service.make_warehouse = orig


def test_g3_06b_warehouse_object_whitelist():
    """Oracle 对象白名单：仅接受 [SCHEMA.]OBJECT 标识符；注入片段被拒。"""
    from sources.db_view.drug_trace import OracleDrugTraceWarehouse
    captured = {}

    class FakeCursor:
        description = []

        def execute(self, sql, params):
            captured["sql"] = sql

        def fetchall(self):
            return []

        def close(self):
            pass

    class FakeConn:
        def cursor(self):
            return FakeCursor()
        def close(self):
            pass

    wh = OracleDrugTraceWarehouse(
        connection_factory=lambda: FakeConn(),
        objects={"bill_view": "HIS.V_DRUG_TRACE_BILL"})
    wh.fetch_all("drugtrace.bill_header",
                 {"source_system": "HIS", "pharmacy_code": "YF01",
                  "business_kind": "K", "source_bill_id": "1"}, 2)
    assert "FROM HIS.V_DRUG_TRACE_BILL" in captured["sql"]
    with pytest.raises(CapabilityError):
        OracleDrugTraceWarehouse(
            connection_factory=lambda: FakeConn(),
            objects={"bill_view": "X; DROP TABLE bill"})


# ================================================================ G3-07 受理自动查询
def test_g3_07_accepted_auto_enqueues_query_to_terminal(env):
    """R3-03/G3-07：ACCEPTED 自动排第一代查询；查询 job 推进到 CONFIRMED
    ——全程无需人工点击 UI。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    f = _frozen(env, bill_id="AU-1")
    with env["sf"]() as db:
        sub = db.get(Submission, f["submission"]["submission_id"])
        before = db.query(Job).filter(
            Job.job_type == C.DomainJobType.QUERY,
            Job.dedup_key.like(f"q:{sub.id}:%")).count()
        result = S.perform_send(db, env["tenant_id"], sub)
        assert result["outcome"] == "ACCEPTED"
        after = db.query(Job).filter(
            Job.job_type == C.DomainJobType.QUERY,
            Job.dedup_key.like(f"q:{sub.id}:%")).count()
        assert after == before + 1, "受理必须自动排第一代查询（不靠 UI）"
        # worker 查询（默认 Mock 通道，官方响应形状）→ 终态 CONFIRMED
        S.perform_query(db, env["tenant_id"], sub)
        assert sub.state == SubmissionState.CONFIRMED


# ================================================================ G3-08 错误≠无记录
def test_g3_08_permission_error_is_query_error_not_notfound(env):
    """R3-03/G3-08：权限错误 → QUERY_ERROR（保留 error 原文），绝不改判
    NOT_FOUND；提交状态不被降级。"""
    from domains.drug_trace import service as S
    f = _frozen(env, bill_id="PE-1")
    with env["sf"]() as db:
        sub = db.get(Submission, f["submission"]["submission_id"])
        sub.state = SubmissionState.ACCEPTED
        db.commit()
        client = FakeQueryClient([], error_from=1)
        out = S.perform_query(db, env["tenant_id"], sub, client=client)
        assert out["classification"]["state"] == "QUERY_ERROR"
        assert out["classification"]["error"]["code"] == "SYN-AUTH"
        db.refresh(sub)
        assert sub.state == SubmissionState.ACCEPTED   # 不降级、不 NOT_FOUND


def test_g3_08b_late_page_error_never_confirmed(env):
    """R3-03/G3-08：第 1 页已见目标成功条目、第 2 页权限错误 → 不能用半份
    数据判成功（QUERY_ERROR，状态不推进 CONFIRMED）。"""
    from domains.drug_trace import service as S
    f = _frozen(env, bill_id="LP-1")
    with env["sf"]() as db:
        sub = db.get(Submission, f["submission"]["submission_id"])
        sub.state = SubmissionState.ACCEPTED
        db.commit()
        client = FakeQueryClient(
            [[_entry(f["bill_code"])], []], total=2, error_from=2)
        out = S.perform_query(db, env["tenant_id"], sub, client=client)
        assert out["classification"]["state"] == "QUERY_ERROR"
        db.refresh(sub)
        assert sub.state == SubmissionState.ACCEPTED


def test_g3_08c_complete_empty_query_is_not_found_with_backoff(env):
    """完整成功查询确实无此单 → NOT_FOUND；传播延迟受限退避排下一代，
    达到上限显式告警转人工（不静默结束）。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    from core_db.models import ReceiptEvent
    f = _frozen(env, bill_id="NF-1")
    with env["sf"]() as db:
        sub = db.get(Submission, f["submission"]["submission_id"])
        sub.state = SubmissionState.ACCEPTED
        db.commit()
        client = FakeQueryClient([[_entry("OTHER-BILL")]])
        out = S.perform_query(db, env["tenant_id"], sub, client=client)
        assert out["classification"]["state"] == "NOT_FOUND"
        db.refresh(sub)
        assert sub.state == SubmissionState.ACCEPTED    # NOT_FOUND 不降级
        # NOT_FOUND 有退避重查（受限）
        qjobs = db.query(Job).filter(
            Job.job_type == C.DomainJobType.QUERY,
            Job.dedup_key.like(f"q:{sub.id}:%")).count()
        assert qjobs >= 1
        # 造满轮询上限：手工补足查询代次再触发 → 告警回执（manual review）
        for gen in range(qjobs + 1, S.QUERY_MAX_POLLS + 1):
            db.add(Job(id=new_uuid(), tenant_id=env["tenant_id"],
                       job_type=C.DomainJobType.QUERY,
                       dedup_key=f"q:{sub.id}:{gen}", payload={}))
        db.commit()
        out2 = S.perform_query(db, env["tenant_id"], sub, client=client)
        assert out2["classification"]["state"] == "NOT_FOUND"
        alerts = db.query(ReceiptEvent).filter(
            ReceiptEvent.submission_ref == sub.id,
            ReceiptEvent.external_receipt_id.like(f"alert:{sub.id}:%")).all()
        assert alerts, "轮询上限必须显式告警（G3-08/09）"


# ================================================================ G3-09 满页推进
def test_g3_09_full_page_without_total_advances():
    """R3-03/G3-09：无 total_num 的满页必须 page+1（旧反例永远请求 page=1）；
    目标单在第 2 页才判 CONFIRMED。"""
    from domains.drug_trace import service as S
    entries1 = [_entry(f"FILL-{i}", rt="1") for i in range(S.QUERY_PAGE_SIZE)]
    client = FakeQueryClient([entries1, [_entry("TARGET")]], total=None)
    # FakeQueryClient 的 total=None → 响应不带 total_num
    merged = S._platform_query_full(client, {"bill_code": "TARGET"})
    assert merged["status"] == "OK"
    assert merged["pages_read"] == 2
    assert [c["page"] for c in client.calls] == ["1", "2"]
    cls = S._classify_query(merged, "TARGET")
    assert cls["state"] == "CONFIRMED"


def test_g3_09b_duplicate_page_and_budget_bounded():
    """R3-03/G3-09：平台忽略 page 参数（重复满页）→ 判不推进（UNKNOWN），
    不无限循环；总时间预算耗尽同样有界结束。"""
    from domains.drug_trace import service as S
    same = [_entry(f"D-{i}", rt="1") for i in range(S.QUERY_PAGE_SIZE)]

    class DupClient:
        def __init__(self):
            self.calls = []

        def query_bill_status(self, biz):
            self.calls.append(dict(biz))
            return {"found": True, "unknown": False, "total_num": None,
                    "bills": {"list": list(same)}}
    dup = DupClient()
    out = S._platform_query_full(dup, {"bill_code": "X"})
    assert out["status"] == "UNKNOWN"
    assert "not advancing" in out["reason"]
    assert len(dup.calls) == 2               # 有界：第 2 页即判重复

    class SlowClient:
        def query_bill_status(self, biz):
            time.sleep(0.08)
            return {"found": True, "unknown": False, "total_num": None,
                    "bills": {"list": list(same)}}
    out2 = S._platform_query_full(SlowClient(), {"bill_code": "X"},
                                  budget_seconds=0.05)
    assert out2["status"] == "UNKNOWN" and "budget" in out2["reason"]


# ================================================================ G3-10 批准scope
def test_g3_10_scope_strict_pharmacy_quota_window_tz(env):
    """R3-04/G3-10/G3-11：排除药房/零配额/空scope/时区边界全部拒绝；
    naive 窗口按 GMT+8 解释、带偏移窗口折算 UTC 比较（不丢偏移）。"""
    from domains.drug_trace import service as S
    f = _frozen(env, bill_id="SC-1")
    payload = f["payload"]
    # 旧反例：排除该药房 + 配额 0 → 仍放行；现在必须拒绝
    with pytest.raises(ApiError) as ei:
        S._check_send_scope({"pharmacies": ["OTHER-PHARMACY"],
                             "max_bills": 0,
                             "window_begin": "2026-10-01T00:00:00",
                             "window_end": "2026-11-01T00:00:00"}, payload)
    assert ei.value.code in ("VALIDATION_FAILED", "PROFILE_UNAPPROVED")
    # 空 scope / 空药房列表 / 缺配额：都不是全开放
    for bad in (None, {}, {"window_begin": "2026-10-01T00:00:00",
                           "window_end": "2026-11-01T00:00:00",
                           "pharmacies": [], "max_bills": 5},
                {"window_begin": "2026-10-01T00:00:00",
                 "window_end": "2026-11-01T00:00:00",
                 "pharmacies": ["YF01"]}):
        with pytest.raises(ApiError):
            S._check_send_scope(bad, payload)
    # 药房白名单精确匹配
    good = {"window_begin": "2026-10-01T00:00:00",
            "window_end": "2026-11-01T00:00:00",
            "pharmacies": ["YF01"], "max_bills": 5}
    S._check_send_scope(good, payload)
    # 半开窗口：bill_time == window_end → 拒绝（[begin, end)）
    with pytest.raises(ApiError):
        S._check_send_scope({**good, "window_end": "2026-10-12T07:55:00"},
                            payload)
    # 时区：带偏移窗口与 naive bill_time（GMT+8）折算 UTC 后相等比较
    tz_scope = {"window_begin": "2026-10-11T16:00:00-04:00",   # =10-12 00:00 +08
                "window_end": "2026-10-13T07:55:00+08:00",
                "pharmacies": ["YF01"], "max_bills": 5}
    S._check_send_scope(tz_scope, payload)


def test_g3_10b_blank_config_evidence_not_wildcard(env):
    """R3-04/G3-13 前置：空 config_release_id 的 send 证据对新配置不再通配。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    f = _frozen(env, bill_id="BC-1")
    keys = [i["key"] for i in S.REQUIRED_EVIDENCE["send"]]
    with env["sf"]() as db:
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=f["dest"], provided_items=[{"key": k} for k in keys],
            approved_by="t", config_release_id="",
            scope={"window_begin": "2026-10-01T00:00:00",
                   "window_end": "2026-11-01T00:00:00",
                   "pharmacies": ["YF01"], "max_bills": 5})
        db.commit()
        st = S.evidence_state(db, env["tenant_id"], profile=C.PROFILE_RETAIL,
                              capability="send", destination_id=f["dest"],
                              config_release_id="NEW-CONFIG")
        assert st["status"] == "MISMATCH_CONFIG", "空配置不得当通配"


# ================================================================ G3-11 配额并发预留
def test_g3_11_quota_atomic_reservation_and_exhaustion(env):
    """G3-11：配额台账事务并发预留（条件原子 UPDATE）；耗尽即拒绝；
    EXT-07 签名阻断的失败尝试也保守占额度（防并发窗口超发）。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    from domains.drug_trace.models import DrugTraceSendQuota
    f = _frozen(env, bill_id="QT-1")
    keys = [i["key"] for i in S.REQUIRED_EVIDENCE["send"]]
    with env["sf"]() as db:
        dest = db.get(Destination, f["dest"])
        dest.is_mock = False
        cfg = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=1,
            content_json={"dmz_base_url": "https://dmz.invalid",
                          "app_key": "k", "app_secret_ref": "env:QT_SECRET",
                          "dmz_allow_hosts": ["dmz.invalid"]},
            digest="d", state="PUBLISHED")
        db.add(cfg)
        db.flush()
        dest.config_revision = cfg.id
        db.commit()
        # 重新 prepare 绑定 cfg（换代）
        f2 = S.prepare_submission(db, env["tenant_id"], f["revision"],
                                  profile=C.PROFILE_RETAIL,
                                  destination_id=dest.id,
                                  config_release_id=cfg.id)
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL, capability="send",
            destination_id=dest.id, provided_items=[{"key": k} for k in keys],
            approved_by="t", config_release_id=cfg.id,
            scope={"window_begin": "2026-10-01T00:00:00",
                   "window_end": "2026-11-01T00:00:00",
                   "pharmacies": ["YF01"], "max_bills": 1})
        db.commit()
        quota = db.query(DrugTraceSendQuota).one()
        assert quota.max_bills == 1 and quota.used == 0
        # FB-02 签名资格已按批准修订关闭（EXT-07）。本地确定性错误的触发点
        # 改用永久安全属性：mock 标记密钥上真实 transport 被拒（T06 语义）。
        os.environ["QT_SECRET"] = "__mock__offline"
        try:
            sub = db.get(Submission, f2["submission_id"])
            # 第一次：配额预留成功 → mock 密钥上真实传输在签名前阻断（本地错误）
            with pytest.raises(CapabilityError):
                S.perform_send(db, env["tenant_id"], sub)
            db.refresh(quota)
            assert quota.used == 1, "失败尝试也占额度（保守计数）"
            db.refresh(sub)
            assert sub.state == SubmissionState.PREPARED   # 本地阻断可恢复
            # 第二次：额度耗尽 → 拒绝（在配额处，不再走到签名）
            with pytest.raises(ApiError) as ei:
                S.perform_send(db, env["tenant_id"], sub)
            assert ei.value.code == "PROFILE_UNAPPROVED"
            assert "quota exhausted" in ei.value.message
        finally:
            os.environ.pop("QT_SECRET", None)


# ================================================================ G3-12 配置隔离
def test_g3_12_config_tenant_owner_state_isolation(env):
    """R3-06/G3-12：跨租户/他 owner/DRAFT 配置一律拒绝加载。"""
    from domains.drug_trace import service as S
    f = _frozen(env, bill_id="CI-1")
    with env["sf"]() as db:
        dest = db.get(Destination, f["dest"])
        other = Tenant(id=new_uuid(), code="other-tenant", name="o")
        db.add(other)
        db.flush()
        foreign = ConfigRevision(
            id=new_uuid(), tenant_id=other.id, config_type="connector",
            owner_id="foreign-owner", revision=1,
            content_json={"app_key": "FOREIGN"}, digest="d", state="DRAFT")
        db.add(foreign)
        draft_own = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=9,
            content_json={"app_key": "OWN-DRAFT"}, digest="d2", state="DRAFT")
        db.add(draft_own)
        wrong_owner = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id="someone-else", revision=9,
            content_json={"app_key": "X"}, digest="d3", state="PUBLISHED")
        db.add(wrong_owner)
        db.commit()
        with pytest.raises(ApiError):     # 跨租户（按不存在拒绝，不泄露存在性）
            S.build_client(db, env["tenant_id"], dest,
                           config_release_id=foreign.id)
        with pytest.raises(ApiError) as e2:   # 本租户 DRAFT
            S.build_client(db, env["tenant_id"], dest,
                           config_release_id=draft_own.id)
        assert e2.value.code == "VERSION_CONFLICT"
        with pytest.raises(ApiError) as e3:   # owner 不绑定该 destination
            S.build_client(db, env["tenant_id"], dest,
                           config_release_id=wrong_owner.id)
        assert e3.value.code == "VERSION_CONFLICT"


# ================================================================ G3-13 字典闭包
def test_g3_13_dict_value_and_revision_same_record(env):
    """R3-06/G3-13：值与 revision 来自同一条实际命中记录——旧批准版供值、
    新草稿不再被误记为审计指针；payload 保存值/revision/version。"""
    from domains.drug_trace import payload as PL
    with env["sf"]() as db:
        older = DictionaryRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], namespace="syn.audit",
            version="1", items=[{"code": "X", "target": "OLD-APPROVED"}],
            source="target", approved_by="admin",
            created_at=datetime(2026, 1, 1))
        newer = DictionaryRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], namespace="syn.audit",
            version="2", items=[{"code": "X", "target": "NEW-DRAFT"}],
            source="target", approved_by=None,
            created_at=datetime(2026, 2, 1))
        db.add_all([older, newer])
        db.commit()
        revisions = {}
        value = PL.lookup_mapping_rev(db, env["tenant_id"], "syn.audit",
                                      "X", revisions)
        assert value == "OLD-APPROVED"
        rec = revisions["syn.audit"]
        assert rec["revision_id"] == older.id     # 命中记录，不是最新草稿
        assert rec["value"] == "OLD-APPROVED" and rec["version"] == "1"


# ================================================================ G3-14 换代
def test_g3_14_config_generation_reprepare(env):
    """R3-06/G3-14：未发送旧提交可安全换代（新不可变提交固定新配置、旧提交
    SUPERSEDED 不可发）；已发送（ACCEPTED）提交拒绝换配置重发。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    f = _frozen(env, bill_id="GP-1")
    with env["sf"]() as db:
        dest = db.get(Destination, f["dest"])
        cfg1 = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=1, content_json={"app_key": "k1"},
            digest="d1", state="PUBLISHED")
        db.add(cfg1)
        db.flush()
        dest.config_revision = cfg1.id
        db.commit()
        sub1 = S.prepare_submission(db, env["tenant_id"], f["revision"],
                                    profile=C.PROFILE_RETAIL,
                                    destination_id=dest.id,
                                    config_release_id=cfg1.id)
        assert sub1["created"] is True
        cfg2 = ConfigRevision(
            id=new_uuid(), tenant_id=env["tenant_id"], config_type="connector",
            owner_id=dest.id, revision=2, content_json={"app_key": "k2"},
            digest="d2", state="PUBLISHED")
        db.add(cfg2)
        db.flush()
        dest.config_revision = cfg2.id
        db.commit()
        # 换代：旧提交 PREPARED（从未发送）→ 新提交固定 cfg2，旧提交退役
        sub2 = S.prepare_submission(db, env["tenant_id"], f["revision"],
                                    profile=C.PROFILE_RETAIL,
                                    destination_id=dest.id,
                                    config_release_id=cfg2.id)
        assert sub2["created"] is True
        assert sub2["submission_id"] != sub1["submission_id"]
        assert sub2.get("superseded_submission_id") == sub1["submission_id"]
        pinned = (db.get(PackageRevision, sub2["package_revision_id"])
                  .provenance or {}).get("config_release_id")
        assert pinned == cfg2.id
        old = db.get(Submission, sub1["submission_id"])
        assert old.state == C.SUBMISSION_STATE_SUPERSEDED
        with pytest.raises(ApiError) as ei:      # 旧提交不可再发送
            S.perform_send(db, env["tenant_id"], old)
        assert ei.value.code == "VERSION_CONFLICT"
        # 幂等：同代重复 prepare 返回同一提交
        again = S.prepare_submission(db, env["tenant_id"], f["revision"],
                                     profile=C.PROFILE_RETAIL,
                                     destination_id=dest.id,
                                     config_release_id=cfg2.id)
        assert again["created"] is False
        assert again["submission_id"] == sub2["submission_id"]
        # 已发送（ACCEPTED）→ 拒绝换配置重发（走查询/对账）
        new_sub = db.get(Submission, sub2["submission_id"])
        new_sub.state = SubmissionState.ACCEPTED
        db.commit()
        with pytest.raises(ApiError) as ei2:
            S.prepare_submission(db, env["tenant_id"], f["revision"],
                                 profile=C.PROFILE_RETAIL,
                                 destination_id=dest.id,
                                 config_release_id=cfg1.id)
        assert ei2.value.code == "VERSION_CONFLICT"


# ================================================================ G3-15 对账完整
def test_g3_15_incomplete_pagination_unverified_not_matched(env):
    """R3-07/G3-15：total_num=2 只取得 1 项 → UNVERIFIED，不得 matched。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    from core_db.models import ReconciliationRun
    f = _frozen(env, bill_id="RC-1")
    keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
    with env["sf"]() as db:
        dest = db.get(Destination, f["dest"])
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL,
            capability="reconcile", destination_id=dest.id,
            provided_items=[{"key": k} for k in keys], approved_by="t")
        db.commit()
        sub = db.get(Submission, f["submission"]["submission_id"])
        sub.state = SubmissionState.CONFIRMED
        db.commit()

        class TruncatedClient:      # total=2 但每页只给 1 条（且重复同页）
            def __init__(self):
                self.calls = []

            def query_bill_status(self, biz):
                self.calls.append(dict(biz))
                return {"found": True, "unknown": False, "total_num": 2,
                        "bills": {"list": [_entry(f["bill_code"])]}}
        rc = TruncatedClient()
        out = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest.id,
            profile=C.PROFILE_RETAIL,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), client=rc)
        assert out["platform_ok"] is False
        assert out["summary"]["matched"] == 0
        assert out["summary"]["unverified_remote"] >= 1
        run = db.get(ReconciliationRun, out["run_id"])
        assert run.state == "UNVERIFIED"


def test_g3_15b_matched_requires_full_pages_and_confirmed(env):
    """完整分页（第 2 页才见目标单）+ 本地 CONFIRMED → matched；请求带日期
    与独立选择器。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    f = _frozen(env, bill_id="RC-2")
    keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
    with env["sf"]() as db:
        dest = db.get(Destination, f["dest"])
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL,
            capability="reconcile", destination_id=dest.id,
            provided_items=[{"key": k} for k in keys], approved_by="t")
        db.commit()
        sub = db.get(Submission, f["submission"]["submission_id"])
        sub.state = SubmissionState.CONFIRMED
        db.commit()
        client = FakeQueryClient(
            [[_entry("OTHER")], [_entry(f["bill_code"])]])
        out = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest.id,
            profile=C.PROFILE_RETAIL,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), client=client)
        assert out["platform_ok"] is True
        assert out["summary"]["matched"] == 1
        req = client.calls[0]
        assert {"begin_date", "end_date"} <= set(req)      # R3-07：不再缺日期
        assert req["bill_type"] == "A"                     # 独立选择器
        assert req["ref_ent_id"] == "HOSP-REF"             # 冻结身份


# ================================================================ G3-16 范围/身份
def test_g3_16_multi_destination_identity_scope(env):
    """G3-16：对账按 destination 精确圈定提交；平台快照范围与
    tenant/connector/profile/机构/药房/窗口一致（scope 显式记录）；无范围
    枚举能力 → remote_extra_checked=False 显式声明。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    from core_db.models import ReconciliationRun
    fa = _frozen(env, bill_id="MA-1")
    fb = _frozen(env, bill_id="MB-1")     # 同 profile 另一目的地
    keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
    with env["sf"]() as db:
        for d in (fa["dest"], fb["dest"]):
            S.approve_evidence(
                db, env["tenant_id"], profile=C.PROFILE_RETAIL,
                capability="reconcile", destination_id=d,
                provided_items=[{"key": k} for k in keys], approved_by="t")
        db.commit()
        for h in (fa, fb):
            sub = db.get(Submission, h["submission"]["submission_id"])
            sub.state = SubmissionState.CONFIRMED
        db.commit()

        class RecordingClient:
            def __init__(self):
                self.seen = []

            def query_bill_status(self, biz):
                self.seen.append(biz.get("bill_code"))
                return {"found": True, "unknown": False, "total_num": 1,
                        "bills": {"list": [_entry(biz.get("bill_code"))]}}
        rc = RecordingClient()
        out = S.run_reconciliation(
            db, env["tenant_id"], destination_id=fa["dest"],
            profile=C.PROFILE_RETAIL,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13), client=rc)
        # 只查询目的地 A 的单据；B 的单据不进本轮范围。
        # FIN-A22：查询日期语义默认 both——bill_time(2026-10-12) 与提交上传
        # 日期(现在)相差 >1 天 → 两个窗口，同单查两次（防历史单漏查）；
        # 身份范围断言不变（只有 A 的 bill_code、无 B）。
        assert set(rc.seen) == {fa["bill_code"]}
        assert rc.seen.count(fa["bill_code"]) == 2
        assert out["summary"]["matched"] == 1
        run = db.get(ReconciliationRun, out["run_id"])
        assert run.scope["remote_extra_checked"] is False
        assert "未检查平台额外单据" in run.scope["remote_extra_note"]
        assert run.scope["profile"] == C.PROFILE_RETAIL


# ================================================================ G3-17 不自动补报
def test_g3_17_missing_local_never_auto_sent(env):
    """G3-17：无本地提交的源单只登记 MISSING_LOCAL_SUBMISSION（PENDING 待
    人工处置）——绝不自动补发；补报必须基于完整独立证据与新批准。"""
    from domains.drug_trace import service as S
    from domains.drug_trace import contracts as C
    from core_db.models import ReconciliationItem
    # 仅采集（不 prepare）：产生无提交的源单
    conn, rev_id = _collect_one(env, bill_id="NOSUB-1")
    _seed_dicts(env)
    dest_id = _dest(env)
    keys = [i["key"] for i in S.REQUIRED_EVIDENCE["reconcile"]]
    with env["sf"]() as db:
        S.approve_evidence(
            db, env["tenant_id"], profile=C.PROFILE_RETAIL,
            capability="reconcile", destination_id=dest_id,
            provided_items=[{"key": k} for k in keys], approved_by="t")
        db.commit()
        out = S.run_reconciliation(
            db, env["tenant_id"], destination_id=dest_id,
            profile=C.PROFILE_RETAIL,
            window_begin=datetime(2026, 10, 12),
            window_end=datetime(2026, 10, 13),
            client=FakeQueryClient([[]]))
        assert out["summary"]["missing_local"] == 1
        item = db.query(ReconciliationItem).one()
        assert item.detail["kind"] == "MISSING_LOCAL_SUBMISSION"
        assert item.disposition == "PENDING"
        # 无任何自动发送：不建 Submission、不排 send job
        assert db.query(Submission).filter_by(
            destination_id=dest_id).count() == 0
        assert db.query(Job).filter_by(
            job_type=C.DomainJobType.SEND).count() == 0


# ================================================================ G3-19 慢流硬顶
def _openssl() -> str | None:
    import shutil
    return shutil.which("openssl")


@pytest.mark.skipif(_openssl() is None,
                    reason="openssl 不可用（本地 TLS 慢流测试证书生成）")
def test_g3_19_slow_stream_hard_deadline(tmp_path):
    """R3-05/G3-19：持续慢流（每块都在 read_timeout 内到达）也必须被
    total_deadline 硬顶中断——不再等到 resp.read() 完成后才检查。"""
    import http.server
    import ssl
    import subprocess
    from destinations.alihealth_drugtrace.transport import RealTransport

    ossl = _openssl()
    crt, key = tmp_path / "srv.crt", tmp_path / "srv.key"
    subprocess.run([ossl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(crt), "-days", "1",
                    "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1"],
                   capture_output=True, check=True, timeout=60)

    class TrickleHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(64 * 1024))
            self.end_headers()
            for _ in range(64):            # 64 块 × 1KB，每块间隔 0.15s 涓流
                self.wfile.write(b"x" * 1024)
                self.wfile.flush()
                time.sleep(0.15)

        def log_message(self, *a):         # 静默
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), TrickleHandler)
    port = httpd.server_address[1]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(crt), str(key))
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        transport = RealTransport(
            f"https://127.0.0.1:{port}", ca_file=str(crt),
            connect_timeout=5.0, read_timeout=10.0, total_deadline=2.0,
            allow_hosts=("127.0.0.1",))
        started = time.monotonic()
        result = transport.post(b"x=1", "application/x-www-form-urlencoded")
        elapsed = time.monotonic() - started
        assert result.error and result.timeout, result.error
        reason = str(result.error.get("reason", ""))
        assert "deadline" in reason or "timeout" in reason
        assert elapsed < 4.0, f"必须在硬顶附近中断（实际 {elapsed:.1f}s）"
    finally:
        httpd.shutdown()
        httpd.server_close()
