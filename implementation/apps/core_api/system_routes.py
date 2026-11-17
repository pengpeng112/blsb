"""用户与角色管理 API（LT-06A；R08；均 platform_admin，写操作全部审计）。

复用 contracts.passwords 的哈希/校验/用户名规范化——不另写一套。
- 列表不返回任何 hash/token；
- 新建：target_scopes 默认 []（无业务，管理员显式授权）；roles 禁止 service；
- 最后 platform_admin 保护（按 tenant；行锁串行化并发移除）；
- 重置密码/锁定撤销该用户全部会话（权限即时生效）。
"""
from __future__ import annotations

import re

from fastapi import Depends

from contracts import ok
from contracts.api import (ALL_ROLES, ROLE_PLATFORM_ADMIN, ROLE_SERVICE,  # noqa: F401
                           audit, hash_token)
from core_db.engine import utcnow
from core_db.models import (AuthSubject, ConsoleAccount, ConsoleSession,
                            ReportTarget)

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_LOCK_FAR_FUTURE_DAYS = 3650


def _account_view(account: ConsoleAccount, subject: AuthSubject) -> dict:
    now = utcnow()
    return {
        "username": account.username,
        "subject_id": subject.id,
        "display_name": subject.display_name,
        "roles": list(subject.roles or []),
        "target_scopes": list(subject.target_scopes or []),
        "status": subject.status,
        "locked": bool(account.locked_until and account.locked_until > now),
        "locked_until": account.locked_until.isoformat()
        if account.locked_until else None,
        "password_changed_at": account.password_changed_at.isoformat(),
        "created_at": account.created_at.isoformat(),
        "has_password_session": True,
    }


def _get_user(db, tenant_id: str, username: str):
    account = (db.query(ConsoleAccount)
               .filter_by(tenant_id=tenant_id, username=username).first())
    if account is None:
        from contracts import ApiError
        raise ApiError("NOT_FOUND", "user not found")
    subject = db.get(AuthSubject, account.subject_id)
    if subject is None or subject.kind != "user":
        from contracts import ApiError
        raise ApiError("NOT_FOUND", "user subject missing")
    return account, subject


def _validate_scopes(db, tenant_id: str, scopes) -> list[str]:
    """范围取值校验：每项须为存在的 ReportTarget code 或 "*"（R08：未知拒绝）。
    返回规范化列表（去重保序）。"""
    from contracts import ApiError
    if scopes is None:
        return []
    if not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes):
        raise ApiError("VALIDATION_FAILED",
                       "target_scopes 必须为字符串数组（ReportTarget code 或 \"*\"）")
    out: list[str] = []
    known = {t.code for t in db.query(ReportTarget)
             .filter_by(tenant_id=tenant_id).all()}
    for s in scopes:
        if s == "*":
            if "*" in out:
                continue
            out.append("*")
            continue
        if s not in known:
            raise ApiError("VALIDATION_FAILED",
                           f"未知业务编码 {s!r}（target_scopes 只允许已登记"
                           " ReportTarget code 或 \"*\"）")
        if s not in out:
            out.append(s)
    return out


def _assert_not_last_active_admin(db, tenant_id: str,
                                  excluding_subject_id: str) -> None:
    """RT-02：最后活跃平台管理员保护（并发安全版）。

    旧实现只 FOR UPDATE 被改那一行，两个管理员并发互相/自我降权时都会读到
    剩余管理员 ≥1 并同时提交，导致全租户 platform_admin 归零。本实现：
    - 按 id 升序锁定该租户全部 ACTIVE 用户主体行（PostgreSQL 行锁覆盖全体
      候选管理员，并发降权事务被串行化，后者必然看到前者已提交的角色变更）；
    - 内存过滤 ROLE_PLATFORM_ADMIN 且 ConsoleAccount 未处于锁定态
      （locked_until 为空或已过期）的活跃管理员——锁定中的账号不充数。
    本次操作（降权/锁定 excluding_subject_id）后剩余数 < 1 → 409 CONFLICT。
    """
    from contracts import ApiError
    now = utcnow()
    admins = (db.query(AuthSubject)
              .filter(AuthSubject.tenant_id == tenant_id,
                      AuthSubject.kind == "user",
                      AuthSubject.status == "ACTIVE")
              .order_by(AuthSubject.id)
              .with_for_update().all())
    survivors = 0
    for s in admins:
        if s.id == excluding_subject_id:
            continue
        if ROLE_PLATFORM_ADMIN not in (s.roles or []):
            continue
        acc = (db.query(ConsoleAccount)
               .filter_by(subject_id=s.id).first())
        if acc is not None and acc.locked_until is not None \
                and acc.locked_until > now:
            continue                     # 锁定中的管理员不充数
        survivors += 1
    if survivors < 1:
        raise ApiError("CONFLICT", "不能移除或锁定最后一名活跃平台管理员"
                                   "（先提升/解锁其他管理员）")


def register(app, sf, auth):
    from fastapi import Body
    from contracts import ApiError, new_uuid
    from contracts.passwords import (hash_password, normalize_username,
                                     validate_password)

    @app.get("/api/v1/system/users")
    def list_users(ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            rows = (db.query(ConsoleAccount, AuthSubject)
                    .join(AuthSubject, AuthSubject.id == ConsoleAccount.subject_id)
                    .filter(ConsoleAccount.tenant_id == p.tenant_id,
                            AuthSubject.kind == "user")
                    .order_by(ConsoleAccount.username).all())
            return ok([_account_view(a, s) for a, s in rows], ctx.request_id)

    @app.post("/api/v1/system/users")
    def create_user(body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        raw_username = str(body.get("username", "") or "")
        if not _USERNAME_RE.fullmatch(raw_username):
            # 先自有校验（422），再 normalize（不会抛 ValueError→500）
            raise ApiError("VALIDATION_FAILED",
                           "username 须为 3—32 位字母/数字/下划线")
        username = normalize_username(raw_username)
        password = body.get("password")
        if not isinstance(password, str):
            raise ApiError("VALIDATION_FAILED", "password 必填")
        validate_password(password)                      # 复用现有强度校验
        roles = body.get("roles") or []
        if not isinstance(roles, list) or not roles or \
                not set(roles) <= ALL_ROLES or ROLE_SERVICE in roles:
            raise ApiError("VALIDATION_FAILED",
                           "roles 必须为非空合法角色集合且不允许分配 service")
        with sf() as db:
            if db.query(ConsoleAccount).filter_by(username=username).first() \
                    is not None:
                raise ApiError("CONFLICT", "username already exists")
            scopes = _validate_scopes(db, p.tenant_id, body.get("target_scopes"))
            code = "console-" + username
            if db.query(AuthSubject).filter_by(tenant_id=p.tenant_id,
                                               subject_code=code).first() \
                    is not None:
                raise ApiError("CONFLICT", "subject code already in use")
            import secrets as _secrets
            subject = AuthSubject(
                id=new_uuid(), tenant_id=p.tenant_id, kind="user",
                subject_code=code,
                display_name=str(body.get("display_name", "") or ""),
                token_hash=hash_token(_secrets.token_urlsafe(48)),
                roles=list(roles), target_scopes=scopes, status="ACTIVE")
            db.add(subject)
            db.flush()
            account = ConsoleAccount(
                id=new_uuid(), tenant_id=p.tenant_id, subject_id=subject.id,
                username=username, password_hash=hash_password(password))
            db.add(account)
            audit(db, p, "system.user_created", f"console_account:{account.id}",
                  new_ref={"username": username, "roles": list(roles),
                           "target_scopes": scopes})
            db.commit()
            return ok(_account_view(account, subject), ctx.request_id)

    @app.put("/api/v1/system/users/{username}/roles")
    def update_roles(username: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        roles = body.get("roles")
        if not isinstance(roles, list) or not roles or \
                not set(roles) <= ALL_ROLES or ROLE_SERVICE in roles:
            raise ApiError("VALIDATION_FAILED",
                           "roles 必须为非空合法角色集合且不允许分配 service")
        with sf() as db:
            account, subject = _get_user(db, p.tenant_id, username)
            # RT-02 最后 platform_admin 保护：先按 id 升序 FOR UPDATE 锁定
            # 全租户活跃管理员集合再判定（旧实现只锁被改一行，双管理员并发
            # 自我降权会同时通过检查导致归零；锁定账号不再充数）
            subject_locked = db.query(AuthSubject).filter_by(
                id=subject.id).with_for_update().first()
            new_roles = list(dict.fromkeys(roles))
            was_admin = ROLE_PLATFORM_ADMIN in (subject.roles or [])
            still_admin = ROLE_PLATFORM_ADMIN in new_roles
            if was_admin and not still_admin:
                _assert_not_last_active_admin(db, p.tenant_id, subject.id)
            old_roles = list(subject.roles or [])
            subject_locked.roles = new_roles
            subject_locked.target_scopes = _validate_scopes(
                db, p.tenant_id, body.get("target_scopes", subject.target_scopes))
            audit(db, p, "system.user_roles_updated",
                  f"auth_subject:{subject.id}",
                  old_ref={"roles": old_roles,
                           "target_scopes": list(subject.target_scopes or [])},
                  new_ref={"roles": new_roles,
                           "target_scopes": list(subject.target_scopes or [])})
            db.commit()
            return ok(_account_view(account, subject_locked), ctx.request_id)

    @app.post("/api/v1/system/users/{username}/reset-password")
    def reset_password(username: str, body: dict = Body(...),
                       ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        new = body.get("password")
        if not isinstance(new, str):
            raise ApiError("VALIDATION_FAILED", "password 必填")
        validate_password(new)
        with sf() as db:
            account, subject = _get_user(db, p.tenant_id, username)
            now = utcnow()
            account.password_hash = hash_password(new)
            account.password_changed_at = now
            account.failed_attempts = 0
            account.locked_until = None
            n = db.query(ConsoleSession).filter_by(
                subject_id=subject.id, tenant_id=p.tenant_id,
                revoked_at=None).update({"revoked_at": now},
                                        synchronize_session=False)
            audit(db, p, "system.user_password_reset",
                  f"console_account:{account.id}",
                  new_ref={"username": username, "sessions_revoked": n})
            db.commit()
            return ok({"username": username, "sessions_revoked": n},
                      ctx.request_id)

    @app.post("/api/v1/system/users/{username}/lock")
    def lock_user(username: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            account, subject = _get_user(db, p.tenant_id, username)
            if subject.id == p.subject_id:
                raise ApiError("VALIDATION_FAILED", "不能锁定自己")
            # RT-02：锁定活跃管理员同样受最后管理员保护（锁定后不再充数；
            # 例如机器 service 管理员主体锁定唯一人类活跃管理员会被拦截）
            if ROLE_PLATFORM_ADMIN in (subject.roles or []) \
                    and subject.status == "ACTIVE":
                _assert_not_last_active_admin(db, p.tenant_id, subject.id)
            from datetime import timedelta
            account.locked_until = utcnow() + timedelta(
                days=_LOCK_FAR_FUTURE_DAYS)
            n = db.query(ConsoleSession).filter_by(
                subject_id=subject.id, tenant_id=p.tenant_id,
                revoked_at=None).update({"revoked_at": utcnow()},
                                        synchronize_session=False)
            audit(db, p, "system.user_locked", f"console_account:{account.id}",
                  new_ref={"username": username, "sessions_revoked": n})
            db.commit()
            return ok({"username": username, "locked": True,
                       "sessions_revoked": n}, ctx.request_id)

    @app.post("/api/v1/system/users/{username}/unlock")
    def unlock_user(username: str, body: dict = Body(...), ctx=Depends(auth)):
        p = ctx.require_principal()
        p.require_any_role(ROLE_PLATFORM_ADMIN)
        with sf() as db:
            account, subject = _get_user(db, p.tenant_id, username)
            account.locked_until = None
            account.failed_attempts = 0
            audit(db, p, "system.user_unlocked",
                  f"console_account:{account.id}",
                  new_ref={"username": username})
            db.commit()
            return ok({"username": username, "locked": False}, ctx.request_id)
