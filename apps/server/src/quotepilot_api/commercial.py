"""Tenant-scoped commercial persistence primitives; callers own transactions."""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import RowMapping, Table, and_, or_, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api import commercial_schema as schema
from quotepilot_api.commercial_inputs import (
    AliasInput,
    AssignmentInput,
    CommercialInput,
    ConversionInput,
    CostInput,
    CustomerInput,
    InventoryInput,
    PolicyInput,
    PricebookInput,
    PriceInput,
    ProductInput,
    SuccessorInput,
    exact,
    utc,
)
from quotepilot_api.tenants import TenantContext, require_context

INPUT_TABLE = {
    CustomerInput: schema.customers,
    ProductInput: schema.products,
    AliasInput: schema.product_aliases,
    SuccessorInput: schema.product_successors,
    PricebookInput: schema.pricebooks,
    AssignmentInput: schema.pricebook_assignments,
    ConversionInput: schema.uom_conversions,
    PriceInput: schema.prices,
    CostInput: schema.product_costs,
    InventoryInput: schema.inventory,
    PolicyInput: schema.discount_policies,
}
RecordKind = Literal[
    "customers",
    "products",
    "product_aliases",
    "product_successors",
    "pricebooks",
    "pricebook_assignments",
    "uom_conversions",
    "prices",
    "product_costs",
    "inventory",
    "discount_policies",
]


class CommercialError(ValueError):
    """Stable error codes are safe for downstream application translation."""


def one(rows: Sequence[RowMapping], missing: str, ambiguous: str) -> RowMapping:
    if len(rows) != 1:
        raise CommercialError(ambiguous if rows else missing)
    return rows[0]


def eligible(t: Table, instant: datetime) -> Any:
    return and_(
        t.c.archived.is_(False),
        t.c.valid_from <= instant,
        or_(t.c.valid_to.is_(None), instant < t.c.valid_to),
    )


def converted_quantity(quantity: str | Decimal, factor: str | Decimal) -> Decimal:
    q, f = exact(quantity), exact(factor)
    if q <= 0 or f <= 0:
        raise ValueError("Positive quantity and conversion required")
    # Each authority has <=24 significant digits; product fits exactly in 48.
    with localcontext() as ctx:
        ctx.prec = 48
        return q * f


def inventory_freshness(
    evidence: RowMapping | None, pricing_as_of: datetime, max_age_minutes: int
) -> str:
    instant = utc(pricing_as_of)
    if type(max_age_minutes) is not int or max_age_minutes <= 0:
        raise ValueError("Positive whole-minute freshness threshold required")
    if evidence is None:
        return "MISSING_INVENTORY"
    age = instant - evidence["observed_at"]
    if age < timedelta(0):
        return "INVALID_FUTURE_INVENTORY"
    # Avoid timedelta overflow for externally configured large minute counts.
    return "FRESH" if age // timedelta(microseconds=1) <= max_age_minutes * 60000000 else "STALE"


class CommercialRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, context: TenantContext, record: CommercialInput) -> UUID:
        tenant = require_context(context)
        t = INPUT_TABLE.get(type(record))
        if t is None:
            raise ValueError("Supported commercial input required")
        # Revalidate even instances created with model_construct/model_copy.
        checked = type(record).model_validate(record.model_dump())
        self.session.execute(t.insert().values(tenant_id=tenant, **checked.model_dump()))
        return checked.id

    def get(
        self,
        context: TenantContext,
        kind: RecordKind,
        record_id: UUID,
        *,
        product_id: UUID | None = None,
        customer_id: UUID | None = None,
        pricebook_id: UUID | None = None,
    ) -> RowMapping | None:
        tenant = require_context(context)
        t = schema.TABLES[kind]
        predicates = [t.c.tenant_id == tenant, t.c.id == record_id]
        for name, value in [
            ("product_id", product_id),
            ("customer_id", customer_id),
            ("pricebook_id", pricebook_id),
        ]:
            if value is not None:
                if name not in t.c:
                    raise ValueError("Parent is not applicable to record kind")
                predicates.append(t.c[name] == value)
        return self.session.execute(select(t).where(*predicates)).mappings().one_or_none()

    def archive(self, context: TenantContext, kind: RecordKind, record_id: UUID) -> bool:
        tenant = require_context(context)
        t = schema.TABLES[kind]
        result = self.session.execute(
            update(t)
            .where(t.c.tenant_id == tenant, t.c.id == record_id)
            .values(archived=True)
            .returning(t.c.id)
        )
        return result.scalar_one_or_none() is not None

    def close_window(
        self, context: TenantContext, kind: RecordKind, record_id: UUID, valid_to: datetime
    ) -> bool:
        tenant = require_context(context)
        end = utc(valid_to)
        t = schema.TABLES[kind]
        if "valid_to" not in t.c:
            raise ValueError("Record has no effective window")
        return (
            self.session.execute(
                update(t)
                .where(t.c.tenant_id == tenant, t.c.id == record_id)
                .values(valid_to=end)
                .returning(t.c.id)
            ).scalar_one_or_none()
            is not None
        )

    def _active_parent(self, context: TenantContext, kind: RecordKind, record_id: UUID) -> None:
        record = self.get(context, kind, record_id)
        if record is None or record["archived"]:
            raise CommercialError("MISSING_ACTIVE_" + kind.upper())

    def pricebook(
        self, context: TenantContext, customer_id: UUID | None, pricing_as_of: datetime
    ) -> RowMapping:
        tenant = require_context(context)
        instant = utc(pricing_as_of)
        if customer_id is not None:
            self._active_parent(context, "customers", customer_id)
        a, b = schema.pricebook_assignments, schema.pricebooks
        # A live assignment with an ineligible book is an error, never fallback.
        for scope in [customer_id, None] if customer_id is not None else [None]:
            rows = (
                self.session.execute(
                    select(a).where(
                        a.c.tenant_id == tenant, a.c.customer_id == scope, eligible(a, instant)
                    )
                )
                .mappings()
                .all()
            )
            if rows:
                assignment = one(rows, "MISSING_PRICEBOOK", "AMBIGUOUS_PRICEBOOK")
                books = (
                    self.session.execute(
                        select(b).where(
                            b.c.tenant_id == tenant,
                            b.c.id == assignment["pricebook_id"],
                            eligible(b, instant),
                        )
                    )
                    .mappings()
                    .all()
                )
                return one(books, "MISSING_PRICEBOOK", "AMBIGUOUS_PRICEBOOK")
        raise CommercialError("MISSING_PRICEBOOK")

    def price(
        self,
        context: TenantContext,
        product_id: UUID,
        pricebook_id: UUID,
        uom: str,
        quantity: Decimal,
        pricing_as_of: datetime,
    ) -> RowMapping:
        tenant = require_context(context)
        instant = utc(pricing_as_of)
        if not isinstance(quantity, Decimal) or not quantity.is_finite() or quantity <= 0:
            raise ValueError("Positive exact pricing quantity required")
        self._active_parent(context, "products", product_id)
        b = schema.pricebooks
        books = (
            self.session.execute(
                select(b).where(
                    b.c.tenant_id == tenant, b.c.id == pricebook_id, eligible(b, instant)
                )
            )
            .mappings()
            .all()
        )
        one(books, "MISSING_PRICEBOOK", "AMBIGUOUS_PRICEBOOK")
        p = schema.prices
        rows = (
            self.session.execute(
                select(p).where(
                    p.c.tenant_id == tenant,
                    p.c.product_id == product_id,
                    p.c.pricebook_id == pricebook_id,
                    p.c.uom == uom,
                    eligible(p, instant),
                    p.c.quantity_min <= quantity,
                    or_(p.c.quantity_max.is_(None), quantity < p.c.quantity_max),
                )
            )
            .mappings()
            .all()
        )
        return one(rows, "MISSING_PRICE", "AMBIGUOUS_PRICE")

    def cost(
        self, context: TenantContext, product_id: UUID, pricing_as_of: datetime
    ) -> RowMapping | None:
        tenant = require_context(context)
        instant = utc(pricing_as_of)
        self._active_parent(context, "products", product_id)
        t = schema.product_costs
        rows = (
            self.session.execute(
                select(t).where(
                    t.c.tenant_id == tenant, t.c.product_id == product_id, eligible(t, instant)
                )
            )
            .mappings()
            .all()
        )
        return one(rows, "MARGIN_UNKNOWN", "AMBIGUOUS_COST") if rows else None

    def conversion(
        self,
        context: TenantContext,
        product_id: UUID,
        from_uom: str,
        to_uom: str,
        pricing_as_of: datetime,
    ) -> RowMapping:
        tenant = require_context(context)
        instant = utc(pricing_as_of)
        self._active_parent(context, "products", product_id)
        t = schema.uom_conversions
        rows = (
            self.session.execute(
                select(t).where(
                    t.c.tenant_id == tenant,
                    t.c.product_id == product_id,
                    t.c.from_uom == from_uom,
                    t.c.to_uom == to_uom,
                    eligible(t, instant),
                )
            )
            .mappings()
            .all()
        )
        return one(rows, "MISSING_CONVERSION", "AMBIGUOUS_CONVERSION")

    def policy(
        self, context: TenantContext, customer_id: UUID | None, pricing_as_of: datetime
    ) -> RowMapping | None:
        tenant = require_context(context)
        instant = utc(pricing_as_of)
        if customer_id is not None:
            self._active_parent(context, "customers", customer_id)
        t = schema.discount_policies
        for scope in [customer_id, None] if customer_id is not None else [None]:
            rows = (
                self.session.execute(
                    select(t).where(
                        t.c.tenant_id == tenant, t.c.customer_id == scope, eligible(t, instant)
                    )
                )
                .mappings()
                .all()
            )
            if rows:
                return one(rows, "NO_DISCOUNT_POLICY", "AMBIGUOUS_DISCOUNT_POLICY")
        return None  # Explicit NO_DISCOUNT_POLICY for consumers; do not invent a policy ID.

    def stock(
        self, context: TenantContext, product_id: UUID, pricing_as_of: datetime
    ) -> RowMapping | None:
        tenant = require_context(context)
        instant = utc(pricing_as_of)
        self._active_parent(context, "products", product_id)
        t = schema.inventory
        return (
            self.session.execute(
                select(t)
                .where(
                    t.c.tenant_id == tenant,
                    t.c.product_id == product_id,
                    t.c.archived.is_(False),
                    t.c.observed_at <= instant,
                    t.c.imported_at <= instant,
                )
                .order_by(t.c.observed_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )


class CommercialService:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions

    def create_batch(
        self, context: TenantContext, records: Sequence[CommercialInput]
    ) -> list[UUID]:
        require_context(context)
        with self.sessions.begin() as session:
            repository = CommercialRepository(session)
            return [repository.add(context, record) for record in records]

    @contextmanager
    def snapshot(self, context: TenantContext) -> Iterator[CommercialRepository]:
        """One read-only MVCC snapshot for related commercial lookups; no mixed commits."""
        require_context(context)
        with self.sessions.begin() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            yield CommercialRepository(session)
