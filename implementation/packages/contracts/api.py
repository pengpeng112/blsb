"""API 公共设施：认证上下文、请求ID、幂等、审计、分页（04 §4）。"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from . import ApiError, new_uuid

ROLE_PLATFORM_ADMIN = "platform_admin"
ROLE_CONFIG_EDITOR = "config_editor"
ROLE_BUSINESS_REVIEWER = "business_reviewer"
ROLE_OPERATOR = "operator"
ROLE_AUDITOR = "auditor"
ROLE_SERVICE = "service"

ALL_ROLES = {ROLE_PLATFORM_ADMIN, ROLE_CONFIG_EDITOR, ROLE_BUSINESS_REVIEWER,
             ROLE_OPERATOR, ROLE_AUDITOR, ROLE_SERVICE}


@dataclass
class Principal:
    subject_id: str
    tenant_id: str
    kind: str
    subject_code: str
    roles: list[str] = field(default_factory=list)
    agent_id: str | None = None
    connector_id: str | None = None
    # LT-06A（R08）：用户类主体的上报业务授权范围。None=未初始化（按无业务
    # fail-closed）；["*"]=全部（仅 018 迁移对既有账号兼容赋值）；[]=无业务
    # （新建账号默认）；具体 code 列表=仅这些业务。机器主体不受 scope 约束。
    target_scopes: list[str] | None = None

    def require_any_role(self, *roles: str) -> None:
        if ROLE_PLATFORM_ADMIN in self.roles:
            return
        if not any(r in self.roles for r in roles):
            raise ApiError("FORBIDDEN", f"requires role {'/'.join(roles)}")

    # ---------------------------------------------------- 业务范围（LT-06A/R08）
    def target_scopes_effective(self) -> list[str] | None:
        """生效范围：None=不受限（platform_admin 或机器主体——既有生产鉴权
        零改变）；list=仅这些业务（[]/None 列值=无业务，fail-closed）。"""
        if self.kind != "user":
            return None
        if ROLE_PLATFORM_ADMIN in self.roles:
            return None
        return list(self.target_scopes or [])

    def require_target_scope(self, target_code: str) -> None:
        """业务级读写/详情/聚合的范围校验（R08：不只过滤列表）。

        未授权（含未知 code）一律 FORBIDDEN——先于存在性校验调用以避免
        泄露目标是否存在。"""
        sc = self.target_scopes_effective()
        if sc is None:
            return
        if "*" in sc or target_code in sc:
            return
        raise ApiError("FORBIDDEN",
                       f"当前账号未被授权业务 {target_code!r}"
                       f"（授权范围：{'/'.join(sc) if sc else '（空）'}）")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


class RequestContext:
    def __init__(self, request: Request):
        self.request = request
        self.request_id = request.headers.get("X-Request-ID") or new_uuid()
        self.principal: Principal | None = None
        self.session_id: str | None = None

    def require_principal(self) -> Principal:
        if self.principal is None:
            raise ApiError("UNAUTHORIZED", "missing principal")
        return self.principal


def make_auth_dependency(session_factory):
    """FastAPI 依赖：解析 Bearer token → AuthSubject → Principal。"""

    def _dep(request: Request, authorization: str | None = Header(default=None)) -> RequestContext:
        ctx = RequestContext(request)
        if not authorization or not authorization.startswith("Bearer "):
            raise ApiError("UNAUTHORIZED", "missing bearer token")
        token = authorization[len("Bearer "):].strip()
        db: Session = session_factory()
        try:
            from core_db.models import AuthSubject, ConsoleSession, Tenant
            from core_db.engine import utcnow
            subject = db.query(AuthSubject).filter_by(token_hash=hash_token(token), status="ACTIVE").first()
            if subject is None:
                session = (db.query(ConsoleSession)
                           .filter(ConsoleSession.token_hash == hash_token(token),
                                   ConsoleSession.revoked_at.is_(None),
                                   ConsoleSession.expires_at > utcnow()).first())
                if session is not None:
                    subject = (db.query(AuthSubject)
                               .filter_by(id=session.subject_id, tenant_id=session.tenant_id,
                                          kind="user", status="ACTIVE").first())
                    tenant = db.get(Tenant, session.tenant_id)
                    if tenant is None or tenant.status != "ACTIVE":
                        subject = None
                    if subject is not None:
                        ctx.session_id = session.id
            if subject is None:
                raise ApiError("UNAUTHORIZED", "invalid token")
            ctx.principal = Principal(
                subject_id=subject.id, tenant_id=subject.tenant_id, kind=subject.kind,
                subject_code=subject.subject_code, roles=list(subject.roles or []),
                agent_id=subject.agent_id, connector_id=subject.connector_id,
                target_scopes=getattr(subject, "target_scopes", None))
            request.state.ctx = ctx
            return ctx
        finally:
            db.close()

    return _dep


def idempotency_guard(db: Session, principal: Principal, key: str, request_obj) -> "object | None":
    """同键同内容 → 返回缓存响应；同键不同内容 → 409（04 §4）。"""
    from core_db.models import IdempotencyRecord
    rec = db.query(IdempotencyRecord).filter_by(
        tenant_id=principal.tenant_id, subject=principal.subject_code, idempotency_key=key).first()
    d = digest_of(request_obj)
    if rec is None:
        return None, d
    if rec.request_digest != d:
        raise ApiError("IDEMPOTENCY_CONFLICT", "idempotency key bound to different content")
    return rec, d


def idempotency_store(db: Session, principal: Principal, key: str, digest: str,
                      status: int, body: dict) -> None:
    from core_db.models import IdempotencyRecord
    db.add(IdempotencyRecord(tenant_id=principal.tenant_id, subject=principal.subject_code,
                             idempotency_key=key, request_digest=digest,
                             response_status=status, response_body=body))


def audit(db: Session, principal: Principal | None, action: str, target: str, *,
          old_ref: dict | None = None, new_ref: dict | None = None,
          reason: str = "", trace_id: str = "") -> None:
    from core_db.models import AuditEvent
    db.add(AuditEvent(
        tenant_id=principal.tenant_id if principal else "",
        actor=f"{principal.kind}:{principal.subject_code}" if principal else "system",
        action=action, target=target,
        old_ref=old_ref or {}, new_ref=new_ref or {},
        reason=reason, trace_id=trace_id))


def page_params(limit: int = 50, cursor: str | None = None) -> tuple[int, str | None]:
    limit = max(1, min(200, limit))
    return limit, cursor


# ================================================================ RT-03 S08 严格 keyset 分页
# 与 plugins/report_ledger.py 的台账游标（v2、坏游标回退首页）不同：本工具为
# RT-03 四接口的**严格校验**分页——坏 base64/字段数/版本/时间/ID/大小一律
# 400 INVALID_CURSOR；上下文（资源/租户/筛选）不匹配 400 CURSOR_CONTEXT_
# MISMATCH。游标绑定接口资源、租户与全部筛选上下文；分页不是安全授权，
# 解码后调用方仍必须重新做 RBAC 与租户筛选。

_CURSOR_VERSION = "kc1"
_CURSOR_MAX_TOKENS = 6
_CURSOR_MAX_RAW_LEN = 512
_CURSOR_MAX_ID_LEN = 64


def _cursor_ctx_hash(context: dict) -> str:
    """上下文绑定哈希（sha256(canonical sorted json)[:16]）。"""
    return digest_of(context or {})[:16]


def keyset_cursor_encode(*, resource: str, tenant_id: str, context: dict,
                         ts, row_id: str) -> str:
    """v=kc1|resource|tenant|ctx_hash|ts_iso|id 的 base64（urlsafe）游标。

    ts 为 DB 取出的 datetime（naive/aware 均以 isoformat 原样编码——比较时
    与同列存储格式一致）；row_id 为最后一条**实际返回**记录的 id。"""
    ts_iso = ts.isoformat() if isinstance(ts, datetime) else str(ts)
    raw = "|".join((_CURSOR_VERSION, str(resource), str(tenant_id),
                    _cursor_ctx_hash(context), ts_iso, str(row_id)))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def keyset_cursor_decode(cursor: str, *, resource: str, tenant_id: str,
                         context: dict) -> tuple[datetime, str]:
    """严格解码：(ts_datetime, row_id)。任何非法形态抛 INVALID_CURSOR；
    resource/tenant/上下文哈希不匹配抛 CURSOR_CONTEXT_MISMATCH。
    与 report_ledger.decode_cursor 的"坏游标当首页"行为**有意不同**。"""
    from contracts import ApiError
    if not cursor or len(cursor) > _CURSOR_MAX_RAW_LEN:
        raise ApiError("INVALID_CURSOR", "游标缺失或超长")
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 —— 非 base64/非 ASCII
        raise ApiError("INVALID_CURSOR", "游标不是合法 base64")
    parts = raw.split("|")
    if len(parts) != _CURSOR_MAX_TOKENS:
        raise ApiError("INVALID_CURSOR",
                       f"游标字段数应为 {_CURSOR_MAX_TOKENS}，当前 {len(parts)}")
    version, res, tenant, ctx_hash, ts_iso, rid = parts
    if version != _CURSOR_VERSION:
        raise ApiError("INVALID_CURSOR", f"游标版本 {version!r} 不受支持")
    if res != str(resource) or tenant != str(tenant_id):
        raise ApiError("CURSOR_CONTEXT_MISMATCH",
                       "游标与当前资源/租户不匹配（须从首页重新查询）")
    if ctx_hash != _cursor_ctx_hash(context):
        raise ApiError("CURSOR_CONTEXT_MISMATCH",
                       "游标与当前筛选上下文不匹配（筛选变化后须从首页重新查询）")
    if not ts_iso or len(rid) > _CURSOR_MAX_ID_LEN or not rid:
        raise ApiError("INVALID_CURSOR", "游标时间/ID 非法")
    try:
        ts = datetime.fromisoformat(ts_iso)
    except ValueError:
        raise ApiError("INVALID_CURSOR", "游标时间不是合法 ISO 格式")
    return ts, rid


def keyset_condition(ts_col, id_col, ts: datetime, rid: str, *,
                     descending: bool = True):
    """数据库内执行 keyset 条件：ts < c_ts OR (ts=c_ts AND id<c_id)（DESC）。
    不能取全表后 Python 截断、不能用 OFFSET 冒充游标。"""
    from sqlalchemy import and_, or_
    if descending:
        return or_(ts_col < ts, and_(ts_col == ts, id_col < rid))
    return or_(ts_col > ts, and_(ts_col == ts, id_col > rid))


def keyset_next_cursor(rows, has_more, *, resource: str, tenant_id: str,
                       context: dict, ts_attr: str,
                       id_attr: str = "id") -> str | None:
    """next_cursor 只由最后一条实际返回记录生成（空页/无更多页 → None）。"""
    if not has_more or not rows:
        return None
    last = rows[-1]
    return keyset_cursor_encode(
        resource=resource, tenant_id=tenant_id, context=context,
        ts=getattr(last, ts_attr), row_id=getattr(last, id_attr))


def normalize_severity(raw) -> str:
    """RT-03 S08：severity 按 lower 值归一——warning/warn/WARN/Warning →
    WARN；统一返回 INFO/WARN/CRITICAL（未知值按存储默认 WARN 兜底）。"""
    s = str(raw or "").strip().lower()
    if s == "warning":
        s = "warn"
    if s not in ("info", "warn", "critical"):
        s = "warn"
    return s.upper()


def severity_filter_set(raw) -> list[str]:
    """severity 筛选参数 → 存储值集合（WARN 覆盖 warn/warning 两种存储）。"""
    s = str(raw or "").strip().lower()
    if s == "warning":
        s = "warn"
    if s == "warn":
        return ["warn", "warning"]
    if s in ("info", "critical"):
        return [s]
    return []


def install_error_handlers(app) -> None:
    """把 ApiError 统一转换为 04 §4 的错误信封（所有API应用装配时调用）。"""
    from fastapi.responses import JSONResponse

    @app.exception_handler(ApiError)
    def _api_error(request, exc: ApiError):
        request_id = ""
        ctx = getattr(request.state, "ctx", None)
        if ctx is not None:
            request_id = ctx.request_id
        return JSONResponse(status_code=exc.status, content=exc.to_body(request_id))

    @app.exception_handler(Exception)
    def _unhandled(request, exc: Exception):  # noqa: BLE001
        import traceback
        from fastapi.responses import JSONResponse as JR
        return JR(status_code=500, content={
            "error": {"code": "INTERNAL_ERROR",
                      "message": f"{type(exc).__name__}: {exc}",
                      "retryable": False, "field_errors": [],
                      "details_ref": {"trace": traceback.format_exc()[-800:]}},
            "request_id": getattr(getattr(request.state, "ctx", None),
                                  "request_id", "")})
