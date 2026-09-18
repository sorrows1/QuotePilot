"""Strict settings input, atomic revision changes, sanitized logos and number allocation."""

import hashlib
import io
import re
import unicodedata
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from PIL import Image, UnidentifiedImageError
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    ValidationInfo,
    field_validator,
)
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api.auth import AuthError, Capability, Principal, audit, now, tenant_lock
from quotepilot_api.settings_models import (
    BrandAsset,
    QuoteNumber,
    QuoteNumberCounter,
    SettingsRevision,
)
from quotepilot_api.tenants import TenantContext, TenantSettings, require_context

CONTROLS = {
    "discount_approval_enabled": "discount_approval_threshold",
    "quote_margin_control_enabled": "minimum_margin_threshold",
    "line_margin_control_enabled": "minimum_line_margin_threshold",
    "quote_value_approval_enabled": "quote_value_approval_threshold",
}


def exact_decimal(value: object, scale: int, maximum: Decimal) -> Decimal:
    if (
        not isinstance(value, str)
        or len(value) > 32
        or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
    ):
        raise ValueError("Use an exact non-negative decimal string.")
    result = Decimal(value)
    if int(result.as_tuple().exponent) < -scale or result > maximum:
        raise ValueError(
            f"Use at most {scale} decimal places and a value no greater than {maximum}."
        )
    return result


Rate = Annotated[Decimal, BeforeValidator(lambda v: exact_decimal(v, 6, Decimal(1)))]
Money = Annotated[
    Decimal, BeforeValidator(lambda v: exact_decimal(v, 2, Decimal("999999999999999999.99")))
]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_name: str | None = None
    company_address: str | None = None
    tax_enabled: StrictBool | None = None
    tax_rate: Rate | None = None
    freight_taxable: StrictBool | None = None
    default_quote_validity_days: Annotated[int, Field(strict=True, ge=1, le=365)] | None = None
    inventory_max_age_minutes: PositiveInt | None = None
    quote_number_prefix: str | None = None
    discount_approval_enabled: StrictBool | None = None
    discount_approval_threshold: Rate | None = None
    quote_margin_control_enabled: StrictBool | None = None
    minimum_margin_threshold: Rate | None = None
    line_margin_control_enabled: StrictBool | None = None
    minimum_line_margin_threshold: Rate | None = None
    quote_value_approval_enabled: StrictBool | None = None
    quote_value_approval_threshold: Money | None = None

    @field_validator("company_name", "company_address")
    @classmethod
    def company_text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        name = info.field_name == "company_name"
        value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        limit = 200 if name else 1000
        if (
            len(value) > limit
            or (name and not value)
            or any(unicodedata.category(c) == "Cc" and (name or c != "\n") for c in value)
        ):
            raise ValueError(
                f"Enter valid text of at most {limit} characters without control characters."
            )
        return value or None

    @field_validator("quote_number_prefix")
    @classmethod
    def prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value.isascii() or not re.fullmatch(r"[a-zA-Z0-9]{1,10}", value):
            raise ValueError("Use 1–10 ASCII letters or digits.")
        return value.upper()


class SettingsOutput(SettingsInput):
    currency_code: Literal["SGD"]
    business_timezone: str
    current_logo_asset_id: UUID | None
    edit_version: int
    active_settings_revision: int | None
    setup_complete: bool
    field_errors: dict[str, str]


class SettingsPatch(SettingsInput):
    expected_edit_version: PositiveInt


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_edit_version: PositiveInt


class LogoInput(VersionInput):
    media_type: Literal["image/png", "image/jpeg"]
    data_base64: str = Field(max_length=2796204)


FIELDS = tuple(SettingsInput.model_fields)
SNAPSHOT_FIELDS = (*FIELDS, "currency_code", "business_timezone", "current_logo_asset_id")


class SettingsError(Exception):
    def __init__(self, fields: dict[str, str], code: str = "SETTINGS_INVALID", status: int = 422):
        self.fields, self.code, self.status = fields, code, status
        super().__init__(code)


def missing(values: dict[str, object]) -> dict[str, str]:
    required = [
        "company_name",
        "tax_enabled",
        "inventory_max_age_minutes",
        "default_quote_validity_days",
        "quote_number_prefix",
        *CONTROLS,
    ]
    if values.get("tax_enabled") is True:
        required += ["tax_rate", "freight_taxable"]
    required += [
        threshold for enabled, threshold in CONTROLS.items() if values.get(enabled) is True
    ]
    return {key: "Choose a value to complete setup." for key in required if values.get(key) is None}


def current(session: Session, context: TenantContext) -> TenantSettings:
    settings = session.get(TenantSettings, require_context(context))
    if settings is None:
        raise AuthError("TENANT_SCOPE_VIOLATION", 404)
    return settings


def setup_complete(session: Session, context: TenantContext) -> bool:
    settings = current(session, context)
    return settings.setup_completed_at is not None and settings.active_settings_revision is not None


def output(settings: TenantSettings) -> dict[str, object]:
    values = {key: getattr(settings, key) for key in SNAPSHOT_FIELDS}
    values.update(
        edit_version=settings.edit_version,
        active_settings_revision=settings.active_settings_revision,
        setup_complete=settings.setup_completed_at is not None
        and settings.active_settings_revision is not None,
        field_errors=missing(values),
    )
    return {
        key: format(value, "f") if isinstance(value, Decimal) else value
        for key, value in values.items()
    }


def mutate(
    session: Session,
    principal: Principal,
    expected: int,
    changes: dict[str, object],
    *,
    complete: bool = False,
    action: str | None = None,
) -> TenantSettings:
    principal.require(Capability.MANAGE_SETTINGS)
    if any(key in changes for key in (*CONTROLS, *CONTROLS.values())):
        principal.require(Capability.CONFIGURE_POLICY)
    tenant_lock(session, principal.context)
    settings = current(session, principal.context)
    if settings.edit_version != expected:
        raise SettingsError({}, "SETTINGS_VERSION_CONFLICT", 409)
    values = {key: getattr(settings, key) for key in SNAPSHOT_FIELDS}
    values.update(changes)
    for enabled, threshold in {**CONTROLS, "tax_enabled": "tax_rate"}.items():
        if values[enabled] is not True:
            if changes.get(threshold) is not None:
                raise SettingsError({threshold: "Enable the control before specifying its value."})
            values[threshold] = None
    if values["tax_enabled"] is not True:
        if changes.get("freight_taxable") is not None:
            raise SettingsError(
                {"freight_taxable": "Enable tax before choosing freight taxability."}
            )
        values["freight_taxable"] = None
    if complete or settings.setup_completed_at is not None:
        errors = missing(values)
        if errors:
            raise SettingsError(errors)
    changed = sorted(key for key in SNAPSHOT_FIELDS if getattr(settings, key) != values[key])
    first_completion = complete and settings.setup_completed_at is None
    if not changed and not first_completion:
        return settings
    for key in changed:
        setattr(settings, key, values[key])
    settings.edit_version += 1
    settings.updated_at = now()
    if first_completion:
        settings.setup_completed_at = now()
    if settings.setup_completed_at is not None:
        revision = (settings.active_settings_revision or 0) + 1
        session.add(
            SettingsRevision(
                tenant_id=principal.context.tenant_id,
                revision=revision,
                creator_id=principal.user_id,
                **values,
            )
        )
        # Insert the referenced immutable revision before changing the active FK.
        with session.no_autoflush:
            session.flush([obj for obj in session.new if isinstance(obj, SettingsRevision)])
        settings.active_settings_revision = revision
    session.flush()
    # Field names are a compact bitmap over the documented SNAPSHOT_FIELDS order, never values.
    mask = sum(1 << SNAPSHOT_FIELDS.index(key) for key in changed)
    audit(
        session,
        principal.context,
        principal.user_id,
        action
        or (
            "setup_completed"
            if first_completion
            else "settings_changed"
            if settings.setup_completed_at
            else "setup_saved"
        ),
        evidence=f"revision={settings.active_settings_revision or 0};fields={mask:x}",
    )
    return settings


def sanitize_logo(data: bytes, media_type: str) -> tuple[bytes, int, int]:
    if not 0 < len(data) <= 2 * 1024 * 1024:
        raise SettingsError({"logo": "Choose a PNG or JPEG of at most 2 MiB."})
    try:
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            if (
                Image.MIME.get(image.format or "") != media_type
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError
            width, height = image.size
            if not (16 <= width <= 2048 and 16 <= height <= 2048 and width * height <= 4194304):
                raise ValueError
            image.verify()
        with Image.open(io.BytesIO(data), formats=["PNG", "JPEG"]) as image:
            image.load()
            pixels = image.convert("RGBA")
            clean = Image.new("RGBA", pixels.size)
            clean.paste(pixels)
            target = io.BytesIO()
            clean.save(target, format="PNG")
            sanitized = target.getvalue()
        if len(sanitized) > 4 * 1024 * 1024:
            raise ValueError
        return sanitized, width, height
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise SettingsError(
            {"logo": "Use a valid single-frame PNG/JPEG, 16–2048 pixels per side."}
        ) from None


def replace_logo(
    session: Session, principal: Principal, expected: int, data: bytes, media_type: str
) -> TenantSettings:
    principal.require(Capability.MANAGE_SETTINGS)
    sanitized, width, height = sanitize_logo(data, media_type)
    digest = hashlib.sha256(sanitized).hexdigest()
    settings = current(session, principal.context)
    if settings.current_logo_asset_id:
        previous = read_logo(session, principal.context, settings.current_logo_asset_id)
        if previous.sha256 == digest:
            return mutate(session, principal, expected, {})
    asset = BrandAsset(
        tenant_id=principal.context.tenant_id,
        data=sanitized,
        sha256=digest,
        byte_size=len(sanitized),
        width=width,
        height=height,
        creator_id=principal.user_id,
    )
    session.add(asset)
    session.flush()
    return mutate(
        session, principal, expected, {"current_logo_asset_id": asset.id}, action="logo_replaced"
    )


def read_logo(session: Session, context: TenantContext, asset_id: UUID) -> BrandAsset:
    asset = session.get(BrandAsset, (require_context(context), asset_id))
    if asset is None:
        raise AuthError("TENANT_SCOPE_VIOLATION", 404)
    return asset


def read_revision(session: Session, context: TenantContext, revision: int) -> SettingsRevision:
    snapshot = session.get(SettingsRevision, (require_context(context), revision))
    if snapshot is None:
        raise AuthError("TENANT_SCOPE_VIOLATION", 404)
    return snapshot


class NumberAllocator:
    """QT-022 calls after final eligibility, before rendering; this commits the reservation."""

    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def allocate(self, context: TenantContext, subject_id: UUID) -> str:
        tenant_id = require_context(context)
        if not isinstance(subject_id, UUID) or subject_id.int == 0:
            raise ValueError("A non-nil stable QuoteCase UUID is required.")
        with self.sessions.begin() as session:
            tenant_lock(session, context)
            previous = session.get(QuoteNumber, (tenant_id, subject_id))
            if previous is not None:
                return previous.number
            settings = current(session, context)
            if not setup_complete(session, context):
                raise SettingsError({}, "SETUP_INCOMPLETE", 409)
            counter = session.get(QuoteNumberCounter, tenant_id)
            if counter is None:
                raise SettingsError({}, "QUOTE_COUNTER_UNAVAILABLE", 503)
            sequence = counter.next_sequence
            if sequence >= 9223372036854775807:
                raise SettingsError({}, "QUOTE_NUMBER_EXHAUSTED", 409)
            counter.next_sequence += 1
            number = f"{settings.quote_number_prefix}-{sequence:06d}"
            session.add(
                QuoteNumber(
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    sequence=sequence,
                    number=number,
                    prefix=settings.quote_number_prefix,
                    settings_revision=settings.active_settings_revision,
                )
            )
            return number
