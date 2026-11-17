"""Core API：认证、设备、治理配置、质量、病理业务、交换、审计（04 §5）。"""
from __future__ import annotations

import csv
import io
import os
from datetime import timedelta

from fastapi import Body, Depends, FastAPI, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from contracts import (ApiError, PackageRevisionState, SubmissionState, new_uuid, ok)
from contracts.api import (ROLE_AUDITOR, ROLE_BUSINESS_REVIEWER, ROLE_CONFIG_EDITOR,
                           ROLE_OPERATOR, ROLE_PLATFORM_ADMIN, ROLE_SERVICE,
                           audit, digest_of, hash_token, install_error_handlers,
                           make_auth_dependency)
from core_db.engine import make_engine, make_session_factory, utcnow
from core_db.models import (Acquisition, Agent, AgentCommand, AlertEvent, Asset,
                            AuditEvent, AuthSubject, ConfigRevision, ConnectorInstance,
                            DataPackage, Destination, DictionaryRevision,
                            ExternalBlocker, InboxEvent, Job, ManualDecision,
                            MappingRevision, Operation, OutboxEvent,
                            PackageRevision, Publication, PublicationItem,
                            QualityIssue, ReceiptEvent, ReconciliationItem,
                            ReconciliationRun, ReportTarget,
                            SourceReconciliationRun, SourceSystem,
                            StorageVolume, Submission, SubmissionItem, Tenant,
                            WorkflowRevision, WorkflowRun)
from workflow_core import dag as dag_mod
from workflow_core import jobs as jobs_mod


# 字段血缘的设计基线映射（10 §3；来源=设计文档，区别于 configured/evaluated
# 两层真实数据——S15：血缘从真实字段评估结果读取，不只有静态占位）。
_DESIGN_LINEAGE = {
    "case.pathology_no": "Pitaya.PathologyID（原值；长度规则配置化）",
    "application.external_id": "Pitaya.ReqNoteID / 集成申请号（交叉校验）",
    "patient.source_ref": "Pitaya.PatientID → HIS.PAT_MASTER_INDEX（精确匹配）",
    "encounter.source_ref": "HIS.PAT_VISIT 复合键（PATIENT_ID+VISIT_ID）",
    "slide.source_key": "Pitaya_Slice.Id（候选源PK，接入核验）",
    "slide.barcode_raw": "Pitaya_Slice.qptmh（原值，反查索引，非全局唯一）",
    "block.source_block_id": "Pitaya_Material.lkh（标本-块外键未确认）",
    "report.gross/micro/diagnosis": "Rysj/Gjsj/Zdyj + 集成报告表病理诊断（按语义分存）",
    "index.application_time": "权威申请时间字段（未批准→等待，CreateTime仅为新增发现通道）",
    "index.category": "权威类别字段/批准规则（'--'为空→等待）",
    "index.report_org": "报告机构→医保码 批准映射（不取库内YYDM）",
}


# 请求体模型必须定义在模块级：本文件有 `from __future__ import annotations`，
# 端点注解是字符串；定义在 create_app 内的局部类无法被 FastAPI 解析为
# Body 参数，会被静默降级为 query 参数（曾导致这批 POST 端点全数 422）。
class ConnectorBody(BaseModel):
    plugin_id: str
    name: str
    direction: str = "source"
    source_system_code: str | None = None
    config: dict = Field(default_factory=dict)


class DictBody(BaseModel):
    namespace: str
    version: str
    items: list[dict]
    approve: bool = False


class PackBody(BaseModel):
    content: dict
    publish: bool = False


class WorkflowBody(BaseModel):
    dsl: dict
    publish: bool = False


class DecisionBody(BaseModel):
    entity_ref: str
    action: str
    after: dict = Field(default_factory=dict)
    reason: str
    expected_row_version: int | None = None


def _mask_index_serialized(s: str) -> str:
    """T10 读取路径防护：索引串第2段为证件号码，按既有掩码手法（前4后4）
    掩码后返回。仅处理数据形状（8个"-"分隔段），不依赖病理模块。"""
    parts = s.split("-")
    if len(parts) == 8 and len(parts[1]) > 8:
        parts[1] = parts[1][:4] + "*" * (len(parts[1]) - 8) + parts[1][-4:]
    return "-".join(parts)


# T2：_route_event 认识的事件类型（core_handlers 与 worker/engine.py 保持一致）
from core_handlers import INGRESS_ROUTABLE_EVENT_TYPES as _INGRESS_ROUTABLE  # noqa: E402


def _keyset_page(q, ts_col, id_col, cursor: str | None, cap: int, *,
                 descending: bool = True):
    """T8 统一 keyset 游标分页：cursor = <时间ISO>|<id>。

    - cursor 解析必须 datetime.fromisoformat 后再与列比较（SQLite 存储格式
      含空格分隔，ISO 字符串直比会漏行——收尾轮已修过一次，勿重蹈）。
    - 取 cap+1 行探测 has_more；返回 (rows, has_more)。
    - cursor 为 None 时不加过滤（首页语义不变）。
    """
    from datetime import datetime
    if cursor:  # 空串视同首页（调用方以 cursor 参数存在与否切换响应形状）
        ts, rid = cursor.split("|", 1)
        ts_dt = datetime.fromisoformat(ts)
        if descending:
            q = q.filter((ts_col < ts_dt) | ((ts_col == ts_dt) & (id_col < rid)))
        else:
            q = q.filter((ts_col > ts_dt) | ((ts_col == ts_dt) & (id_col > rid)))
    order1 = ts_col.desc() if descending else ts_col.asc()
    order2 = id_col.desc() if descending else id_col.asc()
    rows = q.order_by(order1, order2).limit(cap + 1).all()
    has_more = len(rows) > cap
    return rows[:cap], has_more


def _next_cursor(rows, has_more, ts_attr: str = "created_at") -> str | None:
    """末行游标（空页/无更多页 → None）。"""
    if not has_more or not rows:
        return None
    last = rows[-1]
    ts = getattr(last, ts_attr)
    return f"{ts.isoformat()}|{last.id}"


def create_app(session_factory=None, engine=None, *, registry=None) -> FastAPI:
    engine = engine or make_engine()
    sf = session_factory or make_session_factory(engine)
    auth = make_auth_dependency(sf)
    app = FastAPI(title="blyb core-api", version="0.4.0", docs_url="/docs")
    install_error_handlers(app)
    # W11 插件装配 + RT-02 单轨化：registry 注入优先；未注入时 BLYB_PLUGINS
    # 显式给出则按其装配，否则复用旧全量兼容层（pathology+metrics）的同一
    # PluginRegistry 实例——_loaded_registry 永不为 None（旧双轨在未设
    # BLYB_PLUGINS 时置 None，导致 ops_routes 丢失全部领域任务类型）。
    if registry is None:
        import os as _os
        if _os.environ.get("BLYB_PLUGINS"):
            import app_runtime as _ar
            _loaded_registry = _ar.load_registry()
        else:
            import plugin_registry as _pr
            _loaded_registry = _pr.shared_registry()
    else:
        _loaded_registry = registry
    # LT-01 钩子聚合用 registry 与装配 registry 单轨同一对象
    _hooks_registry = _loaded_registry
    _pathology_enabled = _loaded_registry.has("pathology")
    _drug_trace_enabled = _loaded_registry.has("drug_trace")
    from core_handlers import enqueue as _enqueue

    def _pub_pack(db, tenant_id):
        rev = (db.query(ConfigRevision)
               .filter_by(tenant_id=tenant_id, config_type="hospital_pack",
                          state="PUBLISHED")
               .order_by(ConfigRevision.revision.desc()).first())
        return rev

    # ================================================================ 基础
    @app.get("/health")
    def health():
        with sf() as db:
            db.execute(text("SELECT 1"))
        return ok({"status": "UP"}, "")

    @app.post("/api/v1/auth/login")
    def login(body: dict):
        from contracts.passwords import password_login, subject_info
        if "username" in body or "password" in body:
            username, password = body.get("username"), body.get("password")
            if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 128:
                raise ApiError("UNAUTHORIZED", "用户名或密码错误，或账户暂时不可用")
            with sf() as db:
                data = password_login(db, username, password)
            return JSONResponse(content=ok(data, ""), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        # Compatibility for existing automation; the production UI uses passwords.
        token = body.get("token", "")
        if not isinstance(token, str) or not token or len(token) > 512:
            raise ApiError("UNAUTHORIZED", "invalid token")
        with sf() as db:
            s = db.query(AuthSubject).filter_by(token_hash=hash_token(token),
                                                status="ACTIVE").first()
            if s is None:
                raise ApiError("UNAUTHORIZED", "invalid token")
            return ok(subject_info(s), "")

    @app.get("/api/v1/auth/me")
    def current_user(ctx=Depends(auth)):
        from contracts.passwords import subject_info
        p = ctx.require_principal()
        with sf() as db:
            subject = db.query(AuthSubject).filter_by(id=p.subject_id, tenant_id=p.tenant_id).one()
            # RT-01：附带业务授权范围（None=不受限→前端按 '*' 处理），供侧栏
            # 菜单按 target_scopes 过滤；纯增字段，不改既有键。
            return ok({**subject_info(subject), "password_session": ctx.session_id is not None,
                       "target_scopes": p.target_scopes_effective()}, ctx.request_id)

    @app.post("/api/v1/auth/logout")
    def logout(ctx=Depends(auth)):
        from core_db.models import ConsoleSession
        p = ctx.require_principal()
        if ctx.session_id is not None:
            with sf() as db:
                session = db.query(ConsoleSession).filter_by(id=ctx.session_id, tenant_id=p.tenant_id,
                                                             subject_id=p.subject_id).one()
                session.revoked_at = utcnow()
                audit(db, p, "auth.logout", "console_session:" + session.id)
                db.commit()
        return ok({"logged_out": True}, ctx.request_id)

    @app.post("/api/v1/auth/password")
    def update_password(body: dict, ctx=Depends(auth)):
        from contracts.passwords import change_password
        p = ctx.require_principal()
        if ctx.session_id is None:
            raise ApiError("FORBIDDEN", "请先通过用户名和密码登录")
        current, new = body.get("current_password"), body.get("new_password")
        if not isinstance(current, str) or not isinstance(new, str) or len(current) > 128:
            raise ApiError("VALIDATION_FAILED", "请填写原密码和新密码")
        with sf() as db:
            change_password(db, p, current, new)
        return ok({"password_changed": True, "login_required": True}, ctx.request_id)

    @app.get("/api/v1/tenants")
    def tenants(ctx=Depends(auth)):
        ctx.require_principal().require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            rows = db.query(Tenant).all()
            return ok([{"id": t.id, "code": t.code, "name": t.name,
                        "timezone": t.timezone, "status": t.status} for t in rows],
                      ctx.request_id)

    # ================================================================ 设备
    @app.post("/api/v1/agents/enroll")
    def enroll(body: dict, request: Request):
        """设备注册/凭据轮换。令牌为高熵随机值，哈希存储，同设备重注册即轮换（R27）。"""
        import secrets as _secrets
        enroll_token = body.get("enroll_token", "")
        expected = os.environ.get("BLYB_ENROLL_TOKEN", "")
        if not expected and os.environ.get("BLYB_DISABLE_DEV_TOKENS", "") != "1":
            # 本地开发默认；生产禁用 dev 令牌时未显式配置 BLYB_ENROLL_TOKEN
            # 即注册不可用（fail-closed），默认注册密钥失效（execution-plan §5）。
            expected = "dev-enroll-token"
        if not expected or enroll_token != expected:
            raise ApiError("UNAUTHORIZED", "invalid enrollment token")
        device_code = body.get("device_code", "")
        if not device_code:
            raise ApiError("VALIDATION_FAILED", "device_code required")
        with sf() as db:
            t = db.query(Tenant).filter_by(code=body.get("tenant_code",
                                                         "first-hospital")).first()
            if t is None:
                raise ApiError("NOT_FOUND", "tenant not found")
            src = (db.query(SourceSystem)
                   .filter_by(tenant_id=t.id, code=body.get("source_system_code",
                                                            "scanner-simulator")).first())
            if src is None:
                raise ApiError("NOT_FOUND", "source system not found")
            agent = db.query(Agent).filter_by(tenant_id=t.id,
                                              device_code=device_code).first()
            created = agent is None
            if agent is None:
                agent = Agent(id=new_uuid(), tenant_id=t.id, source_system_id=src.id,
                              device_code=device_code,
                              device_name=body.get("device_name", ""),
                              cert_fingerprint=body.get("cert_fingerprint", ""))
                db.add(agent)
                db.flush()
            # 每次注册签发新的高熵令牌（不可由 agent_id 推导；重注册=轮换）
            token = f"agent-{_secrets.token_urlsafe(32)}"
            sub = (db.query(AuthSubject)
                   .filter_by(tenant_id=t.id, subject_code=f"agent:{device_code}").first())
            rotated = sub is not None
            if sub is None:
                sub = AuthSubject(id=new_uuid(), tenant_id=t.id, kind="agent",
                                  subject_code=f"agent:{device_code}",
                                  display_name=f"Agent {device_code}",
                                  roles=["service"], agent_id=agent.id)
                db.add(sub)
            sub.token_hash = hash_token(token)
            db.flush()
            db.add(AuditEvent(
                id=new_uuid(), tenant_id=t.id, actor="system",
                action="agent.enroll" if created else "agent.credential.rotate",
                target=f"agent:{agent.id}",
                new_ref={"device_code": device_code, "rotated": rotated}))
            db.commit()
            return ok({"agent_id": agent.id, "token": token, "created": created,
                       "credential_rotated": rotated,
                       "source_system_code": src.code}, "")

    @app.post("/api/v1/agents/{agent_id}/heartbeat")
    def heartbeat(agent_id: str, body: dict, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            agent = db.get(Agent, agent_id)
            if agent is None or agent.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "agent not found")
            agent.last_seen = utcnow()
            if body.get("applied_config_revision"):
                agent.applied_config_revision = body["applied_config_revision"]
            cmds = (db.query(AgentCommand)
                    .filter_by(agent_id=agent.id, state="PENDING")
                    .order_by(AgentCommand.created_at).all())
            for c in cmds:
                c.state = "SENT"
            db.commit()
            return ok({"desired_config_revision": agent.desired_config_revision,
                       "commands": [{"command_id": c.id, "type": c.type,
                                     "payload": c.payload} for c in cmds]},
                      ctx.request_id)

    @app.get("/api/v1/agents")
    def list_agents(ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN, ROLE_AUDITOR)
        with sf() as db:
            rows = db.query(Agent).filter_by(tenant_id=p.tenant_id).all()
            out = []
            for a in rows:
                src = db.get(SourceSystem, a.source_system_id)
                out.append({"id": a.id, "device_code": a.device_code,
                            "device_name": a.device_name, "status": a.status,
                            "source_system": src.code if src else None,
                            "last_seen": a.last_seen.isoformat() if a.last_seen else None,
                            "online": a.last_seen is not None and
                            (utcnow() - a.last_seen) < timedelta(minutes=5)})
            return ok(out, ctx.request_id)

    @app.post("/api/v1/agents/{agent_id}/commands")
    def send_command(agent_id: str, body: dict, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        allowed = {"apply_config", "pause", "resume", "rescan", "diagnostic", "upgrade"}
        ctype = body.get("type", "")
        if ctype not in allowed:
            raise ApiError("VALIDATION_FAILED",
                           f"command type {ctype!r} not in whitelist {sorted(allowed)}")
        with sf() as db:
            agent = db.get(Agent, agent_id)
            if agent is None or agent.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "agent not found")
            cmd = AgentCommand(id=new_uuid(), tenant_id=p.tenant_id, agent_id=agent.id,
                               type=ctype, payload=body.get("payload", {}),
                               config_revision=body.get("config_revision"),
                               expires_at=utcnow() + timedelta(hours=1))
            db.add(cmd)
            db.commit()
            audit(db, p, "agent.command", f"agent_command:{cmd.id}",
                  new_ref={"type": ctype})
            db.commit()
            return ok({"command_id": cmd.id}, ctx.request_id)

    @app.post("/api/v1/agents/{agent_id}/commands/{command_id}/ack")
    def ack_command(agent_id: str, command_id: str, body: dict, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            cmd = db.get(AgentCommand, command_id)
            if cmd is None or cmd.tenant_id != p.tenant_id or cmd.agent_id != agent_id:
                raise ApiError("NOT_FOUND", "command not found")
            dup = db.get(AgentCommand, command_id)
            if dup.state == "ACKED" and dup.result == body.get("result"):
                return ok({"command_id": command_id, "idempotent": True}, ctx.request_id)
            cmd.state = "ACKED" if body.get("success", True) else "FAILED"
            cmd.result = body.get("result", {})
            db.commit()
            return ok({"command_id": command_id}, ctx.request_id)

    # ================================================================ 总览
    @app.get("/api/v1/admin/overview")
    def overview(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            tid = p.tenant_id
            acqs = db.query(Acquisition).filter_by(tenant_id=tid)
            assets = db.query(Asset).filter_by(tenant_id=tid)
            from core_db.models import Blob
            blobs = db.query(Blob).filter_by(tenant_id=tid, state="AVAILABLE")
            open_q = db.query(QualityIssue).filter_by(tenant_id=tid, state="OPEN")
            subs = db.query(Submission).filter_by(tenant_id=tid)
            pubs = db.query(Publication).filter_by(tenant_id=tid)
            agents = db.query(Agent).filter_by(tenant_id=tid)
            # LT-01：cases/cases_clinically_ready 改由病理插件 overview 钩子提供
            # （core 不再 import 领域模型；键名/口径与改前一致）
            oldest_pending = (db.query(Job)
                              .filter_by(tenant_id=tid, state="PENDING")
                              .order_by(Job.available_at).first())
            return ok({
                "acquisitions": acqs.count(),
                "files_available": assets.filter_by(state="AVAILABLE").count(),
                "bytes_available": sum(b.size_bytes for b in blobs.all()),
                "quality_open": open_q.count(),
                "quality_open_by_rule": {
                    r[0]: r[1] for r in db.query(QualityIssue.rule_code,
                                                 text("COUNT(*)"))
                    .filter(QualityIssue.tenant_id == tid,
                            QualityIssue.state == "OPEN")
                    .group_by(QualityIssue.rule_code).all()},
                "submissions": subs.count(),
                "submissions_by_state": {
                    r[0]: r[1] for r in db.query(Submission.state, text("COUNT(*)"))
                    .filter(Submission.tenant_id == tid).group_by(Submission.state).all()},
                "publications": pubs.count(),
                "agents_total": agents.count(),
                "agents_online": sum(1 for a in agents.all()
                                     if a.last_seen and
                                     (utcnow() - a.last_seen) < timedelta(minutes=5)),
                "oldest_pending_job_age_seconds":
                    (utcnow() - oldest_pending.available_at).total_seconds()
                    if oldest_pending else 0,
                **_hooks_registry.overview_contribs(db, tid),
            }, ctx.request_id)

    # ================================================================ 来源/连接器
    @app.get("/api/v1/source-systems")
    def list_sources(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = db.query(SourceSystem).filter_by(tenant_id=p.tenant_id).all()
            return ok([{"id": s.id, "code": s.code, "kind": s.kind,
                        "namespace": s.identifier_namespace,
                        "status": s.status} for s in rows], ctx.request_id)

    @app.get("/api/v1/connectors")
    def list_connectors(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(ConnectorInstance)
                    .filter_by(tenant_id=p.tenant_id).all())
            out = []
            for c in rows:
                coverage = _coverage(db, c)
                out.append({"id": c.id, "plugin_id": c.plugin_id, "name": c.name,
                            "direction": c.direction, "status": c.status,
                            "coverage": coverage})
            return ok(out, ctx.request_id)

    @app.post("/api/v1/connectors")
    def create_connector(body: ConnectorBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            src = None
            if body.source_system_code:
                src = (db.query(SourceSystem)
                       .filter_by(tenant_id=p.tenant_id, code=body.source_system_code)
                       .first())
                if src is None:
                    raise ApiError("NOT_FOUND", "source system not found")
            ci = ConnectorInstance(id=new_uuid(), tenant_id=p.tenant_id,
                                   direction=body.direction, plugin_id=body.plugin_id,
                                   name=body.name,
                                   source_system_id=src.id if src else None,
                                   status="DRAFT")
            db.add(ci)
            db.flush()
            db.add(ConfigRevision(
                id=new_uuid(), tenant_id=p.tenant_id, config_type="connector",
                owner_id=ci.id, revision=1, content_json=body.config,
                digest=digest_of(body.config), state="DRAFT", created_by=p.subject_code))
            db.commit()
            audit(db, p, "connector.create", f"connector:{ci.id}",
                  new_ref={"plugin_id": body.plugin_id})
            db.commit()
            return ok({"connector_id": ci.id}, ctx.request_id)

    @app.post("/api/v1/connectors/{cid}/test")
    def test_connector(cid: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN, ROLE_OPERATOR)
        with sf() as db:
            ci = db.get(ConnectorInstance, cid)
            if ci is None or ci.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "connector not found")
            result = {"ok": False, "detail": "", "dialect": "", "probe": "synthetic"}
            if not _pathology_enabled and ci.plugin_id != "metrics.csv":
                result.update(detail="domain plugin not enabled in this process")
            elif ci.plugin_id == "pis.pitaya.sqlserver":
                from domains.pathology.handlers import synthetic_warehouse
                wh = synthetic_warehouse()
                try:
                    rows = wh.fetch_all("pis.slide_by_barcode", {"barcode": "900001"}, 2)
                    result.update(ok=True, detail=f"synthetic probe: {len(rows)} row(s)",
                                  dialect=wh.dialect())
                except Exception as exc:  # noqa: BLE001
                    result.update(detail=str(exc))
            elif ci.plugin_id == "his.medrec.oracle":
                from domains.pathology.handlers import synthetic_warehouse
                wh = synthetic_warehouse()
                try:
                    rows = wh.fetch_all("his.patient_by_id", {"patient_id": "P0001"}, 2)
                    result.update(ok=True, detail=f"synthetic probe: {len(rows)} row(s)",
                                  dialect=wh.dialect())
                except Exception as exc:  # noqa: BLE001
                    result.update(detail=str(exc))
            else:
                from connector_sdk import CapabilityError
                try:
                    from sources.db_view import (OracleWarehouse, SqlServerWarehouse)
                    SqlServerWarehouse()
                except CapabilityError as exc:
                    result.update(detail=f"{exc.code}: {exc.message}")
            # 真实驱动状态单独展示（不算测试失败）
            drivers = {}
            for name, mod_name in (("sqlserver", "pyodbc"), ("oracle", "oracledb")):
                try:
                    __import__(mod_name)
                    drivers[name] = "installed"
                except ImportError:
                    drivers[name] = "not-installed"
            return ok({"connector": ci.plugin_id, **result,
                       "real_driver_status": drivers,
                       "note": "连通性测试仅探测；不采集全量；真实驱动需配置DSN后另行验证"},
                      ctx.request_id)

    @app.get("/api/v1/connectors/{cid}/coverage")
    def connector_coverage(cid: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            ci = db.get(ConnectorInstance, cid)
            if ci is None or ci.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "connector not found")
            return ok(_coverage(db, ci), ctx.request_id)

    def _coverage(db, ci) -> dict:
        pack = _pub_pack(db, ci.tenant_id)
        cp = pack.content_json if pack else {}
        coll = (cp.get("collection_policy") or {})
        runs = (db.query(SourceReconciliationRun)
                .filter_by(connector_id=ci.id)
                .order_by(SourceReconciliationRun.started_at.desc()).limit(5).all())
        return {
            "streams": [{
                "stream": "pis.created",
                "coverage": "INSERT_DISCOVERY_ONLY",
                "verified_update_field": coll.get("verified_update_field"),
                "delete_coverage": "UNCONFIRMED",
                "note": "CreateTime只覆盖新增发现；更新/删除靠分区盘点（10 §6）"},
                {"stream": "pis.cases.partition_scan",
                 "coverage": "CONTENT_DIGEST_RECONCILE",
                 "last_complete_scan": runs[0].finished_at.isoformat()
                 if runs and runs[0].finished_at else None,
                 "complete_coverage": runs[0].complete_coverage if runs else None}],
            "recent_reconciliations": [
                {"id": r.id, "state": r.state, "summary": r.summary,
                 "complete_coverage": r.complete_coverage,
                 "started_at": r.started_at.isoformat()} for r in runs],
        }

    # ================================================================ 字典/Pack/工作流/发布
    @app.get("/api/v1/dictionaries")
    def list_dicts(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(DictionaryRevision)
                    .filter_by(tenant_id=p.tenant_id).all())
            return ok([{"id": d.id, "namespace": d.namespace, "version": d.version,
                        "source": d.source, "items": d.items,
                        "approved_by": d.approved_by} for d in rows], ctx.request_id)

    @app.post("/api/v1/dictionaries")
    def create_dict(body: DictBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            if db.query(DictionaryRevision).filter_by(
                    tenant_id=p.tenant_id, namespace=body.namespace,
                    version=body.version).first():
                raise ApiError("CONFLICT", "dictionary version exists")
            d = DictionaryRevision(
                id=new_uuid(), tenant_id=p.tenant_id, namespace=body.namespace,
                version=body.version, items=body.items, source="target",
                approved_by=p.subject_code if body.approve else "",
                effective_from=utcnow() if body.approve else None)
            db.add(d)
            db.commit()
            audit(db, p, "dictionary.create", f"dictionary:{d.id}",
                  new_ref={"namespace": body.namespace, "version": body.version})
            db.commit()
            return ok({"dictionary_id": d.id}, ctx.request_id)

    def _guard_production(db, tenant_id: str, *, capability: str) -> None:
        """所有生产启用入口统一守卫（W10/R38）：受影响能力遇 OPEN 外部阻断即拒绝。
        守卫按能力描述，不一刀切阻断文件归集。"""
        blockers = (db.query(ExternalBlocker)
                    .filter_by(tenant_id=tenant_id, status="OPEN").all())
        if blockers:
            codes = ", ".join(b.blocker_code for b in blockers)
            raise ApiError(
                "PROFILE_UNAPPROVED",
                f"cannot enable production capability {capability!r} with open "
                f"external blockers ({codes}); B01—B09")

    @app.get("/api/v1/hospital-pack")
    def get_pack(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(ConfigRevision)
                    .filter_by(tenant_id=p.tenant_id, config_type="hospital_pack")
                    .order_by(ConfigRevision.revision).all())
            return ok([{"id": r.id, "revision": r.revision, "state": r.state,
                        "digest": r.digest, "content": r.content_json,
                        "created_by": r.created_by} for r in rows], ctx.request_id)

    @app.post("/api/v1/hospital-pack")
    def save_pack(body: PackBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            last = (db.query(ConfigRevision)
                    .filter_by(tenant_id=p.tenant_id, config_type="hospital_pack")
                    .order_by(ConfigRevision.revision.desc()).first())
            rev = (last.revision + 1) if last else 1
            if body.publish and body.content.get("production_enabled"):
                _guard_production(db, p.tenant_id, capability="hospital_pack.publish")
            cr = ConfigRevision(
                id=new_uuid(), tenant_id=p.tenant_id, config_type="hospital_pack",
                owner_id="first-hospital", revision=rev, content_json=body.content,
                digest=digest_of(body.content),
                state="PUBLISHED" if body.publish else "DRAFT",
                created_by=p.subject_code)
            db.add(cr)
            if body.publish:
                for r in db.query(ConfigRevision).filter_by(
                        tenant_id=p.tenant_id, config_type="hospital_pack",
                        state="PUBLISHED").all():
                    if r.id != cr.id:
                        r.state = "RETIRED"
            db.commit()
            audit(db, p, "hospital_pack.save", f"config_revision:{cr.id}",
                  new_ref={"revision": rev, "state": cr.state})
            db.commit()
            return ok({"revision": rev, "state": cr.state}, ctx.request_id)

    @app.get("/api/v1/workflows")
    def list_workflows(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(WorkflowRevision)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(WorkflowRevision.workflow_key, WorkflowRevision.revision)
                    .all())
            return ok([{"id": r.id, "workflow_key": r.workflow_key,
                        "revision": r.revision, "status": r.status,
                        "production_enabled": r.production_enabled,
                        "dsl": r.dsl} for r in rows], ctx.request_id)

    @app.post("/api/v1/workflows")
    def save_workflow(body: WorkflowBody, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        errors = dag_mod.validate_dsl(body.dsl)
        if errors:
            raise ApiError("VALIDATION_FAILED", "workflow DSL invalid",
                           field_errors=[{"path": e} for e in errors])
        with sf() as db:
            key = body.dsl.get("workflow_key", "")
            last = (db.query(WorkflowRevision)
                    .filter_by(tenant_id=p.tenant_id, workflow_key=key)
                    .order_by(WorkflowRevision.revision.desc()).first())
            rev = (last.revision + 1) if last else 1
            if body.publish and body.dsl.get("production_enabled"):
                # 生产启用守卫不能只守 Pack 一个入口（R38）
                _guard_production(db, p.tenant_id,
                                  capability=f"workflow.publish:{key}")
            wf = WorkflowRevision(
                id=new_uuid(), tenant_id=p.tenant_id, workflow_key=key, revision=rev,
                dsl=body.dsl, digest=digest_of(body.dsl),
                status="PUBLISHED" if body.publish else "DRAFT",
                production_enabled=bool(body.dsl.get("production_enabled")),
                created_by=p.subject_code)
            db.add(wf)
            if body.publish:
                for r in db.query(WorkflowRevision).filter_by(
                        tenant_id=p.tenant_id, workflow_key=key,
                        status="PUBLISHED").all():
                    if r.id != wf.id:
                        r.status = "RETIRED"
            db.commit()
            audit(db, p, "workflow.save", f"workflow:{wf.id}",
                  new_ref={"key": key, "revision": rev, "errors": 0})
            db.commit()
            return ok({"workflow_revision_id": wf.id, "revision": rev,
                       "status": wf.status}, ctx.request_id)

    @app.post("/api/v1/workflows/validate")
    def validate_workflow(body: dict, ctx=Depends(auth)):
        p = ctx.require_principal()
        errors = dag_mod.validate_dsl(body.get("dsl") or body)
        return ok({"valid": not errors, "errors": errors}, ctx.request_id)

    # ================================================================ workflow-runs（W12）
    @app.get("/api/v1/workflow-runs")
    def list_workflow_runs(ctx=Depends(auth), workflow_key: str = "",
                           state: str = "", limit: int = 50,
                           cursor: str | None = None):
        """run列表（keyset游标分页：cursor=created_at|id）。"""
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(WorkflowRun).filter_by(tenant_id=p.tenant_id)
            if workflow_key:
                wf_ids = [w.id for w in db.query(WorkflowRevision)
                          .filter_by(tenant_id=p.tenant_id,
                                     workflow_key=workflow_key).all()]
                q = q.filter(WorkflowRun.workflow_revision_id.in_(wf_ids or ["-"]))
            if state:
                q = q.filter_by(state=state)
            if cursor:
                from datetime import datetime
                ts, rid = cursor.split("|", 1)
                ts_dt = datetime.fromisoformat(ts)
                q = q.filter((WorkflowRun.created_at > ts_dt)
                             | ((WorkflowRun.created_at == ts_dt)
                                & (WorkflowRun.id > rid)))
            cap = min(100, limit)
            rows = (q.order_by(WorkflowRun.created_at, WorkflowRun.id)
                    .limit(cap + 1).all())
            next_cursor = None
            if len(rows) > cap:
                rows = rows[:cap]
                last = rows[-1]
                next_cursor = f"{last.created_at.isoformat()}|{last.id}"
            wf_map = {w.id: w for w in db.query(WorkflowRevision).filter(
                WorkflowRevision.id.in_([r.workflow_revision_id for r in rows]
                                        or ["-"])).all()}
            return ok({
                "items": [{
                    "id": r.id,
                    "workflow_key": wf_map[r.workflow_revision_id].workflow_key
                    if r.workflow_revision_id in wf_map else "?",
                    "workflow_revision_id": r.workflow_revision_id,
                    "state": r.state, "trigger": r.trigger,
                    "created_at": r.created_at.isoformat(),
                    "dry_run": bool((r.context or {}).get("_dry_run")),
                    "config_release_id": ((r.context or {}).get("_config_closure")
                                          or {}).get("config_release_id"),
                } for r in rows],
                "next_cursor": next_cursor,
            }, ctx.request_id)

    @app.get("/api/v1/workflow-runs/{rid}")
    def workflow_run_detail(rid: str, ctx=Depends(auth)):
        """run详情+逐步骤状态/错误（失败定位）。"""
        from core_db.models import StepRun
        p = ctx.require_principal()
        with sf() as db:
            r = db.get(WorkflowRun, rid)
            if r is None or r.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "run not found")
            wf = db.get(WorkflowRevision, r.workflow_revision_id)
            steps = (db.query(StepRun).filter_by(run_id=r.id)
                     .order_by(StepRun.id).all())
            closure = dict((r.context or {}).get("_config_closure") or {})
            return ok({
                "id": r.id, "state": r.state, "trigger": r.trigger,
                "workflow_key": wf.workflow_key if wf else "?",
                "workflow_revision": wf.revision if wf else None,
                "created_at": r.created_at.isoformat(),
                "context_keys": sorted((r.context or {}).keys()),
                "config_closure": closure,
                "steps": [{
                    "node_id": s.node_id, "state": s.state,
                    "error": s.error or {}, "outputs": s.output_refs or {},
                    "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                } for s in steps],
            }, ctx.request_id)

    @app.post("/api/v1/workflow-runs")
    def create_workflow_run(body: dict, ctx=Depends(auth),
                            idempotency_key: str = Header(default="")):
        """通用run创建（W12）：workflow_key + context；dry_run标记进上下文。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        key = body.get("workflow_key", "")
        context = dict(body.get("context") or {})
        dry = bool(body.get("dry_run"))
        from core_db.models import ConfigRelease
        with sf() as db:
            wf = (db.query(WorkflowRevision)
                  .filter_by(tenant_id=p.tenant_id, workflow_key=key,
                             status="PUBLISHED")
                  .order_by(WorkflowRevision.revision.desc()).first())
            if wf is None:
                raise ApiError("NOT_FOUND", f"workflow {key} not published")
            dedup = context.get("_dedup") or idempotency_key or new_uuid()
            ctxd = dict(context)
            ctxd["_dedup"] = dedup
            ctxd["_dry_run"] = dry
            # RT-05 U06：run 闭包解析与 worker _launch 同语义（修复 API 侧
            # 与 worker 侧不一致——API 原实现无视 rolled_back_at 且不按
            # release 固定 pack）。存在未回退 release 且闭包含 hospital_pack
            # 条目 → pack 固定为闭包修订；否则回落最新 PUBLISHED pack。
            pack_rev = None
            release = (db.query(ConfigRelease)
                       .filter_by(tenant_id=p.tenant_id)
                       .order_by(ConfigRelease.released_at.desc()).first())
            if release is not None and release.rolled_back_at is None:
                pack_entry = next(
                    (e for e in (release.revision_ids or [])
                     if e.get("type") == "hospital_pack"), None)
                if pack_entry:
                    pinned = db.get(ConfigRevision,
                                    pack_entry.get("revision_id"))
                    if pinned is not None:
                        pack_rev = pinned
            if pack_rev is None:
                pack_rev = (db.query(ConfigRevision)
                            .filter_by(tenant_id=p.tenant_id,
                                       config_type="hospital_pack",
                                       state="PUBLISHED")
                            .order_by(ConfigRevision.revision.desc()).first())
            ctxd["_config_closure"] = {
                "workflow_revision_id": wf.id, "workflow_digest": wf.digest,
                "pack_revision_id": pack_rev.id if pack_rev else None,
                "pack_digest": pack_rev.digest if pack_rev else None,
                "config_release_id": release.id if release else None,
                "dictionaries": (pack_rev.content_json or {}).get(
                    "approved_dictionaries", {}) if pack_rev else {},
            }
            run = dag_mod.create_run(db, p.tenant_id, wf.id,
                                     trigger="api" + ("+dry" if dry else ""),
                                     context=ctxd)
            dag_mod.materialize_steps(db, run, wf.dsl)
            db.commit()
            return JSONResponse(status_code=201, content=ok(
                {"run_id": run.id, "state": run.state,
                 "workflow_revision": wf.revision}, ctx.request_id))

    @app.post("/api/v1/workflow-runs/{rid}/cancel")
    def cancel_workflow_run(rid: str, ctx=Depends(auth), reason: str = ""):
        """取消：终态409；非终态置CANCELLED、未启动步骤CANCELLED；
        引擎只推进RUNNING/WAITING，故不再产生新副作用。"""
        from core_db.models import StepRun
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            r = db.get(WorkflowRun, rid)
            if r is None or r.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "run not found")
            if r.state in ("SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_REVIEW"):
                raise ApiError("CONFLICT", f"run already terminal: {r.state}")
            r.state = "CANCELLED"
            for s in db.query(StepRun).filter_by(run_id=r.id).all():
                if s.state in ("PENDING", "READY"):
                    s.state = "CANCELLED"
            db.commit()
            audit(db, p, "workflow_run.cancel", f"run:{r.id}", reason=reason)
            db.commit()
            return ok({"run_id": r.id, "state": "CANCELLED"}, ctx.request_id)

    @app.post("/api/v1/workflow-runs/{rid}/replay")
    def replay_workflow_run(rid: str, ctx=Depends(auth)):
        """固定版本回放（W12/G21）：同一workflow revision+原依赖闭包原样复制，
        不读latest（pack/config release均来自原run上下文）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            r = db.get(WorkflowRun, rid)
            if r is None or r.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "run not found")
            if r.state not in ("SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_REVIEW"):
                raise ApiError("CONFLICT", "only terminal runs can be replayed")
            wf = db.get(WorkflowRevision, r.workflow_revision_id)
            if wf is None:
                raise ApiError("NOT_FOUND", "original workflow revision gone")
            orig = dict(r.context or {})
            orig["_replayed_from"] = r.id
            orig["_dedup"] = f"{orig.get('_dedup', r.id)}:replay:{new_uuid()[:8]}"
            new_run = dag_mod.create_run(db, p.tenant_id, wf.id,
                                         trigger="replay", context=orig)
            dag_mod.materialize_steps(db, new_run, wf.dsl)
            db.commit()
            audit(db, p, "workflow_run.replay", f"run:{new_run.id}",
                  new_ref={"replayed_from": r.id,
                           "workflow_revision": wf.revision})
            db.commit()
            return JSONResponse(status_code=201, content=ok(
                {"run_id": new_run.id, "replayed_from": r.id,
                 "workflow_revision": wf.revision,
                 "config_release_id": (orig.get("_config_closure") or {})
                 .get("config_release_id")}, ctx.request_id))

    @app.get("/api/v1/config-releases")
    def list_releases(ctx=Depends(auth)):
        from core_db.models import ConfigRelease
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(ConfigRelease)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(ConfigRelease.released_at.desc()).all())
            return ok([{"id": r.id, "revision_ids": r.revision_ids,
                        "digest": r.digest, "note": r.note,
                        "released_by": r.released_by,
                        "released_at": r.released_at.isoformat(),
                        "rolled_back": bool(r.rolled_back_at)} for r in rows],
                      ctx.request_id)

    @app.get("/api/v1/config-releases/{release_id}")
    def get_release(release_id: str, ctx=Depends(auth)):
        from core_db.models import ConfigRelease
        p = ctx.require_principal()
        with sf() as db:
            r = db.get(ConfigRelease, release_id)
            if r is None or r.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "release not found")
            return ok({"id": r.id, "revision_ids": r.revision_ids,
                       "digest": r.digest, "test_report": r.test_report,
                       "note": r.note, "released_by": r.released_by,
                       "released_at": r.released_at.isoformat(),
                       "rolled_back_at": r.rolled_back_at.isoformat()
                       if r.rolled_back_at else None}, ctx.request_id)

    @app.post("/api/v1/config-releases")
    def create_release(body: dict, ctx=Depends(auth)):
        """W10 ConfigRelease 完整发布链：把当前有效配置收敛为一次可审计发布。

        快照闭包 = workflow + hospital_pack + 其引用的字典 + mapping + schema
        + destination profile + connector，全部为具体 revision + digest；
        发布时校验字典已批准且在有效期内（R37闭包级校验，未批准拒绝409）。
        run 创建时若存在未回退的 release，按 release 固定 pack revision。
        """
        from core_db.models import (ConfigRelease, ConnectorInstance,
                                    DictionaryRevision, Destination, MappingRevision,
                                    SchemaRevision)
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            entries: list[dict] = []
            # workflow：每个 key 的最新 PUBLISHED revision
            for wf in (db.query(WorkflowRevision)
                       .filter_by(tenant_id=p.tenant_id, status="PUBLISHED")
                       .order_by(WorkflowRevision.workflow_key,
                                 WorkflowRevision.revision.desc()).all()):
                if any(e["type"] == "workflow"
                       and e["key"] == wf.workflow_key for e in entries):
                    continue
                entries.append({"type": "workflow", "key": wf.workflow_key,
                                "revision_id": wf.id, "revision": wf.revision,
                                "digest": wf.digest})
            # hospital_pack：最新 PUBLISHED
            pack = (db.query(ConfigRevision)
                    .filter_by(tenant_id=p.tenant_id, config_type="hospital_pack",
                               state="PUBLISHED")
                    .order_by(ConfigRevision.revision.desc()).first())
            if pack is None:
                raise ApiError("VALIDATION_FAILED",
                               "no PUBLISHED hospital_pack; nothing to release")
            entries.append({"type": "hospital_pack", "key": "hospital_pack",
                            "revision_id": pack.id, "revision": pack.revision,
                            "digest": pack.digest})
            # 字典：pack 引用的每个 namespace 必须存在、已批准且在有效期内
            now = utcnow()
            approved_refs = (pack.content_json or {}).get("approved_dictionaries") or {}
            for namespace, version in sorted(approved_refs.items()):
                d = (db.query(DictionaryRevision)
                     .filter_by(tenant_id=p.tenant_id, namespace=namespace,
                                version=version).first())
                if d is None:
                    raise ApiError("PROFILE_UNAPPROVED",
                                   f"pack references dictionary {namespace}@{version} "
                                   "which does not exist")
                if not d.approved_by:
                    raise ApiError("PROFILE_UNAPPROVED",
                                   f"dictionary {namespace}@{version} not approved")
                if d.effective_from and d.effective_from > now:
                    raise ApiError("PROFILE_UNAPPROVED",
                                   f"dictionary {namespace}@{version} not yet effective")
                if d.effective_to and d.effective_to < now:
                    raise ApiError("PROFILE_UNAPPROVED",
                                   f"dictionary {namespace}@{version} expired")
                entries.append({"type": "dictionary",
                                "key": f"{namespace}@{version}",
                                "revision_id": d.id, "version": version,
                                "approved_by": d.approved_by,
                                "digest": digest_of(d.items or "")[:64]})
            # mapping / schema：每个 key 的最新 ACTIVE
            for m in (db.query(MappingRevision)
                      .filter_by(tenant_id=p.tenant_id, state="ACTIVE")
                      .order_by(MappingRevision.mapping_key,
                                MappingRevision.revision.desc()).all()):
                if any(e["type"] == "mapping" and e["key"] == m.mapping_key
                       for e in entries):
                    continue
                entries.append({"type": "mapping", "key": m.mapping_key,
                                "revision_id": m.id, "revision": m.revision,
                                "digest": digest_of(m.rules or "")[:64]})
            for s in (db.query(SchemaRevision)
                      .filter_by(tenant_id=p.tenant_id, status="ACTIVE")
                      .order_by(SchemaRevision.schema_key,
                                SchemaRevision.version.desc()).all()):
                if any(e["type"] == "schema" and e["key"] == s.schema_key
                       for e in entries):
                    continue
                entries.append({"type": "schema", "key": s.schema_key,
                                "revision_id": s.id, "version": s.version,
                                "digest": digest_of(s.json_schema or "")[:64]})
            # destination profiles / connectors
            for d in db.query(Destination).filter_by(
                    tenant_id=p.tenant_id, status="ENABLED").all():
                entries.append({"type": "destination_profile", "key": d.code,
                                "revision_id": d.id,
                                "digest": digest_of(
                                    {"mode": d.mode, "profile_revision": d.profile_revision,
                                     "capabilities": d.capabilities})[:64],
                                "is_mock": bool(d.is_mock)})
            for c in db.query(ConnectorInstance).filter_by(
                    tenant_id=p.tenant_id, status="ENABLED").all():
                entries.append({"type": "connector", "key": c.plugin_id,
                                "revision_id": c.id,
                                "digest": digest_of(
                                    {"active_config_revision": c.active_config_revision,
                                     "source_system_id": c.source_system_id})[:64]})
            release_digest = digest_of(entries)
            rel = ConfigRelease(
                id=new_uuid(), tenant_id=p.tenant_id, revision_ids=entries,
                digest=release_digest,
                test_report={"entry_count": len(entries),
                             "validated": ["dictionary_approvals"]},
                note=(body or {}).get("note", ""), released_by=p.subject_code)
            db.add(rel)
            db.commit()
            audit(db, p, "config.release.publish", f"config_release:{rel.id}",
                  new_ref={"digest": release_digest, "entries": len(entries)})
            db.commit()
            return JSONResponse(status_code=201, content=ok(
                {"release_id": rel.id, "digest": release_digest,
                 "entries": len(entries),
                 "entry_types": sorted({e["type"] for e in entries})},
                ctx.request_id))

    @app.post("/api/v1/config-releases/{release_id}/rollback")
    def rollback_release(release_id: str, body: dict = Body(default=None),
                         ctx=Depends(auth)):
        """RT-05 U06：撤销此发布（标记语义，非恢复）。

        - reason 必填（≥5 字）并进同事务 AuditEvent（写语义修复——原实现
          无理由、审计不含上下文）；
        - 仅标记本发布 rolled_back_at（幂等：重复调用不重设时间戳；
          already_rolled_back 标注如实返回）；
        - **不恢复任何配置**：run 侧闭包解析据此回落到最近未回退发布
          （apps/worker/engine._launch 与 apps/core_api/app.
          create_workflow_run 同语义）；要回到历史版本用 restore 端点。"""
        from core_db.models import ConfigRelease
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        reason = str(((body or {}).get("reason")) or "").strip()
        if len(reason) < 5:
            raise ApiError("VALIDATION_FAILED",
                           "reason 必填（≥5 字）：撤销发布须留审计理由")
        with sf() as db:
            rel = db.get(ConfigRelease, release_id)
            if rel is None or rel.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "release not found")
            already = rel.rolled_back_at is not None
            if not already:
                rel.rolled_back_at = utcnow()
            audit(db, p, "config.release.rollback", f"config_release:{rel.id}",
                  new_ref={"reason": reason,
                           "rolled_back_at": rel.rolled_back_at.isoformat()
                           if rel.rolled_back_at else None,
                           "already_rolled_back": already})
            db.commit()
            return ok({"release_id": rel.id,
                       "rolled_back_at": rel.rolled_back_at.isoformat()
                       if rel.rolled_back_at else None,
                       "already_rolled_back": already},
                      ctx.request_id)

    @app.post("/api/v1/config-releases/{release_id}/restore")
    def restore_release(release_id: str, body: dict = Body(default=None),
                        ctx=Depends(auth)):
        """RT-05 U06：恢复到此版本（与 rollback 不同的独立真实语义）。

        - 以历史发布的**不可变闭包**（revision_ids 逐条 revision+digest）为
          来源，逐条校验本租户内引用对象仍存在且 digest 未漂移（漂移→
          CLOSURE_DRIFTED 409，不造半恢复）；字典条目复用发布链的批准+有效
          期校验；
        - 创建**新的 ConfigRelease**（闭包=源闭包，digest=源 digest，
          test_report.restored_from 记来源），返回新 release_id/digest/
          来源版本；后续 run 闭包解析（未回退的最新 release）自然落到该
          历史闭包——恢复通过"新发布"表达，**不改写历史冻结包、不退休较新
          修订、不删除新账**；
        - 幂等：已存在未回退且 restored_from 相同、digest 相同的最新发布
          → 原样返回（idempotent=true），不重复建发布；
        - reason 必填（≥5 字）并进同事务 AuditEvent；
        - 恢复不自动启用任何调度/发送（本端点只写发布账）。"""
        from core_db.models import (ConfigRelease, ConfigRevision,
                                    ConnectorInstance, DictionaryRevision,
                                    Destination, MappingRevision,
                                    SchemaRevision, WorkflowRevision)
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        reason = str(((body or {}).get("reason")) or "").strip()
        if len(reason) < 5:
            raise ApiError("VALIDATION_FAILED",
                           "reason 必填（≥5 字）：恢复发布须留审计理由")
        with sf() as db:
            src = db.get(ConfigRelease, release_id)
            if src is None or src.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "release not found")
            entries = [dict(e) for e in (src.revision_ids or [])]
            if not entries:
                raise ApiError("VALIDATION_FAILED",
                               "源发布闭包为空，无法作为恢复来源")
            now = utcnow()
            # 闭包完整性：逐条按类型校验存在 + digest 未漂移
            for e in entries:
                t, key, rid = e.get("type"), e.get("key"), e.get("revision_id")
                obj = None
                if t == "workflow":
                    obj = db.get(WorkflowRevision, rid)
                    digest = obj.digest if obj else None
                elif t == "hospital_pack":
                    obj = db.get(ConfigRevision, rid)
                    digest = obj.digest if obj else None
                elif t == "dictionary":
                    ns, _, ver = str(key).partition("@")
                    obj = (db.query(DictionaryRevision)
                           .filter_by(tenant_id=p.tenant_id, namespace=ns,
                                      version=ver).first())
                    digest = digest_of(obj.items or "")[:64] if obj else None
                    if obj is not None:
                        if not obj.approved_by:
                            raise ApiError(
                                "PROFILE_UNAPPROVED",
                                f"dictionary {key} 恢复时尚未批准")
                        if obj.effective_from and obj.effective_from > now:
                            raise ApiError(
                                "PROFILE_UNAPPROVED",
                                f"dictionary {key} 尚未生效")
                        if obj.effective_to and obj.effective_to < now:
                            raise ApiError(
                                "PROFILE_UNAPPROVED",
                                f"dictionary {key} 已过期")
                elif t == "mapping":
                    obj = db.get(MappingRevision, rid)
                    digest = digest_of(obj.rules or "")[:64] if obj else None
                elif t == "schema":
                    obj = db.get(SchemaRevision, rid)
                    digest = digest_of(obj.json_schema or "")[:64] \
                        if obj else None
                elif t == "destination_profile":
                    obj = db.get(Destination, rid)
                    digest = digest_of(
                        {"mode": obj.mode,
                         "profile_revision": obj.profile_revision,
                         "capabilities": obj.capabilities})[:64] \
                        if obj else None
                elif t == "connector":
                    obj = db.get(ConnectorInstance, rid)
                    digest = digest_of(
                        {"active_config_revision": obj.active_config_revision,
                         "source_system_id": obj.source_system_id})[:64] \
                        if obj else None
                else:
                    raise ApiError("VALIDATION_FAILED",
                                   f"闭包含未知条目类型 {t!r}，拒绝恢复")
                if obj is None:
                    raise ApiError(
                        "CLOSURE_DRIFTED",
                        f"闭包条目 {t}/{key} 引用的修订已不存在，拒绝恢复")
                if digest is not None and e.get("digest") and \
                        str(e["digest"]) != str(digest):
                    raise ApiError(
                        "CLOSURE_DRIFTED",
                        f"闭包条目 {t}/{key} 内容摘要与源发布不一致"
                        f"（{str(e['digest'])[:12]}…!={str(digest)[:12]}…），"
                        "拒绝恢复（历史闭包已漂移须人工核对）")
            # 幂等：最新未回退发布若已是同一来源同一闭包 → 原样返回
            latest = (db.query(ConfigRelease)
                      .filter_by(tenant_id=p.tenant_id)
                      .order_by(ConfigRelease.released_at.desc()).first())
            if latest is not None and latest.rolled_back_at is None and \
                    (latest.test_report or {}).get("restored_from") == src.id \
                    and latest.digest == src.digest:
                audit(db, p, "config.release.restore", f"config_release:{latest.id}",
                      new_ref={"reason": reason, "idempotent": True,
                               "source_release_id": src.id})
                db.commit()
                return ok({"release_id": latest.id, "digest": latest.digest,
                           "source_release_id": src.id,
                           "entries": len(entries), "idempotent": True},
                          ctx.request_id)
            rel = ConfigRelease(
                id=new_uuid(), tenant_id=p.tenant_id,
                revision_ids=entries, digest=src.digest,
                test_report={"entry_count": len(entries),
                             "restored_from": src.id,
                             "restored_reason": reason,
                             "restored_source_digest": src.digest,
                             "validated": ["closure_integrity",
                                           "dictionary_approvals"]},
                note=f"恢复自发布 {src.id}（原发布时间 "
                     f"{src.released_at.isoformat() if src.released_at else '?'}）；"
                     f"理由：{reason}",
                released_by=p.subject_code)
            db.add(rel)
            db.flush()
            audit(db, p, "config.release.restore", f"config_release:{rel.id}",
                  new_ref={"reason": reason, "source_release_id": src.id,
                           "digest": rel.digest, "entries": len(entries)})
            db.commit()
            return JSONResponse(status_code=201, content=ok(
                {"release_id": rel.id, "digest": rel.digest,
                 "source_release_id": src.id, "entries": len(entries),
                 "idempotent": False}, ctx.request_id))

    # ================================================================ 质量/人工决定
    @app.get("/api/v1/quality-issues")
    def quality_issues(ctx=Depends(auth), state: str = "OPEN", rule_code: str = "",
                       entity_ref: str = "", limit: int = 50, cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            q = (db.query(QualityIssue)
                 .filter_by(tenant_id=p.tenant_id, state=state))
            if rule_code:
                q = q.filter_by(rule_code=rule_code)
            if entity_ref:
                q = q.filter_by(entity_ref=entity_ref)
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, QualityIssue.created_at,
                                          QualityIssue.id, cursor, cap,
                                          descending=False)
            out = [{"id": i.id, "entity_ref": i.entity_ref,
                    "rule_code": i.rule_code, "severity": i.severity,
                    "blocking_scope": i.blocking_scope, "field_path": i.field_path,
                    "rule_revision": i.rule_revision,
                    "evaluated_at": i.evaluated_at.isoformat(),
                    "context": i.context_json, "state": i.state} for i in rows]
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.post("/api/v1/quality-issues/{qid}/reevaluate")
    def reevaluate(qid: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            issue = db.get(QualityIssue, qid)
            if issue is None or issue.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "issue not found")
            entity = issue.entity_ref
            if entity.startswith("acquisition:"):
                if not _pathology_enabled:
                    raise ApiError("CAPABILITY_UNSUPPORTED",
                                   "pathology plugin not enabled")
                import plugin_registry
                acq = db.get(Acquisition, entity.split(":", 1)[1])
                if acq is None:
                    raise ApiError("NOT_FOUND", "acquisition gone")
                if not _pathology_enabled:
                    raise ApiError("CAPABILITY_UNSUPPORTED",
                                   "pathology plugin not enabled")
                import plugin_registry
                rt = plugin_registry.build_runtime(db, p.tenant_id)
                from domains.pathology import resolver
                result = resolver.resolve_acquisition(rt, acq)
                db.commit()
                return ok({"reevaluated": entity, "result": {
                    k: result[k] for k in ("state", "wait_reason", "case_id")}},
                    ctx.request_id)
            if entity.startswith("pathology_case:"):
                case_id = entity.split(":", 1)[1]
                _enqueue(db, p.tenant_id, "pathology.freeze_attempt",
                         f"case:{case_id}", {"case_id": case_id})
                db.commit()
                return ok({"reevaluated": entity, "queued": True}, ctx.request_id)
            raise ApiError("CAPABILITY_UNSUPPORTED",
                           f"re-evaluation not implemented for {entity}")

    @app.post("/api/v1/manual-decisions")
    def manual_decision(body: DecisionBody, ctx=Depends(auth),
                        if_match: str | None = Header(default=None, alias="If-Match")):
        """人工决定：必须 If-Match（缺省428），与DB当前决定版本比较（过期412）；
        所有关联对象先校验租户归属（R12/R13）；决定/审计/触发同事务。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_PLATFORM_ADMIN)
        if not (body.reason or "").strip():
            raise ApiError("VALIDATION_FAILED",
                           "reason required for every manual decision (10 §5)")
        with sf() as db:
            # -- 目标对象解析（全部带租户校验；跨租户一律404，不泄露存在性）
            target = None
            acq = None
            if body.entity_ref.startswith("pathology_case:"):
                from domains.pathology.models import PathologyCase
                target = (db.query(PathologyCase)
                          .filter_by(id=body.entity_ref.split(":", 1)[1],
                                     tenant_id=p.tenant_id).first())
                if target is None:
                    raise ApiError("NOT_FOUND", "case not found")
            elif body.entity_ref.startswith("acquisition:"):
                acq = (db.query(Acquisition)
                       .filter_by(id=body.entity_ref.split(":", 1)[1],
                                  tenant_id=p.tenant_id).first())
                if acq is None:
                    raise ApiError("NOT_FOUND", "acquisition not found")
            else:
                raise ApiError("VALIDATION_FAILED",
                               f"unsupported entity_ref {body.entity_ref!r}")

            # -- CAS：If-Match 必填（428），与当前决定版本比较（412）
            if if_match is None:
                raise ApiError("PRECONDITION_REQUIRED",
                               "If-Match with current decision_version required")
            current_version = (db.query(ManualDecision)
                               .filter_by(tenant_id=p.tenant_id,
                                          entity_ref=body.entity_ref).count())
            if if_match != "*" and if_match != str(current_version):
                raise ApiError(
                    "VERSION_CONFLICT",
                    f"If-Match {if_match} stale; current decision_version "
                    f"is {current_version}")

            md = ManualDecision(id=new_uuid(), tenant_id=p.tenant_id,
                                entity_ref=body.entity_ref, action=body.action,
                                before_ref={"decision_version": current_version},
                                after_ref=body.after,
                                reason=body.reason, actor=f"user:{p.subject_code}")
            db.add(md)
            # 决定作用于我方纠正层，不修改HIS/PIS；触发新评估
            if body.entity_ref.startswith("pathology_case:") and target is not None:
                if body.action == "confirm_visit":
                    visit_id = body.after.get("visit_id", "")
                    if visit_id:
                        from domains.pathology.models import EncounterReference, PatientReference
                        pat = (db.query(PatientReference)
                               .filter_by(id=target.patient_ref_id).first())
                        if pat is None:
                            raise ApiError("VALIDATION_FAILED",
                                           "case has no patient reference")
                        enc = (db.query(EncounterReference)
                               .filter_by(tenant_id=p.tenant_id,
                                          patient_ref_id=pat.id,
                                          visit_id=visit_id).first())
                        if enc is None:
                            raise ApiError("VALIDATION_FAILED",
                                           "visit not among HIS candidates")
                        enc.state = "CONFIRMED"
                elif body.action == "confirm_slide_candidate":
                    _enqueue(
                        db, p.tenant_id, "pathology.resolve_waiting",
                        f"barcode:{body.after.get('barcode_raw','')}",
                        {"barcode": body.after.get("barcode_raw", "")})
                _enqueue(db, p.tenant_id, "pathology.freeze_attempt",
                         f"case:{target.id}", {"case_id": target.id})
            elif body.entity_ref.startswith("acquisition:") and acq is not None \
                    and body.action == "bind_slide":
                # 人工确认歧义条码候选 → 建立slide绑定（写纠正层）
                from domains.pathology import resolver
                import plugin_registry
                rt = plugin_registry.build_runtime(db, p.tenant_id)
                source_slide_id = str(body.after.get("source_slide_id", ""))
                lookup = rt.pis.get_slide_by_barcode(
                    rt.scope(), body.after.get("barcode_raw", ""))
                cand = next((c for c in lookup["candidates"]
                             if c.source_key == source_slide_id), None)
                if cand is None:
                    raise ApiError("VALIDATION_FAILED",
                                   "candidate not among PIS candidates")
                case_rec = rt.pis.get_case(rt.scope(), cand.pathology_no)
                if case_rec is None:
                    raise ApiError("VALIDATION_FAILED", "case missing in PIS")
                case = resolver.upsert_case(rt, case_rec)
                slide = resolver.upsert_slide(rt, case, cand)
                resolver.link_scan_acquisition(rt, slide, acq)
                db.flush()
                for q in db.query(QualityIssue).filter_by(
                        tenant_id=p.tenant_id, entity_ref=body.entity_ref,
                        state="OPEN").all():
                    q.state = "RESOLVED"
                    q.resolved_by_decision = md.id
                db.flush()
                # 触发重评估（功能测试修复）：绑定只写纠正层关联行，
                # 病例的 slides_linked/质量/就绪状态由 resolve 快速通道
                # （_finalize_association）统一刷新——只发 case.updated 会让
                # 病例停在 DISCOVERED（freeze 不做关联）。
                plugin_registry.enqueue(db, p.tenant_id, "pathology.resolve",
                                        f"resolve:{acq.id}:rebind",
                                        {"acquisition_id": acq.id})
            db.commit()
            return ok({"decision_id": md.id,
                       "decision_version": current_version + 1}, ctx.request_id)

    # ================================================================ 本地收尾轮（第4轮）新增端点
    # T1/T11：映射列表+创建+模拟；T2 推式ingress；T3 固定包读取；T4 撤回；
    # T6 Agent只读配置；T7 手动submissions/publications。

    @app.get("/api/v1/mappings")
    def list_mappings(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(MappingRevision)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(MappingRevision.mapping_key, MappingRevision.revision)
                    .all())
            return ok([{"id": m.id, "mapping_key": m.mapping_key,
                        "revision": m.revision, "state": m.state,
                        "dictionary_refs": m.dictionary_refs,
                        "rules": m.rules} for m in rows], ctx.request_id)

    @app.post("/api/v1/mappings")
    def create_mapping(body: dict, ctx=Depends(auth)):
        """映射修订创建（T11向导支撑）：受限DSL保存前校验；更正走新revision。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        from mapping_engine import validate_mapping
        key = (body or {}).get("mapping_key", "")
        rules = (body or {}).get("rules") or {}
        state = (body or {}).get("state", "ACTIVE")
        if state not in ("DRAFT", "ACTIVE"):
            raise ApiError("VALIDATION_FAILED", "state must be DRAFT or ACTIVE")
        if not key:
            raise ApiError("VALIDATION_FAILED", "mapping_key required")
        errors = validate_mapping(rules)
        if errors:
            raise ApiError("VALIDATION_FAILED", "mapping DSL invalid",
                           field_errors=[{"path": e} for e in errors])
        with sf() as db:
            last = (db.query(MappingRevision)
                    .filter_by(tenant_id=p.tenant_id, mapping_key=key)
                    .order_by(MappingRevision.revision.desc()).first())
            rev = (last.revision + 1) if last else 1
            m = MappingRevision(
                id=new_uuid(), tenant_id=p.tenant_id, mapping_key=key,
                revision=rev, rules=rules,
                dictionary_refs=body.get("dictionary_refs") or [],
                state=state)
            db.add(m)
            db.commit()
            audit(db, p, "mapping.create", f"mapping:{m.id}",
                  new_ref={"mapping_key": key, "revision": rev})
            db.commit()
            return JSONResponse(status_code=201, content=ok(
                {"mapping_revision_id": m.id, "revision": rev}, ctx.request_id))

    @app.post("/api/v1/mappings/{mapping_key}/simulate")
    def simulate_mapping(mapping_key: str, body: dict, ctx=Depends(auth)):
        """T1 映射模拟：逐条记录逐字段返回 {value, source_field,
        dictionary_applied, error}；字段级错误不中断其他字段/记录。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_PLATFORM_ADMIN)
        from mapping_engine import apply_mapping, validate_mapping
        records = (body or {}).get("records")
        if not isinstance(records, list) or not records:
            raise ApiError("VALIDATION_FAILED", "records (non-empty list) required")
        if len(records) > 20:
            raise ApiError("VALIDATION_FAILED", "records limited to 20 per request")
        with sf() as db:
            q = (db.query(MappingRevision)
                 .filter_by(tenant_id=p.tenant_id, mapping_key=mapping_key))
            if body.get("revision") is not None:
                m = q.filter_by(revision=int(body["revision"])).first()
            else:
                m = (q.filter_by(state="ACTIVE")
                     .order_by(MappingRevision.revision.desc()).first())
                if m is None:
                    m = q.order_by(MappingRevision.revision.desc()).first()
            if m is None:
                raise ApiError("NOT_FOUND", "mapping not found")
            errors = validate_mapping(m.rules or {})
            if errors:
                raise ApiError("VALIDATION_FAILED", "mapping DSL invalid",
                               field_errors=[{"path": e} for e in errors])
            # dictionary_refs 解析（租户内、按 version）
            dictionaries: dict[str, dict] = {}
            field_errors = []
            for i, ref in enumerate(m.dictionary_refs or []):
                ref = ref if isinstance(ref, dict) else {}
                ns = str(ref.get("namespace") or ref.get("dictionary") or "")
                ver = str(ref.get("version") or "")
                name = str(ref.get("dictionary") or ns)
                if not ns or not ver:
                    field_errors.append(
                        {"path": f"dictionary_refs[{i}]",
                         "message": "namespace and version required"})
                    continue
                d = (db.query(DictionaryRevision)
                     .filter_by(tenant_id=p.tenant_id, namespace=ns,
                                version=ver).first())
                if d is None:
                    field_errors.append(
                        {"path": f"dictionary_refs[{i}]",
                         "message": f"dictionary {ns}@{ver} not found"})
                    continue
                dictionaries[name] = {str(it.get("code")): it.get("target")
                                      for it in (d.items or [])
                                      if isinstance(it, dict)}
            if field_errors:
                raise ApiError("VALIDATION_FAILED",
                               "dictionary_refs unresolved", field_errors=field_errors)
            out_records = []
            for ri, record in enumerate(records):
                if not isinstance(record, dict):
                    out_records.append({"row": ri + 1, "fields": {},
                                        "error": {"message": "record not an object"}})
                    continue
                fields = {}
                for idx, op in enumerate((m.rules or {}).get("ops", [])):
                    # 无显式 target 时：单op执行的引擎缺省键为 out_0，
                    # 对外仍按 op 序号（out_{idx}）呈现，避免同名覆盖
                    target = op.get("target", f"out_{idx}")
                    engine_key = op.get("target") or "out_0"
                    single = {"ops": [op]}
                    value, err, dict_applied = None, None, False
                    try:
                        value = apply_mapping(single, record,
                                              dictionaries).get(engine_key)
                        dict_applied = (op.get("op") == "dict_map"
                                        and record.get(op.get("source_field"))
                                        in dictionaries.get(op.get("dictionary"), {}))
                    except KeyError as exc:
                        err = {"code": "DICTIONARY_UNMAPPED", "message": str(exc)}
                    except Exception as exc:  # noqa: BLE001
                        err = {"code": "VALIDATION_FAILED", "message": str(exc)[:200]}
                    fields[target] = {
                        "value": value,
                        "source_field": op.get("source_field"),
                        "dictionary_applied": dict_applied,
                        "error": err}
                out_records.append({"row": ri + 1, "fields": fields, "error": None})
            return ok({"mapping_revision_id": m.id, "revision": m.revision,
                       "records": out_records}, ctx.request_id)

    @app.get("/api/v1/packages/{package_id}/revisions/{revision}")
    def get_package_revision(package_id: str, revision: int, ctx=Depends(auth)):
        """T3 固定包读取：冻结内容只读（跨租户/不存在404）；附件仅引用不内联
        二进制；payload.index.serialized 以掩码形态返回（T10读取路径防护，
        digest 仍为冻结原文摘要可供比对）。"""
        p = ctx.require_principal()
        with sf() as db:
            pkg = db.get(DataPackage, package_id)
            if pkg is None or pkg.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "package not found")
            pr = (db.query(PackageRevision)
                  .filter_by(package_id=pkg.id, revision=revision).first())
            if pr is None:
                raise ApiError("NOT_FOUND", "package revision not found")
            payload = dict(pr.payload or {})
            idx = payload.get("index")
            if isinstance(idx, dict) and isinstance(idx.get("serialized"), str):
                idx = dict(idx)
                idx["serialized"] = _mask_index_serialized(idx["serialized"])
                payload["index"] = idx
            attachments = []
            for s in (pr.payload or {}).get("slides", []) or []:
                for scan in s.get("scans", []):
                    if not scan.get("effective"):
                        continue
                    for a in scan.get("assets", []):
                        attachments.append({"asset_id": a.get("asset_id"),
                                            "sha256": a.get("sha256"),
                                            "size_bytes": a.get("size_bytes"),
                                            "role": a.get("role")})
            return ok({
                "package_id": pkg.id, "domain": pkg.domain,
                "business_key": pkg.business_key,
                "revision": pr.revision, "state": pr.state,
                "digest": pr.digest, "schema_ref": pr.schema_ref,
                "provenance": pr.provenance, "payload": payload,
                "attachments": attachments,
                "created_at": pr.created_at.isoformat(),
                "note": "payload.index.serialized 已按读取路径防护掩码；"
                        "digest 为冻结原文摘要",
            }, ctx.request_id)

    @app.post("/api/v1/publications/{publication_id}/revoke")
    def revoke_publication(publication_id: str, body: dict, ctx=Depends(auth)):
        """T4 撤回：仅本租户且 PUBLISHED 可撤回；manifest 路由逐个 revoke，
        PublicationItem 逐个写墓碑；幂等重复撤回 200。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        reason = str(((body or {}).get("reason")) or "").strip()
        if not reason:
            raise ApiError("VALIDATION_FAILED",
                           "reason required for publication revoke")
        with sf() as db:
            pub = db.get(Publication, publication_id)
            if pub is None or pub.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "publication not found")
            if pub.state == "REVOKED":
                return ok({"publication_id": pub.id, "state": "REVOKED",
                           "idempotent": True}, ctx.request_id)
            if pub.state != "PUBLISHED":
                raise ApiError("CONFLICT",
                               f"publication state {pub.state} not revocable")
            manifest = pub.manifest_ref or {}
            routes = list(manifest.get("routes", []))
            batch_id = manifest.get("batch_id", "")
            items = db.query(PublicationItem).filter_by(
                publication_id=pub.id).all()
            from destinations.view_publication.exchange_db import ExchangeDb
            # 与 core_handlers/网关同源的默认交换库路径（apps/core_api 需3层dirname
            # 才到仓库根；层级错配会导致撤回写错库）
            exchange_path = os.environ.get(
                "BLYB_EXCHANGE_DB", os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(
                        os.path.abspath(__file__)))),
                    "var", "exchange.sqlite3"))
            ex = ExchangeDb(exchange_path)
            try:
                for route_id in routes:
                    ex.revoke_route(route_id)
                # 结构化当前视图一致性（LG-13）：批次置REVOKED并移除仍指向
                # 本批次的 dataset_current 指针——撤回后结构化读侧与文件
                # 路由同样不可见；其他批次拥有的键不受影响。
                if batch_id:
                    batch_view = ex.revoke_batch(batch_id)
                else:
                    batch_view = {"batch_state_updated": 0,
                                  "current_pointers_removed": 0}
                for item in items:
                    ex.add_tombstone(batch_id, item.entity_type,
                                     item.external_key, reason)
            finally:
                ex.close()
            pub.state = "REVOKED"
            pub.revoked_at = utcnow()
            audit(db, p, "publication.revoke", f"publication:{pub.id}",
                  old_ref={"state": "PUBLISHED", "generation": pub.generation_id},
                  reason=reason)
            db.commit()
            return ok({"publication_id": pub.id, "state": "REVOKED",
                       "routes_revoked": len(routes),
                       "current_pointers_removed":
                           batch_view.get("current_pointers_removed", 0),
                       "tombstones_written": len(items)}, ctx.request_id)

    @app.get("/api/v1/agents/{agent_id}/configuration")
    def agent_configuration(agent_id: str, ctx=Depends(auth)):
        """T6 只读配置：device/status/applied（最近一次ACK成功的apply_config 的
        version/digest）/watch根摘要；不返回令牌/secret/凭据哈希。"""
        p = ctx.require_principal()
        if p.agent_id != agent_id:
            p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN, ROLE_AUDITOR)
        with sf() as db:
            agent = db.get(Agent, agent_id)
            if agent is None or agent.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "agent not found")
            cmd = (db.query(AgentCommand)
                   .filter_by(agent_id=agent.id, type="apply_config",
                              state="ACKED")
                   .order_by(AgentCommand.created_at.desc()).first())
            applied = None
            watch = None
            if cmd is not None and (cmd.result or {}).get("ok"):
                cfg = (cmd.payload or {}).get("config") or {}
                applied = {"command_id": cmd.id,
                           "config_version": cmd.config_revision,
                           "digest": (cmd.payload or {}).get("digest"),
                           "applied_digest": (cmd.result or {}).get("applied_digest")}
                watch_root = cfg.get("watch_root")
                watch = {"watch_root_configured": bool(watch_root),
                         "watch_root_digest": digest_of(
                             {"watch_root": watch_root})[:16]
                         if watch_root else None}
            return ok({
                "agent_id": agent.id, "device_code": agent.device_code,
                "status": agent.status,
                "desired_config_revision": agent.desired_config_revision,
                "applied": applied,
                "applied_config_revision": agent.applied_config_revision,
                "watch": watch,
            }, ctx.request_id)

    @app.post("/api/v1/submissions")
    def create_submission(body: dict, ctx=Depends(auth)):
        """T7 手动提交：幂等键 dest:package_revision（重复返回既有200）；
        真实目的地遇OPEN阻断409；投递经 destination.push_manual Job由worker执行
        （与工作流 send 节点共用同一套投递函数，不复制逻辑）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        package_id = (body or {}).get("package_id") or ""
        dest_id = (body or {}).get("destination_id") or ""
        if not package_id or not dest_id:
            raise ApiError("VALIDATION_FAILED",
                           "package_id and destination_id required")
        with sf() as db:
            pkg = db.get(DataPackage, package_id)
            if pkg is None or pkg.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "package not found")
            rev_no = body.get("revision") or pkg.current_revision
            pr = (db.query(PackageRevision)
                  .filter_by(package_id=pkg.id, revision=rev_no).first())
            if pr is None:
                raise ApiError("NOT_FOUND", "package revision not found")
            dest = db.get(Destination, dest_id)
            if dest is None or dest.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "destination not found")
            if dest.mode != "PUSH":
                raise ApiError("VALIDATION_FAILED",
                               f"destination mode {dest.mode} not PUSH")
            if not dest.is_mock:
                _guard_production(db, p.tenant_id, capability="submission.manual")
            existing = (db.query(Submission)
                        .filter_by(destination_id=dest.id,
                                   idempotency_key=f"{dest.id}:{pr.id}").first())
            if existing is not None:
                return ok({"submission_id": existing.id, "existing": True,
                           "state": existing.state}, ctx.request_id)
            from core_handlers import (build_delivery_payload,
                                       ensure_push_submission)
            payload = build_delivery_payload(db, pr)
            submission, _ = ensure_push_submission(db, p.tenant_id, dest, pr,
                                                   payload)
            _enqueue(db, p.tenant_id, "destination.push_manual",
                     f"push-manual:{submission.id}",
                     {"submission_id": submission.id, "destination_id": dest.id})
            audit(db, p, "submission.manual_create", f"submission:{submission.id}",
                  new_ref={"package_revision_id": pr.id, "destination": dest.code})
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"submission_id": submission.id, "created": True,
                 "package_revision": {"id": pr.id, "revision": pr.revision,
                                      "state": pr.state}}, ctx.request_id))

    @app.post("/api/v1/publications")
    def create_publication(body: dict, ctx=Depends(auth)):
        """T7 手动发布：{package_id, revision?} 或 {case_id}；触发既有 publish 链
        （workflow.launch → h_deliver_publish，R05全量成员校验语义不变）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            case_id = (body or {}).get("case_id") or ""
            package_id = (body or {}).get("package_id") or ""
            if not case_id and not package_id:
                raise ApiError("VALIDATION_FAILED",
                               "package_id or case_id required")
            if case_id:
                if not _pathology_enabled:
                    raise ApiError("CAPABILITY_UNSUPPORTED",
                                   "pathology plugin not enabled")
                from domains.pathology.models import PathologyCase
                case = (db.query(PathologyCase)
                        .filter_by(id=case_id, tenant_id=p.tenant_id).first())
                if case is None:
                    raise ApiError("NOT_FOUND", "case not found")
                _enqueue(db, p.tenant_id, "workflow.launch",
                         f"publish-manual:{case.id}", {"case_id": case.id})
                audit(db, p, "publication.manual_create", f"case:{case.id}",
                      new_ref={"case_id": case.id})
                db.commit()
                return JSONResponse(status_code=202, content=ok(
                    {"queued": True, "case_id": case.id}, ctx.request_id))
            pkg = db.get(DataPackage, package_id)
            if pkg is None or pkg.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "package not found")
            rev_no = body.get("revision") or pkg.current_revision
            pr = (db.query(PackageRevision)
                  .filter_by(package_id=pkg.id, revision=rev_no).first())
            if pr is None:
                raise ApiError("NOT_FOUND", "package revision not found")
            if pkg.domain == "metrics":
                # 手动发布是新的执行代次（区别于导入时的自动run）：
                # dedup 独立成键，避免复用已完成的 metrics.run Job
                _enqueue(db, p.tenant_id, "metrics.run",
                         f"publish-manual:{pkg.id}",
                         {"package_id": pkg.id,
                          "rows": (pr.payload or {}).get("rows", [])})
            else:
                # 病理等冻结包：定位病例走既有发布链（成员校验在 h_deliver_publish）
                case_id = (pr.provenance or {}).get("case_id") or ""
                if not case_id:
                    raise ApiError("VALIDATION_FAILED",
                                   "package has no case provenance; "
                                   "use case_id form")
                _enqueue(db, p.tenant_id, "workflow.launch",
                         f"publish-manual:{case_id}",
                         {"case_id": case_id, "package_revision_id": pr.id})
            audit(db, p, "publication.manual_create", f"package_revision:{pr.id}",
                  new_ref={"package_id": pkg.id, "revision": pr.revision})
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"queued": True, "package_revision_id": pr.id,
                 "domain": pkg.domain}, ctx.request_id))

    @app.post("/api/v1/ingress/{connector_id}/events")
    def ingress_event(connector_id: str, body: dict, ctx=Depends(auth)):
        """T2 推式事件摄入：认证主体必须是该connector（否则404不泄露存在性）；
        先持久化 InboxEvent 再 202；同(租户,连接器,外部ID,revision)同内容幂等
        200、异内容409；已知事件类型置 ROUTED 并按 _route_event 既有语义发
        OutboxEvent，未知类型保持 RECEIVED（不猜测路由）。"""
        p = ctx.require_principal()
        if not p.connector_id or p.connector_id != connector_id:
            raise ApiError("NOT_FOUND", "connector not found")
        with sf() as db:
            ci = db.get(ConnectorInstance, connector_id)
            if ci is None or ci.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "connector not found")
            ext_id = str((body or {}).get("external_event_id") or "")
            ext_rev = str((body or {}).get("external_revision") or "1")
            payload = (body or {}).get("payload")
            if not ext_id or not isinstance(payload, dict):
                raise ApiError("VALIDATION_FAILED",
                               "external_event_id and payload(object) required")
            digest = digest_of(payload)
            existing = (db.query(InboxEvent)
                        .filter_by(tenant_id=p.tenant_id,
                                   connector_id=connector_id,
                                   external_event_id=ext_id,
                                   external_revision=ext_rev).first())
            if existing is not None:
                if existing.digest != digest:
                    raise ApiError(
                        "IDEMPOTENCY_CONFLICT",
                        "same external_event_id/revision with different payload")
                return ok({"duplicate": True,
                           "inbox_event_id": existing.id}, ctx.request_id)
            etype = payload.get("event_type")
            routable = isinstance(etype, str) and \
                etype in _INGRESS_ROUTABLE
            ev = InboxEvent(
                id=new_uuid(), tenant_id=p.tenant_id, connector_id=connector_id,
                external_event_id=ext_id, external_revision=ext_rev,
                raw_asset_id=(body or {}).get("raw_asset_id"),
                payload=payload, digest=digest,
                processed_state="ROUTED" if routable else "RECEIVED")
            db.add(ev)
            db.flush()
            if routable:
                jobs_mod.emit(db, p.tenant_id, f"inbox:{ev.id}", etype, payload)
            audit(db, p, "ingress.event", f"inbox_event:{ev.id}",
                  new_ref={"external_event_id": ext_id,
                           "event_type": etype if isinstance(etype, str) else None,
                           "routed": routable})
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"inbox_event_id": ev.id, "processed_state": ev.processed_state},
                ctx.request_id))

    # ================================================================ 病理业务路由（插件提供；未启用不注册不import）
    if _pathology_enabled:
        import plugin_registry as _pathology_assembly
        from domains.pathology.routes import register as register_pathology
        register_pathology(app, sf, auth, _pathology_assembly)

    # ================================================================ 药品追溯路由（插件提供；未启用不注册不import）
    if _drug_trace_enabled:
        from domains.drug_trace.routes import register as register_drug_trace
        register_drug_trace(app, sf, auth, _loaded_registry)

    # ================================================================ 通用领域路由挂载（RT-02 单轨）
    # 任何业务插件在自己的 plugin.py 里 reg.attach("domain.routes", register)
    # 即自动挂载路由（当前：medins_emr 联调台）——核心 app.py 不再 import
    # 具体领域模块，新业务插件零改动接入。（注入的自定义 registry 未实现
    # capability_list 时按无领域路由处理，不阻断应用装配。）
    for _route_fn in (getattr(_loaded_registry, "capability_list", None)
                      and _loaded_registry.capability_list("domain.routes")
                      or []):
        _route_fn(app, sf, auth, _loaded_registry)

    # ================================================================ 上报业务目录路由（LT-01；核心能力，不依赖领域启用）
    from apps.core_api import report_target_routes
    report_target_routes.register(app, sf, auth, _hooks_registry)

    # ================================================================ 调度与告警路由（LT-04；旧 /api/v1/alerts 兼容不动）
    from apps.core_api import ops_routes
    ops_routes.register(app, sf, auth, _loaded_registry)

    # ================================================================ 用户与角色（LT-06A；R08）
    from apps.core_api import system_routes
    system_routes.register(app, sf, auth)

    # 单机部署便利：若前端构建产物存在，直接托管于 /ui（不影响API路由）
    _ui_dist = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "apps", "admin_web", "dist")
    if os.path.isdir(_ui_dist) and os.path.exists(os.path.join(_ui_dist, "index.html")):
        from fastapi.staticfiles import StaticFiles
        app.mount("/ui", StaticFiles(directory=_ui_dist, html=True), name="admin-ui")

    # ================================================================ 交换
    @app.get("/api/v1/submissions")
    def list_submissions(ctx=Depends(auth), limit: int = 50,
                         cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(Submission).filter_by(tenant_id=p.tenant_id)
            # RT-02：旧接口同样按 target_scopes 收缩——受限账号只返回授权
            # 业务（ReportTarget.destination_id 绑定）的提交；机器 token 与
            # 管理员（["*"]/platform_admin，生效范围不受限）行为不变。
            _sc = p.target_scopes_effective()
            if _sc is not None:
                _allowed_dests = [
                    t.destination_id for t in
                    db.query(ReportTarget)
                    .filter(ReportTarget.tenant_id == p.tenant_id,
                            ReportTarget.destination_id.isnot(None)).all()
                    if "*" in _sc or t.code in _sc]
                q = q.filter(Submission.destination_id.in_(
                    _allowed_dests or ["__none__"]))
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, Submission.created_at, Submission.id,
                                          cursor, cap, descending=True)
            out = []
            for s in rows:
                dest = db.get(Destination, s.destination_id)
                pr = db.get(PackageRevision, s.package_revision_id)
                out.append({
                    "id": s.id, "destination": dest.code if dest else None,
                    "state": s.state, "submission_revision": s.submission_revision,
                    "snapshot_digest": s.snapshot_digest[:12],
                    "package_revision": {"id": pr.id, "revision": pr.revision,
                                         "state": pr.state} if pr else None,
                    "idempotency_key": s.idempotency_key,
                    "created_at": s.created_at.isoformat()})
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.get("/api/v1/submissions/{sid}")
    def get_submission(sid: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            s = db.get(Submission, sid)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "submission not found")
            # RT-02：受限账号按 ReportTarget→Destination 绑定校验归属；未授权
            # 一律 NOT_FOUND（不泄露存在性；机器 token/管理员行为不变）
            _sc = p.target_scopes_effective()
            if _sc is not None and "*" not in _sc:
                _ok_dests = {
                    t.destination_id for t in
                    db.query(ReportTarget)
                    .filter(ReportTarget.tenant_id == p.tenant_id,
                            ReportTarget.code.in_(_sc),
                            ReportTarget.destination_id.isnot(None)).all()}
                if s.destination_id not in _ok_dests:
                    raise ApiError("NOT_FOUND", "submission not found")
            items = db.query(SubmissionItem).filter_by(submission_id=s.id).all()
            from core_db.models import DeliveryAttempt
            attempts = (db.query(DeliveryAttempt)
                        .filter_by(submission_id=s.id)
                        .order_by(DeliveryAttempt.started_at).all())
            receipts = (db.query(ReceiptEvent)
                        .filter_by(submission_ref=s.id).all())
            return ok({
                "id": s.id, "state": s.state,
                "items": [{"item_key": i.item_key, "state": i.state,
                           "asset_id": i.asset_id} for i in items],
                "attempts": [{"attempt_no": a.attempt_no,
                              "http_status": a.http_status,
                              "outcome": a.outcome,
                              "started_at": a.started_at.isoformat() if a.started_at else None}
                             for a in attempts],
                "receipts": [{"external_receipt_id": r.external_receipt_id,
                              "parsed_result": r.parsed_result,
                              "received_at": r.received_at.isoformat()} for r in receipts],
                "note": "ACCEPTED/CONFIRMED 需回执证据；下载≠医保业务确认",
            }, ctx.request_id)

    @app.post("/api/v1/submissions/{sid}/retry")
    def retry_submission(sid: str, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            s = db.get(Submission, sid)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "submission not found")
            if s.state not in (SubmissionState.UNKNOWN, SubmissionState.REJECTED,
                               "PARTIAL"):
                raise ApiError("CONFLICT", f"state {s.state} not retryable")
            _enqueue(db, p.tenant_id, "destination.query_unknown",
                                    s.id, {"submission_id": s.id})
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"queued": True}, ctx.request_id))

    @app.post("/api/v1/submissions/{sid}/corrections")
    def correction(sid: str, body: dict, ctx=Depends(auth)):
        """新建更正版本，不覆盖原内容（B07/D07）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_BUSINESS_REVIEWER, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            s = db.get(Submission, sid)
            if s is None or s.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "submission not found")
            pr = db.get(PackageRevision, s.package_revision_id)
            pkg = db.get(DataPackage, pr.package_id)
            # 冻结内容不可变：更正=基于同一package产生新修订（由病例重新冻结）
            case_id = (pr.provenance or {}).get("case_id")
            if case_id:
                _enqueue(db, p.tenant_id, "pathology.freeze_attempt",
                         f"case:{case_id}", {"case_id": case_id,
                                             "reason": "correction"})
            db.commit()
            audit(db, p, "submission.correction", f"submission:{sid}",
                  new_ref={"case_id": case_id}, reason=body.get("reason", ""))
            db.commit()
            return JSONResponse(status_code=202, content=ok(
                {"queued": True, "note": "更正将生成新package revision与新submission，"
                                         "不修改已冻结历史"}, ctx.request_id))

    @app.get("/api/v1/publications")
    def list_publications(ctx=Depends(auth), limit: int = 50,
                          cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(Publication).filter_by(tenant_id=p.tenant_id)
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, Publication.created_at, Publication.id,
                                          cursor, cap, descending=True)
            out = []
            for pub in rows:
                dest = db.get(Destination, pub.destination_id)
                items = db.query(PublicationItem).filter_by(
                    publication_id=pub.id).all()
                out.append({"id": pub.id, "state": pub.state,
                            "destination": dest.code if dest else None,
                            "generation_id": pub.generation_id,
                            "items": len(items),
                            "published_at": pub.published_at.isoformat()
                            if pub.published_at else None})
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.get("/api/v1/publications/{pid}/evidence")
    def publication_evidence(pid: str, ctx=Depends(auth)):
        """数据可见 / 下载证据 / 远端回执分别返回（04 §5.3）。
        回执必须精确关联本发布（tenant+destination+发布/提交引用），
        无法关联的回执保留待处理、不计入本发布证据（R02）。"""
        p = ctx.require_principal()
        with sf() as db:
            pub = db.get(Publication, pid)
            if pub is None or pub.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "publication not found")
            from core_db.models import DownloadEvidence
            # 下载证据按交换库 asset route 记录；发布清单引用这些 route
            routes = (pub.manifest_ref or {}).get("routes", [])
            item_ids = [i.id for i in db.query(PublicationItem)
                        .filter_by(publication_id=pub.id).all()]
            dl = (db.query(DownloadEvidence)
                  .filter(DownloadEvidence.publication_item_id.in_(
                      list(set(routes + item_ids)))).all())
            # 精确关联：本包修订在本目的地的提交集合 + 显式 publication_ref/载荷引用
            sub_ids = {s.id for s in db.query(Submission)
                       .filter_by(tenant_id=p.tenant_id,
                                  package_revision_id=pub.package_revision_id,
                                  destination_id=pub.destination_id).all()}

            def _receipt_matches(r: ReceiptEvent) -> bool:
                if r.publication_ref == pub.id:
                    return True
                if r.submission_ref and r.submission_ref in sub_ids:
                    return True
                return (r.raw_payload or {}).get("publication_id") == pub.id

            receipts = [r for r in db.query(ReceiptEvent)
                        .filter_by(tenant_id=p.tenant_id,
                                   destination_id=pub.destination_id).all()
                        if _receipt_matches(r)]
            return ok({
                "data_visible": pub.state == "PUBLISHED",
                "download_evidence": [{"principal": d.principal,
                                       "bytes": d.bytes_sent,
                                       "result": d.result,
                                       "at": d.at.isoformat()} for d in dl],
                "receipts": [{"external_receipt_id": r.external_receipt_id,
                              "parsed_result": r.parsed_result}
                             for r in receipts],
                "business_confirmed": any(
                    (r.parsed_result or {}).get("result") == "CONFIRMED"
                    for r in receipts),
                "note": "视图可见/完整下载/ACCEPTED受理均不等于医保业务确认",
            }, ctx.request_id)

    @app.post("/api/v1/receipts/{destination_id}")
    def ingest_receipt(destination_id: str, body: dict, ctx=Depends(auth)):
        """验证目标身份后持久化原始回执，幂等解析（04 §5.3）。
        ACCEPTED=传输受理；只有 CONFIRMED 语义推进业务确认（R01）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_SERVICE, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            dest = db.get(Destination, destination_id)
            if dest is None or dest.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "destination not found")
            ext_id = body.get("receipt_id", "")
            if not ext_id:
                raise ApiError("VALIDATION_FAILED", "receipt_id required")
            existing = (db.query(ReceiptEvent)
                        .filter_by(tenant_id=p.tenant_id,
                                   destination_id=destination_id,
                                   external_receipt_id=ext_id).first())
            if existing is not None:
                return ok({"receipt_id": ext_id, "duplicate": True}, ctx.request_id)
            sub = None
            key = body.get("submission_key")
            if key:
                sub = (db.query(Submission)
                       .filter_by(tenant_id=p.tenant_id,
                                  destination_id=destination_id,
                                  idempotency_key=key).first())
            ev = ReceiptEvent(id=new_uuid(), tenant_id=p.tenant_id,
                              destination_id=destination_id, external_receipt_id=ext_id,
                              submission_ref=sub.id if sub else None,
                              publication_ref=body.get("publication_id"),
                              raw_payload=body,
                              parsed_result={"result": body.get("result")},
                              occurred_at=utcnow())
            db.add(ev)
            if sub is not None:
                if body.get("result") == "CONFIRMED":
                    # 业务确认落地统一走共享helper（submission+病例分列一致；
                    # 功能测试发现此前API入口不更新case.target_confirmed）
                    import plugin_registry
                    plugin_registry.apply_business_confirmation(db, p.tenant_id,
                                                                sub.id)
                elif body.get("result") == "ACCEPTED":
                    sub.state = SubmissionState.ACCEPTED  # 受理≠业务成功
                # UNKNOWN/PARTIAL/REJECTED 不改写状态，留待逐项对账
            db.commit()
            return ok({"receipt_id": ext_id}, ctx.request_id)

    @app.post("/api/v1/reconciliations")
    def create_reconciliation(body: dict, ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_OPERATOR, ROLE_PLATFORM_ADMIN, ROLE_AUDITOR)
        with sf() as db:
            dest = (db.query(Destination)
                    .filter_by(tenant_id=p.tenant_id,
                               code=body.get("destination", "mock-push")).first())
            run = ReconciliationRun(
                id=new_uuid(), tenant_id=p.tenant_id,
                destination_id=dest.id if dest else "",
                scope=body.get("scope", {}), state="RUNNING")
            db.add(run)
            db.flush()
            # 差异：本地CONFIRMED vs 回执
            subs = (db.query(Submission)
                    .filter_by(tenant_id=p.tenant_id,
                               destination_id=dest.id if dest else "").all())
            diff = 0
            for s in subs:
                receipt = (db.query(ReceiptEvent)
                           .filter_by(submission_ref=s.id).first())
                actual = (receipt.parsed_result or {}).get("result") if receipt else None
                expected = s.state
                disposition = "ACCEPTED"
                if expected in ("ACCEPTED", "CONFIRMED") and actual is None:
                    disposition = "COMPENSATE"
                    diff += 1
                db.add(ReconciliationItem(
                    id=new_uuid(), tenant_id=p.tenant_id, run_id=run.id,
                    item_key=s.idempotency_key, expected_state=expected,
                    actual_state=actual or "NO_RECEIPT", disposition=disposition))
            run.state = "COMPLETE"
            run.summary = {"checked": len(subs), "differences": diff}
            run.finished_at = utcnow()
            db.commit()
            return ok({"reconciliation_id": run.id,
                       "summary": run.summary}, ctx.request_id)

    @app.get("/api/v1/reconciliations")
    def list_reconciliations(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            runs = (db.query(ReconciliationRun)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(ReconciliationRun.created_at.desc()).limit(20).all())
            return ok([{"id": r.id, "state": r.state, "scope": r.scope,
                        "summary": r.summary,
                        "created_at": r.created_at.isoformat()} for r in runs],
                      ctx.request_id)

    # ================================================================ 导入（CSV metrics）
    @app.post("/api/v1/imports/preview")
    def import_preview(body: dict, ctx=Depends(auth)):
        """限量脱敏预览、字段和逐行错误（04 §5.2）。"""
        p = ctx.require_principal()
        csv_text = body.get("csv", "")
        domain = body.get("domain", "metrics")
        rows, errors = _parse_metrics_csv(csv_text, limit=20)
        return ok({"domain": domain, "preview_rows": rows[:20],
                   "row_errors": errors, "total_preview": len(rows)}, ctx.request_id)

    @app.post("/api/v1/imports")
    def import_csv(body: dict, ctx=Depends(auth),
                   idempotency_key: str = Header(default="")):
        p = ctx.require_principal()
        p.require_any_role(ROLE_CONFIG_EDITOR, ROLE_OPERATOR, ROLE_PLATFORM_ADMIN)
        if not idempotency_key:
            raise ApiError("VALIDATION_FAILED", "Idempotency-Key required")
        with sf() as db:
            from contracts.api import idempotency_guard, idempotency_store
            rec, d = idempotency_guard(db, p, idempotency_key, body)
            if rec is not None:
                return ok(rec.response_body, ctx.request_id)
            rows, errors = _parse_metrics_csv(body.get("csv", ""), limit=10000)
            if not rows:
                raise ApiError("VALIDATION_FAILED", "no valid rows",
                               field_errors=errors[:20])
            pkg = DataPackage(id=new_uuid(), tenant_id=p.tenant_id, domain="metrics",
                              business_key=f"metrics:{body.get('business_key', 'monthly')}")
            db.add(pkg)
            db.flush()
            # 冻结 metrics 包（无患者字段；Decimal字符串保留）
            pr = PackageRevision(
                id=new_uuid(), tenant_id=p.tenant_id, package_id=pkg.id, revision=1,
                schema_ref={"key": "metrics.monthly", "version": "1.0"},
                payload={"rows": rows}, digest=digest_of({"rows": rows}),
                provenance={"import": "csv", "idempotency_key": idempotency_key},
                state=PackageRevisionState.FROZEN)
            db.add(pr)
            pkg.current_revision = 1
            jobs_mod.emit(db, p.tenant_id, f"package:{pkg.id}", "package.frozen", {
                "package_id": pkg.id, "domain": "metrics",
                "package_revision_id": pr.id, "rows": rows})
            idempotency_store(db, p, idempotency_key, d, 200,
                              {"package_id": pkg.id, "rows": len(rows)})
            db.commit()
            return ok({"package_id": pkg.id, "rows": len(rows),
                       "row_errors": errors[:20]}, ctx.request_id)

    # ================================================================ 资产/采集/操作/审计
    @app.get("/api/v1/assets")
    def list_assets(ctx=Depends(auth), state: str = "", limit: int = 50,
                    cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(Asset).filter_by(tenant_id=p.tenant_id)
            if state:
                q = q.filter_by(state=state)
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, Asset.created_at, Asset.id,
                                          cursor, cap, descending=True)
            out = [{"id": a.id, "acquisition_id": a.acquisition_id,
                    "role": a.role, "state": a.state,
                    "original_name": a.original_name,
                    "size_bytes": a.expected_size,
                    "sha256": a.expected_sha256[:12]} for a in rows]
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.get("/api/v1/acquisitions")
    def list_acquisitions(ctx=Depends(auth), limit: int = 50,
                          cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(Acquisition).filter_by(tenant_id=p.tenant_id)
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, Acquisition.created_at,
                                          Acquisition.id, cursor, cap,
                                          descending=True)
            out = []
            for a in rows:
                out.append({
                    "id": a.id, "external_event_id": a.external_event_id,
                    "source_scan_id": a.source_scan_id, "state": a.state,
                    "barcode_raw": (a.manifest.get("identity_hints") or {}).get(
                        "barcode_raw"),
                    "created_at": a.created_at.isoformat()})
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.get("/api/v1/acquisitions/{acquisition_id}")
    def get_acquisition(acquisition_id: str, ctx=Depends(auth)):
        """采集详情 + 当前决定版本（人工决定 If-Match 的CAS前置读取）。"""
        p = ctx.require_principal()
        with sf() as db:
            a = (db.query(Acquisition)
                 .filter_by(id=acquisition_id, tenant_id=p.tenant_id).first())
            if a is None:
                raise ApiError("NOT_FOUND", "acquisition not found")
            version = (db.query(ManualDecision)
                       .filter_by(tenant_id=p.tenant_id,
                                  entity_ref=f"acquisition:{a.id}").count())
            return ok({"id": a.id, "state": a.state,
                       "source_scan_id": a.source_scan_id,
                       "barcode_raw": (a.manifest.get("identity_hints") or {}).get(
                           "barcode_raw"),
                       "decision_version": version,
                       "created_at": a.created_at.isoformat()}, ctx.request_id)

    @app.get("/api/v1/operations")
    def list_operations(ctx=Depends(auth), limit: int = 50,
                        cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            q = db.query(Operation).filter_by(tenant_id=p.tenant_id)
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, Operation.created_at, Operation.id,
                                          cursor, cap, descending=True)
            out = [{"id": o.id, "type": o.op_type, "state": o.state,
                    "subject_ref": o.subject_ref} for o in rows]
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.get("/api/v1/operations/{operation_id}")
    def get_operation(operation_id: str, ctx=Depends(auth)):
        with sf() as db:
            o = db.get(Operation, operation_id)
            if o is None or o.tenant_id != ctx.require_principal().tenant_id:
                raise ApiError("NOT_FOUND", "operation not found")
            return ok({"operation_id": o.id, "type": o.op_type, "state": o.state,
                       "result": o.result, "error": o.error}, ctx.request_id)

    @app.get("/api/v1/audit")
    def list_audit(ctx=Depends(auth), limit: int = 100, action: str = "",
                   cursor: str | None = None,
                   actor: str = "", action_prefix: str = "",
                   target: str = "", at_from: str = "", at_to: str = ""):
        """LT-06A 扩展：新增 actor/action_prefix(前缀)/target(前缀)/at_from/
        at_to 过滤与 cursor 分页；不带新参数时行为与旧版完全一致
        （action 仍为精确匹配——前缀检索用 action_prefix，不改变旧语义）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_AUDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            q = db.query(AuditEvent).filter_by(tenant_id=p.tenant_id)
            if action:
                q = q.filter_by(action=action)
            if actor:
                q = q.filter(AuditEvent.actor.like(actor + "%"))
            if action_prefix:
                q = q.filter(AuditEvent.action.like(action_prefix + "%"))
            if target:
                q = q.filter(AuditEvent.target.like(target + "%"))
            from datetime import datetime as _dt
            for v, is_begin in ((at_from, True), (at_to, False)):
                if v:
                    try:
                        t = _dt.fromisoformat(str(v))
                    except ValueError:
                        raise ApiError("VALIDATION_FAILED",
                                       "时间参数须 ISO 格式：" + repr(v))
                    q = q.filter(AuditEvent.at >= t if is_begin
                                 else AuditEvent.at < t)
            cap = min(200, limit)
            # 排序键=at（该表时间列名），游标分页契约与其余列表一致
            rows, has_more = _keyset_page(q, AuditEvent.at, AuditEvent.id,
                                          cursor, cap, descending=True)
            out = [{"id": e.id, "actor": e.actor, "action": e.action,
                    "target": e.target, "reason": e.reason,
                    "at": e.at.isoformat()} for e in rows]
            if cursor is None:
                return ok(out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more, "at")},
                      ctx.request_id)

    @app.get("/api/v1/audit/export")
    def audit_export(ctx=Depends(auth), limit: int = 10000,
                     action: str = "", actor: str = "",
                     action_prefix: str = "", target: str = "",
                     at_from: str = "", at_to: str = ""):
        """审计导出 CSV（LT-06A；auditor/platform_admin）：UTF-8 BOM、字段
        转义并防 Excel 公式注入；≤10000 行超出 400；导出动作本身写审计
        audit.export。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_AUDITOR, ROLE_PLATFORM_ADMIN)
        import csv as _csv
        import io as _io
        with sf() as db:
            q = db.query(AuditEvent).filter_by(tenant_id=p.tenant_id)
            if action:
                q = q.filter_by(action=action)
            if actor:
                q = q.filter(AuditEvent.actor.like(actor + "%"))
            if action_prefix:
                q = q.filter(AuditEvent.action.like(action_prefix + "%"))
            if target:
                q = q.filter(AuditEvent.target.like(target + "%"))
            from datetime import datetime as _dt
            for v, is_begin in ((at_from, True), (at_to, False)):
                if v:
                    try:
                        t = _dt.fromisoformat(str(v))
                    except ValueError:
                        raise ApiError("VALIDATION_FAILED",
                                       "时间参数须 ISO 格式：" + repr(v))
                    q = q.filter(AuditEvent.at >= t if is_begin
                                 else AuditEvent.at < t)
            n = q.count()
            if n > 10000:
                raise ApiError("VALIDATION_FAILED",
                               "匹配 " + str(n) + " 行超过导出上限 10000，"
                               "请缩小范围（用 actor/action/target/时间过滤）")
            rows = q.order_by(AuditEvent.at.desc(), AuditEvent.id.desc()) \
                .limit(min(max(1, limit), 10000)).all()
            buf = _io.StringIO()
            w = _csv.writer(buf, lineterminator="\r\n")
            w.writerow(["id", "at", "actor", "action", "target", "reason"])

            def _safe_cell(v):
                # 防 Excel 公式注入：以 =+-@ 或 Tab 开头的单元格前置单引号
                s = str(v if v is not None else "")
                if s[:1] in ("=", "+", "-", "@") or s[:1] == chr(9):
                    return "'" + s
                return s

            for e in rows:
                w.writerow([_safe_cell(e.id), _safe_cell(e.at.isoformat()),
                            _safe_cell(e.actor), _safe_cell(e.action),
                            _safe_cell(e.target), _safe_cell(e.reason)])
            audit(db, p, "audit.export", "audit_event:*",
                  new_ref={"rows": len(rows), "actor": actor,
                           "action_prefix": action_prefix, "target": target,
                           "at_from": at_from, "at_to": at_to})
            db.commit()
        from fastapi import Response
        data = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
        return Response(
            content=data, media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     'attachment; filename="audit-export.csv"'})

    @app.get("/api/v1/audit/{event_id}")
    def audit_detail(event_id: str, ctx=Depends(auth)):
        """审计详情（LT-06A）：返回 old_ref/new_ref 全文（列表不返回）。"""
        p = ctx.require_principal()
        p.require_any_role(ROLE_AUDITOR, ROLE_PLATFORM_ADMIN)
        with sf() as db:
            e = db.get(AuditEvent, event_id)
            if e is None or e.tenant_id != p.tenant_id:
                raise ApiError("NOT_FOUND", "audit event not found")
            return ok({"id": e.id, "actor": e.actor, "action": e.action,
                       "target": e.target, "reason": e.reason,
                       "at": e.at.isoformat(),
                       "old_ref": e.old_ref or None,
                       "new_ref": e.new_ref or None}, ctx.request_id)

    @app.get("/api/v1/external-blockers")
    def list_blockers(ctx=Depends(auth)):
        p = ctx.require_principal()
        with sf() as db:
            rows = (db.query(ExternalBlocker)
                    .filter_by(tenant_id=p.tenant_id)
                    .order_by(ExternalBlocker.blocker_code).all())
            return ok([{"code": b.blocker_code, "title": b.title,
                        "owner_party": b.owner_party, "impact": b.impact,
                        "status": b.status} for b in rows], ctx.request_id)

    @app.get("/api/v1/alerts")
    def list_alerts(ctx=Depends(auth), limit: int = 50,
                    cursor: str | None = None):
        p = ctx.require_principal()
        with sf() as db:
            # RT-03 S08：过滤/范围防线/严重级归一与 alerts2 共享同一助手
            # （RT-02 语义不变：受限账号只见授权业务，系统级不返回；机器
            # token 与 ["*"]/platform_admin 行为 100% 不变）；数组契约保持。
            from apps.core_api.ops_routes import (alerts_base_query,
                                                  normalize_severity)
            q = alerts_base_query(db, p)
            cap = min(200, limit)
            rows, has_more = _keyset_page(q, AlertEvent.created_at, AlertEvent.id,
                                          cursor, cap, descending=True)
            out = [{"id": r.id, "alert_type": r.alert_type,
                    "severity": normalize_severity(r.severity),
                    "message": r.message,
                    "target_ref": r.target_ref, "state": r.state,
                    "created_at": r.created_at.isoformat()} for r in rows]
            # 动态告警（设备离线）仅在首页（无cursor）前插：不入库、不参与游标
            dynamic = []
            if cursor is None:
                agents = db.query(Agent).filter_by(tenant_id=p.tenant_id).all()
                for a in agents:
                    if a.last_seen and (utcnow() - a.last_seen) > timedelta(minutes=5):
                        dynamic.append({"alert_type": "AGENT_OFFLINE",
                                        "severity": "warning",
                                        "message": f"设备 {a.device_code} "
                                                   f"超过5分钟未心跳",
                                        "created_at": a.last_seen.isoformat(),
                                        "state": "OPEN", "id": f"dyn-{a.id}"})
            if cursor is None:
                return ok(dynamic + out, ctx.request_id)
            return ok({"items": out,
                       "next_cursor": _next_cursor(rows, has_more)},
                      ctx.request_id)

    @app.get("/api/v1/field-lineage")
    def field_lineage(ctx=Depends(auth), case_id: str = ""):
        """字段血缘（S15：真实数据，非静态占位）。

        - design：设计基线映射（10 §3）——标注为设计文档来源；
        - configured：租户内每个 mapping_key 最新 revision 的真实血缘
          （target ← source_field，含转换 op 与字典引用）；
        - evaluated：给 case_id 时，该病例当前冻结包修订 + 索引记录的
          rule_versions / dictionary_revisions（实际生效的规则版本）。
        """
        p = ctx.require_principal()
        with sf() as db:
            configured = []
            keys = [r[0] for r in db.query(MappingRevision.mapping_key)
                    .filter_by(tenant_id=p.tenant_id).distinct().all()]
            for key in sorted(keys):
                q = (db.query(MappingRevision)
                     .filter_by(tenant_id=p.tenant_id, mapping_key=key))
                m = (q.filter_by(state="ACTIVE")
                     .order_by(MappingRevision.revision.desc()).first())
                if m is None:
                    m = q.order_by(MappingRevision.revision.desc()).first()
                if m is None:
                    continue
                fields = []
                for idx, op in enumerate((m.rules or {}).get("ops", [])):
                    op = op if isinstance(op, dict) else {}
                    fields.append({
                        "target": op.get("target", f"out_{idx}"),
                        "source_field": op.get("source_field") or "",
                        "op": op.get("op", ""),
                        "dictionary": op.get("dictionary") or ""})
                configured.append({"mapping_key": key, "revision": m.revision,
                                   "state": m.state, "fields": fields})
            evaluated = None
            if case_id:
                evaluated = {"case_id": case_id, "found": False}
                if _pathology_enabled:
                    from domains.pathology.models import (PathologyCase,
                                                          PathologyIndexRecord)
                    case = db.get(PathologyCase, case_id)
                    if case is not None and case.tenant_id == p.tenant_id:
                        evaluated["found"] = True
                        business_key = (f"{case.registration_namespace}:"
                                        f"{case.source_case_id}")
                        pkg = (db.query(DataPackage)
                               .filter_by(tenant_id=p.tenant_id, domain="pathology",
                                          business_key=business_key).first())
                        if pkg is not None:
                            pr = (db.query(PackageRevision)
                                  .filter_by(package_id=pkg.id,
                                             revision=pkg.current_revision)
                                  .first())
                            if pr is not None:
                                evaluated["package_id"] = pkg.id
                                evaluated["package_revision"] = pr.revision
                                evaluated["package_state"] = pr.state
                                evaluated["payload_keys"] = sorted(
                                    (pr.payload or {}).keys())
                        idx_rec = (db.query(PathologyIndexRecord)
                                   .filter_by(tenant_id=p.tenant_id,
                                              case_id=case.id)
                                   .order_by(PathologyIndexRecord.created_at.desc())
                                   .first())
                        if idx_rec is not None:
                            evaluated["index_status"] = idx_rec.status
                            evaluated["rule_versions"] = idx_rec.rule_versions or {}
                            evaluated["dictionary_revisions"] = \
                                idx_rec.dictionary_revisions or {}
                            # 索引8段原值经掩码展示（不回传完整证件号）
                            evaluated["index_display"] = idx_rec.display_value
            return ok({"design": _DESIGN_LINEAGE, "configured": configured,
                       "evaluated": evaluated}, ctx.request_id)

    def _parse_metrics_csv(csv_text: str, limit: int):
        import decimal
        rows, errors = [], []
        reader = csv.DictReader(io.StringIO(csv_text))
        for i, row in enumerate(reader):
            try:
                amount = str(decimal.Decimal(row["金额"]).quantize(decimal.Decimal("0.01")))
                rows.append({"org_code": row["机构"].strip(),
                             "month": row["月份"].strip(),
                             "item": row["项目"].strip(),
                             "quantity": int(row["数量"]),
                             "amount": amount})
            except (KeyError, ValueError, decimal.InvalidOperation,
                    decimal.DecimalException) as exc:
                errors.append({"row": i + 2, "error": str(exc)})
            if len(rows) >= limit:
                break
        return rows, errors

    return app


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app
