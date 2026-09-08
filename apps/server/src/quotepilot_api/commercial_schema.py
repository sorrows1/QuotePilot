"""Commercial tables share QT-002's registry. No separate datastore or ORM base."""

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
)

from quotepilot_api.tenants import Base


def col(name: str, kind: Any, nullable: bool = False) -> Column[Any]:
    return Column(name, kind, nullable=nullable)


def reference(name: str, target: str, nullable: bool = False) -> list[Any]:
    return [
        col(name, Uuid, nullable),
        ForeignKeyConstraint(
            ["tenant_id", name], [f"{target}.tenant_id", f"{target}.id"], ondelete="RESTRICT"
        ),
    ]


def number(
    name: str,
    *,
    positive: bool = False,
    rate: bool = False,
    signed: bool = False,
    nullable: bool = False,
) -> list[Any]:
    bounds = f"abs({name}) < 1000000000000000000"
    if not signed:
        bounds += f" AND {name} {'>' if positive else '>='} 0"
    if rate:
        bounds += f" AND {name} <= 1"
    return [
        col(name, Numeric(), nullable),
        CheckConstraint(
            f"{name} IS NULL OR (scale({name}) <= 6 AND {bounds})", name=f"{name}_exact"
        ),
    ]


def window() -> list[Any]:
    return [
        col("valid_from", DateTime(timezone=True)),
        col("valid_to", DateTime(timezone=True), True),
        CheckConstraint(
            "isfinite(valid_from) AND (valid_to IS NULL OR "
            "(isfinite(valid_to) AND valid_to > valid_from))",
            name="valid_window",
        ),
    ]


def table(name: str, *items: Any) -> Table:
    return Table(
        name,
        Base.metadata,
        Column("id", Uuid, primary_key=True),
        Column("tenant_id", Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("archived", Boolean, nullable=False, server_default="false"),
        UniqueConstraint("tenant_id", "id", name=f"{name}_tenant_id"),
        *items,
    )


customers = table(
    "customers",
    col("external_key", String(100), True),
    col("name", String(255)),
    UniqueConstraint("tenant_id", "external_key"),
)
products = table(
    "products",
    col("sku", String(100)),
    col("name", String(255)),
    col("description", String(4000)),
    col("manufacturer", String(255), True),
    col("model_number", String(255), True),
    UniqueConstraint("tenant_id", "sku"),
)
product_aliases = table(
    "product_aliases",
    *reference("customer_id", "customers"),
    *reference("product_id", "products"),
    col("alias", String(255)),
    col("source", String(1000)),
    UniqueConstraint("tenant_id", "customer_id", "alias"),
)
product_successors = table(
    "product_successors",
    *reference("product_id", "products"),
    *reference("successor_id", "products"),
    col("source", String(1000)),
    CheckConstraint("product_id <> successor_id"),
    UniqueConstraint("tenant_id", "product_id", "successor_id"),
)
pricebooks = table(
    "pricebooks",
    col("key", String(100)),
    col("version", Integer),
    *window(),
    col("source", String(1000)),
    CheckConstraint("version > 0"),
    UniqueConstraint("tenant_id", "key", "version"),
)
pricebook_assignments = table(
    "pricebook_assignments",
    *reference("customer_id", "customers", True),
    *reference("pricebook_id", "pricebooks"),
    *window(),
    col("source", String(1000)),
)
uom_conversions = table(
    "uom_conversions",
    *reference("product_id", "products"),
    col("from_uom", String(30)),
    col("to_uom", String(30)),
    *number("factor", positive=True),
    *window(),
    col("source", String(1000)),
    CheckConstraint("from_uom <> to_uom"),
    UniqueConstraint("tenant_id", "product_id", "id"),
)
prices = table(
    "prices",
    *reference("product_id", "products"),
    *reference("pricebook_id", "pricebooks"),
    col("uom", String(30)),
    *number("unit_price"),
    *number("quantity_min", positive=True),
    *number("quantity_max", positive=True, nullable=True),
    *window(),
    col("source", String(1000)),
    CheckConstraint("quantity_max IS NULL OR quantity_max > quantity_min"),
    UniqueConstraint("tenant_id", "product_id", "pricebook_id", "id"),
)
product_costs = table(
    "product_costs",
    *reference("product_id", "products"),
    col("uom", String(30)),
    *number("unit_cost"),
    *window(),
    col("source", String(1000)),
    UniqueConstraint("tenant_id", "product_id", "id"),
)
inventory = table(
    "inventory",
    *reference("product_id", "products"),
    col("uom", String(30)),
    *number("quantity", signed=True),
    col("observed_at", DateTime(timezone=True)),
    col("imported_at", DateTime(timezone=True)),
    col("source", String(1000)),
    CheckConstraint(
        "isfinite(observed_at) AND isfinite(imported_at) AND observed_at <= imported_at"
    ),
    UniqueConstraint("tenant_id", "product_id", "observed_at"),
    UniqueConstraint("tenant_id", "product_id", "id"),
)
discount_policies = table(
    "discount_policies",
    *reference("customer_id", "customers", True),
    col("key", String(100)),
    col("version", Integer),
    *number("rate", rate=True),
    col("permitted", Boolean),
    *window(),
    col("source", String(1000)),
    CheckConstraint("version > 0"),
    UniqueConstraint("tenant_id", "key", "version"),
)

TABLES = {
    t.name: t
    for t in (
        customers,
        products,
        product_aliases,
        product_successors,
        pricebooks,
        pricebook_assignments,
        uom_conversions,
        prices,
        product_costs,
        inventory,
        discount_policies,
    )
}
for t in TABLES.values():
    for c in t.columns:
        if isinstance(c.type, String):
            t.append_constraint(
                CheckConstraint(
                    f"{c.name} IS NULL OR length(trim({c.name})) > 0", name=f"{c.name}_not_blank"
                )
            )
    for fk in t.foreign_key_constraints:
        names = [c.name for c in fk.columns]
        if len(names) > 1:
            Index(f"ix_{t.name}_{names[-1]}", *[t.c[n] for n in names])

# Internal transaction lock, separate from authentication and tenant settings authority.
commercial_write_guards = Table(
    "commercial_write_guards",
    Base.metadata,
    Column("tenant_id", Uuid, ForeignKey("tenants.id", ondelete="RESTRICT"), primary_key=True),
    Column("generation", BigInteger, nullable=False, server_default="0"),
)
for t in TABLES.values():
    for name in ("uom", "from_uom", "to_uom"):
        if name in t.c:
            t.append_constraint(
                CheckConstraint(f"{name} ~ '^[A-Z][A-Z0-9_]*$'", name=f"{name}_format")
            )
