"""LT-08 基准：药品追溯采集管道「每单非查询开销」本地测量（不连任何真实系统）。

用法（implementation/ 目录下）::

    python tests/performance/bench_drug_trace_collect.py --bills 500 \
        [--preload 8000] [--latency-ms 0|5] [--seed 42] \
        [--shape production|single] [--profile] [--out ../var/lt08-run.json] \
        [--prepare-dir ../var/lt08-prepared] [--prepare-only] [--from-prepared]

测量口径：
- 生产形态（默认 shape=production）：与 ``plugin._j_collect`` oracle 模式一致，
  ``page_limit=25, max_pages=1`` 反复切片（JobYield 让出重排），每片独立会话。
- 源侧走 ``OracleDrugTraceWarehouse`` + SQLite 视图连接工厂（复用
  ``tests/integration/test_drug_trace_oracle_first_page.py`` 的 ROWNUM→LIMIT
  适配），真实执行 render_sql 的 SQL；可用 ``--latency-ms`` 注入每次查询的
  模拟网络往返（计入查询耗时，从非查询开销中扣除）。
- 应用侧 SQLite（WAL+FULL sync，与 core_db.engine 默认一致）；SQL 语句数按
  模板聚合（字面量归一为 ?）、commit 次数经 Connection commit 事件计数。
- ``--profile``（或环境变量 BLYB_DT_PROFILE=1）打开 ingest/service 内建分阶
  段计时，结果随 collection summary 汇总输出。

合成数据分布（固定种子可复现）见 ``generate_dataset`` docstring。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _bootstrap  # noqa: E402,F401

# ---------------------------------------------------------------- 合成数据
# 时间窗（固定，与运行日期无关——保证 K1 7 天回看判定确定性）
PRELOAD_WINDOW = (datetime(2026, 1, 1, 0, 0, 0), datetime(2026, 1, 1, 4, 0, 0))
TIMED_WINDOW = (datetime(2026, 1, 5, 0, 0, 0), datetime(2026, 1, 5, 6, 0, 0))
# K1 判定确定性锚点：远早于回看窗（→NOT_REPORTABLE）/ 远晚于（→WAITING）
OLD_BILL_TIME = datetime(2020, 6, 1, 8, 0, 0)
FRESH_BILL_TIME = datetime(2035, 6, 1, 8, 0, 0)

BILL_COLUMNS = ["source_system", "source_bill_id", "source_bill_no",
                "source_revision", "business_kind", "pharmacy_code",
                "warehouse_code", "supplier_code", "bill_time", "updated_at",
                "source_event_seq", "finalized", "is_void",
                "expected_code_count", "patient_ref", "encounter_ref",
                "source_bill_kind_raw"]
CODE_COLUMNS = ["source_system", "source_bill_id", "pharmacy_code",
                "business_kind", "source_revision", "source_line_id",
                "source_code_record_id", "trace_code", "quantity", "unit",
                "code_level", "parent_code", "updated_at", "is_deleted",
                "f_trace_code", "s_trace_code", "t_trace_code", "bill_time"]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def generate_dataset(n_bills: int, *, seed: int = 42,
                     window: tuple[datetime, datetime] = TIMED_WINDOW,
                     id_prefix: str = "BL"):
    """固定种子生成 n_bills 张合成单（无任何真实患者数据），返回 (bills, codes)。

    分布（对齐任务书：每单 1—20 码，含缺码/多行大处方/重复扫码/住院分支等）：
    - ~72% PATIENT_DISPENSE（门诊，两药房 YF01/YF02）：
      * 常规 1—20 有效码（多数 1—6）；
      * 无码单 ~6%：一半 bill_time 锚 OLD（2020）→ QD-NO-CODES-FINAL
        NOT_REPORTABLE 终态；一半锚 FRESH（2035）→ WAITING；
      * 精心构造各 1 张：>200 码大处方（keyset 翻页）/ 同单重复码
        （QD-DUP-CODE）/ 重复 source_code_record_id（QD-DUP-SOURCE-ROW）/
        父子同单（QD-PARENT-CHILD-SAME-BILL）/ 数量非法
        （QD-QUANTITY-INVALID）/ 码数不符（QD-COUNT-MISMATCH）/ 未完成
        （QD-NOT-FINALIZED）/ 含删除码行 / 前导零 record_id；
    - ~13% PATIENT_INOUT（住院出入库）：1—20 码；首张 3600 码大单；
    - ~9% PATIENT_RETURN（门诊退药）：一半有码、一半无码
      （QD-RETURN-ORIGINAL-PENDING 等待）；
    - ~6% UNKNOWN（source_bill_kind_raw 未批准 → QD-KIND-UNKNOWN +
      QD-BRANCH-UNAPPROVED 等待）；最后 1 张 is_void（QD-VOIDED INVALID）。
    """
    rng = random.Random(seed)
    bills: list[dict] = []
    codes: list[dict] = []
    wb, we = window
    span = int((we - wb).total_seconds())
    seq = 0

    def add_bill(kind: str, *, codes_n=0, finalized=True, is_void=False,
                 expected=None, bill_time=None, tag="normal"):
        nonlocal seq
        seq += 1
        bill_id = f"{id_prefix}{seq:06d}"
        pharmacy = rng.choice(["YF01", "YF02"])
        bt = bill_time or (wb + timedelta(seconds=rng.randrange(span)))
        if wb <= bt < we:
            # updated_at 游标必须落在半开窗口内（否则该单会被窗口裁掉）
            up = min(bt + timedelta(seconds=rng.randrange(1, 300)),
                     we - timedelta(seconds=1))
            if up < bt:
                up = bt
        else:
            # bill_time 锚定在窗外（K1 老单/未来单）：updated_at 独立取窗内值
            up = wb + timedelta(seconds=rng.randrange(span))
        bills.append({
            "source_system": "HIS", "source_bill_id": bill_id,
            "source_bill_no": bill_id, "source_revision": "1",
            "business_kind": kind, "pharmacy_code": pharmacy,
            "warehouse_code": "CK01" if kind == "PATIENT_INOUT" else "",
            "supplier_code": "", "bill_time": _iso(bt), "updated_at": _iso(up),
            "source_event_seq": "", "finalized": 1 if finalized else 0,
            "is_void": 1 if is_void else 0,
            "expected_code_count": expected if expected is not None else "",
            "patient_ref": "", "encounter_ref": "",
            "source_bill_kind_raw":
                "PRESC_SOURCE=1;FLAG=2" if kind == "UNKNOWN" else "",
        })
        base_rid = seq
        first = len(codes)
        for i in range(codes_n):
            code = f"{rng.randrange(10**19, 10**20):020d}"
            rid = (f"{base_rid:08d}{i:04d}" if tag == "leading-zero"
                   else f"R{base_rid:06d}{i:04d}")
            codes.append({
                "source_system": "HIS", "source_bill_id": bill_id,
                "pharmacy_code": pharmacy, "business_kind": kind,
                "source_revision": "1",
                "source_line_id": f"L{base_rid:06d}{i:04d}",
                "source_code_record_id": rid, "trace_code": code,
                "quantity": "1", "unit": "盒", "code_level": "",
                "parent_code": code if (tag == "parent-child" and i == 0) else "",
                "updated_at": _iso(up),
                "is_deleted": 1 if (tag == "deleted-mix" and i % 3 == 0) else 0,
                "f_trace_code": "", "s_trace_code": "", "t_trace_code": "",
                "bill_time": _iso(bt),
            })
        own = codes[first:]
        if tag == "dup-code" and len(own) >= 2:
            own[-1]["trace_code"] = own[-2]["trace_code"]
        if tag == "dup-rid" and len(own) >= 2:
            own[-1]["source_code_record_id"] = own[-2]["source_code_record_id"]
        if tag == "bad-qty" and own:
            own[0]["quantity"] = "abc"

    n_disp = int(n_bills * 0.72)
    n_inout = int(n_bills * 0.13)
    n_return = int(n_bills * 0.09)
    for i in range(n_bills):
        if i < n_disp:
            if i == 0:
                add_bill("PATIENT_DISPENSE", codes_n=260, tag="big")
            elif i == 1:
                add_bill("PATIENT_DISPENSE", codes_n=3, tag="dup-code")
            elif i == 2:
                add_bill("PATIENT_DISPENSE", codes_n=3, tag="dup-rid")
            elif i == 3:
                add_bill("PATIENT_DISPENSE", codes_n=4, tag="parent-child")
            elif i == 4:
                add_bill("PATIENT_DISPENSE", codes_n=2, tag="bad-qty")
            elif i == 5:
                add_bill("PATIENT_DISPENSE", codes_n=3, expected=5,
                         tag="count-mismatch")
            elif i == 6:
                add_bill("PATIENT_DISPENSE", codes_n=2, finalized=False,
                         tag="not-finalized")
            elif i == 7:
                add_bill("PATIENT_DISPENSE", codes_n=6, tag="deleted-mix")
            elif i == 8:
                add_bill("PATIENT_DISPENSE", codes_n=3, tag="leading-zero")
            elif i % 17 == 9:
                old = (i // 17) % 2 == 0
                add_bill("PATIENT_DISPENSE", codes_n=0,
                         bill_time=OLD_BILL_TIME if old else FRESH_BILL_TIME)
            else:
                add_bill("PATIENT_DISPENSE", codes_n=rng.choice(
                    [1, 1, 1, 2, 2, 3, 3, 4, 5, 6, 8, 10, 12, 15, 18, 20]))
        elif i < n_disp + n_inout:
            add_bill("PATIENT_INOUT",
                     codes_n=3600 if i == n_disp else rng.choice(
                         [1, 2, 4, 8, 12, 20]))
        elif i < n_disp + n_inout + n_return:
            j = i - n_disp - n_inout
            add_bill("PATIENT_RETURN",
                     codes_n=0 if j % 2 == 0 else rng.choice([1, 2, 3]))
        else:
            add_bill("UNKNOWN", codes_n=rng.choice([0, 2]),
                     is_void=(i == n_bills - 1))
    return bills, codes


# ---------------------------------------------------------------- 源侧视图（SQLite 扮演 Oracle）
class _LatencyCursor:
    """sqlite 游标 + Oracle 语义适配（'' 绑定→NULL；ROWNUM→LIMIT）+ 注入延迟。"""

    def __init__(self, cursor, latency_ms: float):
        self.cursor = cursor
        self.latency = latency_ms

    def execute(self, sql, params):
        if self.latency:
            time.sleep(self.latency / 1000.0)
        sql = re.sub(r"\bWHERE ROWNUM <= (\d+)\s*$", r"LIMIT \1", sql)
        self.cursor.execute(sql, {k: None if v == "" else v
                                  for k, v in params.items()})

    @property
    def description(self):
        return self.cursor.description

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()


class _LatencyConnection:
    def __init__(self, path, latency_ms: float):
        self.conn = sqlite3.connect(path)
        self.latency = latency_ms

    def cursor(self):
        return _LatencyCursor(self.conn.cursor(), self.latency)

    def close(self):
        self.conn.close()


def build_view_db(path, bills, codes):
    """SQLite 视图库：列结构/索引对齐生产 FBI 形态（复合键等值 + updated_at keyset）。"""
    if os.path.exists(path):
        os.remove(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"CREATE TABLE V_DRUG_TRACE_BILL ({','.join(BILL_COLUMNS)})")
        conn.execute(f"CREATE TABLE V_DRUG_TRACE_CODE ({','.join(CODE_COLUMNS)})")
        conn.execute("CREATE INDEX ix_v_bill_key ON V_DRUG_TRACE_BILL"
                     "(source_system, pharmacy_code, business_kind, source_bill_id)")
        conn.execute("CREATE INDEX ix_v_bill_cursor ON V_DRUG_TRACE_BILL"
                     "(updated_at, source_system, pharmacy_code, business_kind,"
                     " source_bill_id)")
        conn.execute("CREATE INDEX ix_v_code_key ON V_DRUG_TRACE_CODE"
                     "(source_system, pharmacy_code, business_kind, source_bill_id,"
                     " source_code_record_id)")
        conn.executemany(
            f"INSERT INTO V_DRUG_TRACE_BILL VALUES ({','.join('?' * len(BILL_COLUMNS))})",
            [[b.get(c, "") for c in BILL_COLUMNS] for b in bills])
        conn.executemany(
            f"INSERT INTO V_DRUG_TRACE_CODE VALUES ({','.join('?' * len(CODE_COLUMNS))})",
            [[c.get(c_, "") for c_ in CODE_COLUMNS] for c in codes])
        conn.commit()


def make_view_warehouse(view_db_path: str, latency_ms: float = 0.0):
    from sources.db_view.drug_trace import OracleDrugTraceWarehouse
    return OracleDrugTraceWarehouse(
        connection_factory=lambda: _LatencyConnection(view_db_path, latency_ms))


# ---------------------------------------------------------------- 应用库准备
TENANT_CODE = "bench-hospital"
CONNECTOR_NAME = "his-drugtrace"


def open_app_db(app_db_path: str, *, migrate: bool):
    """打开（或新建+migrate）应用库；返回 (session_factory, engine, tenant_id, connector_id)。"""
    os.environ["BLYB_DATABASE_URL"] = f"sqlite:///{app_db_path}"
    from core_db.engine import make_engine, make_session_factory
    engine = make_engine()
    tenant_id = connector_id = None
    if migrate:
        import app_runtime
        from core_db import run_migrations
        from contracts import new_uuid
        from core_db.models import ConnectorInstance, Tenant
        reg = app_runtime.load_registry(["drug_trace"])
        run_migrations(engine, extra_migrations=reg.migrations)
        sf = make_session_factory(engine)
        with sf() as db:
            t = Tenant(id=new_uuid(), code=TENANT_CODE, name="基准医院")
            db.add(t)
            db.commit()
            tenant_id = t.id
            c = ConnectorInstance(id=new_uuid(), tenant_id=tenant_id,
                                  direction="source",
                                  plugin_id="sources.db_view.drug_trace",
                                  name=CONNECTOR_NAME, status="ENABLED")
            db.add(c)
            db.commit()
            connector_id = c.id
        return sf, engine, tenant_id, connector_id
    sf = make_session_factory(engine)
    with sf() as db:
        from core_db.models import ConnectorInstance, Tenant
        t = db.query(Tenant).filter_by(code=TENANT_CODE).one()
        c = (db.query(ConnectorInstance)
             .filter_by(tenant_id=t.id, name=CONNECTOR_NAME).one())
        tenant_id, connector_id = t.id, c.id
    return sf, engine, tenant_id, connector_id


def run_collect_loop(sf, tenant_id, connector_id, warehouse, *, window,
                     partition: str, shape: str = "production"):
    """按生产切片形态跑完整个窗口；返回 (final_result, per_slice_seconds, n_slices)。"""
    from domains.drug_trace import service as svc
    wb, we = window
    page_limit, max_pages = (25, 1) if shape == "production" else (100, None)
    slice_seconds: list[float] = []
    result: dict = {}
    run_id = None
    n = 0
    prof_stages: dict[str, float] = {}
    prof_counts: dict[str, int] = {}
    while True:
        n += 1
        with sf() as db:
            t0 = time.perf_counter()
            with warehouse.collection_session():
                result = svc.run_collection(
                    db, tenant_id, connector_id, warehouse,
                    window_begin=wb, window_end=we,
                    partition=partition, mode="synthetic",
                    page_limit=page_limit, max_pages=max_pages,
                    run_id=run_id)
            slice_seconds.append(time.perf_counter() - t0)
        p = result.get("profile")
        if p:
            for k, v in p.get("stages_ms", {}).items():
                prof_stages[k] = prof_stages.get(k, 0.0) + v
            for k, v in p.get("counts", {}).items():
                prof_counts[k] = prof_counts.get(k, 0) + v
        run_id = result["run_id"]
        if not (result.get("yielded") and result.get("state") == "RUNNING"):
            break
    if prof_stages:
        result["profile"] = {"stages_ms": dict(sorted(prof_stages.items())),
                             "counts": dict(sorted(prof_counts.items()))}
    return result, slice_seconds, n


# ---------------------------------------------------------------- SQL/commit 计数
def _tmpl(sql: str) -> str:
    s = re.sub(r"'(?:[^']|'')*'", "?", sql)
    s = re.sub(r"\b\d+(?:\.\d+)?\b", "?", s)
    return re.sub(r"\s+", " ", s).strip()[:160]


class SqlStats:
    """应用库 SQL 语句（按模板）与 commit 计数（engine 级事件）。"""

    def __init__(self, engine):
        from sqlalchemy import event
        from sqlalchemy.engine import Connection
        self.templates: dict[str, int] = {}
        self.statements = 0
        self.commits = 0
        self._engine = engine
        event.listen(engine, "before_cursor_execute", self._on_exec)
        event.listen(Connection, "commit", self._on_commit)

    def _on_exec(self, conn, cursor, statement, parameters, context, executemany):
        self.statements += 1
        key = _tmpl(statement)
        self.templates[key] = self.templates.get(key, 0) + 1

    def _on_commit(self, conn):
        self.commits += 1

    def detach(self):
        from sqlalchemy import event
        from sqlalchemy.engine import Connection
        event.remove(self._engine, "before_cursor_execute", self._on_exec)
        event.remove(Connection, "commit", self._on_commit)


def run_worker_followup(sf, tenant_id: str, connector_id: str) -> dict:
    """诊断阶段：真实 WorkerEngine 驱动 outbox 路由→prepare 全链路至空闲。

    用于量化生产路径的 commit 放大系数（每单 BILL_INGESTED 事件 → drain
    提交 + prepare job 声称/执行/完成提交）。Mock 零售目的地使 VALID 单
    全部走真实 prepare 路径（冻结包+Submission+SubmissionItem）。
    """
    import app_runtime
    from apps.worker.engine import PluginRegistryAdapter, WorkerEngine
    from contracts import new_uuid
    from core_db.models import Destination
    with sf() as db:
        existing = (db.query(Destination)
                    .filter_by(tenant_id=tenant_id, code="bench-retail").first())
        if existing is None:
            db.add(Destination(
                id=new_uuid(), tenant_id=tenant_id, code="bench-retail",
                name="基准Mock零售目的地",
                profile_revision="alihealth_yljg_retail_v1@bench",
                mode="PUSH", capabilities=[], is_mock=True,
                status="ENABLED"))
            db.commit()
    reg = app_runtime.load_registry(["drug_trace"])
    worker = WorkerEngine(sf, registry=PluginRegistryAdapter(reg))
    idle = 0
    ticks = 0
    counts = {"outbox": 0, "jobs": 0, "steps": 0, "polls": 0}
    while idle < 3 and ticks < 2000:
        r = worker.tick()
        ticks += 1
        for k in counts:
            counts[k] += r.get(k, 0)
        busy = r["outbox"] + r["jobs"] + r["steps"] + r["polls"]
        idle = 0 if busy else idle + 1
    return {"ticks": ticks, **counts}


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bills", type=int, default=500)
    ap.add_argument("--preload", type=int, default=0,
                    help="预载历史单数（模拟累积存量；0=空库）")
    ap.add_argument("--latency-ms", type=float, default=0.0,
                    help="每次源查询注入的模拟网络往返毫秒")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--shape", choices=["production", "single"],
                    default="production")
    ap.add_argument("--profile", action="store_true",
                    help="打开 BLYB_DT_PROFILE 分阶段计时")
    ap.add_argument("--batch-fetch", action="store_true",
                    help="打开源端批量取数 batch_fetch（生产默认关）")
    ap.add_argument("--after-worker", action="store_true",
                    help="采集后用真实 WorkerEngine 跑完 outbox→prepare 链"
                         "（诊断 commit 放大系数）")
    ap.add_argument("--out", default="")
    ap.add_argument("--prepare-dir", default="",
                    help="预载准备产物目录（app+view 两个 sqlite）；"
                         "配合 --prepare-only/--from-prepared 跨次复用")
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--from-prepared", action="store_true",
                    help="从 --prepare-dir 恢复预载库（跳过重建）")
    args = ap.parse_args(argv)

    if args.profile:
        os.environ["BLYB_DT_PROFILE"] = "1"
    else:
        os.environ.pop("BLYB_DT_PROFILE", None)

    import tempfile
    workdir = args.prepare_dir or tempfile.mkdtemp(prefix="lt08-bench-")
    os.makedirs(workdir, exist_ok=True)
    app_db = os.path.join(workdir, "app.sqlite3")
    pristine_db = os.path.join(workdir, "app.pristine.sqlite3")
    view_db = os.path.join(workdir, "view.sqlite3")

    reuse = bool(args.from_prepared and os.path.exists(pristine_db)
                 and os.path.exists(view_db))
    if reuse:
        # 从原始备份恢复应用库（预载态）——每次测量同一初始状态，可重复 A/B
        for suffix in ("", "-wal", "-shm"):
            tgt = app_db + suffix
            if os.path.exists(tgt):
                os.remove(tgt)
        shutil.copyfile(pristine_db, app_db)
        print(f"[prepare] restored pristine preloaded db in {workdir}")
    else:
        preload_bills = preload_codes = 0
        pb = pc = []
        if args.preload:
            # 预载单号空间（PL）与计时单号空间（BL）分离：自然键绝不相撞
            pb, pc = generate_dataset(args.preload, seed=args.seed + 1,
                                      window=PRELOAD_WINDOW, id_prefix="PL")
            preload_bills, preload_codes = len(pb), len(pc)
        bills, codes = generate_dataset(args.bills, seed=args.seed,
                                        window=TIMED_WINDOW)
        print(f"[prepare] view db: preload={preload_bills}b/{preload_codes}c "
              f"timed={len(bills)}b/{len(codes)}c")
        build_view_db(view_db, pb + bills, pc + codes)
        sf, engine, tenant_id, connector_id = open_app_db(app_db, migrate=True)
        if args.preload:
            wh = make_view_warehouse(view_db)
            t0 = time.perf_counter()
            res, _, _ = run_collect_loop(sf, tenant_id, connector_id, wh,
                                         window=PRELOAD_WINDOW,
                                         partition="bench", shape="production")
            print(f"[prepare] preload collect {res.get('bills_seen')} bills "
                  f"in {time.perf_counter() - t0:.1f}s "
                  f"(state={res.get('state')})")
            assert res.get("state") == "DONE", res
        engine.dispose()
        shutil.copyfile(app_db, pristine_db)
        if args.prepare_only:
            print(f"[prepare] saved to {workdir}")
            return 0

    sf, engine, tenant_id, connector_id = open_app_db(app_db, migrate=False)
    wh = make_view_warehouse(view_db, args.latency_ms)
    if args.batch_fetch:
        wh.batch_fetch = True

    stats = SqlStats(engine)
    t0 = time.perf_counter()
    result, slice_seconds, n_slices = run_collect_loop(
        sf, tenant_id, connector_id, wh, window=TIMED_WINDOW,
        partition="bench", shape=args.shape)
    total = time.perf_counter() - t0
    stats.detach()
    worker_followup = None
    if args.after_worker:
        t1 = time.perf_counter()
        stats2 = SqlStats(engine)
        worker_followup = run_worker_followup(sf, tenant_id, connector_id)
        worker_followup["seconds"] = round(time.perf_counter() - t1, 3)
        stats2.detach()
        worker_followup["sql_statements"] = stats2.statements
        worker_followup["commits"] = stats2.commits
    engine.dispose()

    bills_seen = int(result.get("bills_seen", 0))
    q_seconds = float(wh.metrics.get("query_seconds", 0.0))
    q_count = int(wh.metrics.get("query_count", 0))
    nonquery = max(total - q_seconds, 0.0)
    report = {
        "config": {k: getattr(args, k) for k in
                   ("bills", "preload", "latency_ms", "seed", "shape")},
        "total_seconds": round(total, 3),
        "bills_seen": bills_seen,
        "slices": n_slices,
        "slice_seconds": [round(x, 3) for x in slice_seconds],
        "per_bill_ms": round(total * 1000 / bills_seen, 3) if bills_seen else None,
        "nonquery_per_bill_ms": round(nonquery * 1000 / bills_seen, 3)
        if bills_seen else None,
        "source_query": {
            "count": q_count, "seconds": round(q_seconds, 3),
            "per_query_ms": round(q_seconds * 1000 / q_count, 3) if q_count else 0},
        "sql_statements": stats.statements,
        "sql_per_bill": round(stats.statements / bills_seen, 2) if bills_seen else None,
        "commits": stats.commits,
        "commits_per_bill": round(stats.commits / bills_seen, 2) if bills_seen else None,
        "sql_templates": dict(sorted(stats.templates.items(), key=lambda kv: -kv[1])),
        "summary": {k: v for k, v in result.items() if k != "profile"},
        "profile": result.get("profile"),
        "worker_followup": worker_followup,
    }
    if worker_followup and bills_seen:
        report["worker_followup"]["commits_per_bill"] = round(
            worker_followup["commits"] / bills_seen, 2)
        report["worker_followup"]["ms_per_bill"] = round(
            worker_followup["seconds"] * 1000 / bills_seen, 3)
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("sql_templates", "slice_seconds")},
                     ensure_ascii=False, indent=2))
    print("top sql templates:")
    for tmpl, cnt in list(report["sql_templates"].items())[:12]:
        print(f"  {cnt:6d}  {tmpl}")
    if args.out:
        out_dir = os.path.dirname(os.path.abspath(args.out))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
