"""Typed current/snapshot columns shared by the tenant settings authority."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, Integer, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column


class SettingsFields:
    company_name: Mapped[str | None] = mapped_column(String(200))
    company_address: Mapped[str | None] = mapped_column(String(1000))
    current_logo_asset_id: Mapped[UUID | None] = mapped_column(Uuid)
    currency_code: Mapped[str] = mapped_column(String(3), server_default="SGD")
    tax_enabled: Mapped[bool | None] = mapped_column(Boolean)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    freight_taxable: Mapped[bool | None] = mapped_column(Boolean)
    default_quote_validity_days: Mapped[int | None] = mapped_column(Integer)
    inventory_max_age_minutes: Mapped[int | None] = mapped_column(Integer)
    quote_number_prefix: Mapped[str | None] = mapped_column(String(10))
    discount_approval_enabled: Mapped[bool | None] = mapped_column(Boolean)
    discount_approval_threshold: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    quote_margin_control_enabled: Mapped[bool | None] = mapped_column(Boolean)
    minimum_margin_threshold: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    line_margin_control_enabled: Mapped[bool | None] = mapped_column(Boolean)
    minimum_line_margin_threshold: Mapped[Decimal | None] = mapped_column(Numeric(7, 6))
    quote_value_approval_enabled: Mapped[bool | None] = mapped_column(Boolean)
    quote_value_approval_threshold: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
