"""Deterministic commercial evaluation. No writes, approval grants, or public endpoint."""

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api.auth import AuthError, Capability, Principal
from quotepilot_api.calculation_models import (
    CalculationLine,
    CalculationRequest,
    CalculationResult,
    Evidence,
    Finding,
    LineCalculation,
    Margin,
)
from quotepilot_api.commercial import CommercialError, CommercialRepository, CommercialService
from quotepilot_api.commercial import inventory_freshness as freshness
from quotepilot_api.commercial_inputs import utc
from quotepilot_api.settings import SNAPSHOT_FIELDS, current, missing, read_revision
from quotepilot_api.settings_models import SettingsRevision
from quotepilot_api.tenants import TenantContext, require_context

ZERO = Decimal("0")
CENT = Decimal("0.01")
# Max 1000 lines, four <=24-digit factors and a <=6-place threshold fit in 128 digits.
# A fresh Context also isolates rounding, exponent limits and traps from ambient callers.
ARITHMETIC = Context(prec=128, rounding=ROUND_HALF_UP)


def canonical(value: object) -> str:
    def encode(item: object) -> str:
        if isinstance(item, Decimal):
            return format(item, "f")
        return str(item)

    return json.dumps(value, default=encode, sort_keys=True, separators=(",", ":"))


def evidence(kind: str, row: Mapping[Any, Any]) -> Evidence:
    return Evidence(kind=kind, record_json=canonical(dict(row)))


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def margin(net: Decimal, cost: Decimal | None, *, ambiguous: bool = False) -> Margin:
    if ambiguous:
        return Margin(state="AMBIGUOUS_COST")
    if cost is None:
        return Margin(state="MARGIN_UNKNOWN")
    if net <= 0:
        return Margin(state="MARGIN_UNDEFINED", extended_cost=cost)
    numerator = net - cost
    return Margin(
        state="DEFINED",
        extended_cost=cost,
        numerator=numerator,
        denominator=net,
        fraction_display=(numerator / net).quantize(Decimal("0.000001")),
    )


def check_margin(
    value: Margin, threshold: Decimal | None, code: str, line_id: str | None = None
) -> list[Finding]:
    if threshold is None:
        return []
    if value.state != "DEFINED":
        return [Finding(code=value.state, kind="hard_block", line_id=line_id)]
    assert value.numerator is not None and value.denominator is not None
    if value.numerator < threshold * value.denominator:
        return [Finding(code=code, kind="approval", line_id=line_id)]
    return []


class CalculationService:
    """Trusted application API. Pass only a server-authenticated Principal for adoption.

    Untrusted candidate text is deliberately absent from CalculationRequest. The caller
    must explicitly adopt it through its authenticated edit workflow before passing a
    NegotiatedPrice. case_id/revision bind evaluation to the caller's immutable revision;
    QT-008 persists it and QT-020 revalidates and records approval, never this service.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.commercial = CommercialService(sessions)

    def calculate(
        self,
        context: TenantContext,
        request: CalculationRequest,
        *,
        actor: Principal | None = None,
        pricing_as_of: datetime | None = None,
    ) -> CalculationResult:
        tenant = require_context(context)
        # model_copy/model_construct must not bypass this boundary or nested validation.
        checked = CalculationRequest.model_validate(request.model_dump())
        instant = utc(pricing_as_of) if pricing_as_of is not None else datetime.now(UTC)
        if actor is not None and actor.context != context:
            raise AuthError("TENANT_SCOPE_VIOLATION", 403)
        with self.commercial.snapshot(context) as repo, localcontext(ARITHMETIC):
            findings: list[Finding] = []
            records: list[Evidence] = []
            settings = current(repo.session, context)
            config: SettingsRevision | None = None
            if settings.setup_completed_at is not None and settings.active_settings_revision:
                config = read_revision(repo.session, context, settings.active_settings_revision)
                values = {key: getattr(config, key) for key in SNAPSHOT_FIELDS}
                records.append(evidence("settings", {**values, "revision": config.revision}))
                if missing(values) or config.currency_code != "SGD":
                    config = None
            if config is None:
                findings.append(Finding(code="SETUP_INCOMPLETE", kind="hard_block"))

            lines: list[LineCalculation] = []
            if config is not None:
                try:
                    assignment, book = repo.pricebook_selection(
                        context, checked.customer_id, instant
                    )
                    policy = repo.policy(context, checked.customer_id, instant)
                    records.append(evidence("pricebook_assignment", assignment))
                    records.append(evidence("pricebook", book))
                    if checked.customer_id is not None:
                        customer = repo.get(context, "customers", checked.customer_id)
                        assert customer is not None
                        records.append(evidence("customer", customer))
                    records.append(
                        evidence("discount_policy", policy)
                        if policy is not None
                        else evidence("discount_policy", {"outcome": "NO_DISCOUNT_POLICY"})
                    )
                    for line in checked.lines:
                        result, issues = self._line(
                            repo, context, line, config, book, policy, instant, actor
                        )
                        lines.append(result)
                        findings.extend(issues)
                except CommercialError as exc:
                    findings.append(Finding(code=str(exc), kind="hard_block"))

            subtotal = tax = total = taxable_base = None
            quote_margin = Margin(state="MARGIN_UNKNOWN")
            if len(lines) == len(checked.lines) and all(x.line_net is not None for x in lines):
                assert config is not None
                subtotal = sum((x.line_net for x in lines if x.line_net is not None), ZERO)
                taxable_base = subtotal + (checked.freight if config.freight_taxable else ZERO)
                if config.tax_enabled:
                    assert config.tax_rate is not None
                    tax = money(taxable_base * config.tax_rate)
                else:
                    tax = money(ZERO)
                total = subtotal + checked.freight + tax
                costs = [x.margin.extended_cost for x in lines]
                cost = sum((c for c in costs if c is not None), ZERO)
                quote_margin = margin(
                    subtotal,
                    cost if all(c is not None for c in costs) else None,
                    ambiguous=any(x.margin.state == "AMBIGUOUS_COST" for x in lines),
                )
                findings.extend(
                    check_margin(
                        quote_margin,
                        config.minimum_margin_threshold
                        if config.quote_margin_control_enabled
                        else None,
                        "QUOTE_MARGIN_BELOW_THRESHOLD",
                    )
                )
                if config.quote_value_approval_enabled:
                    assert config.quote_value_approval_threshold is not None
                    if total > config.quote_value_approval_threshold:
                        findings.append(
                            Finding(code="QUOTE_VALUE_ABOVE_THRESHOLD", kind="approval")
                        )

            state: Any = "CALCULATED"
            for kind, candidate in [
                ("approval", "APPROVAL_REQUIRED"),
                ("clarification", "CLARIFICATION_REQUIRED"),
                ("hard_block", "HARD_BLOCK"),
            ]:
                if any(f.kind == kind for f in findings):
                    state = candidate
            # Exclude only evaluation time from comparison; explicit evidence windows,
            # settings, inputs, outcomes and relevant availability all remain material.
            material_lines = [x.model_dump(exclude={"proposed_at"}) for x in lines]
            for input_line, material in zip(checked.lines, material_lines, strict=False):
                if not input_line.availability_required:
                    for key in ("inventory_state", "aggregate_available", "inventory_uom"):
                        material.pop(key, None)
                    material["evidence"] = [
                        e for e in material["evidence"] if e["kind"] != "inventory"
                    ]
            fingerprint = hashlib.sha256(
                canonical(
                    {
                        "tenant": tenant,
                        "request": checked.model_dump(),
                        "evidence": [x.model_dump() for x in records],
                        "lines": material_lines,
                        "findings": [x.model_dump() for x in findings],
                    }
                ).encode()
            ).hexdigest()
            return CalculationResult(
                tenant_id=tenant,
                case_id=checked.case_id,
                revision=checked.revision,
                pricing_as_of=instant,
                settings_revision=config.revision if config else None,
                lines=tuple(lines),
                findings=tuple(findings),
                evidence=tuple(records),
                subtotal=subtotal,
                freight=money(checked.freight),
                taxable_base=taxable_base,
                tax=tax,
                total=total,
                margin=quote_margin,
                state=state,
                commercial_fingerprint=fingerprint,
            )

    def _line(
        self,
        repo: CommercialRepository,
        context: TenantContext,
        line: CalculationLine,
        config: SettingsRevision,
        book: Mapping[Any, Any],
        policy: Mapping[Any, Any] | None,
        instant: datetime,
        actor: Principal | None,
    ) -> tuple[LineCalculation, list[Finding]]:
        records: list[Evidence] = []
        issues: list[Finding] = []

        def issue(code: str, kind: Any = "hard_block") -> None:
            issues.append(Finding(code=code, kind=kind, line_id=line.line_id))

        if line.resolution != "resolved":
            issue(
                "AMBIGUOUS_PRODUCT" if line.resolution == "ambiguous" else "MISSING_PRODUCT",
                "clarification" if line.resolution == "ambiguous" else "hard_block",
            )
            return LineCalculation(line_id=line.line_id), issues
        assert line.product_id is not None
        product_id = line.product_id

        def factor(from_uom: str, to_uom: str) -> Decimal:
            if from_uom == to_uom:
                return Decimal(1)
            row = repo.conversion(context, product_id, from_uom, to_uom, instant)
            records.append(evidence("conversion", row))
            return Decimal(row["factor"])

        try:
            product = repo.get(context, "products", line.product_id)
            if product is None or product["archived"]:
                raise CommercialError("MISSING_PRODUCT")
            records.append(evidence("product", product))
            if line.substitute_for is not None:
                requested = repo.get(context, "products", line.substitute_for)
                if requested is None or requested["archived"]:
                    raise CommercialError("MISSING_REQUESTED_PRODUCT")
                records.append(evidence("requested_product", requested))
            qty = line.quantity * factor(line.quote_uom, line.pricing_uom)
            price = repo.price(context, line.product_id, book["id"], line.pricing_uom, qty, instant)
            records.append(evidence("price", price))
        except CommercialError as exc:
            issue(str(exc))
            return LineCalculation(line_id=line.line_id, evidence=tuple(records)), issues

        rate = Decimal(policy["rate"]) if policy is not None else ZERO
        reference = price["unit_price"] * (1 - rate)
        selected = reference
        proposal: dict[str, Any] = {}
        if line.negotiated is not None:
            try:
                if actor is None:
                    raise AuthError("NEGOTIATED_ADOPTION_REQUIRED", 403)
                actor.require(Capability.EDIT_DRAFT)
            except AuthError:
                issue("NEGOTIATED_ADOPTION_REQUIRED")
                return LineCalculation(line_id=line.line_id, evidence=tuple(records)), issues
            selected = line.negotiated.unit_price
            proposal = dict(
                negotiated_unit_price=selected,
                proposer_id=actor.user_id,
                proposal_reason=line.negotiated.reason,
                proposed_at=instant,
                absolute_unit_delta=selected - reference,
                deviation_numerator=reference - selected if reference > 0 else None,
                deviation_denominator=reference if reference > 0 else None,
            )
            issue("NEGOTIATED_UNIT_PRICE", "approval")
        if policy is not None:
            if not policy["permitted"]:
                issue("DISCOUNT_NOT_PERMITTED")
            if config.discount_approval_enabled:
                assert config.discount_approval_threshold is not None
                if rate > config.discount_approval_threshold:
                    issue("DISCOUNT_ABOVE_THRESHOLD", "approval")
        if line.substitute_for is not None:
            issue("SUBSTITUTION_PROPOSED", "approval")
        raw = qty * (selected if line.negotiated else price["unit_price"])
        net = money(qty * selected)
        cost = None
        ambiguous = False
        try:
            cost_row = repo.cost(context, line.product_id, instant)
            if cost_row is not None:
                records.append(evidence("cost", cost_row))
                cost = qty * factor(line.pricing_uom, cost_row["uom"]) * cost_row["unit_cost"]
        except CommercialError as exc:
            ambiguous = str(exc) == "AMBIGUOUS_COST"
            if config.line_margin_control_enabled or config.quote_margin_control_enabled:
                issue(str(exc))
        line_margin = margin(net, cost, ambiguous=ambiguous)
        issues.extend(
            check_margin(
                line_margin,
                config.minimum_line_margin_threshold
                if config.line_margin_control_enabled
                else None,
                "LINE_MARGIN_BELOW_THRESHOLD",
                line.line_id,
            )
        )
        inventory = repo.stock(context, line.product_id, instant)
        assert config.inventory_max_age_minutes is not None
        inventory_state = freshness(inventory, instant, config.inventory_max_age_minutes)
        if inventory is not None:
            records.append(evidence("inventory", inventory))
        if line.availability_required and inventory_state != "FRESH":
            issue(inventory_state)
        return LineCalculation(
            line_id=line.line_id,
            pricing_quantity=qty,
            selected_unit_price=price["unit_price"],
            discount_rate=rate,
            policy_outcome="SELECTED" if policy else "NO_DISCOUNT_POLICY",
            normal_reference_unit_price=reference,
            raw_line=raw,
            line_net=net,
            margin=line_margin,
            inventory_state=inventory_state,
            aggregate_available=inventory["quantity"]
            if inventory is not None and inventory_state == "FRESH"
            else None,
            inventory_uom=inventory["uom"]
            if inventory is not None and inventory_state == "FRESH"
            else None,
            evidence=tuple(records),
            **proposal,
        ), issues
