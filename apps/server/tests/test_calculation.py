"""Independent COM-001/002 and QA-001 oracles, not snapshots of engine output."""

import json
import random
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import sessionmaker

from quotepilot_api import calculation
from quotepilot_api.auth import AuthError, Principal, Role
from quotepilot_api.calculation import CalculationService
from quotepilot_api.calculation_models import CalculationLine, CalculationRequest, NegotiatedPrice
from quotepilot_api.commercial import CommercialError
from quotepilot_api.settings_models import SettingsRevision
from quotepilot_api.tenants import TenantContext

D = Decimal
AS_OF = datetime(2026, 9, 9, 12, tzinfo=UTC)


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch):
        self.context = TenantContext(uuid4())
        self.actor = Principal(self.context, uuid4(), Role.SALES_ADMIN, "sales", False, uuid4(), "")
        self.product = uuid4()
        self.repo = MagicMock()
        self.repo.get.return_value = {"id": self.product, "archived": False}
        self.repo.pricebook.return_value = {"id": uuid4(), "version": 1}
        self.repo.pricebook_selection.return_value = (
            {"id": uuid4(), "customer_id": None},
            self.repo.pricebook.return_value,
        )
        self.repo.price.return_value = {"id": uuid4(), "unit_price": D("100.00")}
        self.repo.policy.return_value = None
        self.repo.cost.return_value = {"id": uuid4(), "uom": "EA", "unit_cost": D("70.00")}
        self.repo.stock.return_value = None
        self.repo.conversion.return_value = {"id": uuid4(), "factor": D("3")}
        self.config = SettingsRevision(
            revision=1,
            company_name="Synthetic",
            currency_code="SGD",
            tax_enabled=False,
            inventory_max_age_minutes=120,
            default_quote_validity_days=30,
            quote_number_prefix="Q",
            discount_approval_enabled=False,
            quote_margin_control_enabled=False,
            line_margin_control_enabled=False,
            quote_value_approval_enabled=False,
        )
        monkeypatch.setattr(
            calculation,
            "current",
            lambda *a: MagicMock(setup_completed_at=AS_OF, active_settings_revision=1),
        )
        monkeypatch.setattr(calculation, "read_revision", lambda *a: self.config)
        self.service = CalculationService(sessionmaker())
        snapshot = MagicMock()
        snapshot.return_value.__enter__.return_value = self.repo
        monkeypatch.setattr(self.service.commercial, "snapshot", snapshot)

    def line(self, **changes: Any) -> CalculationLine:
        return CalculationLine(
            **{
                "line_id": "L1",
                "product_id": self.product,
                "quantity": "1",
                "quote_uom": "EA",
                "pricing_uom": "EA",
                **changes,
            }
        )

    def negotiated(
        self,
        unit_price: str | Decimal = "39.50",
        reason: str = "project",
        **changes: Any,
    ) -> NegotiatedPrice:
        return NegotiatedPrice(
            **{
                "unit_price": D(unit_price),
                "reason": reason,
                "proposer_id": self.actor.user_id,
                "proposed_at": AS_OF - timedelta(minutes=1),
                **changes,
            }
        )

    def request(self, **changes: Any) -> CalculationRequest:
        return CalculationRequest(
            **{
                "case_id": uuid4(),
                "revision": 1,
                "lines": (self.line(),),
                **changes,
            }
        )

    def run(self, **changes: Any) -> Any:
        return self.service.calculate(
            self.context, self.request(**changes), actor=self.actor, pricing_as_of=AS_OF
        )

    def policy(self, rate: str, permitted: bool = True) -> None:
        self.repo.policy.return_value = {"id": uuid4(), "rate": D(rate), "permitted": permitted}


@pytest.fixture
def h(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


def codes(result: Any) -> set[str]:
    return {f.code for f in result.findings}


def test_gq001_multiline_and_gq012_rounding(h: Harness) -> None:
    h.policy("0.05")
    h.config.tax_enabled, h.config.tax_rate, h.config.freight_taxable = True, D("0.09"), True
    h.repo.price.side_effect = [
        {"id": uuid4(), "unit_price": D("12.40")},
        {"id": uuid4(), "unit_price": D("20.00")},
    ]
    result = h.run(lines=(h.line(quantity="5"), h.line(line_id="L2", quantity="3")), freight="5.00")
    assert [x.line_net for x in result.lines] == [D("58.90"), D("57.00")]
    assert (result.subtotal, result.taxable_base, result.tax, result.total) == (
        D("115.90"),
        D("120.90"),
        D("10.88"),
        D("131.78"),
    )
    h.repo.price.side_effect = None
    h.repo.price.return_value["unit_price"] = D("12.3456")
    h.policy("0.10")
    result = h.run(lines=(h.line(quantity="3"),), freight="5.00")
    assert (result.lines[0].raw_line, result.subtotal, result.tax, result.total) == (
        D("37.0368"),
        D("33.33"),
        D("3.45"),
        D("41.78"),
    )
    payload = json.loads(result.model_dump_json())
    assert payload["total"] == "41.78" and payload["lines"][0]["pricing_quantity"] == "3"


def test_gq007_conversion_is_not_rounded_before_tier(h: Harness) -> None:
    h.repo.price.return_value["unit_price"] = D("15.00")
    result = h.run(lines=(h.line(quantity="0.333333", quote_uom="BOX"),))
    assert result.lines[0].pricing_quantity == D("0.999999")
    assert h.repo.price.call_args.args[4] == D("0.999999")
    assert result.subtotal == D("15.00")


@pytest.mark.parametrize(
    "bad", [1.1, 1, True, "NaN", "Infinity", "1e999999", "0", "-1", "1.0000001"]
)
def test_gq008_invalid_quantity(h: Harness, bad: Any) -> None:
    with pytest.raises(ValueError):
        h.line(quantity=bad)


@pytest.mark.parametrize("bad", [1.1, True, "-0.01", "NaN", "Infinity", "1e18", "0.001"])
def test_gq012_invalid_freight(h: Harness, bad: Any) -> None:
    with pytest.raises(ValueError):
        h.request(freight=bad)


@pytest.mark.parametrize("cost,breach", [("70.00", False), ("70.000001", True)])
def test_gq013_014_exact_quote_margin(h: Harness, cost: str, breach: bool) -> None:
    h.config.quote_margin_control_enabled = True
    h.config.minimum_margin_threshold = D("0.30")
    h.repo.cost.return_value["unit_cost"] = D(cost)
    result = h.run()
    assert ("QUOTE_MARGIN_BELOW_THRESHOLD" in codes(result)) is breach
    assert result.margin.fraction_display == D("0.300000")


@pytest.mark.parametrize("line_control,quote_control", [(True, False), (False, True), (True, True)])
@pytest.mark.parametrize("mode", ["missing", "ambiguous", "zero", "conversion"])
def test_gq015_016_020_margin_blocks(
    h: Harness,
    line_control: bool,
    quote_control: bool,
    mode: str,
) -> None:
    h.config.line_margin_control_enabled, h.config.minimum_line_margin_threshold = (
        line_control,
        D("0.2"),
    )
    h.config.quote_margin_control_enabled, h.config.minimum_margin_threshold = (
        quote_control,
        D("0.2"),
    )
    expected = "MARGIN_UNKNOWN"
    if mode == "missing":
        h.repo.cost.return_value = None
    elif mode == "ambiguous":
        h.repo.cost.side_effect = CommercialError("AMBIGUOUS_COST")
        expected = "AMBIGUOUS_COST"
    elif mode == "zero":
        h.policy("1")
        expected = "MARGIN_UNDEFINED"
    else:
        h.repo.cost.return_value["uom"] = "BOX"
        h.repo.conversion.side_effect = CommercialError("MISSING_CONVERSION")
    result = h.run()
    assert result.state == "HARD_BLOCK" and expected in codes(result)
    assert result.total == (D("0.00") if mode == "zero" else D("100.00"))


@pytest.mark.parametrize(
    "seconds,state", [(7200, "FRESH"), (7201, "STALE"), (-1, "INVALID_FUTURE_INVENTORY")]
)
def test_gq017_034_inventory(h: Harness, seconds: int, state: str) -> None:
    h.repo.stock.return_value = {
        "id": uuid4(),
        "quantity": D("12"),
        "uom": "EA",
        "observed_at": AS_OF - timedelta(seconds=seconds),
        "imported_at": AS_OF,
    }
    result = h.run(lines=(h.line(availability_required=True),))
    assert result.lines[0].inventory_state == state
    assert result.lines[0].aggregate_available == (D("12") if state == "FRESH" else None)
    assert result.state == ("CALCULATED" if state == "FRESH" else "HARD_BLOCK")
    assert result.total == D("100.00")
    assert not {"warehouse", "delivery_date", "shipment"} & result.lines[0].model_dump().keys()
    assert h.run().state == "CALCULATED"


@pytest.mark.parametrize("rate,breach", [("0.10", False), ("0.100001", True)])
def test_gq018_discount_boundary(h: Harness, rate: str, breach: bool) -> None:
    h.policy(rate)
    h.config.discount_approval_enabled, h.config.discount_approval_threshold = True, D("0.10")
    result = h.run()
    assert result.total == D("90.00")
    assert ("DISCOUNT_ABOVE_THRESHOLD" in codes(result)) is breach


@pytest.mark.parametrize("freight,breach", [("0", False), ("0.01", True)])
def test_gq019_final_total_boundary(h: Harness, freight: str, breach: bool) -> None:
    h.repo.price.return_value["unit_price"] = D("10000")
    h.config.quote_value_approval_enabled, h.config.quote_value_approval_threshold = (
        True,
        D("10000"),
    )
    assert ("QUOTE_VALUE_ABOVE_THRESHOLD" in codes(h.run(freight=freight))) is breach


def test_gq021_022_023_negotiation(h: Harness) -> None:
    h.repo.price.return_value["unit_price"] = D("42.00")
    h.policy("0.02")
    assert h.run(lines=(h.line(quantity="10"),)).total == D("411.60")
    line = h.line(quantity="10", negotiated=h.negotiated())
    result = h.run(lines=(line,))
    calculated = result.lines[0]
    assert result.total == D("395.00")
    assert calculated.normal_reference_unit_price == D("41.1600")
    assert calculated.absolute_unit_delta == D("-1.6600")
    assert (calculated.deviation_numerator, calculated.deviation_denominator) == (
        D("1.6600"),
        D("41.1600"),
    )
    assert calculated.proposer_id == h.actor.user_id
    assert codes(result) == {"NEGOTIATED_UNIT_PRICE"} and result.state == "APPROVAL_REQUIRED"
    h.repo.price.return_value["unit_price"] = D("0")
    result = h.run(lines=(h.line(negotiated=h.negotiated(unit_price="0", reason="sample")),))
    assert result.total == D("0.00") and result.lines[0].deviation_denominator is None
    assert result.state == "APPROVAL_REQUIRED"


def test_gq024_025_independent_margins_and_all_exceptions(h: Harness) -> None:
    h.config.line_margin_control_enabled = h.config.quote_margin_control_enabled = True
    h.config.minimum_line_margin_threshold = h.config.minimum_margin_threshold = D("0.20")
    h.repo.cost.side_effect = [
        {"id": uuid4(), "unit_cost": D("80.01"), "uom": "EA"},
        {"id": uuid4(), "unit_cost": D("70"), "uom": "EA"},
    ]
    result = h.run(lines=(h.line(), h.line(line_id="L2", quantity="3")))
    assert result.total == D("400.00") and result.margin.extended_cost == D("290.01")
    assert result.margin.fraction_display == D("0.274975")
    assert codes(result) == {"LINE_MARGIN_BELOW_THRESHOLD"}
    h.repo.cost.side_effect = None
    h.repo.cost.return_value["unit_cost"] = D("33")
    h.repo.price.return_value["unit_price"] = D("42")
    h.policy("0.02")
    h.config.quote_value_approval_enabled, h.config.quote_value_approval_threshold = (
        True,
        D("10000"),
    )
    result = h.run(lines=(h.line(quantity="300", negotiated=h.negotiated(reason="package")),))
    assert result.total == D("11850.00") and result.margin.extended_cost == D("9900")
    assert codes(result) == {
        "NEGOTIATED_UNIT_PRICE",
        "LINE_MARGIN_BELOW_THRESHOLD",
        "QUOTE_MARGIN_BELOW_THRESHOLD",
        "QUOTE_VALUE_ABOVE_THRESHOLD",
    }


@pytest.mark.parametrize("resolution", ["missing", "ambiguous"])
def test_gq026_041_product_resolution_before_proposal(h: Harness, resolution: str) -> None:
    result = h.run(
        lines=(
            h.line(
                product_id=None,
                resolution=resolution,
                negotiated=h.negotiated(),
            ),
        )
    )
    assert result.total is None and "NEGOTIATED_UNIT_PRICE" not in codes(result)
    assert result.state == ("CLARIFICATION_REQUIRED" if resolution == "ambiguous" else "HARD_BLOCK")


def test_gq027_substitute(h: Harness) -> None:
    result = h.run(lines=(h.line(substitute_for=uuid4()),))
    assert codes(result) == {"SUBSTITUTION_PROPOSED"} and result.state == "APPROVAL_REQUIRED"


def test_gq036_missing_control_is_not_disabled(h: Harness) -> None:
    h.config.discount_approval_enabled = None
    assert codes(h.run()) == {"SETUP_INCOMPLETE"}


def test_gq038_line_equality(h: Harness) -> None:
    h.config.line_margin_control_enabled, h.config.minimum_line_margin_threshold = True, D("0.2")
    h.repo.cost.return_value["unit_cost"] = D("80")
    assert h.run().state == "CALCULATED"


@pytest.mark.parametrize("error", ["MISSING_PRICE", "AMBIGUOUS_PRICE"])
def test_gq004_040_no_negotiated_fallback(h: Harness, error: str) -> None:
    h.repo.price.side_effect = CommercialError(error)
    result = h.run(lines=(h.line(negotiated=h.negotiated()),))
    assert result.total is None and codes(result) == {error}
    assert result.lines[0].negotiated_unit_price is None


@pytest.mark.parametrize(
    "changes", [{"unit_price": "-0.01"}, {"reason": ""}, {"reason": "   "}, {"unit_price": 39.5}]
)
def test_gq040_041_invalid_adoption(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        NegotiatedPrice(
            **{
                "unit_price": "39.50",
                "reason": "project",
                "proposer_id": uuid4(),
                "proposed_at": AS_OF - timedelta(minutes=1),
                **changes,
            }
        )


@pytest.mark.parametrize("missing", ["proposer_id", "proposed_at"])
def test_negotiated_provenance_is_required(missing: str) -> None:
    values: dict[str, Any] = {
        "unit_price": "39.50",
        "reason": "project",
        "proposer_id": uuid4(),
        "proposed_at": AS_OF - timedelta(minutes=1),
    }
    values.pop(missing)
    with pytest.raises(ValueError):
        NegotiatedPrice(**values)


def test_recalculation_preserves_negotiated_provenance_and_tenant(h: Harness) -> None:
    proposal = h.negotiated(unit_price="1")
    request = h.request(lines=(h.line(negotiated=proposal),))
    manager = Principal(h.context, uuid4(), Role.SALES_MANAGER, "manager", False, uuid4(), "")
    results = [
        h.service.calculate(h.context, request, actor=None, pricing_as_of=AS_OF),
        h.service.calculate(h.context, request, actor=h.actor, pricing_as_of=AS_OF),
        h.service.calculate(h.context, request, actor=manager, pricing_as_of=AS_OF),
    ]
    assert {result.commercial_fingerprint for result in results} == {
        results[0].commercial_fingerprint
    }
    assert all(result.lines[0].proposer_id == proposal.proposer_id for result in results)
    assert all(result.lines[0].proposed_at == proposal.proposed_at for result in results)

    changed_proposal = proposal.model_copy(
        update={"proposed_at": proposal.proposed_at - timedelta(seconds=1)}
    )
    changed = request.model_copy(
        update={"lines": (request.lines[0].model_copy(update={"negotiated": changed_proposal}),)}
    )
    assert (
        h.service.calculate(h.context, changed, pricing_as_of=AS_OF).commercial_fingerprint
        != results[0].commercial_fingerprint
    )

    with pytest.raises(AuthError, match="TENANT_SCOPE_VIOLATION"):
        h.service.calculate(TenantContext(uuid4()), request, actor=h.actor)


def test_nested_construct_cannot_bypass_decimal_validation(h: Harness) -> None:
    line = h.line().model_copy(update={"quantity": 1.2})
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"), pytest.raises(ValueError):
        h.service.calculate(h.context, h.request(lines=(line,)))


def test_ambient_decimal_context_cannot_change_money_or_policy(h: Harness) -> None:
    h.repo.price.return_value["unit_price"] = D("12.3456")
    h.policy("0.1")
    with localcontext() as ctx:
        ctx.prec, ctx.rounding, ctx.Emax = 2, ROUND_DOWN, 2
        ctx.traps[Inexact] = True
        result = h.run(lines=(h.line(quantity="3"),))
    assert result.total == D("33.33")


def test_gq028_029_030_033_material_fingerprint(h: Harness) -> None:
    request = h.request()
    first = h.service.calculate(h.context, request, pricing_as_of=AS_OF)
    second = h.service.calculate(h.context, request, pricing_as_of=AS_OF + timedelta(seconds=1))
    assert first.commercial_fingerprint == second.commercial_fingerprint
    h.policy("0.02")
    third = h.service.calculate(h.context, request, pricing_as_of=AS_OF)
    assert third.commercial_fingerprint != first.commercial_fingerprint
    assert first.lines[0].policy_outcome == "NO_DISCOUNT_POLICY"
    assert third.lines[0].policy_outcome == "SELECTED"
    edited = request.model_copy(update={"revision": 2, "lines": (h.line(quantity="2"),)})
    assert (
        h.service.calculate(h.context, edited).commercial_fingerprint
        != third.commercial_fingerprint
    )


def test_exact_cost_conversion_and_no_intermediate_rounding(h: Harness) -> None:
    h.repo.cost.return_value.update(uom="BOX", unit_cost=D("0.123456"))
    h.repo.conversion.return_value["factor"] = D("0.333333")
    result = h.run(lines=(h.line(quantity="0.333333"),))
    assert result.margin.extended_cost == D("0.013717305898680384")


@pytest.mark.parametrize("taxable,expected", [(False, "9.00"), (True, "9.45")])
def test_freight_taxability(h: Harness, taxable: bool, expected: str) -> None:
    h.config.tax_enabled, h.config.tax_rate, h.config.freight_taxable = True, D("0.09"), taxable
    assert h.run(freight="5.00").tax == D(expected)


@pytest.mark.parametrize(
    "operation,error",
    [
        ("pricebook_selection", "MISSING_PRICEBOOK"),
        ("pricebook_selection", "AMBIGUOUS_PRICEBOOK"),
        ("policy", "AMBIGUOUS_DISCOUNT_POLICY"),
        ("conversion", "MISSING_CONVERSION"),
        ("conversion", "AMBIGUOUS_CONVERSION"),
    ],
)
def test_gq008_011_039_authority_errors(h: Harness, operation: str, error: str) -> None:
    getattr(h.repo, operation).side_effect = CommercialError(error)
    result = h.run(lines=(h.line(quote_uom="BOX"),))
    assert result.total is None and result.state == "HARD_BLOCK" and codes(result) == {error}


def test_unpermitted_policy_and_missing_inventory_fail_closed(h: Harness) -> None:
    h.policy("1", permitted=False)
    result = h.run(lines=(h.line(availability_required=True),))
    assert result.state == "HARD_BLOCK"
    assert codes(result) == {"DISCOUNT_NOT_PERMITTED", "MISSING_INVENTORY"}


def test_integer_oracle_arithmetic_properties(h: Harness) -> None:
    """300 seeded scenarios, with independent integer half-up calculations (COM-R06)."""
    rng = random.Random(7007)
    scale = 1000000
    for _ in range(300):
        quantity, price, rate = (
            rng.randrange(1, 10**9),
            rng.randrange(0, 10**10),
            rng.randrange(scale + 1),
        )
        h.repo.price.return_value["unit_price"] = D(price).scaleb(-6)
        h.policy(format(D(rate).scaleb(-6), "f"))
        result = h.run(lines=(h.line(quantity=format(D(quantity).scaleb(-6), "f")),))
        numerator, denominator = quantity * price * (scale - rate) * 100, scale**3
        expected_cents = (2 * numerator + denominator) // (2 * denominator)
        assert result.total == D(expected_cents).scaleb(-2)


def test_largest_supported_factors_remain_exact(h: Harness) -> None:
    value = D("999999999999999999.999999")
    h.repo.price.return_value["unit_price"] = value
    h.repo.conversion.return_value["factor"] = value
    h.repo.cost.return_value.update(uom="COST", unit_cost=value)
    result = h.run(lines=(h.line(quantity=value, quote_uom="BOX"),))
    # Independent arbitrary-precision integer numerator/scale oracle.
    integer = 10**24 - 1
    expected = D(f"{integer**4 // 10**24}.{integer**4 % 10**24:024d}")
    assert result.margin.extended_cost == expected


def test_half_up_tie_and_quote_level_tax(h: Harness) -> None:
    h.repo.price.return_value["unit_price"] = D("0.005")
    assert h.run().total == D("0.01")
    h.repo.price.return_value["unit_price"] = D("0.05")
    h.config.tax_enabled, h.config.tax_rate, h.config.freight_taxable = True, D("0.1"), False
    result = h.run(lines=(h.line(), h.line(line_id="L2")))
    assert result.subtotal == D("0.10") and result.tax == D("0.01") and result.total == D("0.11")
