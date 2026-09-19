"""Bounded browser requests, explicit origin/custom-header CSRF and safe failures."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from quotepilot_api.auth import AuthError
from quotepilot_api.auth_api import auth_config
from quotepilot_api.import_parser import ImportError
from quotepilot_api.settings import SettingsError, SettingsInput

QUOTE_BODY_LIMIT = 16 * 1024 * 1024

MESSAGES = {
    "LAST_ADMIN_REQUIRED": "Keep at least one enabled system administrator for this organization.",
    "AUTH_INVALID_CREDENTIALS": "Unable to sign in. Check your credentials or sign in again.",
    "AUTH_FORBIDDEN": "This action is not permitted for your account.",
    "AUTH_CSRF_REJECTED": "Request could not be verified. Reload and try again.",
    "AUTH_RATE_LIMITED": "Too many attempts. Try again in 15 minutes.",
    "VALIDATION_ERROR": "Check the supplied fields and password requirements.",
    "SERVICE_UNAVAILABLE": "Service unavailable. Please try again later.",
}


def failure(code: str, status: int) -> JSONResponse:
    return JSONResponse(
        {
            "code": code,
            "message": MESSAGES.get(
                code, "Request could not be completed. Refresh and check the current account state."
            ),
        },
        status_code=status,
        headers={"Cache-Control": "no-store", **({"Retry-After": "900"} if status == 429 else {})},
    )


class BrowserBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        try:
            config = auth_config()
        except ValueError:
            await failure("SERVICE_UNAVAILABLE", 503)(scope, receive, send)
            return
        if request.method not in {"GET", "HEAD", "OPTIONS"} and (
            request.headers.get("origin") != config.origin
            or request.headers.get("x-quotepilot-request") != "1"
            or request.headers.get("sec-fetch-site") == "cross-site"
            or request.headers.get("content-type", "").split(";")[0] != "application/json"
        ):
            await failure("AUTH_CSRF_REJECTED", 403)(scope, receive, send)
            return
        large_quote_body = (
            request.method == "POST"
            and scope["path"].startswith("/api/quotes/")
            and scope["path"].rsplit("/", 1)[-1] in {"calculate", "revisions"}
        )
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            limit = (
                QUOTE_BODY_LIMIT
                if large_quote_body
                else 2800000
                if scope["path"] == "/api/admin/imports" and request.method == "POST"
                else 16384
                if scope["path"].startswith("/api/admin/imports/")
                else 2800000
                if scope["path"] == "/api/admin/settings/logo" and request.method == "PUT"
                else 16384
                if scope["path"] == "/api/admin/settings"
                else 4096
            )
            if len(body) > limit:
                await failure("VALIDATION_ERROR", 413)(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        async def bounded_receive() -> Message:
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        async def protected_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [
                    *message.get("headers", []),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                ]
            await send(message)

        await self.app(scope, bounded_receive, protected_send)


def quote_validation_fields(error: RequestValidationError) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in error.errors():
        location = tuple(item["loc"])
        if location and location[0] == "body":
            location = location[1:]
        key: str | None = None
        message: str | None = None
        if location == ("freight",):
            key = "freight"
            message = "Enter a nonnegative amount with at most two decimal places."
        elif (
            len(location) >= 3
            and location[0] == "lines"
            and isinstance(location[1], int)
        ):
            index = location[1]
            suffix = location[2:]
            if suffix == ("quantity",):
                key = f"lines[{index}].quantity"
                message = "Enter a positive decimal quantity."
            elif suffix == ("quote_uom",):
                key = f"lines[{index}].quote_uom"
                message = "Enter an uppercase UOM such as EA."
            elif suffix == ("negotiated", "unit_price"):
                key = f"lines[{index}].negotiated.unit_price"
                message = "Enter a nonnegative decimal price."
            elif suffix == ("negotiated", "reason"):
                key = f"lines[{index}].negotiated.reason"
                message = "Enter a proposal reason of 1–1000 characters."
        if key is not None and message is not None:
            fields.setdefault(key, message)
    return fields


def install_errors(app: FastAPI) -> None:
    from quotepilot_api.quotes import QuoteError

    @app.exception_handler(QuoteError)
    async def quote_error(request: Request, error: QuoteError) -> JSONResponse:
        return JSONResponse(
            {
                "code": error.code,
                "message": error.message,
            },
            status_code=error.status,
        )

    @app.exception_handler(ImportError)
    async def import_error(request: Request, error: ImportError) -> JSONResponse:
        return JSONResponse(
            {
                "code": "IMPORT_CONFLICT" if error.status == 409 else "IMPORT_INVALID",
                "message": error.message,
            },
            status_code=error.status,
        )

    @app.exception_handler(SettingsError)
    async def settings_error(request: Request, error: SettingsError) -> JSONResponse:
        return JSONResponse(
            {
                "code": error.code,
                "message": "Settings changed elsewhere. Reload and review before saving."
                if error.status == 409
                else "Check the settings fields below.",
                "fields": error.fields,
            },
            status_code=error.status,
        )

    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError) -> JSONResponse:
        return failure(error.code, error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        if request.url.path.startswith("/api/quotes"):
            return JSONResponse(
                {
                    "code": "QUOTE_INVALID",
                    "message": "Correct the highlighted quote fields, then try again.",
                    "fields": quote_validation_fields(error),
                },
                status_code=422,
            )
        if request.url.path == "/api/customers":
            return JSONResponse(
                {
                    "code": "QUOTE_INVALID",
                    "message": "Enter an external customer key of 1–100 characters "
                    "and a customer name of 1–255 characters.",
                },
                status_code=422,
            )
        if request.url.path.startswith("/api/admin/settings"):
            allowed = {
                *SettingsInput.model_fields,
                "expected_edit_version",
                "logo",
                "media_type",
                "data_base64",
            }
            fields = {
                str(e["loc"][-1]): "Check the field type, required range and decimal precision."
                for e in error.errors()
                if str(e["loc"][-1]) in allowed
            }
            return JSONResponse(
                {
                    "code": "SETTINGS_INVALID",
                    "message": "Check the settings fields below.",
                    "fields": fields,
                },
                status_code=422,
            )
        # Pydantic errors may contain the original password or unknown input values.
        return failure("VALIDATION_ERROR", 422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        logging.getLogger(__name__).error("auth_database_operation_failed")
        return failure("SERVICE_UNAVAILABLE", 503)
