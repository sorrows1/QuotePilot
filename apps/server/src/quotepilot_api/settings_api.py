"""Authenticated admin settings adapter; auth owns transaction/CSRF boundaries."""

import base64
import binascii

from fastapi import APIRouter, Request, Response

from quotepilot_api.auth import AuthError, Capability
from quotepilot_api.auth_api import Config, Service, credentials
from quotepilot_api.settings import (
    LogoInput,
    SettingsError,
    SettingsOutput,
    SettingsPatch,
    VersionInput,
    current,
    mutate,
    output,
    read_logo,
    replace_logo,
)

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])


@router.get("", response_model=SettingsOutput)
def get_settings(request: Request, service: Service, config: Config) -> dict[str, object]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        principal.require(Capability.MANAGE_SETTINGS)
        return output(current(session, principal.context))


@router.patch("", response_model=SettingsOutput)
def patch_settings(
    body: SettingsPatch, request: Request, service: Service, config: Config
) -> dict[str, object]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        changes = body.model_dump(exclude_unset=True, exclude={"expected_edit_version"})
        return output(mutate(session, principal, body.expected_edit_version, changes))


@router.post("/complete", response_model=SettingsOutput)
def complete_settings(
    body: VersionInput, request: Request, service: Service, config: Config
) -> dict[str, object]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return output(mutate(session, principal, body.expected_edit_version, {}, complete=True))


@router.put("/logo", response_model=SettingsOutput)
def put_logo(
    body: LogoInput, request: Request, service: Service, config: Config
) -> dict[str, object]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        principal.require(Capability.MANAGE_SETTINGS)
        try:
            data = base64.b64decode(body.data_base64, validate=True)
        except (ValueError, binascii.Error):
            raise SettingsError({"logo": "Choose a valid PNG or JPEG."}) from None
        return output(
            replace_logo(session, principal, body.expected_edit_version, data, body.media_type)
        )


@router.delete("/logo", response_model=SettingsOutput)
def delete_logo(
    body: VersionInput, request: Request, service: Service, config: Config
) -> dict[str, object]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return output(
            mutate(
                session,
                principal,
                body.expected_edit_version,
                {"current_logo_asset_id": None},
                action="logo_removed",
            )
        )


@router.get("/logo")
def get_logo(request: Request, service: Service, config: Config) -> Response:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        principal.require(Capability.MANAGE_SETTINGS)
        asset_id = current(session, principal.context).current_logo_asset_id
        if asset_id is None:
            raise AuthError("TENANT_SCOPE_VIOLATION", 404)
        return Response(
            read_logo(session, principal.context, asset_id).data, media_type="image/png"
        )
