"""Password credentials and browser sessions; standard library, no plaintext DB.

PBKDF2-HMAC-SHA256 with 600,000 iterations and a random 128-bit salt.
Existing automation credentials remain independent of browser sessions.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from contracts import ApiError, new_uuid
from core_db.engine import utcnow

ITERATIONS = 600_000
SESSION_HOURS = 8
MAX_FAILURES = 5
LOCK_MINUTES = 15
_DUMMY = "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$" + base64.b64encode(bytes(32)).decode()


def normalize_username(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid username")
    value = value.strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,63}", value):
        raise ValueError("username must be 3-64 ASCII letters, digits, dots, underscores or hyphens")
    return value


def validate_password(password: str) -> None:
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ApiError("VALIDATION_FAILED", "密码长度须为12至128个字符")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode("utf8"), salt, ITERATIONS)
    return "$".join(["pbkdf2_sha256", str(ITERATIONS), base64.b64encode(salt).decode(),
                     base64.b64encode(value).decode()])


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        algorithm, rounds, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        salt = base64.b64decode(salt, validate=True)
        expected = base64.b64decode(expected, validate=True)
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf8"), salt, ITERATIONS)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def subject_info(subject) -> dict:
    return {"subject_id": subject.id, "kind": subject.kind, "code": subject.subject_code,
            "roles": list(subject.roles or []), "tenant_id": subject.tenant_id,
            "display_name": subject.display_name}


def _principal(subject):
    from contracts.api import Principal
    return Principal(subject.id, subject.tenant_id, subject.kind, subject.subject_code,
                     list(subject.roles or []),
                     target_scopes=getattr(subject, "target_scopes", None))


def _failure(db, account, subject=None) -> None:
    from contracts.api import audit
    now = utcnow()
    if account is not None:
        if account.locked_until is None or account.locked_until <= now:
            account.failed_attempts = (account.failed_attempts or 0) + 1
            if account.failed_attempts >= MAX_FAILURES:
                account.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                account.failed_attempts = 0
        if subject is not None and subject.tenant_id == account.tenant_id:
            audit(db, _principal(subject), "auth.login_failed", "console_account:" + account.id)
        db.commit()
    # Same status/message for nonexistent, disabled, locked and incorrect accounts.
    raise ApiError("UNAUTHORIZED", "用户名或密码错误，或账户暂时不可用")


def password_login(db, username: str, password: str) -> dict:
    from core_db.models import AuthSubject, ConsoleAccount, ConsoleSession, Tenant
    from contracts.api import audit, hash_token
    try:
        username = normalize_username(username)
    except ValueError:
        verify_password(password, _DUMMY)
        _failure(db, None)
    account = (db.query(ConsoleAccount).filter_by(username=username).with_for_update().first())
    subject = db.get(AuthSubject, account.subject_id) if account else None
    valid = verify_password(password, account.password_hash if account else _DUMMY)
    now = utcnow()
    tenant = db.get(Tenant, account.tenant_id) if account else None
    if (not account or not valid or not subject or subject.status != "ACTIVE"
            or subject.kind != "user" or subject.tenant_id != account.tenant_id
            or tenant is None or tenant.status != "ACTIVE"
            or (account.locked_until is not None and account.locked_until > now)):
        _failure(db, account, subject)
    account.failed_attempts = 0
    account.locked_until = None
    # Bound retained sessions per user and remove expired/revoked history on login.
    db.query(ConsoleSession).filter(
        ConsoleSession.subject_id == subject.id,
        ConsoleSession.tenant_id == subject.tenant_id,
        (ConsoleSession.expires_at <= now) | ConsoleSession.revoked_at.is_not(None)).delete(synchronize_session=False)
    live = (db.query(ConsoleSession).filter_by(subject_id=subject.id, tenant_id=subject.tenant_id)
            .order_by(ConsoleSession.created_at.desc()).all())
    for old in live[4:]:
        old.revoked_at = now
    token = secrets.token_urlsafe(32)
    expires = now + timedelta(hours=SESSION_HOURS)
    db.add(ConsoleSession(id=new_uuid(), tenant_id=subject.tenant_id, subject_id=subject.id,
                          token_hash=hash_token(token), created_at=now, expires_at=expires))
    audit(db, _principal(subject), "auth.login", "subject:" + subject.id,
          new_ref={"mode": "password", "expires_at": expires.isoformat()})
    db.commit()
    return {**subject_info(subject), "access_token": token, "token_type": "Bearer",
            "expires_at": expires.isoformat() + "Z"}


def change_password(db, principal, current: str, new: str) -> None:
    from core_db.models import ConsoleAccount, ConsoleSession
    from contracts.api import audit
    validate_password(new)
    account = (db.query(ConsoleAccount).filter_by(subject_id=principal.subject_id,
                    tenant_id=principal.tenant_id).with_for_update().first())
    now = utcnow()
    if not account or (account.locked_until and account.locked_until > now) or not verify_password(current, account.password_hash):
        from core_db.models import AuthSubject
        _failure(db, account, db.get(AuthSubject, principal.subject_id))
    if hmac.compare_digest(current.encode("utf8"), new.encode("utf8")):
        raise ApiError("VALIDATION_FAILED", "新密码须与原密码不同")
    account.password_hash = hash_password(new)
    account.password_changed_at = now
    account.failed_attempts = 0
    account.locked_until = None
    db.query(ConsoleSession).filter_by(subject_id=principal.subject_id,
        tenant_id=principal.tenant_id, revoked_at=None).update({"revoked_at": now}, synchronize_session=False)
    audit(db, principal, "auth.password_changed", "console_account:" + account.id)
    db.commit()


def provision_account(db, *, tenant_code: str, username: str, password: str,
                      display_name: str, roles: list[str], reset: bool = False) -> dict:
    """Local operator CLI only; never publish a password/user-creation endpoint."""
    from core_db.models import AuthSubject, ConsoleAccount, ConsoleSession, Tenant
    from contracts.api import ALL_ROLES, audit, hash_token
    username = normalize_username(username)
    validate_password(password)
    if not roles or not set(roles) <= ALL_ROLES:
        raise ValueError("explicit valid roles required")
    tenant = db.query(Tenant).filter_by(code=tenant_code, status="ACTIVE").one()
    account = db.query(ConsoleAccount).filter_by(username=username).with_for_update().first()
    now = utcnow()
    if account is not None:
        if account.tenant_id != tenant.id:
            raise ValueError("username belongs to a different tenant")
        if not reset:
            raise ValueError("account already exists; use explicit --reset-password")
        subject = db.get(AuthSubject, account.subject_id)
        if subject is None or subject.tenant_id != tenant.id or subject.kind != "user":
            raise ValueError("inconsistent account subject")
        # Password reset never changes existing roles or re-enables a disabled user.
        db.query(ConsoleSession).filter_by(subject_id=subject.id, tenant_id=tenant.id,
            revoked_at=None).update({"revoked_at": now}, synchronize_session=False)
    else:
        code = "console-" + username
        if db.query(AuthSubject).filter_by(tenant_id=tenant.id, subject_code=code).first():
            raise ValueError("subject code is already in use")
        # LT-06A：CLI 建号等同"既有账号"语义 → target_scopes=["*"]（API 新建
        # 才默认 []）；reset 分支不改既有 scopes。
        subject = AuthSubject(id=new_uuid(), tenant_id=tenant.id, kind="user", subject_code=code,
            display_name=display_name, roles=roles, status="ACTIVE", token_hash=hash_token(secrets.token_urlsafe(48)),
            target_scopes=["*"])
        db.add(subject);db.flush()
        account = ConsoleAccount(id=new_uuid(), tenant_id=tenant.id, subject_id=subject.id, username=username)
        db.add(account)
    account.password_hash = hash_password(password)
    account.password_changed_at = now
    account.failed_attempts = 0;account.locked_until = None
    audit(db, None, "auth.account_reset" if reset else "auth.account_created", "console_account:" + account.id,
          new_ref={"username": username, "tenant_id": tenant.id, "subject_id": subject.id})
    db.commit()
    return {"username": username, "subject_id": subject.id, "tenant_id": tenant.id, "password_reset": reset}
