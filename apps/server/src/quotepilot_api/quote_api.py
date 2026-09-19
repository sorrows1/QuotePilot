"""Authenticated browser adapter for manual draft quotations."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, Request

from quotepilot_api import quotes
from quotepilot_api.auth_api import Config, Service, credentials
from quotepilot_api.quote_contract import Candidate, CustomerCreate, QuoteCreate, SaveInput

router = APIRouter(prefix="/api", tags=["quotes"])


@router.get("/quotes")
def list_quotes(request: Request, service: Service, config: Config) -> list[dict[str, Any]]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.list_cases(session, principal)


@router.post("/quotes", status_code=201)
def create_quote(
    body: QuoteCreate, request: Request, service: Service, config: Config
) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.create_case(session, principal, body)


@router.get("/quotes/{case_id}")
def get_quote(case_id: UUID, request: Request, service: Service, config: Config) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.read_case(session, principal, case_id)


@router.post("/quotes/{case_id}/calculate")
def calculate(
    case_id: UUID, body: Candidate, request: Request, service: Service, config: Config
) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.calculate(session, service.sessions, principal, case_id, body)


@router.post("/quotes/{case_id}/revisions", status_code=201)
def save(
    case_id: UUID, body: SaveInput, request: Request, service: Service, config: Config
) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.save(session, service.sessions, principal, case_id, body)


@router.get("/customers")
def customers(
    request: Request, service: Service, config: Config, q: str = Query(default="", max_length=100)
) -> list[dict[str, Any]]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.lookup(session, principal, "customers", q)


@router.post("/customers", status_code=201)
def create_customer(
    body: CustomerCreate, request: Request, service: Service, config: Config
) -> dict[str, Any]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.create_customer(session, principal, body)


@router.get("/products")
def products(
    request: Request, service: Service, config: Config, q: str = Query(default="", max_length=100)
) -> list[dict[str, Any]]:
    with service.authenticated(*credentials(request, config)) as (session, principal):
        return quotes.lookup(session, principal, "products", q)
