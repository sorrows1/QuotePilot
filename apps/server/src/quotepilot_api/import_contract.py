"""Explicit import fields and normalization; no spreadsheet transformations."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quotepilot_api.commercial_inputs import (
    AssignmentInput,
    ConversionInput,
    CostInput,
    CustomerInput,
    InventoryInput,
    PricebookInput,
    PriceInput,
    ProductInput,
)

Kind = Literal[
    "customers",
    "products",
    "pricebooks",
    "prices",
    "inventory",
    "pricebook_assignments",
    "uom_conversions",
    "product_costs",
]
Mode = Literal["insert", "skip_identical", "replace_effective"]
MODELS: dict[str, type[BaseModel]] = {
    "customers": CustomerInput,
    "products": ProductInput,
    "pricebooks": PricebookInput,
    "prices": PriceInput,
    "inventory": InventoryInput,
    "pricebook_assignments": AssignmentInput,
    "uom_conversions": ConversionInput,
    "product_costs": CostInput,
}
# Required fields precede optional fields. Empty values in optional fields become null/default.
FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "customers": (["external_key", "name"], []),
    "products": (["sku", "name", "description"], ["manufacturer", "model_number"]),
    "pricebooks": (["key", "version", "valid_from"], ["valid_to", "source"]),
    "prices": (
        [
            "sku",
            "pricebook_key",
            "pricebook_version",
            "uom",
            "unit_price",
            "quantity_min",
            "valid_from",
        ],
        ["quantity_max", "valid_to", "source"],
    ),
    "inventory": (["sku", "uom", "quantity", "observed_at"], ["source"]),
    "pricebook_assignments": (
        ["pricebook_key", "pricebook_version", "valid_from"],
        ["customer_key", "valid_to", "source"],
    ),
    "uom_conversions": (
        ["sku", "from_uom", "to_uom", "factor", "valid_from"],
        ["valid_to", "source"],
    ),
    "product_costs": (["sku", "uom", "unit_cost", "valid_from"], ["valid_to", "source"]),
}


class UploadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00]+$")
    kind: Kind
    data_base64: str = Field(max_length=2_666_668)


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mapping: dict[str, str] = Field(max_length=40)
    mode: Mode = "insert"


class CommitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    preview_token: str = Field(pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


def normalize(values: dict[str, str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, raw in values.items():
        value = raw.strip()
        if not value:
            continue
        if key in {"valid_from", "valid_to", "observed_at"}:
            output[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif key in {"version", "pricebook_version"}:
            if not value.isascii() or not value.isdecimal() or len(value) > 10:
                raise ValueError("Version must be a positive whole number")
            output[key] = int(value)
        else:
            output[key] = value
    return output
