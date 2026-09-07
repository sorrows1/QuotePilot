"""Trusted tenant provisioning and explicitly scoped settings access; no HTTP authority."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    business_timezone: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@dataclass(frozen=True)
class TenantContext:
    """Construct only at a trusted application boundary; QT-003 supplies authentication."""

    tenant_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, UUID) or self.tenant_id.int == 0:
            raise ValueError("Tenant context requires a non-null, non-nil UUID.")


def require_context(context: TenantContext) -> UUID:
    if not isinstance(context, TenantContext):
        raise ValueError("Explicit TenantContext is required.")
    context.__post_init__()
    return context.tenant_id


def validate_timezone(value: str) -> str:
    try:
        if not isinstance(value, str) or not value or len(value) > 255:
            raise ValueError
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError("business_timezone must be a valid IANA timezone.") from None
    return value


class TenantRepository:
    """Repositories never commit; each operation requires the trusted tenant context."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, context: TenantContext, business_timezone: str) -> None:
        tenant_id = require_context(context)
        timezone = validate_timezone(business_timezone)
        self.session.add(Tenant(id=tenant_id))
        self.session.flush()
        self.session.add(TenantSettings(tenant_id=tenant_id, business_timezone=timezone))
        self.session.flush()

    def get(self, context: TenantContext, tenant_id: UUID) -> Tenant | None:
        owner = require_context(context)
        return self.session.scalar(select(Tenant).where(Tenant.id == owner, Tenant.id == tenant_id))

    def get_settings(self, context: TenantContext, tenant_id: UUID) -> TenantSettings | None:
        owner = require_context(context)
        return self.session.scalar(
            select(TenantSettings).where(
                TenantSettings.tenant_id == owner, TenantSettings.tenant_id == tenant_id
            )
        )

    def update_timezone(
        self, context: TenantContext, tenant_id: UUID, business_timezone: str
    ) -> bool:
        owner = require_context(context)
        timezone = validate_timezone(business_timezone)
        changed = self.session.scalar(
            update(TenantSettings)
            .where(TenantSettings.tenant_id == owner, TenantSettings.tenant_id == tenant_id)
            .values(business_timezone=timezone, updated_at=func.clock_timestamp())
            .returning(TenantSettings.tenant_id)
        )
        return changed is not None


class TenantService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def provision(self, business_timezone: str) -> TenantContext:
        """Trusted bootstrap only; no tenant selector or public provisioning endpoint."""
        context = TenantContext(uuid4())
        with self.sessions.begin() as session:
            TenantRepository(session).create(context, business_timezone)
        return context

    def get_settings(self, context: TenantContext, tenant_id: UUID) -> TenantSettings | None:
        require_context(context)
        with self.sessions.begin() as session:
            settings = TenantRepository(session).get_settings(context, tenant_id)
            if settings is not None:
                session.expunge(settings)
            return settings

    def update_timezone(
        self, context: TenantContext, tenant_id: UUID, business_timezone: str
    ) -> bool:
        require_context(context)
        with self.sessions.begin() as session:
            return TenantRepository(session).update_timezone(context, tenant_id, business_timezone)
