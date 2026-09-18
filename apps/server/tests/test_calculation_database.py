"""QT-007 real PostgreSQL settings, authority selection, isolation and MVCC tests."""

import json
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from test_calculation import AS_OF, codes
from test_commercial import book, price, product

from quotepilot_api.auth import Principal, Role
from quotepilot_api.auth_models import User
from quotepilot_api.calculation import CalculationService
from quotepilot_api.calculation_models import CalculationLine, CalculationRequest, NegotiatedPrice
from quotepilot_api.commercial import CommercialRepository, CommercialService
from quotepilot_api.commercial_inputs import (
    AssignmentInput,
    ConversionInput,
    CostInput,
    CustomerInput,
    InventoryInput,
    PolicyInput,
)
from quotepilot_api.settings import mutate
from quotepilot_api.tenants import TenantService, TenantSettings

D = Decimal


class Catalog:
    def __init__(self, engine: Engine):
        self.sessions = sessionmaker(engine)
        self.context = TenantService(self.sessions).provision("UTC")
        self.admin = Principal(
            self.context, uuid4(), Role.SYSTEM_ADMIN, "admin", False, uuid4(), ""
        )
        with self.sessions.begin() as session:
            session.add(
                User(
                    id=self.admin.user_id,
                    tenant_id=self.context.tenant_id,
                    login="admin",
                    role="system_admin",
                    password_hash="not-used-for-authentication",
                )
            )
            session.flush()
            mutate(
                session,
                self.admin,
                1,
                {
                    "company_name": "Synthetic tenant",
                    "tax_enabled": False,
                    "inventory_max_age_minutes": 120,
                    "default_quote_validity_days": 30,
                    "quote_number_prefix": "Q",
                    "discount_approval_enabled": False,
                    "quote_margin_control_enabled": False,
                    "line_margin_control_enabled": False,
                    "quote_value_approval_enabled": False,
                },
                complete=True,
            )
        self.p, self.b = product("SYN-SKU-VALVE-DN50-BRASS"), book("SYN-PB-STD-V1")
        self.customer = CustomerInput(external_key="SYN-CUST-100", name="Synthetic")
        self.commercial = CommercialService(self.sessions)
        self.commercial.create_batch(
            self.context,
            [
                self.p,
                self.b,
                self.customer,
                AssignmentInput(
                    pricebook_id=self.b.id, valid_from=self.b.valid_from, source="test-default"
                ),
            ],
        )
        self.service = CalculationService(self.sessions)
        self.request = CalculationRequest(
            case_id=uuid4(),
            revision=1,
            customer_id=self.customer.id,
            lines=(
                CalculationLine(
                    line_id="L1",
                    product_id=self.p.id,
                    quantity=D("10"),
                    quote_uom="EA",
                    pricing_uom="EA",
                ),
            ),
        )

    def configure(self, changes: dict[str, object]) -> None:
        with self.sessions.begin() as session:
            current = session.get(TenantSettings, self.context.tenant_id)
            assert current is not None
            mutate(session, self.admin, current.edit_version, changes)

    def calculate(self, **kwargs: Any) -> Any:
        return self.service.calculate(self.context, self.request, pricing_as_of=AS_OF, **kwargs)


def test_gq002_039_customer_precedence_and_no_price_fallback(database: Engine) -> None:
    c = Catalog(database)
    default = price(c.p.id, c.b.id, unit_price="10")
    c.commercial.create_batch(c.context, [default])
    assert c.calculate().total == D("100.00")
    customer_book = book("SYN-PB-C100-V1")
    c.commercial.create_batch(
        c.context,
        [
            customer_book,
            AssignmentInput(
                customer_id=c.customer.id,
                pricebook_id=customer_book.id,
                valid_from=customer_book.valid_from,
                source="customer assignment",
            ),
        ],
    )
    assert codes(c.calculate()) == {"MISSING_PRICE"}
    selected = price(c.p.id, customer_book.id, unit_price="9")
    c.commercial.create_batch(c.context, [selected])
    result = c.calculate()
    assert result.total == D("90.00")
    assert any(str(selected.id) in e.record_json for e in result.lines[0].evidence)


def test_gq003_005_006_007_actual_conversion_tier_and_price_window(database: Engine) -> None:
    c = Catalog(database)
    c.commercial.create_batch(
        c.context,
        [
            ConversionInput(
                product_id=c.p.id,
                from_uom="BOX",
                to_uom="EA",
                factor=D("3"),
                valid_from=c.b.valid_from,
                source="three each",
            ),
            price(c.p.id, c.b.id, quantity_min="0.000001", quantity_max="1", unit_price="15"),
            price(c.p.id, c.b.id, quantity_min="1", unit_price="12", valid_to=AS_OF),
            price(c.p.id, c.b.id, quantity_min="1", unit_price="10", valid_from=AS_OF),
        ],
    )
    line = c.request.lines[0].model_copy(update={"quantity": D("0.333333"), "quote_uom": "BOX"})
    c.request = c.request.model_copy(update={"lines": (line,)})
    result = c.calculate()
    assert result.lines[0].pricing_quantity == D("0.999999") and result.total == D("15.00")
    c.request = c.request.model_copy(
        update={"lines": (line.model_copy(update={"quantity": D("1")}),)}
    )
    assert c.calculate().total == D("30.00")
    assert c.service.calculate(
        c.context, c.request, pricing_as_of=AS_OF - timedelta(microseconds=1)
    ).total == D("36.00")


def test_gq009_010_011_035_discount_cost_windows_and_no_policy(database: Engine) -> None:
    c = Catalog(database)
    c.commercial.create_batch(c.context, [price(c.p.id, c.b.id, unit_price="10")])
    original = c.calculate()
    assert original.lines[0].policy_outcome == "NO_DISCOUNT_POLICY" and original.total == D(
        "100.00"
    )
    c.commercial.create_batch(
        c.context,
        [
            PolicyInput(
                key="default",
                version=1,
                rate=D("0.1"),
                permitted=True,
                valid_from=c.b.valid_from,
                source="default",
            ),
            PolicyInput(
                customer_id=c.customer.id,
                key="customer",
                version=1,
                rate=D("0.05"),
                permitted=True,
                valid_from=c.b.valid_from,
                valid_to=AS_OF,
                source="old",
            ),
            PolicyInput(
                customer_id=c.customer.id,
                key="customer",
                version=2,
                rate=D("0.02"),
                permitted=True,
                valid_from=AS_OF,
                source="new",
            ),
            CostInput(
                product_id=c.p.id,
                uom="EA",
                unit_cost=D("7"),
                valid_from=c.b.valid_from,
                valid_to=AS_OF,
                source="old",
            ),
            CostInput(
                product_id=c.p.id, uom="EA", unit_cost=D("8"), valid_from=AS_OF, source="new"
            ),
        ],
    )
    before = c.service.calculate(c.context, c.request, pricing_as_of=AS_OF - timedelta(seconds=1))
    after = c.calculate()
    assert (before.total, before.margin.extended_cost) == (D("95.00"), D("70"))
    assert (after.total, after.margin.extended_cost) == (D("98.00"), D("80"))
    assert after.commercial_fingerprint != original.commercial_fingerprint
    assert before.commercial_fingerprint != after.commercial_fingerprint


def test_real_settings_and_quote_margin_version_invalidation(database: Engine) -> None:
    c = Catalog(database)
    c.commercial.create_batch(
        c.context,
        [
            price(c.p.id, c.b.id, unit_price="10"),
            CostInput(
                product_id=c.p.id,
                uom="EA",
                unit_cost=D("8"),
                valid_from=c.b.valid_from,
                source="cost",
            ),
        ],
    )
    initial = c.calculate()
    c.configure(
        {
            "quote_margin_control_enabled": True,
            "minimum_margin_threshold": D("0.20"),
            "tax_enabled": True,
            "tax_rate": D("0.09"),
            "freight_taxable": True,
        }
    )
    equality = c.calculate()
    assert equality.total == D("109.00") and equality.state == "CALCULATED"
    assert equality.settings_revision == 2
    assert equality.commercial_fingerprint != initial.commercial_fingerprint
    c.configure({"minimum_margin_threshold": D("0.200001")})
    assert codes(c.calculate()) == {"QUOTE_MARGIN_BELOW_THRESHOLD"}


def test_tenant_ids_cannot_select_foreign_authority(database: Engine) -> None:
    a, b = Catalog(database), Catalog(database)
    a.commercial.create_batch(a.context, [price(a.p.id, a.b.id, unit_price="10")])
    b.commercial.create_batch(b.context, [price(b.p.id, b.b.id, unit_price="20")])
    assert a.calculate().total == D("100") and b.calculate().total == D("200")
    a.request = a.request.model_copy(update={"customer_id": b.customer.id})
    assert codes(a.calculate()) == {"MISSING_ACTIVE_CUSTOMERS"}
    a.request = a.request.model_copy(
        update={
            "customer_id": a.customer.id,
            "lines": (a.request.lines[0].model_copy(update={"product_id": b.p.id}),),
        }
    )
    result = a.calculate()
    assert result.total is None and codes(result) == {"MISSING_PRODUCT"}
    assert all(str(b.context.tenant_id) not in e.record_json for e in result.evidence)


def test_negotiation_does_not_write_master_data(database: Engine) -> None:
    c = Catalog(database)
    original = price(c.p.id, c.b.id, unit_price="42")
    c.commercial.create_batch(c.context, [original])
    sales = Principal(c.context, uuid4(), Role.SALES_ADMIN, "sales", False, uuid4(), "")
    proposal = NegotiatedPrice(
        unit_price=D("39.50"),
        reason="package",
        proposer_id=sales.user_id,
        proposed_at=AS_OF - timedelta(minutes=1),
    )
    c.request = c.request.model_copy(
        update={
            "lines": (
                c.request.lines[0].model_copy(update={"negotiated": proposal}),
            )
        }
    )
    manager = Principal(c.context, uuid4(), Role.SALES_MANAGER, "manager", False, uuid4(), "")
    first = c.calculate(actor=sales)
    second = c.calculate(actor=manager)
    third = c.calculate()
    assert first.total == D("395.00") and second.state == third.state == "APPROVAL_REQUIRED"
    assert first.lines[0].proposer_id == second.lines[0].proposer_id == proposal.proposer_id
    assert first.lines[0].proposed_at == second.lines[0].proposed_at == proposal.proposed_at
    assert (
        first.commercial_fingerprint
        == second.commercial_fingerprint
        == third.commercial_fingerprint
    )
    with c.sessions() as session:
        stored = CommercialRepository(session).get(c.context, "prices", original.id)
        assert stored is not None and stored["unit_price"] == D("42")


def test_settings_and_commercial_reads_share_one_snapshot(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = Catalog(database)
    c.commercial.create_batch(c.context, [price(c.p.id, c.b.id, unit_price="10")])
    previous = CommercialRepository.pricebook_selection
    invoked = False

    def concurrent_change(repo: CommercialRepository, *args: Any, **kwargs: Any) -> Any:
        nonlocal invoked
        if not invoked:
            invoked = True
            c.configure({"tax_enabled": True, "tax_rate": D("0.09"), "freight_taxable": True})
            c.commercial.create_batch(
                c.context,
                [
                    PolicyInput(
                        key="new",
                        version=1,
                        rate=D("0.1"),
                        permitted=True,
                        valid_from=c.b.valid_from,
                        source="concurrent policy",
                    )
                ],
            )
        return previous(repo, *args, **kwargs)

    monkeypatch.setattr(CommercialRepository, "pricebook_selection", concurrent_change)
    first = c.calculate()
    second = c.calculate()
    assert first.total == D("100.00") and first.settings_revision == 1
    assert first.lines[0].policy_outcome == "NO_DISCOUNT_POLICY"
    assert second.total == D("98.10") and second.settings_revision == 2
    assert second.lines[0].discount_rate == D("0.1")


def test_inventory_is_evidence_only_and_freshness_rechecked(database: Engine) -> None:
    c = Catalog(database)
    c.commercial.create_batch(
        c.context,
        [
            price(c.p.id, c.b.id, unit_price="10"),
            InventoryInput(
                product_id=c.p.id,
                uom="EA",
                quantity=D("12"),
                observed_at=AS_OF - timedelta(minutes=120),
                imported_at=AS_OF,
                source="aggregate",
            ),
        ],
    )
    c.request = c.request.model_copy(
        update={"lines": (c.request.lines[0].model_copy(update={"availability_required": True}),)}
    )
    first = c.calculate()
    later = c.service.calculate(
        c.context, c.request, pricing_as_of=AS_OF + timedelta(microseconds=1)
    )
    assert first.lines[0].aggregate_available == D("12")
    assert later.lines[0].aggregate_available is None and later.state == "HARD_BLOCK"
    assert first.commercial_fingerprint != later.commercial_fingerprint
    record = next(e for e in first.lines[0].evidence if e.kind == "inventory")
    assert json.loads(record.record_json)["source"] == "aggregate"


def test_same_book_assignment_rollover_changes_authority(database: Engine) -> None:
    c = Catalog(database)
    c.commercial.create_batch(c.context, [price(c.p.id, c.b.id, unit_price="10")])
    first = c.calculate()
    selected = json.loads(
        next(e.record_json for e in first.evidence if e.kind == "pricebook_assignment")
    )
    boundary = AS_OF + timedelta(seconds=1)
    replacement = AssignmentInput(pricebook_id=c.b.id, valid_from=boundary, source="replacement")
    with c.sessions.begin() as session:
        repo = CommercialRepository(session)
        assert repo.close_window(c.context, "pricebook_assignments", UUID(selected["id"]), boundary)
        repo.add(c.context, replacement)
    second = c.service.calculate(c.context, c.request, pricing_as_of=boundary)
    assert first.total == second.total == D("100.00")
    assert first.commercial_fingerprint != second.commercial_fingerprint
    new_assignment = json.loads(
        next(e.record_json for e in second.evidence if e.kind == "pricebook_assignment")
    )
    assert new_assignment["id"] == str(replacement.id)
    assert new_assignment["pricebook_id"] == selected["pricebook_id"] == str(c.b.id)


def test_substitution_requires_tenant_local_product_truth(database: Engine) -> None:
    a, b = Catalog(database), Catalog(database)
    a.commercial.create_batch(a.context, [price(a.p.id, a.b.id, unit_price="10")])
    original = product("ORIGINAL")
    a.commercial.create_batch(a.context, [original])
    line = a.request.lines[0]
    for missing in (uuid4(), b.p.id):
        a.request = a.request.model_copy(
            update={"lines": (line.model_copy(update={"substitute_for": missing}),)}
        )
        result = a.calculate()
        assert result.total is None and result.state == "HARD_BLOCK"
        assert codes(result) == {"MISSING_REQUESTED_PRODUCT"}
        assert all(str(b.context.tenant_id) not in e.record_json for e in result.lines[0].evidence)
    a.request = a.request.model_copy(
        update={"lines": (line.model_copy(update={"substitute_for": original.id}),)}
    )
    result = a.calculate()
    assert result.total == D("100.00") and codes(result) == {"SUBSTITUTION_PROPOSED"}
    assert any(
        e.kind == "requested_product" and str(original.id) in e.record_json
        for e in result.lines[0].evidence
    )
    with a.sessions.begin() as session:
        CommercialRepository(session).archive(a.context, "products", original.id)
    assert codes(a.calculate()) == {"MISSING_REQUESTED_PRODUCT"}


def test_incomplete_setup_blocks_calculation(database: Engine) -> None:
    c = Catalog(database)
    other = TenantService(c.sessions).provision("UTC")
    assert codes(c.service.calculate(other, c.request)) == {"SETUP_INCOMPLETE"}
    with Session(database) as session:
        assert (
            session.scalar(
                select(TenantSettings.active_settings_revision).where(
                    TenantSettings.tenant_id == other.tenant_id
                )
            )
            is None
        )
