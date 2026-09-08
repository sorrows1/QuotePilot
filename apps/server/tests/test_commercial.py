from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api import commercial_schema as s
from quotepilot_api.commercial import (
    CommercialError,
    CommercialRepository,
    CommercialService,
    converted_quantity,
    inventory_freshness,
)
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
)
from quotepilot_api.migrate import migrate
from quotepilot_api.tenants import TenantContext, TenantService

START = datetime(2026, 1, 1, tzinfo=UTC)
END = START + timedelta(days=30)


def product(sku: str = "P-100") -> ProductInput:
    return ProductInput(sku=sku, name="Pump", description="Synthetic pump")


def book(key: str = "STD") -> PricebookInput:
    return PricebookInput(key=key, version=1, valid_from=START, source="test-book")


def price(p: UUID, b: UUID, **overrides: Any) -> PriceInput:
    values: dict[str, Any] = dict(
        product_id=p,
        pricebook_id=b,
        uom="EA",
        unit_price="12.345600",
        quantity_min="0.000001",
        valid_from=START,
        source="test-price",
    )
    values.update(overrides)
    return PriceInput(**values)


def seed(engine: Engine) -> tuple[TenantContext, ProductInput, PricebookInput, CustomerInput]:
    ctx = TenantService(sessionmaker(engine)).provision("UTC")
    p, b, c = product(), book(), CustomerInput(external_key="C1", name="Synthetic customer")
    CommercialService(sessionmaker(engine)).create_batch(ctx, [p, b, c])
    return ctx, p, b, c


@pytest.mark.parametrize(
    "value",
    [
        1.1,
        1,
        True,
        None,
        "NaN",
        "Infinity",
        "-Infinity",
        "0.0000001",
        "1.0000000",
        "1e18",
        "1e999999",
        "bad",
    ],
)
def test_reject_invalid_decimal(value: Any) -> None:
    with pytest.raises(ValueError):
        exact(value)


def test_decimal_exact_and_context_independent() -> None:
    with localcontext() as ctx:
        ctx.prec = 2
        assert exact("999999999999999999.999999") == Decimal("999999999999999999.999999")
        assert converted_quantity("0.333333", "0.333333") == Decimal("0.111110888889")
    assert exact("0") == 0


@pytest.mark.parametrize(
    "changes",
    [
        dict(unit_price="-1"),
        dict(quantity_min="0"),
        dict(quantity_min="1", quantity_max="1"),
        dict(uom=""),
        dict(valid_from=datetime(2026, 1, 1)),
        dict(valid_to=START),
    ],
)
def test_price_input_validation(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        price(uuid4(), uuid4(), **changes)


@pytest.mark.parametrize("invalid", [None, "", uuid4(), {"tenant_id": uuid4()}])
def test_context_before_queries(invalid: Any) -> None:
    with Session() as session:
        r = CommercialRepository(session)
        operations: list[Callable[[], object]] = [
            lambda: r.add(invalid, product()),
            lambda: r.get(invalid, "products", uuid4()),
            lambda: r.archive(invalid, "products", uuid4()),
            lambda: r.close_window(invalid, "prices", uuid4(), END),
            lambda: r.pricebook(invalid, None, START),
            lambda: r.price(invalid, uuid4(), uuid4(), "EA", Decimal(1), START),
            lambda: r.cost(invalid, uuid4(), START),
            lambda: r.policy(invalid, None, START),
            lambda: r.conversion(invalid, uuid4(), "BOX", "EA", START),
            lambda: r.stock(invalid, uuid4(), START),
            lambda: CommercialService(sessionmaker()).create_batch(invalid, []),
        ]
        for operation in operations:
            with pytest.raises(ValueError, match="TenantContext"):
                operation()


def test_tenant_integrity_and_atomicity(database: Engine) -> None:
    a, pa, ba, ca = seed(database)
    b, pb, bb, cb = seed(database)
    service = CommercialService(sessionmaker(database))
    invalid_records: list[CommercialInput] = [
        price(pb.id, ba.id),
        price(pa.id, bb.id),
        AliasInput(customer_id=cb.id, product_id=pa.id, alias="Blue", source="test"),
        ConversionInput(
            product_id=pb.id,
            from_uom="BOX",
            to_uom="EA",
            factor=Decimal("12"),
            valid_from=START,
            source="test",
        ),
    ]
    for invalid in invalid_records:
        with pytest.raises(IntegrityError):
            service.create_batch(a, [product("ROLLBACK"), invalid])
    with Session(database) as session:
        r = CommercialRepository(session)
        assert r.get(a, "products", pb.id) is None
        assert r.get(b, "customers", ca.id) is None
        assert not r.archive(a, "products", pb.id)
        assert (
            session.execute(select(s.products).where(s.products.c.sku == "ROLLBACK")).first()
            is None
        )
    with pytest.raises(IntegrityError):
        service.create_batch(a, [product()])
    valid_price = price(pa.id, ba.id)
    service.create_batch(a, [valid_price])
    with Session(database) as session:
        r = CommercialRepository(session)
        assert r.get(a, "prices", valid_price.id, product_id=pa.id, pricebook_id=ba.id)
        assert r.get(a, "prices", valid_price.id, product_id=pb.id) is None
        other = product("OTHER")
        r.add(a, other)
        assert r.get(a, "prices", valid_price.id, product_id=other.id) is None
        # Full-path keys reject same-tenant wrong-parent references.
        session.execute(
            text(
                "CREATE TABLE pinned_price (tenant_id uuid, product_id uuid, "
                "pricebook_id uuid, price_id uuid, "
                "FOREIGN KEY (tenant_id,product_id,pricebook_id,price_id) "
                "REFERENCES prices(tenant_id,product_id,pricebook_id,id))"
            )
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text("INSERT INTO pinned_price VALUES (:t,:p,:b,:i)"),
                dict(t=a.tenant_id, p=other.id, b=ba.id, i=valid_price.id),
            )


def test_pricebook_precedence_and_tier_boundaries(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    custom = book("CUSTOM")
    default_assignment = AssignmentInput(pricebook_id=b.id, valid_from=START, source="test")
    assignment = AssignmentInput(
        customer_id=c.id, pricebook_id=custom.id, valid_from=START, valid_to=END, source="test"
    )
    lower = price(p.id, custom.id, quantity_max="10", valid_to=END)
    upper = price(p.id, custom.id, quantity_min="10", valid_to=END, unit_price="0")
    service = CommercialService(sessionmaker(database))
    service.create_batch(ctx, [custom, default_assignment, assignment, lower, upper])
    with Session(database) as session:
        r = CommercialRepository(session)
        assert r.pricebook(ctx, c.id, START)["id"] == custom.id
        assert r.pricebook(ctx, c.id, END)["id"] == b.id
        assert r.price(ctx, p.id, custom.id, "EA", Decimal("9.999999"), START)["id"] == lower.id
        assert r.price(ctx, p.id, custom.id, "EA", Decimal("10"), START)["unit_price"] == 0
        for instant, uom in [(END, "EA"), (START - timedelta(seconds=1), "EA"), (START, "BOX")]:
            with pytest.raises(CommercialError):
                r.price(ctx, p.id, custom.id, uom, Decimal(1), instant)
        with pytest.raises(CommercialError, match="MISSING_PRICE"):
            r.price(ctx, p.id, b.id, "EA", Decimal(1), START)
    with pytest.raises(IntegrityError):
        service.create_batch(ctx, [price(p.id, custom.id, quantity_min="9", quantity_max="11")])
    service.create_batch(ctx, [price(p.id, custom.id, valid_from=END)])


def test_cost_policy_conversion_and_inventory(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    cost = CostInput(
        product_id=p.id,
        uom="EA",
        unit_cost=Decimal("0"),
        valid_from=START,
        valid_to=END,
        source="cost-v1",
    )
    default = PolicyInput(
        key="default",
        version=1,
        rate=Decimal("0.02"),
        permitted=True,
        valid_from=START,
        source="policy",
    )
    custom = PolicyInput(
        key="customer",
        version=1,
        customer_id=c.id,
        rate=Decimal("0.05"),
        permitted=True,
        valid_from=START,
        valid_to=END,
        source="policy",
    )
    conv = ConversionInput(
        product_id=p.id,
        from_uom="BOX",
        to_uom="EA",
        factor=Decimal("0.333333"),
        valid_from=START,
        valid_to=END,
        source="conversion",
    )
    stock = InventoryInput(
        product_id=p.id,
        uom="EA",
        quantity=Decimal("-1"),
        observed_at=START,
        imported_at=START + timedelta(minutes=30),
        source="stock-v1",
    )
    service = CommercialService(sessionmaker(database))
    with Session(database) as session:
        r = CommercialRepository(session)
        assert r.cost(ctx, p.id, START) is None
        assert r.policy(ctx, c.id, START) is None
        assert r.stock(ctx, p.id, START) is None
    service.create_batch(ctx, [cost, default, custom, conv, stock])
    with Session(database) as session:
        r = CommercialRepository(session)
        assert r.cost(ctx, p.id, START)["unit_cost"] == 0  # type: ignore[index]
        assert r.cost(ctx, p.id, END) is None
        assert r.cost(ctx, p.id, START - timedelta(seconds=1)) is None
        assert r.policy(ctx, c.id, START)["id"] == custom.id  # type: ignore[index]
        assert r.policy(ctx, c.id, END)["id"] == default.id  # type: ignore[index]
        assert r.conversion(ctx, p.id, "BOX", "EA", START)["id"] == conv.id
        assert r.stock(ctx, p.id, START) is None  # Not imported yet at this instant.
        evidence = r.stock(ctx, p.id, START + timedelta(hours=2))
        assert evidence is not None and evidence["quantity"] == -1
        assert inventory_freshness(evidence, START + timedelta(hours=2), 120) == "FRESH"
        assert inventory_freshness(evidence, START + timedelta(hours=2, seconds=1), 120) == "STALE"
        assert (
            inventory_freshness(evidence, START - timedelta(seconds=1), 120)
            == "INVALID_FUTURE_INVENTORY"
        )
        assert inventory_freshness(None, START, 120) == "MISSING_INVENTORY"
    with pytest.raises(IntegrityError):
        service.create_batch(
            ctx,
            [
                CostInput(
                    product_id=p.id,
                    uom="BOX",
                    unit_cost=Decimal("1"),
                    valid_from=START,
                    source="overlap",
                )
            ],
        )
    with pytest.raises(IntegrityError):
        service.create_batch(
            ctx,
            [
                PolicyInput(
                    key="another",
                    version=1,
                    rate=Decimal("1"),
                    permitted=False,
                    valid_from=START,
                    source="overlap",
                )
            ],
        )


@pytest.mark.parametrize("kind", ["price", "cost", "policy", "assignment"])
def test_concurrent_conflicting_inserts(database: Engine, kind: str) -> None:
    ctx, p, b, c = seed(database)
    barrier = Barrier(2)

    def insert() -> str:
        record: CommercialInput
        if kind == "price":
            record = price(p.id, b.id)
        elif kind == "cost":
            record = CostInput(
                product_id=p.id, uom="EA", unit_cost=Decimal("1"), valid_from=START, source="test"
            )
        elif kind == "policy":
            record = PolicyInput(
                key=str(uuid4()),
                version=1,
                rate=Decimal("0.1"),
                permitted=True,
                valid_from=START,
                source="test",
            )
        else:
            record = AssignmentInput(pricebook_id=b.id, valid_from=START, source="test")
        barrier.wait(timeout=10)
        try:
            CommercialService(sessionmaker(database)).create_batch(ctx, [record])
            return "committed"
        except IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(insert) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures) == ["committed", "conflict"]


def test_history_and_storage_constraints(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    old = price(p.id, b.id)
    service = CommercialService(sessionmaker(database))
    service.create_batch(ctx, [old])
    with Session(database) as session, session.begin():
        r = CommercialRepository(session)
        assert r.close_window(ctx, "prices", old.id, END)
        r.add(ctx, price(p.id, b.id, valid_from=END, unit_price="20"))
        assert r.archive(ctx, "products", p.id)
    with Session(database) as session:
        r = CommercialRepository(session)
        assert r.get(ctx, "prices", old.id)["unit_price"] == Decimal("12.345600")  # type: ignore[index]
        with pytest.raises(CommercialError):
            r.price(ctx, p.id, b.id, "EA", Decimal(1), END)
        for command in [
            s.prices.delete().where(s.prices.c.id == old.id),
            s.prices.update().where(s.prices.c.id == old.id).values(unit_price=20),
            s.products.delete().where(s.products.c.id == p.id),
            s.prices.update().where(s.prices.c.id == old.id).values(valid_to=None),
        ]:
            with pytest.raises(IntegrityError), session.begin_nested():
                session.execute(command)
        numeric_book = book("NUMERIC")
        r.add(ctx, numeric_book)
        for value in ["NaN", "Infinity", "-1", "0.0000001", "1000000000000000000"]:
            raw = price(p.id, numeric_book.id, valid_from=END + timedelta(days=1)).model_dump()
            raw["unit_price"] = Decimal(value)
            with pytest.raises(IntegrityError), session.begin_nested():
                session.execute(s.prices.insert().values(tenant_id=ctx.tenant_id, **raw))


def test_migration_preserves_baseline_and_reupgrade(empty_database: Engine) -> None:
    migrate(empty_database, revision="0001_tenant_baseline")
    tenant = TenantService(sessionmaker(empty_database)).provision("Asia/Singapore")
    migrate(empty_database)
    assert set(s.TABLES) <= set(inspect(empty_database).get_table_names())
    with Session(empty_database) as session:
        assert (
            session.scalar(
                text("SELECT business_timezone FROM tenant_settings WHERE tenant_id=:t"),
                {"t": tenant.tenant_id},
            )
            == "Asia/Singapore"
        )
    migrate(empty_database, "downgrade", "0001_tenant_baseline")
    assert set(inspect(empty_database).get_table_names()) == {
        "tenants",
        "tenant_settings",
        "alembic_version",
    }
    migrate(empty_database)
    CommercialService(sessionmaker(empty_database)).create_batch(tenant, [product()])


def test_bounded_catalog_fixture(database: Engine) -> None:
    # Two independent synthetic 180-product catalogs; same business keys, separate evidence IDs.
    for _ in range(2):
        ctx, p, b, c = seed(database)
        records: list[CommercialInput] = [
            AssignmentInput(pricebook_id=b.id, valid_from=START, source="fixture")
        ]
        for n in range(180):
            item = product(f"SYN-{n:03}")
            records.extend(
                [
                    item,
                    price(item.id, b.id),
                    CostInput(
                        product_id=item.id,
                        uom="EA",
                        unit_cost=Decimal("7.123456"),
                        valid_from=START,
                        source="fixture",
                    ),
                    ConversionInput(
                        product_id=item.id,
                        from_uom="BOX",
                        to_uom="EA",
                        factor=Decimal("12"),
                        valid_from=START,
                        source="fixture",
                    ),
                    InventoryInput(
                        product_id=item.id,
                        uom="EA",
                        quantity=Decimal("100"),
                        observed_at=START,
                        imported_at=START,
                        source="fixture",
                    ),
                    AliasInput(
                        customer_id=c.id,
                        product_id=item.id,
                        alias=f"Blue pump {n}",
                        source="fixture",
                    ),
                ]
            )
        records.append(
            SuccessorInput(product_id=p.id, successor_id=records[1].id, source="fixture")
        )
        records.append(
            PolicyInput(
                key="standard",
                version=1,
                rate=Decimal("0.05"),
                permitted=True,
                valid_from=START,
                source="fixture",
            )
        )
        CommercialService(sessionmaker(database)).create_batch(ctx, records)
        with Session(database) as session:
            r = CommercialRepository(session)
            assert r.pricebook(ctx, c.id, START)["id"] == b.id
            for record in records:
                if isinstance(record, ProductInput):
                    selected = r.price(ctx, record.id, b.id, "EA", Decimal("12"), START)
                    assert selected["unit_price"] == Decimal("12.345600")
                    assert r.cost(ctx, record.id, START)["unit_cost"] == Decimal("7.123456")  # type: ignore[index]


def test_snapshot_does_not_mix_commits(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    service = CommercialService(sessionmaker(database))
    with service.snapshot(ctx) as r:
        assert r.policy(ctx, c.id, START) is None
        service.create_batch(
            ctx,
            [
                PolicyInput(
                    key="new",
                    version=1,
                    rate=Decimal("0.1"),
                    permitted=True,
                    valid_from=START,
                    source="snapshot",
                )
            ],
        )
        assert r.policy(ctx, c.id, START) is None
    with service.snapshot(ctx) as r:
        assert r.policy(ctx, c.id, START) is not None


@pytest.mark.parametrize("kind", ["price", "cost", "policy", "assignment"])
def test_concurrent_close_and_replacement(database: Engine, kind: str) -> None:
    ctx, p, b, c = seed(database)
    old: CommercialInput
    if kind == "price":
        old = price(p.id, b.id)
    elif kind == "cost":
        old = CostInput(
            product_id=p.id, uom="EA", unit_cost=Decimal("1"), valid_from=START, source="old"
        )
    elif kind == "policy":
        old = PolicyInput(
            key="old",
            version=1,
            rate=Decimal("0.1"),
            permitted=True,
            valid_from=START,
            source="old",
        )
    else:
        old = AssignmentInput(pricebook_id=b.id, valid_from=START, source="old")
    CommercialService(sessionmaker(database)).create_batch(ctx, [old])
    barrier = Barrier(2)

    def replace() -> str:
        fields = old.model_dump()
        fields.update(id=uuid4(), valid_from=END)
        if kind == "policy":
            fields["key"] = str(uuid4())
        replacement = type(old).model_validate(fields)
        barrier.wait(timeout=10)
        try:
            with Session(database) as session, session.begin():
                from quotepilot_api.commercial import INPUT_TABLE

                t = INPUT_TABLE[type(old)]
                session.execute(
                    t.update()
                    .where(t.c.tenant_id == ctx.tenant_id, t.c.id == old.id)
                    .values(valid_to=END)
                )
                CommercialRepository(session).add(ctx, replacement)
            return "committed"
        except IntegrityError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(replace) for _ in range(2)]
        assert sorted(f.result(timeout=20) for f in futures) == ["committed", "conflict"]


def test_missing_book_and_selected_book_without_price(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    service = CommercialService(sessionmaker(database))
    with service.snapshot(ctx) as r, pytest.raises(CommercialError, match="MISSING_PRICEBOOK"):
        r.pricebook(ctx, c.id, START)
    custom = book("CUSTOM")
    service.create_batch(
        ctx,
        [
            custom,
            price(p.id, b.id),
            AssignmentInput(pricebook_id=b.id, valid_from=START, source="default"),
            AssignmentInput(
                pricebook_id=custom.id, customer_id=c.id, valid_from=START, source="customer"
            ),
        ],
    )
    with service.snapshot(ctx) as r:
        chosen = r.pricebook(ctx, c.id, START)
        assert chosen["id"] == custom.id
        with pytest.raises(CommercialError, match="MISSING_PRICE"):
            r.price(ctx, p.id, chosen["id"], "EA", Decimal("1"), START)


def test_defensive_ambiguity_when_corrupt_data_encountered(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    with Session(database) as session, session.begin():
        r = CommercialRepository(session)
        session.execute(text("ALTER TABLE pricebook_assignments DISABLE TRIGGER c_overlap"))
        for _ in range(2):
            r.add(ctx, AssignmentInput(pricebook_id=b.id, valid_from=START, source="corrupt-test"))
        session.execute(text("ALTER TABLE pricebook_assignments ENABLE TRIGGER c_overlap"))
    with (
        CommercialService(sessionmaker(database)).snapshot(ctx) as r,
        pytest.raises(CommercialError, match="AMBIGUOUS_PRICEBOOK"),
    ):
        r.pricebook(ctx, c.id, START)


@pytest.mark.parametrize("value", ["0", "-1", "0.0000001", "NaN", "Infinity", "1e18"])
def test_conversion_input_rejects_invalid_factor(value: str) -> None:
    with pytest.raises(ValueError):
        ConversionInput.model_validate(
            dict(
                product_id=uuid4(),
                from_uom="BOX",
                to_uom="EA",
                factor=value,
                valid_from=START,
                source="test",
            )
        )


@pytest.mark.parametrize("value", ["-0.1", "1.000001", "0.0000001", "NaN"])
def test_policy_input_rejects_invalid_rate(value: str) -> None:
    with pytest.raises(ValueError):
        PolicyInput.model_validate(
            dict(key="p", version=1, rate=value, permitted=True, valid_from=START, source="test")
        )


def test_inventory_future_and_bad_units_rejected_in_database(database: Engine) -> None:
    ctx, p, b, c = seed(database)
    with Session(database) as session:
        raw = InventoryInput(
            product_id=p.id,
            uom="EA",
            quantity=Decimal(0),
            observed_at=START,
            imported_at=START,
            source="test",
        ).model_dump()
        invalid_changes: list[dict[str, Any]] = [
            dict(observed_at=END),
            dict(imported_at=datetime.now(UTC) + timedelta(days=1)),
            dict(uom="not valid"),
        ]
        for changes in invalid_changes:
            with pytest.raises(IntegrityError), session.begin_nested():
                session.execute(
                    s.inventory.insert().values(tenant_id=ctx.tenant_id, **(raw | changes))
                )


def test_registered_schema_matches_migration(database: Engine) -> None:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from quotepilot_api.tenants import Base

    with database.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
