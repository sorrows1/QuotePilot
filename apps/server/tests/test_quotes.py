"""QT-008 real PostgreSQL lifecycle, immutable evidence and trust boundaries."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_calculation_database import Catalog
from test_commercial import price

from quotepilot_api import quote_schema as schema
from quotepilot_api import quotes
from quotepilot_api.auth import AuthError, AuthService, Role
from quotepilot_api.auth_api import auth_service
from quotepilot_api.auth_models import BusinessAuditEvent
from quotepilot_api.commercial_schema import prices, products
from quotepilot_api.main import app
from quotepilot_api.quote_contract import Candidate, CustomerCreate, QuoteCreate, SaveInput
from quotepilot_api.settings_models import QuoteNumber, QuoteNumberCounter
from quotepilot_api.tenants import TenantSettings


def fixture(database: Engine) -> tuple[Catalog, Any, UUID, dict[str, Any]]:
    catalog = Catalog(database)
    catalog.commercial.create_batch(
        catalog.context, [price(catalog.p.id, catalog.b.id, unit_price="10")]
    )
    actor = replace(catalog.admin, role=Role.SALES_ADMIN)
    with catalog.sessions.begin() as session:
        case = quotes.create_case(
            session,
            actor,
            QuoteCreate(request_key="create", customer_id=catalog.customer.id),
        )
    body = {
        "freight": "1.00",
        "lines": [
            {
                "line_id": "L1",
                "product_id": str(catalog.p.id),
                "quantity": "2",
                "quote_uom": "EA",
                "negotiated": {"unit_price": "9.50", "reason": "Package concession"},
            }
        ],
    }
    return catalog, actor, UUID(case["id"]), body


def save(
    c: Catalog, actor: Any, case_id: UUID, body: dict[str, Any], version: int = 0, key: str = "save"
) -> dict[str, Any]:
    with c.sessions.begin() as session:
        return quotes.save(
            session,
            c.sessions,
            actor,
            case_id,
            SaveInput.model_validate(
                {
                    **body,
                    "expected_version": version,
                    "request_key": key,
                }
            ),
        )


def test_exact_snapshot_retry_history_settings_and_numbering(database: Engine) -> None:
    c, actor, case_id, body = fixture(database)
    with c.sessions() as session:
        numbers = session.execute(select(QuoteNumber)).all()
        counters = session.execute(select(QuoteNumberCounter.__table__)).all()
        authority = session.execute(select(prices)).all()
    first = save(c, actor, case_id, body)
    assert save(c, actor, case_id, body) == first
    revision = first["revision"]
    result = revision["result"]
    assert result["total"] == "20.00"
    assert result["lines"][0]["normal_reference_unit_price"] == "10"
    assert result["lines"][0]["negotiated_unit_price"] == "9.50"
    assert result["lines"][0]["proposer_id"] == str(actor.user_id)
    assert result["lines"][0]["proposed_at"]
    assert revision["inputs"]["customer_id"] == str(c.customer.id)
    assert revision["inputs"]["lines"][0]["pricing_uom"] == "EA"
    assert revision["inputs"]["lines"][0]["availability_required"] is False
    assert revision["inputs"]["lines"][0]["substitute_for"] is None
    approvals = [f for f in result["findings"] if f["kind"] == "approval"]
    members = revision["exception_set"]["members"]
    assert [(m["code"], m["line_id"]) for m in members] == [
        (f["code"], f["line_id"]) for f in approvals
    ]
    assert len({m["id"] for m in members}) == len(members)
    assert result["state"] == "APPROVAL_REQUIRED"
    # New service/session has no in-memory history dependency.
    with Session(database) as session:
        reopened = quotes.read_case(session, actor, case_id)
        assert reopened["revisions"][0] == revision
    c.configure({"company_name": "Updated company"})
    with c.sessions.begin() as session:
        stale = quotes.read_case(session, actor, case_id)["revisions"][0]
        assert stale["settings_stale"] and stale["result"] == result
    body["lines"][0]["quantity"] = "3"
    second = save(c, actor, case_id, body, 1, "second")
    assert second["revision"]["result"]["total"] == "29.50"
    assert second["revision"]["result"]["settings_revision"] == result["settings_revision"] + 1
    assert second["revision"]["exception_set"]["id"] != revision["exception_set"]["id"]
    with c.sessions() as session:
        assert session.execute(select(QuoteNumber)).all() == numbers
        assert session.execute(select(QuoteNumberCounter.__table__)).all() == counters
        assert session.execute(select(prices)).all() == authority
        assert session.scalar(select(func.count()).select_from(schema.revisions)) == 2
        actions = list(session.scalars(select(BusinessAuditEvent.action)))
        assert actions.count("quote_revision_saved") == 2
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.execute(update(schema.revisions).values(state="CALCULATED"))
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.execute(update(schema.lines).values(quantity=1))
    with c.sessions.begin() as session:
        replacement = quotes.create_customer(
            session,
            actor,
            CustomerCreate(
                request_key="replacement-customer",
                external_key="QT-CUST-REPLACEMENT",
                name="Replacement",
            ),
        )
    with pytest.raises(IntegrityError), c.sessions.begin() as session:
        session.execute(
            update(schema.cases)
            .where(schema.cases.c.id == case_id)
            .values(customer_id=UUID(replacement["id"]))
        )
    c.commercial.create_batch(c.context, [])
    with c.sessions.begin() as session:
        session.execute(update(products).where(products.c.id == c.p.id).values(archived=True))
    with c.sessions.begin() as session:
        assert quotes.read_case(session, actor, case_id)["revisions"][1]["result"] == result
    with pytest.raises(quotes.QuoteError, match="unavailable"):
        save(c, actor, case_id, body, 2, "archived")


def test_conflicting_retries_and_concurrent_edits(database: Engine) -> None:
    c, actor, case_id, body = fixture(database)

    def attempt(key: str) -> str:
        try:
            save(c, actor, case_id, body, key=key)
            return "saved"
        except quotes.QuoteError as exc:
            assert exc.status == 409
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(attempt, ["one", "two"]))
    assert sorted(outcomes) == ["conflict", "saved"]
    successful = "one" if outcomes[0] == "saved" else "two"
    body["freight"] = "2.00"
    with pytest.raises(quotes.QuoteError, match="retry key"):
        save(c, actor, case_id, body, key=successful)
    with c.sessions.begin() as session:
        assert quotes.create_case(
            session,
            actor,
            QuoteCreate(request_key="create", customer_id=c.customer.id),
        )["id"] == str(case_id)
        customer = quotes.create_customer(
            session,
            actor,
            CustomerCreate(request_key="customer", external_key="QT-CUST-NEW", name="New"),
        )
    with c.sessions.begin() as session:
        assert (
            quotes.create_customer(
                session,
                actor,
                CustomerCreate(request_key="customer", external_key="QT-CUST-NEW", name="New"),
            )
            == customer
        )


def test_tenant_role_and_reference_boundaries(database: Engine) -> None:
    c, actor, case_id, body = fixture(database)
    other, foreign_actor, foreign_case, foreign_body = fixture(database)
    with pytest.raises(AuthError), c.sessions.begin() as session:
        quotes.read_case(session, c.admin, case_id)
    with pytest.raises(quotes.QuoteError, match="unavailable"), c.sessions.begin() as session:
        quotes.read_case(session, actor, foreign_case)
    with pytest.raises(quotes.QuoteError, match="unavailable"), c.sessions.begin() as session:
        quotes.create_case(
            session,
            actor,
            QuoteCreate(request_key="foreign-customer", customer_id=other.customer.id),
        )
    for bad in [foreign_body, {**body, "lines": foreign_body["lines"]}]:
        with pytest.raises(quotes.QuoteError, match="unavailable"):
            save(c, actor, case_id, bad)
    manager = replace(actor, role=Role.SALES_MANAGER)
    assert save(c, manager, case_id, body)["version"] == 1
    with other.sessions.begin() as session:
        assert len(quotes.list_cases(session, foreign_actor)) == 1
        assert all(
            x["id"] != str(c.customer.id)
            for x in quotes.lookup(session, foreign_actor, "customers", "")
        )


def test_http_input_authority_and_decimal_boundary(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, actor, case_id, body = fixture(database)
    service = AuthService(c.sessions)
    # Issue a real session for the fixture user with sales role.
    from quotepilot_api.auth_models import User

    with c.sessions.begin() as session:
        user = session.get(User, actor.user_id)
        assert user is not None
        user.role = "sales_admin"
        user.must_change_password = False
        principal, token = service._issue(session, user)
    monkeypatch.setenv("AUTH_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("AUTH_LOCAL_HTTP", "1")
    app.dependency_overrides[auth_service] = lambda: service
    try:
        with TestClient(
            app,
            headers={
                "Origin": "http://localhost:5173",
                "X-QuotePilot-Request": "1",
                "X-CSRF-Token": principal.csrf,
            },
        ) as client:
            client.cookies.set("quotepilot_dev", token)
            assert client.post(
                "/api/customers", json={"request_key": "customer-no-key", "name": "Missing key"}
            ).status_code == 422
            existing_customer = client.post(
                "/api/customers",
                json={
                    "request_key": "customer-existing",
                    "external_key": c.customer.external_key,
                    "name": "Name must not overwrite imported authority",
                },
            )
            assert existing_customer.status_code == 201
            assert existing_customer.json() == {
                "id": str(c.customer.id),
                "external_key": c.customer.external_key,
                "name": c.customer.name,
            }
            distinct_customer = client.post(
                "/api/customers",
                json={
                    "request_key": "customer-distinct",
                    "external_key": "QT-CUST-DISTINCT",
                    "name": c.customer.name,
                },
            )
            assert distinct_customer.status_code == 201
            assert distinct_customer.json()["id"] != str(c.customer.id)
            assert distinct_customer.json()["name"] == c.customer.name
            missing_customer = client.post(
                "/api/quotes", json={"request_key": "missing-customer"}
            )
            assert missing_customer.status_code == 422
            created = client.post(
                "/api/quotes",
                json={"request_key": "http-create", "customer_id": str(c.customer.id)},
            )
            assert created.status_code == 201, created.text
            assert created.json()["customer_id"] == str(c.customer.id)

            large_lines = []
            for index in range(24):
                line = json.loads(json.dumps(body["lines"][0]))
                line["line_id"] = f"LARGE-{index}"
                line["negotiated"]["reason"] = "valid negotiated quote reason " * 12
                large_lines.append(line)
            large_candidate = {"freight": "0.00", "lines": large_lines}
            assert len(json.dumps(large_candidate).encode()) > 4096
            large_case_id = created.json()["id"]
            large_calculation = client.post(
                f"/api/quotes/{large_case_id}/calculate", json=large_candidate
            )
            assert large_calculation.status_code == 200, large_calculation.text
            large_save = {
                **large_candidate,
                "expected_version": 0,
                "request_key": "large-valid-quote",
            }
            assert len(json.dumps(large_save).encode()) > 4096
            large_revision = client.post(
                f"/api/quotes/{large_case_id}/revisions", json=large_save
            )
            assert large_revision.status_code == 201, large_revision.text

            route = f"/api/quotes/{case_id}/revisions"
            payload = {**body, "expected_version": 0, "request_key": "http"}
            for field in [
                "tenant_id",
                "customer_id",
                "settings_revision",
                "revision",
                "quote_number",
                "proposer_id",
            ]:
                assert client.post(route, json={**payload, field: "forged"}).status_code == 422
            for field, value in [
                ("pricing_uom", "EA"),
                ("availability_required", True),
                ("substitute_for", str(uuid4())),
                ("resolution", "missing"),
            ]:
                forged = json.loads(json.dumps(payload))
                forged["lines"][0][field] = value
                assert client.post(route, json=forged).status_code == 422
            bad = json.loads(json.dumps(payload))
            bad["lines"][0]["quantity"] = 2.0
            invalid = client.post(route, json=bad)
            assert invalid.status_code == 422
            assert "quantities must be positive" in invalid.json()["message"]
            assert "password" not in invalid.json()["message"]
            assert "detail" not in invalid.json()
            assert client.post(route, json={**payload, "freight": "1.001"}).status_code == 422
            bad = json.loads(json.dumps(payload))
            bad["lines"][0]["negotiated"]["proposer_id"] = str(uuid4())
            assert client.post(route, json=bad).status_code == 422
            result = client.post(route, json=payload)
            assert result.status_code == 201, result.text
            assert client.post(route, json=payload).json() == result.json()
            assert (
                client.get(f"/api/quotes/{case_id}").json()["revisions"][0]
                == result.json()["revision"]
            )
            assert client.post(f"/api/quotes/{case_id}/calculate", json=body).status_code == 200
            client.headers["X-CSRF-Token"] = "wrong"
            assert client.post(route, json=payload).status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_hard_blocks_never_enter_approval_set(database: Engine) -> None:
    c, actor, case_id, body = fixture(database)
    c.configure({"line_margin_control_enabled": True, "minimum_line_margin_threshold": "0.2"})
    result = save(c, actor, case_id, body)["revision"]
    assert result["result"]["state"] == "HARD_BLOCK"
    assert any(f["kind"] == "hard_block" for f in result["result"]["findings"])
    assert {m["code"] for m in result["exception_set"]["members"]} == {"NEGOTIATED_UNIT_PRICE"}
    with c.sessions.begin() as session:
        provisional = quotes.calculate(
            session, c.sessions, actor, case_id, Candidate.model_validate(body)
        )
        assert provisional["state"] == "HARD_BLOCK"
        assert session.scalar(select(func.count()).select_from(schema.revisions)) == 1


def test_setup_gate_and_complete_multiple_exception_set(database: Engine) -> None:
    c, actor, case_id, body = fixture(database)
    c.configure({"quote_value_approval_enabled": True, "quote_value_approval_threshold": "1"})
    revision = save(c, actor, case_id, body)["revision"]
    assert {m["code"] for m in revision["exception_set"]["members"]} == {
        "NEGOTIATED_UNIT_PRICE",
        "QUOTE_VALUE_ABOVE_THRESHOLD",
    }
    with c.sessions.begin() as session:
        session.execute(
            update(TenantSettings)
            .where(
                TenantSettings.tenant_id == c.context.tenant_id,
            )
            .values(setup_completed_at=None, active_settings_revision=None)
        )
    operations: tuple[Callable[[Session], object], ...] = (
        lambda session: quotes.create_case(
            session,
            actor,
            QuoteCreate(request_key="not-ready", customer_id=c.customer.id),
        ),
        lambda session: quotes.calculate(
            session, c.sessions, actor, case_id, Candidate.model_validate(body)
        ),
    )
    for operation in operations:
        with pytest.raises(quotes.QuoteError, match="setup pending"), c.sessions.begin() as session:
            operation(session)
