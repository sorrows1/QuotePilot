"""Untrusted manual quote inputs. Decimal authority is always text, never float."""

from decimal import Decimal
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quotepilot_api.commercial_inputs import Nonnegative, Positive, Uom


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RetryInput(Input):
    request_key: str = Field(min_length=1, max_length=100)


class CustomerCreate(RetryInput):
    name: str = Field(min_length=1, max_length=255)


class Proposal(Input):
    unit_price: Nonnegative
    reason: str = Field(min_length=1, max_length=1000)


class QuoteLine(Input):
    line_id: str = Field(min_length=1, max_length=100)
    product_id: UUID
    quantity: Positive
    quote_uom: Uom
    pricing_uom: Uom
    negotiated: Proposal | None = None
    substitute_for: UUID | None = None
    availability_required: bool = Field(default=False, strict=True)


class Candidate(Input):
    customer_id: UUID
    lines: list[QuoteLine] = Field(min_length=1, max_length=1000)
    freight: Nonnegative = Decimal("0.00")

    @field_validator("freight")
    @classmethod
    def freight_scale(cls, value: Decimal) -> Decimal:
        if int(value.as_tuple().exponent) < -2:
            raise ValueError("Freight supports two decimal places")
        return value

    @model_validator(mode="after")
    def identities(self) -> Self:
        if self.customer_id.int == 0 or len({x.line_id for x in self.lines}) != len(self.lines):
            raise ValueError("Valid customer and unique line identities required")
        for line in self.lines:
            if line.product_id.int == 0 or line.substitute_for == line.product_id:
                raise ValueError("Invalid product identity")
        return self


class SaveInput(Candidate, RetryInput):
    expected_version: int = Field(ge=0, le=2147483646, strict=True)
