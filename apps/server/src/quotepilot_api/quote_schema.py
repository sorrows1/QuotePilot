"""Tenant-scoped case coordination and immutable commercial evidence."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB

from quotepilot_api.tenants import Base

cases = Table(
    "quote_cases",
    Base.metadata,
    Column("tenant_id", Uuid, ForeignKey("tenants.id"), primary_key=True),
    Column("id", Uuid, primary_key=True),
    Column("creator_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
    ForeignKeyConstraint(["tenant_id", "creator_id"], ["users.tenant_id", "users.id"]),
)
revisions = Table(
    "quote_revisions",
    Base.metadata,
    Column("tenant_id", Uuid, primary_key=True),
    Column("id", Uuid, primary_key=True),
    Column("case_id", Uuid, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("customer_id", Uuid, nullable=False),
    Column("creator_id", Uuid, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("settings_revision", Integer, nullable=False),
    Column("pricing_as_of", DateTime(timezone=True), nullable=False),
    Column("state", String(32), nullable=False),
    Column("commercial_fingerprint", String(64), nullable=False),
    Column("freight", Numeric, nullable=False),
    Column("total", Numeric),
    Column("snapshot", JSONB, nullable=False),
    Column("inputs", JSONB, nullable=False),
    Column("exception_set", JSONB, nullable=False),
    UniqueConstraint("tenant_id", "case_id", "revision"),
    ForeignKeyConstraint(["tenant_id", "case_id"], ["quote_cases.tenant_id", "quote_cases.id"]),
    ForeignKeyConstraint(["tenant_id", "customer_id"], ["customers.tenant_id", "customers.id"]),
    ForeignKeyConstraint(["tenant_id", "creator_id"], ["users.tenant_id", "users.id"]),
    ForeignKeyConstraint(
        ["tenant_id", "settings_revision"],
        ["settings_revisions.tenant_id", "settings_revisions.revision"],
    ),
)
lines = Table(
    "quote_lines",
    Base.metadata,
    Column("tenant_id", Uuid, primary_key=True),
    Column("revision_id", Uuid, primary_key=True),
    Column("line_id", String(100), primary_key=True),
    Column("product_id", Uuid, nullable=False),
    Column("quantity", Numeric, nullable=False),
    Column("quote_uom", String(30), nullable=False),
    Column("pricing_uom", String(30), nullable=False),
    Column("negotiated_unit_price", Numeric),
    ForeignKeyConstraint(
        ["tenant_id", "revision_id"], ["quote_revisions.tenant_id", "quote_revisions.id"]
    ),
    ForeignKeyConstraint(["tenant_id", "product_id"], ["products.tenant_id", "products.id"]),
)
receipts = Table(
    "quote_requests",
    Base.metadata,
    Column("tenant_id", Uuid, ForeignKey("tenants.id"), primary_key=True),
    Column("request_key", String(100), primary_key=True),
    Column("payload_hash", String(64), nullable=False),
    Column("response", JSONB, nullable=False),
)
