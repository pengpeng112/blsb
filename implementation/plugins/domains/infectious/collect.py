"""RT-04 A2：六视图真实增量采集（主入口 inf.collect 的真实 SQL 路径）。

- `collect_from_connection(conn, db, ...)`：SQLAlchemy Connection + 受控
  TABLES 白名单（视图名/列名只来自注册表，标识符不与用户字符串拼接，
  查询值一律参数化）；
- 首跑 `full_days`、增量 `水位−overlap_days`；WHERE 窗口与
  `ORDER BY (OPERATION_TIME, 稳定业务键)` 配对，**keyset 分页**
  （limit_per_table 默认 500）——同页 500 条同时间戳不丢后续行；
- 采集→映射→校验→**冻结**（快照入 infectious_shadow_record.payload_json，
  记录 tables_fingerprint）→排队（PENDING）；**快照提交后**才推进水位
  （A0：可靠持久化到本地待处理队列）；行级处理异常=FAILED 且**水位停在
  该行之前**（失败不越水位，下一班次从重叠窗重扫）；
- parent_ops 跨页/跨班次关联：子表父记录不在当前窗时按父键安全补查
  （SELECT 参数化）；补查仍缺→QUALITY_WAIT（可见质量等待，不编造父时间、
  不丢子项）；
- 本轮仅配置 SQLite 合成源；未来 Oracle 只读接入保持未测（不新增驱动、
  不碰真实 DSN、不悄悄使用机器上的真实配置）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from contracts.api import digest_of
from core_db.engine import utcnow

from . import mapping, state
from .evidence import normalize_op_time, scan_key
from .send import source_version_digest
from .tables import ROLL_OUT_ORDER, TABLES

CST = timezone(timedelta(hours=8))
DATE_FMT = "%Y-%m-%d %H:%M:%S"

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def tables_fingerprint() -> str:
    """采集配置指纹（TABLES+ROLL_OUT_ORDER 的规范化摘要）：冻结快照记录
    该指纹——断点恢复后可检测配置漂移（旧 ConfigRevision 不再参与）。"""
    from .tables import ROLL_OUT_ORDER as ORDER
    return digest_of({"tables": TABLES, "roll_out_order": ORDER})[:64]


def _quote(ident: str, dialect_name: str = "") -> str:
    """标识符白名单校验（来自 TABLES 注册表，仍防御性校验形态）。

    带点号的视图名（ODS.V_INF_*）按方言处理：SQLite 合成源把整名作为
    单一标识符引用（"ODS.V_INF_..."，视图侧同名创建）；其他方言按
    schema.object 分段引用。"""
    parts = ident.split(".")
    if any(not _IDENT_RE.match(p) for p in parts):
        raise ValueError(f"illegal identifier {ident!r}")
    if len(parts) > 1 and dialect_name == "sqlite":
        return '"' + ident + '"'
    return ".".join(parts)


def _columns_of(name: str) -> list[str]:
    return sorted(TABLES[name]["map"].keys())


def _to_op_str(value) -> str:
    """SQL 值（datetime/str）→ 规范化源时间串。"""
    return normalize_op_time(value)


def _window_start(name: str, cursor_scan_key: str, now: datetime) \
        -> tuple[datetime, str]:
    """(窗口起点, 模式)。增量=水位 op_time − overlap_days（重叠回扫，
    记录去重靠持久化源版本状态）；首跑=now − full_days。"""
    t = TABLES[name]
    if cursor_scan_key:
        wm_op = cursor_scan_key.split("|", 1)[0]
        start = datetime.strptime(wm_op, DATE_FMT).replace(tzinfo=CST) \
            - timedelta(days=int(t.get("overlap_days", 0)))
        return start, "watermark+overlap"
    return now.astimezone(CST) - timedelta(days=int(t["full_days"])), \
        "full(first-run)"


def _key_column(name: str) -> str:
    """稳定业务键列（TABLES map 中映射到 id 的源列名）。"""
    t = TABLES[name]
    cands = sorted(c for c, v in t["map"].items() if v == "id")
    if not cands:
        raise ValueError(f"table {name} has no id column mapping")
    return cands[0]


def fetch_page(conn, name: str, *, start_op: str, end_op: str,
               after_key: tuple[str, str] | None, limit: int) -> list[dict]:
    """单页 keyset 查询（值参数化；ORDER BY OPERATION_TIME, ID）。

    after_key=(op,id)：首页 None → 用窗口起点；后续页用上一页末行
    (op > x OR (op = x AND id > y))——同时间戳 500 条不丢行。
    """
    from sqlalchemy import text
    t = TABLES[name]
    dn = getattr(conn.dialect, "name", "")
    view = _quote(t["view"], dn)
    op_col = _quote(t.get("watermark", "OPERATION_TIME"), dn)
    key_col = _quote(_key_column(name), dn)
    cols = ", ".join(_quote(c, dn) for c in _columns_of(name))
    sql = text(
        f"SELECT {cols} FROM {view} WHERE {op_col} >= :start_op "
        f"AND {op_col} < :end_op")
    params: dict = {"start_op": start_op, "end_op": end_op, "lim": limit}
    if after_key is not None:
        sql = text(sql.text + (
            f" AND ({op_col} > :after_op OR ({op_col} = :after_op"
            f" AND {key_col} > :after_id))"))
        params["after_op"], params["after_id"] = after_key
    sql = text(sql.text + f" ORDER BY {op_col}, {key_col} LIMIT :lim")
    rows = conn.execute(sql, params)
    try:
        return [dict(r) for r in rows.mappings()]
    finally:
        rows.close()


def lookup_parent_op(conn, parent_table: str, parent_key_col: str,
                     parent_key: str, want_col: str):
    """子表父记录安全补查（按父键参数化查询，只读）。"""
    from sqlalchemy import text
    t = TABLES[parent_table]
    dn = getattr(conn.dialect, "name", "")
    view = _quote(t["view"], dn)
    sql = text(f"SELECT {_quote(want_col, dn)} FROM {view} "
               f"WHERE {_quote(parent_key_col, dn)} = :k ORDER BY 1 LIMIT 1")
    rows = conn.execute(sql, {"k": parent_key})
    try:
        for r in rows.mappings():
            return dict(r).get(want_col)
        return None
    finally:
        rows.close()


def _to_cst(dt: datetime) -> datetime:
    """统一源时区口径：naive 视为 UTC（utcnow 朴素约定），aware 归一 CST。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(CST)


def collect_from_connection(conn, db, tenant_id: str, *,
                            source_instance: str = "default",
                            tables: list[str] | None = None,
                            limit_per_table: int = 500,
                            now: datetime | None = None,
                            _parent_cache: dict | None = None) -> dict:
    """真实 SQL 增量采集（主入口）。返回逐表统计。

    水位推进（A0）：每行快照随本事务持久化（PENDING/QUALITY_WAIT/CONFLICT
    均视为已可靠持久化）；**行级处理异常**→该行 FAILED、表水位停在最后
    一个成功持久化的扫描键（失败不越水位）。
    """
    now = now or utcnow()
    now_cst = _to_cst(now)
    fingerprint = tables_fingerprint()
    results: dict[str, dict] = {"tables": {}, "now": now_cst.isoformat(),
                                "tables_fingerprint": fingerprint}
    parent_ops: dict[str, str] = {}   # 跨表父关联（exam→exam_item 等垫片）
    for name in (tables or ROLL_OUT_ORDER):
        t = TABLES[name]
        cursor_row = state.load_cursor(db, tenant_id, source_instance, name)
        cursor_scan = cursor_row.source_cursor if cursor_row else ""
        start, mode = _window_start(name, cursor_scan, now_cst)
        end_op = now_cst.strftime(DATE_FMT)
        start_op = start.strftime(DATE_FMT)
        stats = dict(rows_scanned=0, frozen=0, quality_wait=0, conflict=0,
                     duplicate_version=0, failed=0, pages=0, mode=mode,
                     window=[start_op, end_op])
        last_key: tuple[str, str] | None = None
        last_persisted_key: str = cursor_scan
        try:
            while True:
                rows = fetch_page(conn, name, start_op=start_op,
                                  end_op=end_op, after_key=last_key,
                                  limit=limit_per_table)
                stats["pages"] += 1
                if not rows:
                    break
                for row in rows:
                    stats["rows_scanned"] += 1
                    op_raw = row.get(t.get("watermark", "OPERATION_TIME"))
                    op = _to_op_str(op_raw)
                    rid = str(row.get(_key_column(name)) or "")
                    key = scan_key(op, rid)
                    payload: dict = {}
                    try:
                        payload = mapping.build_payload(name, row, parent_ops)
                        if t.get("depends_on"):
                            # 父操作人补查（当前窗 parent_ops 未命中时按父键
                            # 安全补查），补到后重跑垫片（幂等）
                            _ensure_parent_ops(conn, name, payload, parent_ops,
                                               _parent_cache)
                            mapping.apply_shims(name, payload, parent_ops)
                        _process_row(db, tenant_id, source_instance, name,
                                     payload, op, rid, fingerprint,
                                     parent_ops, stats)
                        last_persisted_key = key   # 已持久化（含质量等待）
                    except Exception as exc:      # noqa: BLE001
                        # 行级处理失败：登记 FAILED 行，水位不越过该行
                        stats["failed"] += 1
                        _mark_failed(db, tenant_id, source_instance, name,
                                     rid, op, payload, fingerprint,
                                     f"{type(exc).__name__}: {exc}")
                        break
                    last_key = (op, rid)
                else:
                    if len(rows) < limit_per_table:
                        break
                    continue
                break          # 行级失败：终止本表后续页（重叠窗重扫）
        finally:
            if last_persisted_key and last_persisted_key > (cursor_scan or ""):
                state.advance_cursor(db, tenant_id, source_instance, name,
                                     last_persisted_key)
        results["tables"][name] = stats
    db.commit()
    return results


def _process_row(db, tenant_id: str, source_instance: str, name: str,
                 payload: dict, op: str, rid: str, fingerprint: str,
                 parent_ops: dict, stats: dict) -> None:
    """映射后处理：注册版本状态 → 校验 → 冻结快照 → 排队（父补查在调用方
    build 后、本函数前完成——见 collect_from_connection 行循环）。"""
    t = TABLES[name]
    content_digest = source_version_digest(payload)
    rec, action = state.register_record(
        db, tenant_id, source_instance, name, rid,
        source_op_time=op, source_version_digest=content_digest)
    if action in ("same_version", "old_version"):
        stats["duplicate_version"] += 1
        return
    if action == "conflict":
        stats["conflict"] += 1
        return
    issues: list[str] = []
    if not op:
        issues.append("MISSING_SOURCE_OP_TIME")
    missing = mapping.validate(name, payload)
    if missing:
        issues.append("REQUIRED_MISSING:" + ",".join(missing[:8]))
    rec.payload_json = payload                    # 不可变源快照（冻结）
    rec.frozen_at = utcnow().isoformat()
    rec.tables_fingerprint = fingerprint
    rec.quality_issues = issues
    if issues:
        rec.state = "QUALITY_WAIT"
        rec.last_error = ";".join(issues)[:200]
        stats["quality_wait"] += 1
    else:
        rec.state = "PENDING"
        stats["frozen"] += 1


_PARENT_WANT = {"exam_item": ("exam", "ID", "exClinicalId", "OPERATOR_ID"),
                "ex_lab_item": ("ex_lab", "ID", "exLabId", "OPERATOR_ID"),
                "order_item": ("order", "ID", "orderId", "OPERATOR_ID")}


def _ensure_parent_ops(conn, name: str, payload: dict,
                       parent_ops: dict, _parent_cache: dict | None) -> None:
    """depends_on 子表的父操作人补查：当前窗 parent_ops 未命中时按父键
    安全补查（只读参数化查询）；仍缺→留给校验/质量层（不编造父时间）。"""
    cfg = _PARENT_WANT.get(name)
    if not cfg:
        return
    parent_table, parent_key_col, child_ref, want_col = cfg
    parent_key = str(payload.get(child_ref, "") or "")
    if not parent_key or parent_key in parent_ops:
        return
    cache = _parent_cache if _parent_cache is not None else {}
    if parent_key not in cache:
        cache[parent_key] = lookup_parent_op(
            conn, parent_table, parent_key_col, parent_key, want_col)
    val = cache.get(parent_key)
    if val not in (None, ""):
        parent_ops[parent_key] = str(val)


def _mark_failed(db, tenant_id: str, source_instance: str, name: str,
                 rid: str, op: str, payload: dict, fingerprint: str,
                 reason: str) -> None:
    """行级处理异常的可见失败登记（不丢行：快照尽力保留）。"""
    from core_db.models import InfectiousShadowRecord
    from contracts import new_uuid
    row = state.get_record(db, tenant_id, source_instance, name, rid)
    if row is None:
        row = InfectiousShadowRecord(
            id=new_uuid(), tenant_id=tenant_id,
            target_code=state.TARGET_CODE, source_instance=source_instance,
            table_key=name, source_record_key=rid, source_op_time=op,
            source_version_digest=source_version_digest(payload)
            if payload else "")
        db.add(row)
    row.state = "FAILED"
    row.last_error = reason[:200]
    row.payload_json = payload or {}
    row.tables_fingerprint = fingerprint
    row.frozen_at = utcnow().isoformat()


# ---------------------------------------------------------------- job 入口
def collect_job(db, tenant_id: str, payload: dict) -> dict:
    """inf.collect 主入口：真实 SQL 路径。连接配置缺失→显式能力不可用
    （不静默、不退回内存字典）。"""
    from connector_sdk import CapabilityError
    url = str((payload or {}).get("database_url", "")
              or _env_source_url())
    if not url:
        raise CapabilityError(
            "INF_SOURCE_UNCONFIGURED",
            "影子采集需要合成源数据库配置（payload.database_url 或环境变量"
            " BLYB_INF_SHADOW_SOURCE_URL；本轮仅 SQLite 合成源，真实 Oracle "
            "只读接入属未来切换前提）")
    tables = payload.get("tables")
    source_instance = str(payload.get("source_instance", "default"))
    from sqlalchemy import create_engine
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return collect_from_connection(
                conn, db, tenant_id, source_instance=source_instance,
                tables=tables,
                limit_per_table=int(payload.get("limit_per_table", 500)))
    finally:
        engine.dispose()


def _env_source_url() -> str:
    import os
    return os.environ.get("BLYB_INF_SHADOW_SOURCE_URL", "")


def collect_rows(db, tenant_id: str, *, source_instance: str = "default",
                 rows_by_table: dict) -> dict:
    """显式测试入口：内存行直采（不经过 SQL；仅测试装配使用——真实采集
    一律走 collect_from_connection）。水位推进与真实路径同规则：仅越过
    已持久化（冻结/质量等待/冲突）的行。"""
    fingerprint = tables_fingerprint()
    results: dict[str, dict] = {"tables": {},
                                "tables_fingerprint": fingerprint}
    for name in ROLL_OUT_ORDER:
        rows = rows_by_table.get(name) or []
        if not rows:
            continue
        t = TABLES[name]
        stats = dict(rows_scanned=len(rows), frozen=0, quality_wait=0,
                     conflict=0, duplicate_version=0, failed=0, pages=1,
                     mode="rows(test-only)")
        parent_ops: dict[str, str] = {}
        last_persisted = ""
        for row in rows:
            payload = mapping.build_payload(name, row, parent_ops)
            op = normalize_op_time(mapping.source_op_time(payload))
            rid = str(payload.get("id", "") or "")
            key = scan_key(op, rid)
            try:
                _process_row(db, tenant_id, source_instance, name, payload,
                             op, rid, fingerprint, parent_ops, stats)
                last_persisted = key
            except Exception as exc:          # noqa: BLE001
                stats["failed"] += 1
                _mark_failed(db, tenant_id, source_instance, name, rid, op,
                             payload, fingerprint,
                             f"{type(exc).__name__}: {exc}")
                break                          # 失败不越水位
        if last_persisted:
            state.advance_cursor(db, tenant_id, source_instance, name,
                                 last_persisted)
        results["tables"][name] = stats
    db.commit()
    return results
