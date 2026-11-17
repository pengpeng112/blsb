"""迁移执行器：幂等、可重复（07 §9 阶段A退出条件）。

001 为核心全量建表（SQLAlchemy metadata，幂等 checkfirst）。
领域插件迁移由应用装配时通过 extra_migrations 注册，核心不 import 插件。
生产 PostgreSQL 的后续 expand-contract 迁移在此登记新编号。
"""
from __future__ import annotations

import logging

from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Engine

from . import models as core_models
from .engine import Base

log = logging.getLogger("core_db.migrate")

_MIGRATIONS: list[tuple[int, str]] = []


def register(step: int, name: str):
    def deco(fn):
        _MIGRATIONS.append((step, name, fn))
        return fn
    return deco


def _ensure_table(engine: Engine, table: Table):
    table.create(engine, checkfirst=True)


@register(1, "001_core_init")
def _001_core(engine: Engine) -> None:
    import inspect
    from sqlalchemy import MetaData
    core_tables = {c.__table__ for _, c in inspect.getmembers(core_models, inspect.isclass)
                   if hasattr(c, "__table__")}
    # 拓扑序（父表先建）：PG 建表即校验 FK 目标，字母序不保证依赖就绪
    md = MetaData()
    for t in core_tables:
        t.to_metadata(md)                # RT-02：SQLAlchemy 2.0 API（tometadata 弃用）
    md.create_all(engine, tables=md.sorted_tables, checkfirst=True)


@register(3, "003_receipt_publication_ref")
def _003_receipt_publication_ref(engine: Engine) -> None:
    """W06：回执与发布的精确关联列（expand迁移，SQLite/PG同构ALTER）。"""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if not insp.has_table("receipt_event"):
        return
    cols = {c["name"] for c in insp.get_columns("receipt_event")}
    if "publication_ref" not in cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE receipt_event ADD COLUMN publication_ref VARCHAR(36)"))


def available_migrations() -> list[tuple[int, str]]:
    return sorted([(s, n) for s, n, _ in _MIGRATIONS])


@register(13, "013_console_password_sessions")
def _013_console_password_sessions(engine: Engine) -> None:
    """Add password/session tables without changing existing machine tokens."""
    _ensure_table(engine, core_models.ConsoleAccount.__table__)
    _ensure_table(engine, core_models.ConsoleSession.__table__)


@register(14, "014_report_target")
def _014_report_target(engine: Engine) -> None:
    """LT-01 上报业务目录：只新增 report_target 表，不改任何旧表。"""
    _ensure_table(engine, core_models.ReportTarget.__table__)


def _add_columns(engine: Engine, table: str, cols: dict) -> None:
    """SQLite/PG 同构幂等 ALTER ADD COLUMN（缺哪列补哪列）。"""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if not insp.has_table(table):
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    for name, ddl in cols.items():
        if name not in existing:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@register(15, "015_schedule")
def _015_schedule(engine: Engine) -> None:
    """LT-04 A 段：schedule / schedule_run（只新增表；015 为保留号核实后使用，
    001—014 与插件 020 不触碰）。只前进不回退：回退按 runbook DROP 两表并清除
    schema_migrations 行（调度定义/运行记录为可再生数据，不含业务提交）。"""
    _ensure_table(engine, core_models.Schedule.__table__)
    _ensure_table(engine, core_models.ScheduleRun.__table__)


@register(16, "016_alerting")
def _016_alerting(engine: Engine) -> None:
    """LT-04 C 段：alert_rule / notification_channel / notification_log 新表 +
    alert_event 扩列（可空/默认值，旧数据兼容；/api/v1/alerts 兼容只增字段）。
    只前进：回退不删 alert_event 旧列（扩列回退无意义），新表按 runbook 处理。"""
    _ensure_table(engine, core_models.AlertRule.__table__)
    _ensure_table(engine, core_models.NotificationChannel.__table__)
    _ensure_table(engine, core_models.NotificationLog.__table__)
    _add_columns(engine, "alert_event", {

        "fingerprint": "VARCHAR(128)",
        "rule_code": "VARCHAR(64) DEFAULT ''",
        "target_code": "VARCHAR(64) DEFAULT ''",
        "first_seen_at": "TIMESTAMP",
        "last_seen_at": "TIMESTAMP",
        "occurrences": "INTEGER DEFAULT 1",
        "acked_by": "VARCHAR(64) DEFAULT ''",
        "acked_at": "TIMESTAMP",
        "resolved_at": "TIMESTAMP",
        "detail": "JSON",
    })

@register(17, "017_external_ledger")
def _017_external_ledger(engine: Engine) -> None:
    """LT-05 A 段：external_import_batch / external_import_line /
    external_submission_record（只新增表；017 为保留号核实后使用）。
    只前进：台账为可再导入数据，回退按 runbook DROP 三表并清 schema_migrations。"""
    _ensure_table(engine, core_models.ExternalImportBatch.__table__)
    _ensure_table(engine, core_models.ExternalImportLine.__table__)
    _ensure_table(engine, core_models.ExternalSubmissionRecord.__table__)


def _auth_subject_has_target_scopes(engine: Engine) -> bool:
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(engine)
    return insp.has_table("auth_subject") and "target_scopes" in {
        c["name"] for c in insp.get_columns("auth_subject")}


@register(18, "018_target_scopes")
def _018_target_scopes(engine: Engine) -> None:
    """LT-06A（R08）：auth_subject.target_scopes 扩列 + 既有用户账号兼容赋
    ["*"]（新账号由应用层建为 []）。机器主体（agent/connector/collector）不赋、
    不受 scope 约束（既有生产鉴权零改变）。幂等：列在即跳过 DDL；UPDATE 仅
    作用于仍为 NULL 的行。只前进：回退按 runbook 处理（列保留无害）。"""
    from sqlalchemy import text
    if not _auth_subject_has_target_scopes(engine):
        _add_columns(engine, "auth_subject", {"target_scopes": "JSON"})
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE auth_subject SET target_scopes = '[\"*\"]' "
            "WHERE kind = 'user' AND target_scopes IS NULL"))


@register(19, "019_debug_exchange")
def _019_debug_exchange(engine: Engine) -> None:
    """LT-07：debug_exchange（联调记录；只新增表）。只前进：联调历史为
    审计证据，回退不 DROP（列/表保留无害）。"""
    _ensure_table(engine, core_models.DebugExchange.__table__)


@register(21, "021_infectious_shadow_state")
def _021_infectious_shadow_state(engine: Engine) -> None:
    """RT-04 A1：infectious_shadow_cursor / infectious_shadow_record（只新增表）。

    影子运行状态从"写 PUBLISHED ConfigRevision"改为独立状态表（旧配置保留
    只读兼容，首次使用时一次性迁入且不改写旧配置——见
    domains/infectious/state.py migrate_legacy_config）。只前进：状态行可
    按需清理重建（影子数据可再生），回退按 runbook DROP 两表并清
    schema_migrations 行。
    """
    _ensure_table(engine, core_models.InfectiousShadowCursor.__table__)
    _ensure_table(engine, core_models.InfectiousShadowRecord.__table__)


@register(22, "022_external_record_source_instance_identity")
def _022_external_record_source_instance_identity(engine: Engine) -> None:
    """RT-04 A3：external_submission_record 补 source_instance 列 + 唯一键
    uq_esr_key → uq_esr_key_src（tenant+target+**source_instance**+record_key）
    ——跨来源实例同 record_key 不再合并（LT-05 遗留缺陷：记录表原本无来源
    实例列，唯一键跨实例合并）。旧行补空串（旧库记录无实例标识；行账
    external_import_line 一直带实例，不受影响）；数据原样保留。SQLite 旧
    约束为表级 UNIQUE，需重建表；PG 直接 DROP+ADD。幂等：目标约束在即跳过。"""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if not insp.has_table("external_submission_record"):
        return
    _add_columns(engine, "external_submission_record",
                 {"source_instance": "VARCHAR(128) DEFAULT ''"})
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE external_submission_record SET source_instance = '' "
            "WHERE source_instance IS NULL"))
        # 重建前回填一切遗留 NULL（新表 NOT NULL 无库级默认；真实旧库行均
        # 由模型默认填充，此处防御手工/异常行）
        conn.execute(text(
            "UPDATE external_submission_record SET "
            "first_seen_at = COALESCE(first_seen_at, CURRENT_TIMESTAMP), "
            "attempts = COALESCE(attempts, 0), "
            "business_type = COALESCE(business_type, ''), "
            "doc_type_code = COALESCE(doc_type_code, ''), "
            "last_result = COALESCE(last_result, ''), "
            "bucket = COALESCE(bucket, ''), "
            "evidence_level = COALESCE(evidence_level, ''), "
            "last_error_code = COALESCE(last_error_code, ''), "
            "last_error_message = COALESCE(last_error_message, ''), "
            "source_file = COALESCE(source_file, ''), "
            "import_batch_id = COALESCE(import_batch_id, '') "
            "WHERE first_seen_at IS NULL OR attempts IS NULL OR "
            "business_type IS NULL OR doc_type_code IS NULL OR "
            "last_result IS NULL OR bucket IS NULL OR evidence_level IS NULL "
            "OR last_error_code IS NULL OR last_error_message IS NULL OR "
            "source_file IS NULL OR import_batch_id IS NULL"))
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE external_submission_record"
                " DROP CONSTRAINT IF EXISTS uq_esr_key"))
            # 幂等（RT-05 修复）：PG 升级路径可能重放本迁移（旧库已带新约束
            # → DuplicateTable）；pg_constraint 存在性检查后才 ADD
            has = conn.execute(text(
                "SELECT 1 FROM pg_constraint WHERE conname = 'uq_esr_key_src'"
            )).fetchone()
            if not has:
                conn.execute(text(
                    "ALTER TABLE external_submission_record"
                    " ADD CONSTRAINT uq_esr_key_src UNIQUE"
                    " (tenant_id, target_code, source_instance, record_key)"))
        return
    # SQLite：检查现行 DDL 是否仍为旧约束（新库由模型直接建新约束）
    with engine.connect() as conn:
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='external_submission_record'")).scalar() or ""
    if "uq_esr_key_src" in ddl:
        return
    from core_db.models import ExternalSubmissionRecord as M
    from sqlalchemy.schema import CreateTable
    new_ddl = str(CreateTable(M.__table__).compile(dialect=engine.dialect))
    tmp = "external_submission_record_rebuild"
    tmp_ddl = new_ddl.replace("CREATE TABLE external_submission_record",
                              f"CREATE TABLE {tmp}", 1)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        cur.execute(tmp_ddl)
        cur.execute(
            f"INSERT INTO {tmp} (id, tenant_id, target_code, record_key,"
            " business_type, doc_type_code, first_seen_at, last_attempt_at,"
            " attempts, last_result, bucket, evidence_level,"
            " last_error_code, last_error_message, op_time, source_file,"
            " import_batch_id, source_instance)"
            " SELECT id, tenant_id, target_code, record_key,"
            " business_type, doc_type_code, first_seen_at, last_attempt_at,"
            " attempts, last_result, bucket, evidence_level,"
            " last_error_code, last_error_message, op_time, source_file,"
            " import_batch_id, source_instance"
            " FROM external_submission_record")
        cur.execute("DROP TABLE external_submission_record")
        cur.execute(f"ALTER TABLE {tmp} RENAME TO external_submission_record")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_esr_last_attempt"
                    " ON external_submission_record"
                    " (tenant_id, target_code, last_attempt_at)")
        raw.commit()
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        raw.close()


def run(engine: Engine, *, extra_migrations: list = ()) -> list[tuple[int, str, str]]:
    """执行所有已注册迁移；返回 [(step, name, result)]。"""
    from sqlalchemy import text
    steps = sorted(list(_MIGRATIONS) + [(getattr(m, "step", 10_000 + i),
                                         getattr(m, "name", f"extra_{i}"), m) for i, m in enumerate(extra_migrations)])
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "step INTEGER PRIMARY KEY, name VARCHAR(255), applied_at TIMESTAMP)"))
    results: list[tuple[int, str, str]] = []
    with engine.connect() as conn:
        done = {row[0] for row in conn.execute(text("SELECT step FROM schema_migrations")).fetchall()}
    for step, name, fn in steps:
        if step in done:
            results.append((step, name, "ALREADY_APPLIED"))
            continue
        log.info("applying migration %s %s", step, name)
        fn(engine)
        with engine.begin() as conn:
            from .engine import utcnow
            conn.execute(text(
                "INSERT INTO schema_migrations (step, name, applied_at) VALUES (:s,:n,:t)"),
                {"s": step, "n": name, "t": utcnow()})
        results.append((step, name, "APPLIED"))
    return results


@register(4, "004_bigint_sizes")
def _004_bigint_sizes(engine: Engine) -> None:
    """R23：尺寸/容量/预留列升 BigInteger。SQLite 动态类型无需变更；
    PostgreSQL 对既有 INTEGER 列执行 ALTER ... TYPE BIGINT（expand迁移）。"""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    stmts = [
        "ALTER TABLE asset ALTER COLUMN expected_size TYPE BIGINT",
        "ALTER TABLE blob ALTER COLUMN size_bytes TYPE BIGINT",
        "ALTER TABLE storage_volume ALTER COLUMN capacity_bytes TYPE BIGINT",
        "ALTER TABLE storage_volume ALTER COLUMN reserved_bytes TYPE BIGINT",
        "ALTER TABLE storage_volume ALTER COLUMN safety_margin_bytes TYPE BIGINT",
        "ALTER TABLE upload_session ALTER COLUMN expected_size TYPE BIGINT",
        "ALTER TABLE upload_chunk ALTER COLUMN size_bytes TYPE BIGINT",
    ]
    with engine.begin() as conn:
        for stmt in stmts:
            conn.execute(text(stmt))


@register(5, "005_storage_reservation_ledger")
def _005_storage_reservation_ledger(engine: Engine) -> None:
    """W07扩大：预留账本表（CAS释放恰好一次）。

    - 建表 storage_reservation；
    - 回填：既有未终态 upload_session 生成 ACTIVE 账本行（2×expected_size，
      与运行时口径一致），保证旧库升级后释放语义不丢；
    - 既有 ACTIVE 文件卷且根下已有数据目录时补写挂载哨兵文件
      （一次性升级动作；空根/根缺失不补，交由清扫判 LOST）。
    幂等：重复执行不重复回填（唯一约束 + 哨兵已存在）。
    """
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if not insp.has_table("storage_reservation"):
        from .models import StorageReservation
        StorageReservation.__table__.create(engine)
    if not insp.has_table("upload_session"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT id, tenant_id, volume_id, expected_size, created_at "
            "FROM upload_session "
            "WHERE state IN ('CREATED','RECEIVING','VERIFYING')")).fetchall()
        for sid, tenant_id, volume_id, expected_size, created_at in rows:
            dup = conn.execute(text(
                "SELECT 1 FROM storage_reservation WHERE session_id = :s"),
                {"s": sid}).fetchone()
            if dup:
                continue
            from .engine import utcnow
            from contracts import new_uuid
            conn.execute(text(
                "INSERT INTO storage_reservation "
                "(id, tenant_id, volume_id, session_id, bytes_amount, state, reason, created_at) "
                "VALUES (:i,:t,:v,:s,:b,'ACTIVE','',:c)"),
                {"i": new_uuid(), "t": tenant_id, "v": volume_id, "s": sid,
                 "b": 2 * (expected_size or 0), "c": created_at or utcnow()})
        # 挂载哨兵一次性回填（仅限已确立的卷：根存在且已有数据目录）
        if insp.has_table("storage_volume"):
            import os
            vols = conn.execute(text(
                "SELECT id, root_ref FROM storage_volume "
                "WHERE state = 'ACTIVE' AND backend = 'filesystem'")).fetchall()
            for vid, root in vols:
                if not root or not os.path.isdir(root):
                    continue
                if not (os.path.isdir(os.path.join(root, "objects"))
                        or os.path.isdir(os.path.join(root, "staging"))):
                    continue  # 空根/疑似卸载重建目录：不补哨兵，交清扫判LOST
                sentinel = os.path.join(root, f".blyb-vol-{vid}")
                if not os.path.exists(sentinel):
                    with open(sentinel, "w", encoding="ascii") as f:
                        f.write(vid)


@register(6, "006_config_release_digest")
def _006_config_release_digest(engine: Engine) -> None:
    """W10：ConfigRelease 聚合摘要列（发布闭包防篡改证据；expand迁移）。"""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if not insp.has_table("config_release"):
        return
    cols = {c["name"] for c in insp.get_columns("config_release")}
    if "digest" not in cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE config_release ADD COLUMN digest VARCHAR(64)"))


# T9：父子表租户一致性约束对。SQLite 无法 ADD FK：父表建 (tenant_id,id) 唯一索引，
# 查询层校验兜底（文档注明）；PG 建复合FK。冲突数据只告警（AlertEvent）不默改。
_TENANT_REF_PAIRS = [
    # (子表, 子表外键列, 父表)
    ("asset", "acquisition_id", "acquisition"),
    ("upload_session", "asset_id", "asset"),
    ("submission", "package_revision_id", "package_revision"),
    ("submission", "destination_id", "destination"),
    ("publication", "package_revision_id", "package_revision"),
    ("asset_location", "blob_id", "blob"),
]


@register(7, "007_tenant_composite_constraints")
def _007_tenant_composite_constraints(engine: Engine) -> None:
    """T9（S08）：租户维度库级约束（SQLite/PG 分档）。

    1. 前置盘点：每对父子表查租户不一致的既有行；发现即写 AlertEvent
       （code=TENANT_REF_VIOLATION, target=表:行）并**跳过该对约束**
       （不默删不猜改，迁移照常完成，AlertEvent 即证据）。
    2. SQLite：父表建 CREATE UNIQUE INDEX IF NOT EXISTS ux_<parent>_tenant_id
       ON <parent>(tenant_id, id)（SQLite 无法 ADD FK，唯一索引+查询层校验兜底）。
    3. PostgreSQL：父表同样先建唯一索引，再对干净的对 ADD CONSTRAINT
       fk_<child>_tenant_<parent> FOREIGN KEY (tenant_id, <parent_key>)
       REFERENCES <parent>(tenant_id, id)（幂等：information_schema 查存在即跳过）。
    """
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    tables = set(insp.get_table_names())
    if not {"asset", "submission", "alert_event"} <= tables:
        return
    pairs = [(c, k, p) for c, k, p in _TENANT_REF_PAIRS
             if c in tables and p in tables]
    existing_fks: set[str] = set()
    if engine.dialect.name == "postgresql":
        for t in ("asset", "upload_session", "submission", "publication",
                  "asset_location"):
            if t in tables:
                existing_fks |= {r["name"] for r in insp.get_foreign_keys(t)}
    from contracts import new_uuid
    from .engine import utcnow
    with engine.begin() as conn:
        violated_pairs: set[tuple[str, str, str]] = set()
        for child, child_key, parent in pairs:
            rows = conn.execute(text(
                f"SELECT c.id, c.tenant_id FROM {child} c "
                f"JOIN {parent} p ON p.id = c.{child_key} "
                f"WHERE c.tenant_id <> p.tenant_id")).fetchall()
            for rid, tenant_id in rows:
                violated_pairs.add((child, child_key, parent))
                conn.execute(text(
                    "INSERT INTO alert_event (id, tenant_id, alert_type, severity,"
                    " message, target_ref, state, created_at)"
                    " VALUES (:i,:t,'TENANT_REF_VIOLATION','warning',:m,:tg,'OPEN',:at)"),
                    {"i": new_uuid(), "t": tenant_id or "",
                     "m": f"{child}.tenant_id differs from {parent} via {child_key};"
                          f" constraint pair skipped (no auto-fix)",
                     "tg": f"{child}:{rid}", "at": utcnow()})
        clean_parents = {p for _, _, p in pairs
                         if not any(v[2] == p for v in violated_pairs)}
        # 父表 (tenant_id, id) 唯一索引：两种方言都需要（PG复合FK引用列必须有唯一约束）
        for parent in sorted(clean_parents):
            conn.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{parent}_tenant_id"
                f" ON {parent}(tenant_id, id)"))
        if engine.dialect.name == "postgresql":
            for child, child_key, parent in pairs:
                if (child, child_key, parent) in violated_pairs:
                    continue
                fk_name = f"fk_{child}_tenant_{parent}"
                if fk_name in existing_fks:
                    continue
                conn.execute(text(
                    f"ALTER TABLE {child} ADD CONSTRAINT {fk_name}"
                    f" FOREIGN KEY (tenant_id, {child_key})"
                    f" REFERENCES {parent}(tenant_id, id)"))


@register(8, "008_publication_revoked_at_reservation_generalize")
def _008_publication_revoked_at(engine: Engine) -> None:
    """T4/T5 支撑（expand迁移，幂等）：
    - publication.revoked_at：撤回时点列（T4）；
    - storage_reservation.session_id 去除上传会话FK（T5 存储迁移以确定性
      派生键复用预留CAS账本）。SQLite 连接启用 PRAGMA foreign_keys=ON，
      旧库的FK会被执行，故经表重建去除（数据原样拷贝）；PG 侧 DROP
      CONSTRAINT。"""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if insp.has_table("publication"):
        cols = {c["name"] for c in insp.get_columns("publication")}
        if "revoked_at" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE publication ADD COLUMN revoked_at TIMESTAMP"))
    if not insp.has_table("storage_reservation"):
        return
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE storage_reservation"
                " DROP CONSTRAINT IF EXISTS storage_reservation_session_id_fkey"))
        return
    # SQLite：旧库 DDL 含 upload_session FK 时重建表（raw连接，避开事务内PRAGMA）
    with engine.connect() as conn:
        ddl = conn.execute(text(
            "SELECT sql FROM sqlite_master WHERE type='table'"
            " AND name='storage_reservation'")).scalar() or ""
    if "REFERENCES upload_session" not in ddl:
        return  # 新库（模型已无FK）或已重建
    from .models import StorageReservation
    from sqlalchemy.schema import CreateTable
    new_ddl = str(CreateTable(StorageReservation.__table__)
                  .compile(dialect=engine.dialect))
    tmp = "storage_reservation_rebuild"
    tmp_ddl = new_ddl.replace("CREATE TABLE storage_reservation",
                              f"CREATE TABLE {tmp}", 1)
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=OFF")
        cur.execute(f"DROP TABLE IF EXISTS {tmp}")
        cur.execute(tmp_ddl)
        cur.execute(
            f"INSERT INTO {tmp} (id, tenant_id, volume_id, session_id,"
            " bytes_amount, state, reason, created_at, released_at)"
            " SELECT id, tenant_id, volume_id, session_id, bytes_amount,"
            " state, reason, created_at, released_at"
            " FROM storage_reservation")
        cur.execute("DROP TABLE storage_reservation")
        cur.execute(f"ALTER TABLE {tmp} RENAME TO storage_reservation")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_storage_reservation_volume_id"
                    " ON storage_reservation (volume_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS ix_storage_reservation_session_id"
                    " ON storage_reservation (session_id)")
        raw.commit()
        cur.execute("PRAGMA foreign_keys=ON")
    finally:
        raw.close()


@register(23, "023_infectious_monitor_telemetry")
def _023_infectious_monitor_telemetry(engine: Engine) -> None:
    """PT-03：传染病监控观测三表（只新增）+ 修正存量 doc_type_code 错位。

    - infectious_table_telemetry：每表最新水位/最近班次/最近日终对账快照；
    - infectious_watchdog_snapshot：守护 status.json C1–C11 + 晨报 + 告警流水
      （全部守护白名单输出，零患者字段）；
    - external_file_cursor：只读目录流式尾读游标（字节偏移+头摘要轮转检测+
      全局行号，100% O_RDONLY 零写源目录）；
    - **数据修正**：external_submission_record 存量行 doc_type_code 按
      business_type 重映射——《03-接口总览与状态矩阵》权威编码与旧映射系统性
      错位（旧 death→07 应为 17、exam→09 应为 11 等），按幂等 UPDATE 修正
      （重复执行同结果；不影响任何其他列）。
    """
    _ensure_table(engine, core_models.InfectiousTableTelemetry.__table__)
    _ensure_table(engine, core_models.InfectiousWatchdogSnapshot.__table__)
    _ensure_table(engine, core_models.ExternalFileCursor.__table__)
    # 权威编码修正（business_type=表 key 是唯一事实源；只改 doc_type_code 列）
    from sqlalchemy import text
    fixes = {
        "admission": "09", "admission_info": "06", "discharge_info": "10",
        "death": "17", "first_course": "07", "daily_course": "08",
        "exam": "11", "exam_item": "12", "ex_lab": "13", "ex_lab_item": "14",
        "order": "15", "order_item": "16", "vital_signs": "18",
    }
    with engine.begin() as conn:
        for btype, code in fixes.items():
            conn.execute(text(
                "UPDATE external_submission_record SET doc_type_code = :c "
                "WHERE business_type = :b AND doc_type_code <> :c"),
                {"c": code, "b": btype})
