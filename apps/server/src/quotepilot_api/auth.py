"""Authentication application service. PostgreSQL serializes tenant security changes."""

import hashlib
import logging
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from argon2 import PasswordHasher, profiles
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api.auth_models import AuthLimit, AuthSession, BusinessAuditEvent, TenantLogin, User
from quotepilot_api.tenants import Tenant, TenantContext, TenantRepository, require_context

HASHER = PasswordHasher.from_parameters(profiles.RFC_9106_LOW_MEMORY)
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
IDLE = timedelta(minutes=30)
ABSOLUTE = timedelta(hours=12)
WINDOW = timedelta(minutes=15)
logger = logging.getLogger(__name__)


class Role(StrEnum):
    SALES_ADMIN = "sales_admin"
    SALES_MANAGER = "sales_manager"
    SYSTEM_ADMIN = "system_admin"


class Capability(StrEnum):
    EDIT_DRAFT = "edit_draft"
    GENERATE_ELIGIBLE = "generate_eligible"
    APPROVE = "approve"
    MANAGE_USERS = "manage_users"
    MANAGE_IMPORTS = "manage_imports"
    MANAGE_SETTINGS = "manage_settings"
    CONFIGURE_POLICY = "configure_policy"
    AUDIT_CASES = "audit_allowed_cases"
    AUDIT_COMMERCIAL = "audit_tenant_commercial"
    AUDIT_OPERATIONS = "audit_tenant_operations"


PERMISSIONS = {
    Role.SALES_ADMIN: frozenset(
        {Capability.EDIT_DRAFT, Capability.GENERATE_ELIGIBLE, Capability.AUDIT_CASES}
    ),
    Role.SALES_MANAGER: frozenset(
        {
            Capability.EDIT_DRAFT,
            Capability.GENERATE_ELIGIBLE,
            Capability.APPROVE,
            Capability.AUDIT_COMMERCIAL,
        }
    ),
    Role.SYSTEM_ADMIN: frozenset(
        {
            Capability.MANAGE_USERS,
            Capability.MANAGE_IMPORTS,
            Capability.MANAGE_SETTINGS,
            Capability.CONFIGURE_POLICY,
            Capability.AUDIT_OPERATIONS,
        }
    ),
}


class AuthError(Exception):
    def __init__(self, code: str = "AUTH_INVALID_CREDENTIALS", status: int = 401) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def normalized_login(value: str) -> str:
    if not value.isascii():
        raise ValueError("Login must use ASCII characters.")
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.@_+\-]{0,127}", value):
        raise ValueError("Use 1–128 ASCII letters, digits, or . @ _ + - for login.")
    return value


def normalized_locator(value: str) -> str:
    if not value.isascii():
        raise ValueError("Organization code must use ASCII characters.")
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-]{2,63}", value):
        raise ValueError("Use 3–64 ASCII letters, digits or hyphens for organization code.")
    return value


def validate_password(value: str) -> str:
    if not 15 <= len(value) <= 128 or len(value.encode("utf-8")) > 512:
        raise ValueError("Password must contain 15–128 characters (at most 512 UTF-8 bytes).")
    return value


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def now() -> datetime:
    return datetime.now(UTC)


def tenant_lock(session: Session, context: TenantContext) -> None:
    tenant_id = require_context(context)
    session.execute(text("SET LOCAL lock_timeout = '10s'"))
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(tenant_id.bytes[:8], signed=True)},
    )


@dataclass(frozen=True)
class Principal:
    context: TenantContext
    user_id: UUID
    role: Role
    login: str
    must_change_password: bool
    session_id: UUID
    csrf: str

    def require(self, capability: Capability) -> None:
        if self.must_change_password or capability not in PERMISSIONS[self.role]:
            raise AuthError("AUTH_FORBIDDEN", 403)


def audit(
    session: Session,
    context: TenantContext,
    actor: UUID | None,
    action: str,
    target: UUID | None = None,
    outcome: str = "success",
    evidence: str = "",
) -> None:
    session.add(
        BusinessAuditEvent(
            tenant_id=require_context(context),
            actor_id=actor,
            action=action,
            target_id=target,
            outcome=outcome,
            evidence=evidence,
        )
    )
    session.flush()


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def user(self, context: TenantContext, user_id: UUID) -> User:
        user = self.session.scalar(
            select(User).where(User.tenant_id == require_context(context), User.id == user_id)
        )
        if user is None:
            raise AuthError("TENANT_SCOPE_VIOLATION", 404)
        return user

    def revoke(self, context: TenantContext, user_id: UUID) -> None:
        self.session.execute(
            update(AuthSession)
            .where(
                AuthSession.tenant_id == require_context(context),
                AuthSession.user_id == user_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=now())
        )


class AuthService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def limit(self, dimensions: list[tuple[str, int]]) -> None:
        """Fixed 15-minute windows; counters persist even when authentication is rejected."""
        timestamp = now()
        denied = False
        with self.sessions.begin() as session:
            # Expired counters are disposable, not business audit retention.
            session.execute(delete(AuthLimit).where(AuthLimit.started_at < timestamp - WINDOW))
            for label, maximum in sorted(dimensions):
                key = digest(label)
                session.execute(
                    insert(AuthLimit)
                    .values(key=key, started_at=timestamp, attempts=0)
                    .on_conflict_do_nothing()
                )
                counter = session.scalar(
                    select(AuthLimit).where(AuthLimit.key == key).with_for_update()
                )
                assert counter is not None
                if counter.started_at <= timestamp - WINDOW:
                    counter.started_at, counter.attempts = timestamp, 0
                counter.attempts = min(counter.attempts + 1, maximum + 1)
                denied |= counter.attempts > maximum
        if denied:
            raise AuthError("AUTH_RATE_LIMITED", 429)

    def bootstrap(
        self,
        locator: str,
        login: str,
        password: str,
        timezone: str = "UTC",
        existing_tenant: UUID | None = None,
    ) -> TenantContext:
        locator, login = normalized_locator(locator), normalized_login(login)
        password_hash = HASHER.hash(validate_password(password))
        with self.sessions.begin() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(762003)"))
            if existing_tenant is None:
                if session.scalar(select(Tenant.id).limit(1)) is not None:
                    raise AuthError("BOOTSTRAP_ALREADY_INITIALIZED", 409)
                context = TenantContext(uuid4())
                TenantRepository(session).create(context, timezone)
            else:
                context = TenantContext(existing_tenant)
                tenant_lock(session, context)
                if session.get(Tenant, existing_tenant) is None:
                    raise AuthError("BOOTSTRAP_TENANT_NOT_FOUND", 404)
            if (
                session.scalar(select(User.id).where(User.tenant_id == context.tenant_id).limit(1))
                is not None
            ):
                raise AuthError("BOOTSTRAP_ALREADY_INITIALIZED", 409)
            if (
                session.scalar(select(TenantLogin).where(TenantLogin.locator == locator))
                is not None
            ):
                raise AuthError("BOOTSTRAP_LOCATOR_CONFLICT", 409)
            session.add(TenantLogin(tenant_id=context.tenant_id, locator=locator))
            user = User(
                tenant_id=context.tenant_id,
                login=login,
                password_hash=password_hash,
                role=Role.SYSTEM_ADMIN,
            )
            session.add(user)
            session.flush()
            audit(session, context, user.id, "bootstrap", user.id)
        return context

    def _issue(self, session: Session, user: User) -> tuple[Principal, str]:
        timestamp, token = now(), secrets.token_urlsafe(32)
        authority = AuthSession(
            tenant_id=user.tenant_id,
            user_id=user.id,
            token_hash=digest(token),
            csrf=secrets.token_urlsafe(32),
            created_at=timestamp,
            idle_expires_at=timestamp + IDLE,
            absolute_expires_at=timestamp + ABSOLUTE,
        )
        session.add(authority)
        session.flush()
        return self._principal(user, authority), token

    @staticmethod
    def _principal(user: User, authority: AuthSession) -> Principal:
        return Principal(
            TenantContext(user.tenant_id),
            user.id,
            Role(user.role),
            user.login,
            user.must_change_password,
            authority.id,
            authority.csrf,
        )

    def login(self, locator: str, login: str, password: str, source: str) -> tuple[Principal, str]:
        self.limit(
            [
                ("source:" + source, 60),
                ("tenant:" + locator, 200),
                ("account:" + locator + ":" + login, 10),
            ]
        )
        result = None
        with self.sessions.begin() as session:
            tenant = session.scalar(select(TenantLogin).where(TenantLogin.locator == locator))
            user = None
            if tenant is not None:
                context = TenantContext(tenant.tenant_id)
                tenant_lock(session, context)
                user = session.scalar(
                    select(User).where(User.tenant_id == context.tenant_id, User.login == login)
                )
            try:
                valid: bool = HASHER.verify(user.password_hash if user else DUMMY_HASH, password)
            except VerifyMismatchError:
                valid = False
            if valid and user is not None and not user.disabled:
                if HASHER.check_needs_rehash(user.password_hash):
                    user.password_hash = HASHER.hash(password)
                result = self._issue(session, user)
                audit(session, context, user.id, "login", user.id)
            elif tenant is not None:
                audit(
                    session,
                    context,
                    None,
                    "login",
                    outcome="denied",
                    evidence="invalid_credentials",
                )
            else:
                logger.warning("auth_login_denied_unknown_tenant")
        if result is None:
            raise AuthError()
        return result

    @contextmanager
    def authenticated(
        self, token: str, csrf: str | None = None
    ) -> Iterator[tuple[Session, Principal]]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise AuthError()
        with self.sessions.begin() as session:
            tenant_id = session.scalar(
                select(AuthSession.tenant_id).where(AuthSession.token_hash == digest(token))
            )
            if tenant_id is None:
                raise AuthError()
            context = TenantContext(tenant_id)
            tenant_lock(session, context)
            authority = session.scalar(
                select(AuthSession).where(
                    AuthSession.tenant_id == tenant_id, AuthSession.token_hash == digest(token)
                )
            )
            assert authority is not None
            user = AuthRepository(session).user(context, authority.user_id)
            timestamp = now()
            if (
                user.disabled
                or authority.revoked_at is not None
                or min(authority.idle_expires_at, authority.absolute_expires_at) <= timestamp
            ):
                raise AuthError()
            if csrf is not None and not secrets.compare_digest(
                csrf.encode(), authority.csrf.encode()
            ):
                raise AuthError("AUTH_CSRF_REJECTED", 403)
            yield session, self._principal(user, authority)

    def renew(self, session: Session, principal: Principal) -> None:
        session.execute(
            update(AuthSession)
            .where(
                AuthSession.tenant_id == principal.context.tenant_id,
                AuthSession.id == principal.session_id,
            )
            .values(idle_expires_at=now() + IDLE)
        )

    def logout(self, session: Session, principal: Principal) -> None:
        session.execute(
            update(AuthSession)
            .where(
                AuthSession.tenant_id == principal.context.tenant_id,
                AuthSession.id == principal.session_id,
            )
            .values(revoked_at=now())
        )
        audit(session, principal.context, principal.user_id, "logout", principal.user_id)

    def change_password(
        self, session: Session, principal: Principal, old: str, new: str
    ) -> tuple[Principal, str]:
        user = AuthRepository(session).user(principal.context, principal.user_id)
        try:
            HASHER.verify(user.password_hash, old)
        except VerifyMismatchError:
            raise AuthError() from None
        if secrets.compare_digest(old.encode(), new.encode()):
            raise AuthError("PASSWORD_MUST_DIFFER", 422)
        user.password_hash = HASHER.hash(validate_password(new))
        user.must_change_password = False
        user.version += 1
        user.updated_at = now()
        AuthRepository(session).revoke(principal.context, user.id)
        audit(session, principal.context, user.id, "password_changed", user.id)
        return self._issue(session, user)

    def create_user(
        self, session: Session, principal: Principal, login: str, password: str, role: Role
    ) -> User:
        principal.require(Capability.MANAGE_USERS)
        login = normalized_login(login)
        if session.scalar(
            select(User.id).where(
                User.tenant_id == principal.context.tenant_id, User.login == login
            )
        ):
            raise AuthError("USER_LOGIN_CONFLICT", 409)
        user = User(
            tenant_id=principal.context.tenant_id,
            login=login,
            password_hash=HASHER.hash(validate_password(password)),
            role=role,
        )
        session.add(user)
        session.flush()
        audit(
            session,
            principal.context,
            principal.user_id,
            "user_created",
            user.id,
            evidence=role.value,
        )
        return user

    def update_user(
        self,
        session: Session,
        principal: Principal,
        user_id: UUID,
        version: int,
        disabled: bool | None,
        revoke: bool,
    ) -> User:
        principal.require(Capability.MANAGE_USERS)
        user = AuthRepository(session).user(principal.context, user_id)
        if user.version != version:
            raise AuthError("USER_VERSION_CONFLICT", 409)
        if disabled is not None:
            user.disabled = disabled
        if disabled or revoke:
            AuthRepository(session).revoke(principal.context, user.id)
        user.version += 1
        user.updated_at = now()
        audit(
            session,
            principal.context,
            principal.user_id,
            "user_updated",
            user.id,
            evidence=f"disabled={user.disabled};revoke={revoke};version={user.version}",
        )
        session.flush()
        return user
