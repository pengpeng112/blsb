"""API 公共设施：认证上下文、请求ID、幂等、审计、分页（04 §4）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

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

    def require_any_role(self, *roles: str) -> None:
        if ROLE_PLATFORM_ADMIN in self.roles:
            return
        if not any(r in self.roles for r in roles):
            raise ApiError("FORBIDDEN", f"requires role {'/'.join(roles)}")


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
            from core_db.models import AuthSubject
            subject = db.query(AuthSubject).filter_by(token_hash=hash_token(token), status="ACTIVE").first()
            if subject is None:
                raise ApiError("UNAUTHORIZED", "invalid token")
            ctx.principal = Principal(
                subject_id=subject.id, tenant_id=subject.tenant_id, kind=subject.kind,
                subject_code=subject.subject_code, roles=list(subject.roles or []),
                agent_id=subject.agent_id, connector_id=subject.connector_id)
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
