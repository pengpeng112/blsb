"""药品追溯领域模型 + 编号迁移 009（W03）。

- 外部标识一律字符串原值（前导零/超长数字串不转数值）。
- 所有表带 tenant_id；子表按 (tenant_id, parent_id) 语义由唯一键与查询层保证
  （与 core 现有风格一致：SQLite 不 ADD FK，PG 建复合唯一索引）。
- 复合业务键含药房/业务分类：跨药房同号不串单（DTR-A07/S07）。
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (JSON, Boolean, Date, DateTime, Index, Integer,
                        Numeric, String, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from contracts import new_uuid
from core_db.engine import Base, utcnow

TS = DateTime()


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_uuid)


class DrugTraceBill(Base):
    """来源单据登记（当前态指针；历史快照在 DrugTraceBillRevision）。

    FIX2-03/R2-08 修订：稳定身份 = 完整来源主键（tenant+connector+
    source_system+药房+业务+source_bill_id）；source_bill_no 仅展示，
    不同 source_bill_id 不得因同展示号合并（010 迁移替换 009 的旧约束）。
    """
    __tablename__ = "drug_trace_bill"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_id", "source_system",
                         "pharmacy_code", "business_kind", "source_bill_id",
                         name="uq_dt_bill_source_key"),
        Index("ix_dt_bill_updated", "tenant_id", "connector_id", "source_updated_at"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    connector_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pharmacy_code: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    warehouse_code: Mapped[str] = mapped_column(String(128), default="")
    business_kind: Mapped[str] = mapped_column(String(64), nullable=False,
                                               default="UNKNOWN")
    source_bill_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_bill_no: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    current_source_revision: Mapped[str] = mapped_column(String(128), default="")
    source_status: Mapped[str] = mapped_column(String(32), default="ACTIVE")
    finalized: Mapped[bool] = mapped_column(Boolean, default=False)
    is_void: Mapped[bool] = mapped_column(Boolean, default=False)
    bill_time: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    source_event_seq: Mapped[str] = mapped_column(String(128), default="")
    current_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class DrugTraceBillRevision(Base):
    """不可变整单快照（header + lines 原值；同版本重导幂等，内容冲突报错）。"""
    __tablename__ = "drug_trace_bill_revision"
    __table_args__ = (
        UniqueConstraint("bill_id", "source_revision", name="uq_dt_bill_rev"),
        Index("ix_dt_brev_tenant", "tenant_id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    bill_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    header: Mapped[dict] = mapped_column(JSON, nullable=False)
    lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    quality_state: Mapped[str] = mapped_column(String(16), default="WAITING")
    quality_issues: Mapped[list] = mapped_column(JSON, default=list)
    event_time: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    source_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    config_release_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DrugTraceCodeIndex(Base):
    """码级索引：revision 内逐条扫码记录（查询/对账；原值仍在 revision.lines）。"""
    __tablename__ = "drug_trace_code_index"
    __table_args__ = (
        UniqueConstraint("bill_revision_id", "source_code_record_id",
                         name="uq_dt_code_record"),
        Index("ix_dt_code_bill", "tenant_id", "bill_id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    bill_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    bill_revision_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    source_line_id: Mapped[str] = mapped_column(String(255), default="")
    source_code_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trace_code: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    code_level: Mapped[str] = mapped_column(String(16), default="")
    parent_code: Mapped[str] = mapped_column(String(255), default="")
    quantity: Mapped[str] = mapped_column(String(64), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class DrugTraceBillCode(Base):
    """平台单号映射：tenant+profile+来源复合键 → 稳定 bill_code。

    默认规则=前缀+复合键哈希（rule_revision 固定）；配置变更不隐式重编旧单号；
    碰撞检测与平台长度上限在建码时执行，不用随机重试号。
    """
    __tablename__ = "drug_trace_bill_code"
    __table_args__ = (
        UniqueConstraint("tenant_id", "profile", "source_bill_key",
                         name="uq_dt_billcode_src"),
        UniqueConstraint("tenant_id", "profile", "bill_code",
                         name="uq_dt_billcode_value"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    source_bill_key: Mapped[str] = mapped_column(String(512), nullable=False)
    bill_code: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_revision: Mapped[str] = mapped_column(String(64), default="v1")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DrugTraceProductionEvidence(Base):
    """生产接入证据（DTR-A02/A03；空证据拒绝，病理 B01—B09 规则不变）。

    required_items 由代码常量固定（FIX2-02：用户不可缩减）；审批逐项登记
    provided 证据引用。缺项/撤销/过期/destination/config_release 不匹配 → 拒绝。
    scope（010 新增）：发送授权范围 {window_begin, window_end, pharmacies[],
    max_bills}——EXT-12 首批发送逐单复核用。
    """
    __tablename__ = "drug_trace_production_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "profile", "destination_id", "capability",
                         name="uq_dt_evidence_scope"),
        Index("ix_dt_evidence_tenant", "tenant_id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(64), nullable=False, default="drug_trace")
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    config_release_id: Mapped[str] = mapped_column(String(36), default="")
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    required_items: Mapped[list] = mapped_column(JSON, default=list)
    provided_items: Mapped[list] = mapped_column(JSON, default=list)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")
    approved_by: Mapped[str] = mapped_column(String(64), default="")
    valid_until: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DrugTraceRetryBill(Base):
    """持久重取账本（FIX2-03/R2-08）：整单读取持续变更/失败的单据登记于此，
    下一轮采集窗口扫描前先重取（成功出账，失败保留计数），不再只依赖短回看。"""
    __tablename__ = "drug_trace_retry_bill"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_id", "source_system",
                         "pharmacy_code", "business_kind", "source_bill_id",
                         name="uq_dt_retry_key"),
        Index("ix_dt_retry_tenant", "tenant_id", "connector_id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    connector_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_system: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    pharmacy_code: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    business_kind: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_bill_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(TS, default=utcnow)


class DrugTraceCollectionRun(Base):
    """采集运行（窗口、断点、结果摘要；可暂停/续跑）。"""
    __tablename__ = "drug_trace_collection_run"
    __table_args__ = (Index("ix_dt_collect_tenant", "tenant_id", "state"),)
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    connector_id: Mapped[str] = mapped_column(String(36), nullable=False)
    stream: Mapped[str] = mapped_column(String(64), default="bills")
    profile: Mapped[str] = mapped_column(String(64), default="")
    window_begin: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(TS, nullable=True)  # 固定上界（半开）
    lookback_seconds: Mapped[int] = mapped_column(Integer, default=600)
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(TS, nullable=True)


class DrugTraceSendQuota(Base):
    """发送配额台账（FIX3-04/G3-11）：EXT-12 批准配额的并发预留计数。

    行在 approve_evidence（send capability）时创建/更新（max_bills 来自批准
    scope）；perform_send 用条件原子 UPDATE（used < max_bills 才 +1）预留——
    并发发送在 PG 行锁/SQLite 写串行下不会超发。失败尝试也占额度（保守计数，
    防并发窗口超发）。
    """
    __tablename__ = "drug_trace_send_quota"
    __table_args__ = (
        UniqueConstraint("tenant_id", "evidence_id", name="uq_dt_send_quota"),
        Index("ix_dt_quota_tenant", "tenant_id"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(36), nullable=False)
    used: Mapped[int] = mapped_column(Integer, default=0)
    max_bills: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


class DrugTraceDailyReport(Base):
    """每日定时上报运行日报（TUI-01/迁移012，DTR-TIMED-UI-20261024）。

    驱动 v2 跑完 POST 入库（按 tenant+report_date+source 幂等 upsert），界面/
    查询不再读宿主回执文件。JSON 字段存驱动回执对应结构（任务书 §5.1）：
    collection={id,state,summary}；send={sent,prepared_new,resumed,
    skipped_terminal,errors[],aborted_reason}；query_stats={queried,errors[]}。
    """
    __tablename__ = "drug_trace_daily_report"
    __table_args__ = (
        UniqueConstraint("tenant_id", "report_date", "source",
                         name="uq_dt_daily_report"),
        Index("ix_dt_daily_report_tenant", "tenant_id", "report_date"),
    )
    id: Mapped[str] = _pk()
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # timer|manual
    window_begin: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(TS, nullable=True)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    collection: Mapped[dict] = mapped_column(JSON, default=dict)
    send: Mapped[dict] = mapped_column(JSON, default=dict)
    query_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    outcome: Mapped[str] = mapped_column(String(16), default="OK")  # OK|PARTIAL_FAILURE|FAILED
    elapsed_seconds: Mapped[float] = mapped_column(Numeric(12, 1), default=0)
    created_at: Mapped[datetime] = mapped_column(TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TS, default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------- 迁移 009
def migration(engine) -> None:
    """009_drug_trace_domain：建全部领域表（幂等 checkfirst）。

    - 目标派发复用 destination.profile_revision（profile key@version），不加列：
      已部署 001—008 库在未启用本插件时保持旧代码可运行（schema 稳定）。
    - PG 对 bill/revision 建 (tenant_id,id) 唯一索引（core 007 同风格）；
      SQLite 由查询层兜底。不回填、不改 001—008 语义。
    - 本函数保持 009 原语义（建表幂等）；010 及之后的结构性调整见后续迁移，
      已应用 009 的存量库由 010 做约束替换（不原地重写 009）。
    """
    from sqlalchemy import inspect as sa_inspect, text
    tables = [DrugTraceBill, DrugTraceBillRevision, DrugTraceCodeIndex,
              DrugTraceBillCode, DrugTraceProductionEvidence,
              DrugTraceCollectionRun]
    for t in tables:
        t.__table__.create(engine, checkfirst=True)
    insp = sa_inspect(engine)
    if engine.dialect.name == "postgresql" and insp.has_table("drug_trace_bill_revision"):
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_drug_trace_bill_tenant_id"
                " ON drug_trace_bill(tenant_id, id)"))
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_drug_trace_bill_revision_tenant_id"
                " ON drug_trace_bill_revision(tenant_id, id)"))


migration.step = 9
migration.name = "009_drug_trace_domain"


# ---------------------------------------------------------------- 迁移 010
def migration_fix2(engine) -> None:
    """010_drug_trace_fix2（FIX2-03/07；不原地重写 009）：

    - 新表 drug_trace_retry_bill（持久重取账本）。
    - drug_trace_production_evidence 增加 scope 列（发送授权范围 EXT-12）。
    - PG：drug_trace_bill 唯一约束由展示单号（009 uq_dt_bill_composite）替换为
      完整来源主键 uq_dt_bill_source_key——替换前做重复检查，存在坏数据即报错
      隔离（绝不默删）；为其余领域表补 (tenant_id,id) 复合唯一索引。
    - SQLite（测试库）：模型建表已按新键；本迁移仅补 retry 表/scope 列（幂等）。
    """
    from sqlalchemy import inspect as sa_inspect, text
    DrugTraceRetryBill.__table__.create(engine, checkfirst=True)
    insp = sa_inspect(engine)
    if insp.has_table("drug_trace_production_evidence"):
        cols = [c["name"] for c in insp.get_columns(
            "drug_trace_production_evidence")]
        if "scope" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE drug_trace_production_evidence "
                    "ADD COLUMN scope JSON"))
    if engine.dialect.name == "postgresql" and insp.has_table("drug_trace_bill"):
        with engine.begin() as conn:
            dup = conn.execute(text(
                "SELECT count(*) FROM (SELECT tenant_id, connector_id,"
                " source_system, pharmacy_code, business_kind, source_bill_id"
                " FROM drug_trace_bill GROUP BY 1,2,3,4,5,6"
                " HAVING count(*) > 1) t")).scalar()
            if dup:
                raise RuntimeError(
                    f"drug_trace_bill contains {dup} duplicate source keys; "
                    "isolate manually before applying 010 (no silent delete)")
            has_old = conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = "
                "'uq_dt_bill_composite' AND conrelid = 'drug_trace_bill'"
                "::regclass")).scalar()
            if has_old:
                conn.execute(text(
                    "ALTER TABLE drug_trace_bill "
                    "DROP CONSTRAINT uq_dt_bill_composite"))
            has_new = conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = "
                "'uq_dt_bill_source_key' AND conrelid = 'drug_trace_bill'"
                "::regclass")).scalar()
            if not has_new:
                conn.execute(text(
                    "ALTER TABLE drug_trace_bill ADD CONSTRAINT "
                    "uq_dt_bill_source_key UNIQUE (tenant_id, connector_id,"
                    " source_system, pharmacy_code, business_kind,"
                    " source_bill_id)"))
            for tbl in ("drug_trace_code_index", "drug_trace_bill_code",
                        "drug_trace_production_evidence",
                        "drug_trace_collection_run", "drug_trace_retry_bill"):
                conn.execute(text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{tbl}_tenant_id"
                    f" ON {tbl}(tenant_id, id)"))


migration_fix2.step = 10
migration_fix2.name = "010_drug_trace_fix2"


# ---------------------------------------------------------------- 迁移 011
def migration_fix3(engine) -> None:
    """011_drug_trace_fix3（FIX3-04/06；不原地重写 009/010）：

    - 新表 drug_trace_send_quota（EXT-12 发送配额并发台账）。
    - PG：为领域子表补复合 tenant 外键——(tenant_id, 父键) REFERENCES 父表
      (tenant_id, id)。复合唯一索引（010 的 ux_*）只保证唯一性，不阻止跨租户
      悬挂引用；外键补齐后跨租户/孤儿行在写入时即被拒。加键前先做孤儿行预检，
      存在坏数据即报错隔离（绝不默删）。被引用端 connector_instance 补
      (tenant_id,id) 唯一索引。
    - SQLite：仅建 quota 表（测试库始终按最终模型建库；SQLite 存量 009 库的
      旧唯一约束重建不在本迁移范围——无 SQLite 生产部署，见会话记录）。
    """
    from sqlalchemy import inspect as sa_inspect, text
    DrugTraceSendQuota.__table__.create(engine, checkfirst=True)
    if engine.dialect.name != "postgresql":
        return
    insp = sa_inspect(engine)
    for tbl in ("drug_trace_bill", "drug_trace_bill_revision",
                "drug_trace_code_index", "connector_instance"):
        if not insp.has_table(tbl):
            return                      # 旧库未启用药品域：011 对其为空操作
    fk_plan = (
        ("drug_trace_bill_revision", "bill_id", "drug_trace_bill",
         "id", "fk_dt_rev_bill_tenant"),
        ("drug_trace_code_index", "bill_revision_id",
         "drug_trace_bill_revision", "id", "fk_dt_code_rev_tenant"),
        ("drug_trace_collection_run", "connector_id", "connector_instance",
         "id", "fk_dt_run_conn_tenant"),
        ("drug_trace_retry_bill", "connector_id", "connector_instance",
         "id", "fk_dt_retry_conn_tenant"),
    )
    with engine.begin() as conn:
        # 坏数据预检：孤儿/跨租户悬挂行 → 报错隔离（不默删、不跳过）
        for child, ccol, parent, pcol, _name in fk_plan:
            if not insp.has_table(child):
                continue
            orphan = conn.execute(text(
                f"SELECT count(*) FROM {child} c LEFT JOIN {parent} p "
                f"ON p.tenant_id = c.tenant_id AND p.{pcol} = c.{ccol} "
                f"WHERE p.id IS NULL")).scalar()
            if orphan:
                raise RuntimeError(
                    f"{child}.{ccol} has {orphan} orphan/cross-tenant rows; "
                    f"isolate manually before applying 011 (no silent delete)")
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_connector_instance_tenant_id"
            " ON connector_instance(tenant_id, id)"))
        for child, ccol, parent, pcol, name in fk_plan:
            if not insp.has_table(child):
                continue
            exists = conn.execute(text(
                "SELECT count(*) FROM pg_constraint WHERE conname = :n "
                "AND conrelid = CAST(:t AS regclass)"),
                {"n": name, "t": child}).scalar()
            if not exists:
                conn.execute(text(
                    f"ALTER TABLE {child} ADD CONSTRAINT {name} "
                    f"FOREIGN KEY (tenant_id, {ccol}) "
                    f"REFERENCES {parent}(tenant_id, {pcol})"))


migration_fix3.step = 11
migration_fix3.name = "011_drug_trace_fix3"


# ---------------------------------------------------------------- 迁移 012
def migration_fix4(engine) -> None:
    """012_drug_trace_daily_report（TUI-01，DTR-TIMED-UI-20261024；不重写 009—011）：

    - 新表 drug_trace_daily_report（每日定时上报运行日报，驱动 v2 POST 入库；
      按 tenant+report_date+source 幂等 upsert）。
    - 建表幂等（checkfirst）；PG 补 (tenant_id,id) 复合唯一索引（010/011 同风格）。
    - 回滚见 migration_fix4_downgrade（DROP TABLE 幂等）；已入库日报为新增数据，
      回滚不删除亦无害（任务书 §8）。
    """
    from sqlalchemy import inspect as sa_inspect, text
    DrugTraceDailyReport.__table__.create(engine, checkfirst=True)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS"
                " ux_drug_trace_daily_report_tenant_id"
                " ON drug_trace_daily_report(tenant_id, id)"))


def migration_fix4_downgrade(engine) -> None:
    """012 回滚（幂等）：drop 日报表；schema_migrations 行由运维按需清除。"""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS drug_trace_daily_report"))


migration_fix4.step = 12
migration_fix4.name = "012_drug_trace_daily_report"


# ---------------------------------------------------------------- 迁移 020
def migration_perf(engine) -> None:
    """020_drug_trace_perf_indexes（DTR-BATCH-FIX-20261108；只加索引，不改表）：

    - quality_issue(entity_ref)：rescan/存量重判路径的清旧 DELETE 原先只能走
      (tenant_id, state) 前缀再过滤 entity_ref——生产 OPEN 项 7.4 万行时每次
      DELETE 顺序扫全表（2026-11-05 实测 seq_scan 37,280 次≈单据数）。
    - PG 专属：package_revision((provenance->>'bill_revision_id')) 表达式索引，
      加速 prepare 换代检查的定点 JOIN（Fix1 的配套；22K 行量级兜底）。
    - 编号说明：013 为全局当前最大号，014—019 已被平台演进长任务预留
      （platform-evolution-20261103/README §5.3），故用 020。
    - 幂等（IF NOT EXISTS）；回滚见 migration_perf_downgrade（DROP INDEX 幂等，
      纯索引回滚无数据影响）。
    """
    from sqlalchemy import text
    from core_db.models import QualityIssue  # noqa: F401 —— 确保建表先于索引
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_quality_issue_entity"
            " ON quality_issue(entity_ref)"))
        if engine.dialect.name == "postgresql":
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_pkg_rev_bill_revision"
                " ON package_revision((provenance->>'bill_revision_id'))"))


def migration_perf_downgrade(engine) -> None:
    """020 回滚（幂等）：纯索引，DROP 无数据影响。"""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_quality_issue_entity"))
        if engine.dialect.name == "postgresql":
            conn.execute(text("DROP INDEX IF EXISTS ix_pkg_rev_bill_revision"))


migration_perf.step = 20
migration_perf.name = "020_drug_trace_perf_indexes"
