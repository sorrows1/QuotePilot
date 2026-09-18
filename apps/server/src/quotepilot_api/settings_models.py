"""Immutable settings, sanitized branding and committed numbering history."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from quotepilot_api.settings_fields import SettingsFields
from quotepilot_api.tenants import Base


class SettingsRevision(SettingsFields, Base):
    __tablename__ = "settings_revisions"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "creator_id"], ["users.tenant_id", "users.id"]),
        ForeignKeyConstraint(
            ["tenant_id", "current_logo_asset_id"],
            ["brand_assets.tenant_id", "brand_assets.id"],
            name="settings_revisions_logo",
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_timezone: Mapped[str] = mapped_column(String(255))
    creator_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BrandAsset(Base):
    __tablename__ = "brand_assets"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id", "creator_id"], ["users.tenant_id", "users.id"]),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    creator_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuoteNumber(Base):
    __tablename__ = "quote_numbers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "settings_revision"],
            ["settings_revisions.tenant_id", "settings_revisions.revision"],
        ),
        UniqueConstraint("tenant_id", "sequence"),
        UniqueConstraint("tenant_id", "number"),
    )
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    subject_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    number: Mapped[str] = mapped_column(String(40))
    prefix: Mapped[str] = mapped_column(String(10))
    settings_revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuoteNumberCounter(Base):
    __tablename__ = "quote_number_counters"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), primary_key=True)
    next_sequence: Mapped[int] = mapped_column(BigInteger, server_default="1")
