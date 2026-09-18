"""QT-007 trusted service contracts; JSON monetary values are decimal strings."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quotepilot_api.commercial_inputs import Nonnegative, Positive, Uom, utc


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)


class NegotiatedPrice(FrozenModel):
    unit_price: Nonnegative
    reason: str = Field(min_length=1, max_length=1000)
    proposer_id: UUID
    proposed_at: datetime

    @field_validator("proposer_id")
    @classmethod
    def non_nil_proposer(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("Negotiated proposal requires a non-nil proposer")
        return value

    @field_validator("proposed_at")
    @classmethod
    def proposal_time(cls, value: datetime) -> datetime:
        return utc(value)


class CalculationLine(FrozenModel):
    line_id: str = Field(min_length=1, max_length=100)
    product_id: UUID | None
    resolution: Literal["resolved", "missing", "ambiguous"] = "resolved"
    quantity: Positive
    quote_uom: Uom
    pricing_uom: Uom
    negotiated: NegotiatedPrice | None = None
    # Set only after explicit selection by the caller's product-resolution workflow.
    substitute_for: UUID | None = None
    availability_required: bool = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.resolution == "resolved" and self.product_id is None:
            raise ValueError("Resolved lines require a product identity")
        if self.product_id is not None and self.product_id.int == 0:
            raise ValueError("Nil product identity is invalid")
        if self.substitute_for is not None and (
            self.substitute_for.int == 0 or self.substitute_for == self.product_id
        ):
            raise ValueError("Substitution must identify a different requested product")
        return self


class CalculationRequest(FrozenModel):
    case_id: UUID
    revision: Annotated[int, Field(gt=0, le=2147483647)]
    customer_id: UUID | None = None
    lines: tuple[CalculationLine, ...] = Field(min_length=1, max_length=1000)
    freight: Nonnegative = Decimal("0.00")

    @field_validator("freight")
    @classmethod
    def freight_scale(cls, value: Decimal) -> Decimal:
        if int(value.as_tuple().exponent) < -2:
            raise ValueError("Freight supports at most two decimal places")
        return value

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        if self.case_id.int == 0 or (self.customer_id is not None and self.customer_id.int == 0):
            raise ValueError("Non-nil identities required")
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Line identities must be unique within the revision")
        return self


class Finding(FrozenModel):
    code: str
    kind: Literal["hard_block", "clarification", "approval"]
    line_id: str | None = None


class Evidence(FrozenModel):
    kind: str
    # Canonical JSON preserves full selected immutable values/source/window/identity.
    record_json: str


class Margin(FrozenModel):
    state: Literal["DEFINED", "MARGIN_UNKNOWN", "MARGIN_UNDEFINED", "AMBIGUOUS_COST"]
    extended_cost: Decimal | None = None
    numerator: Decimal | None = None
    denominator: Decimal | None = None
    fraction_display: Decimal | None = None


class LineCalculation(FrozenModel):
    line_id: str
    pricing_quantity: Decimal | None = None
    selected_unit_price: Decimal | None = None
    discount_rate: Decimal | None = None
    policy_outcome: Literal["SELECTED", "NO_DISCOUNT_POLICY"] | None = None
    normal_reference_unit_price: Decimal | None = None
    negotiated_unit_price: Decimal | None = None
    proposer_id: UUID | None = None
    proposal_reason: str | None = None
    proposed_at: datetime | None = None
    absolute_unit_delta: Decimal | None = None
    # Exact deviation is a rational pair; a nonterminating ratio is never policy authority.
    deviation_numerator: Decimal | None = None
    deviation_denominator: Decimal | None = None
    raw_line: Decimal | None = None
    line_net: Decimal | None = None
    margin: Margin = Margin(state="MARGIN_UNKNOWN")
    inventory_state: str = "MISSING_INVENTORY"
    aggregate_available: Decimal | None = None
    inventory_uom: str | None = None
    evidence: tuple[Evidence, ...] = ()


class CalculationResult(FrozenModel):
    tenant_id: UUID
    case_id: UUID
    revision: int
    pricing_as_of: datetime
    currency: Literal["SGD"] = "SGD"
    settings_revision: int | None
    lines: tuple[LineCalculation, ...]
    findings: tuple[Finding, ...]
    evidence: tuple[Evidence, ...]
    subtotal: Decimal | None = None
    freight: Decimal
    taxable_base: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    margin: Margin = Margin(state="MARGIN_UNKNOWN")
    state: Literal["CALCULATED", "APPROVAL_REQUIRED", "HARD_BLOCK", "CLARIFICATION_REQUIRED"]
    # No READY/approved flag: downstream verification and approval own eligibility.
    commercial_fingerprint: str
