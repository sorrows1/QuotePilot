"""Durable tenant-local import staging and successful commit receipts."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from quotepilot_api.tenants import Base


class ImportJob(Base):
    __tablename__ = "import_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "actor_id"], ["users.tenant_id", "users.id"]),
        UniqueConstraint("tenant_id", "commit_key"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    actor_id: Mapped[UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    filename: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    file_hash: Mapped[str] = mapped_column(String(64))
    headers: Mapped[list[str]] = mapped_column(JSONB)
    rows: Mapped[list[list[str]]] = mapped_column(JSONB)
    mapping: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))
    mode: Mapped[str | None] = mapped_column(String(32))
    preview_token: Mapped[str | None] = mapped_column(String(64))
    preview: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    commit_key: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
