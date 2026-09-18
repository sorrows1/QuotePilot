"""Validated trusted inputs. Decimal storage authority is text or Decimal, never float."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator


def exact(value: object) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise ValueError("Authoritative decimal must be text or Decimal")
    if isinstance(value, str) and len(value) > 80:
        raise ValueError("Decimal representation is too large")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("Invalid decimal") from None
    exponent = result.as_tuple().exponent
    if not result.is_finite() or not isinstance(exponent, int) or exponent < -6 or exponent > 18:
        raise ValueError("Finite decimal with scale at most six required")
    if result.copy_abs() >= Decimal("1e18"):
        raise ValueError("Decimal exceeds storage range")
    return result


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(UTC)


Exact = Annotated[Decimal, BeforeValidator(exact)]
Nonnegative = Annotated[Exact, Field(ge=0)]
Positive = Annotated[Exact, Field(gt=0)]
Rate = Annotated[Exact, Field(ge=0, le=1)]
Text = Annotated[str, Field(min_length=1, max_length=1000)]
Uom = Annotated[str, Field(min_length=1, max_length=30, pattern=r"^[A-Z][A-Z0-9_]*$")]


class RecordInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)

    @field_validator("id")
    @classmethod
    def non_nil(cls, value: UUID) -> UUID:
        if value.int == 0:
            raise ValueError("Nil identity is invalid")
        return value


class CustomerInput(RecordInput):
    external_key: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=255)]


class ProductInput(RecordInput):
    sku: Annotated[str, Field(min_length=1, max_length=100)]
    name: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(min_length=1, max_length=4000)]
    manufacturer: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    model_number: Annotated[str, Field(min_length=1, max_length=255)] | None = None


class AliasInput(RecordInput):
    customer_id: UUID
    product_id: UUID
    alias: Annotated[str, Field(min_length=1, max_length=255)]
    source: Text


class SuccessorInput(RecordInput):
    product_id: UUID
    successor_id: UUID
    source: Text


class EffectiveInput(RecordInput):
    valid_from: datetime
    valid_to: datetime | None = None
    source: Text

    @field_validator("valid_from", "valid_to")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value is not None else None

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("End must be strictly after start")
        return self


class PricebookInput(EffectiveInput):
    key: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[int, Field(gt=0, le=2147483647)]


class AssignmentInput(EffectiveInput):
    customer_id: UUID | None = None
    pricebook_id: UUID


class ConversionInput(EffectiveInput):
    product_id: UUID
    from_uom: Uom
    to_uom: Uom
    factor: Positive


class PriceInput(EffectiveInput):
    product_id: UUID
    pricebook_id: UUID
    uom: Uom
    unit_price: Nonnegative
    quantity_min: Positive
    quantity_max: Positive | None = None

    @model_validator(mode="after")
    def valid_tier(self) -> Self:
        if self.quantity_max is not None and self.quantity_max <= self.quantity_min:
            raise ValueError("Upper tier bound must exceed lower bound")
        return self


class CostInput(EffectiveInput):
    product_id: UUID
    uom: Uom
    unit_cost: Nonnegative


class InventoryInput(RecordInput):
    product_id: UUID
    uom: Uom
    quantity: Exact
    observed_at: datetime
    imported_at: datetime
    source: Text

    @field_validator("observed_at", "imported_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return utc(value)

    @model_validator(mode="after")
    def valid_evidence(self) -> Self:
        if self.observed_at > self.imported_at or self.imported_at > datetime.now(UTC):
            raise ValueError("Future inventory evidence is invalid")
        return self


class PolicyInput(EffectiveInput):
    customer_id: UUID | None = None
    key: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[int, Field(gt=0, le=2147483647)]
    rate: Rate
    permitted: bool


CommercialInput = (
    CustomerInput
    | ProductInput
    | AliasInput
    | SuccessorInput
    | PricebookInput
    | AssignmentInput
    | ConversionInput
    | PriceInput
    | CostInput
    | InventoryInput
    | PolicyInput
)
