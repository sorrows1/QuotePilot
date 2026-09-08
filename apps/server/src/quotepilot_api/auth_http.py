"""Bounded browser requests, explicit origin/custom-header CSRF and safe failures."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from quotepilot_api.auth import AuthError
from quotepilot_api.auth_api import auth_config

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
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 4096:
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


def install_errors(app: FastAPI) -> None:
    @app.exception_handler(AuthError)
    async def auth_error(request: Request, error: AuthError) -> JSONResponse:
        return failure(error.code, error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        # Pydantic errors may contain the original password or unknown input values.
        return failure("VALIDATION_ERROR", 422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError) -> JSONResponse:
        logging.getLogger(__name__).error("auth_database_operation_failed")
        return failure("SERVICE_UNAVAILABLE", 503)
