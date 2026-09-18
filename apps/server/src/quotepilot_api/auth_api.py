"""Typed HTTP adapter; authentication transactions finish before responses are sent."""

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from quotepilot_api.auth import (
    AuthService,
    Capability,
    Principal,
    Role,
    normalized_locator,
    normalized_login,
    validate_password,
)
from quotepilot_api.auth_models import User
from quotepilot_api.database import database_engine


@dataclass(frozen=True)
class AuthConfig:
    origin: str
    secure: bool = True

    @property
    def cookie(self) -> str:
        return "__Host-quotepilot" if self.secure else "quotepilot_dev"

    @classmethod
    def environment(cls) -> "AuthConfig":
        origin = os.environ.get("AUTH_ORIGIN", "")
        local = os.environ.get("AUTH_LOCAL_HTTP") == "1"
        parsed = urlsplit(origin)
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.scheme not in {"http", "https"}
        ):
            raise ValueError("Set AUTH_ORIGIN to the exact browser origin.")
        if parsed.scheme != "https" and not (
            local and parsed.hostname in {"localhost", "127.0.0.1"}
        ):
            raise ValueError(
                "Authentication requires HTTPS; local HTTP must be explicit and loopback."
            )
        if local and parsed.scheme != "http":
            raise ValueError("AUTH_LOCAL_HTTP is only valid with a loopback HTTP origin.")
        return cls(origin, not local)


@lru_cache
def auth_service() -> AuthService:
    return AuthService(sessionmaker(database_engine(), expire_on_commit=False))


def auth_config() -> AuthConfig:
    return AuthConfig.environment()


Service = Annotated[AuthService, Depends(auth_service)]
Config = Annotated[AuthConfig, Depends(auth_config)]
router = APIRouter()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    organization: str = Field(min_length=3, max_length=64)
    login: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=128)

    _locator = field_validator("organization")(normalized_locator)
    _login = field_validator("login")(normalized_login)


class CreateUserInput(StrictModel):
    login: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=15, max_length=128)
    role: Role
    _login = field_validator("login")(normalized_login)

    @field_validator("password")
    @classmethod
    def password_policy(cls, value: SecretStr) -> SecretStr:
        validate_password(value.get_secret_value())
        return value


class ChangePasswordInput(StrictModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=15, max_length=128)

    @field_validator("new_password")
    @classmethod
    def password_policy(cls, value: SecretStr) -> SecretStr:
        validate_password(value.get_secret_value())
        return value


class UpdateUserInput(StrictModel):
    version: int = Field(ge=1)
    disabled: bool | None = None
    revoke_sessions: bool = False

    @model_validator(mode="after")
    def operation_required(self) -> "UpdateUserInput":
        if self.disabled is None and not self.revoke_sessions:
            raise ValueError("Specify a status change or session revocation.")
        return self


class Me(BaseModel):
    user_id: UUID
    tenant_id: UUID
    login: str
    role: Role
    must_change_password: bool
    csrf_token: str

    @classmethod
    def principal(cls, principal: Principal) -> "Me":
        return cls(
            user_id=principal.user_id,
            tenant_id=principal.context.tenant_id,
            login=principal.login,
            role=principal.role,
            must_change_password=principal.must_change_password,
            csrf_token=principal.csrf,
        )


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    login: str
    role: Role
    disabled: bool
    must_change_password: bool
    version: int


def credentials(request: Request, config: AuthConfig) -> tuple[str, str | None]:
    csrf = None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        csrf = request.headers.get("x-csrf-token", "")
    return request.cookies.get(config.cookie, ""), csrf


def set_cookie(response: Response, config: AuthConfig, token: str) -> None:
    response.set_cookie(
        config.cookie,
        token,
        httponly=True,
        secure=config.secure,
        samesite="strict",
        path="/",
        max_age=43200,
    )


@router.post("/api/auth/login", response_model=Me)
def login(
    body: LoginInput, request: Request, response: Response, service: Service, config: Config
) -> Me:
    principal, token = service.login(
        body.organization,
        body.login,
        body.password.get_secret_value(),
        request.client.host if request.client else "unknown",
    )
    set_cookie(response, config, token)
    return Me.principal(principal)


@router.get("/api/me", response_model=Me)
def me(request: Request, service: Service, config: Config) -> Me:
    with service.authenticated(*credentials(request, config)) as (_, principal):
        return Me.principal(principal)


@router.post("/api/auth/refresh", response_model=Me)
def refresh(request: Request, service: Service, config: Config) -> Me:
    service.limit([("renew-source:" + (request.client.host if request.client else "unknown"), 300)])
    with service.authenticated(*credentials(request, config)) as (session, principal):
        service.renew(session, principal)
        return Me.principal(principal)


@router.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, service: Service, config: Config) -> None:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        service.logout(session, principal)
    response.delete_cookie(
        config.cookie, path="/", secure=config.secure, httponly=True, samesite="strict"
    )


@router.post("/api/auth/password", response_model=Me)
def password(
    body: ChangePasswordInput,
    request: Request,
    response: Response,
    service: Service,
    config: Config,
) -> Me:
    token, csrf = credentials(request, config)
    service.limit(
        [("password-source:" + (request.client.host if request.client else "unknown"), 20)]
    )
    with service.authenticated(token, csrf) as (session, principal):
        changed, replacement = service.change_password(
            session,
            principal,
            body.current_password.get_secret_value(),
            body.new_password.get_secret_value(),
        )
    set_cookie(response, config, replacement)
    return Me.principal(changed)


@router.get("/api/admin/users", response_model=list[UserOutput])
def users(request: Request, service: Service, config: Config) -> list[UserOutput]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        principal.require(Capability.MANAGE_USERS)
        return [
            UserOutput.model_validate(user)
            for user in session.scalars(
                select(User)
                .where(User.tenant_id == principal.context.tenant_id)
                .order_by(User.login)
                .limit(200)
            )
        ]


@router.post("/api/admin/users", response_model=UserOutput, status_code=201)
def create_user(
    body: CreateUserInput, request: Request, service: Service, config: Config
) -> UserOutput:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return UserOutput.model_validate(
            service.create_user(
                session, principal, body.login, body.password.get_secret_value(), body.role
            )
        )


@router.patch("/api/admin/users/{user_id}", response_model=UserOutput)
def update_user(
    user_id: UUID, body: UpdateUserInput, request: Request, service: Service, config: Config
) -> UserOutput:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return UserOutput.model_validate(
            service.update_user(
                session, principal, user_id, body.version, body.disabled, body.revoke_sessions
            )
        )
