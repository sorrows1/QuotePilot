import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api import auth
from quotepilot_api.auth import AuthError, AuthService, Capability, Principal, Role
from quotepilot_api.auth_api import auth_service
from quotepilot_api.auth_models import AuthSession, BusinessAuditEvent, User
from quotepilot_api.main import app
from quotepilot_api.migrate import migrate
from quotepilot_api.tenants import Tenant, TenantContext, TenantService

PASSWORD = "initial-test-password-938!"
NEW = "replacement-test-password-824!"
HEADERS = {
    "Origin": "http://localhost:5173",
    "X-QuotePilot-Request": "1",
    "Content-Type": "application/json",
}


@pytest.fixture
def service(database: Engine) -> AuthService:
    return AuthService(sessionmaker(database, expire_on_commit=False))


@pytest.fixture
def client(service: AuthService, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("AUTH_LOCAL_HTTP", "1")
    app.dependency_overrides[auth_service] = lambda: service
    with TestClient(app, headers=HEADERS) as client:
        yield client
    app.dependency_overrides.clear()


def sign_in(
    client: TestClient, login: str = "admin", password: str = PASSWORD, organization: str = "alpha"
) -> dict[str, object]:
    response = client.post(
        "/api/auth/login", json={"organization": organization, "login": login, "password": password}
    )
    assert response.status_code == 200, response.text
    data: dict[str, object] = response.json()
    client.headers["x-csrf-token"] = str(data["csrf_token"])
    return data


def ready(client: TestClient, service: AuthService) -> None:
    service.bootstrap("alpha", "admin", PASSWORD)
    sign_in(client)
    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    assert response.status_code == 200, response.text
    client.headers["x-csrf-token"] = response.json()["csrf_token"]


def test_bootstrap_repeated_concurrent_and_atomic(
    service: AuthService, database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = auth.audit

    def failed_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(auth, "audit", failed_audit)
    with pytest.raises(RuntimeError):
        service.bootstrap("alpha", "admin", PASSWORD)
    with Session(database) as session:
        assert session.scalar(select(Tenant.id)) is None
        assert session.scalar(select(User.id)) is None
    monkeypatch.setattr(auth, "audit", original)

    def attempt(_: int) -> str:
        try:
            service.bootstrap("alpha", "admin", PASSWORD)
            return "ok"
        except AuthError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["BOOTSTRAP_ALREADY_INITIALIZED", "ok"]
    assert attempt(0) == "BOOTSTRAP_ALREADY_INITIALIZED"
    with Session(database) as session:
        user = session.scalar(select(User))
        assert user is not None and user.password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert auth.HASHER.verify(user.password_hash, PASSWORD)
        assert len(list(session.scalars(select(BusinessAuditEvent)))) == 1


def test_session_lifecycle_and_forced_change(client: TestClient, service: AuthService) -> None:
    service.bootstrap("alpha", " ADMIN ", PASSWORD)
    data = sign_in(client, " Admin ")
    old_token = client.cookies.get("quotepilot_dev")
    assert data["must_change_password"] is True
    assert client.get("/api/admin/users").status_code == 403
    assert client.post("/api/auth/refresh", json={}).status_code == 200
    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    assert response.status_code == 200
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    assert response.json()["must_change_password"] is False
    assert client.cookies.get("quotepilot_dev") != old_token
    with pytest.raises(AuthError), service.authenticated(str(old_token)):
        pass
    # A new application service checks the same durable authority after restart.
    restarted = AuthService(service.sessions)
    with restarted.authenticated(str(client.cookies.get("quotepilot_dev"))) as (_, principal):
        assert principal.user_id == UUID(str(data["user_id"]))
    assert client.get("/api/admin/users").status_code == 200
    assert client.post("/api/auth/logout", json={}).status_code == 204
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/refresh", json={}).status_code == 401
    assert client.post("/api/auth/bootstrap", json={}).status_code == 404


def test_admin_disable_revoke_conflict_and_tenant_isolation(
    client: TestClient, service: AuthService
) -> None:
    ready(client, service)
    response = client.post(
        "/api/admin/users", json={"login": "sales", "password": PASSWORD, "role": "sales_admin"}
    )
    assert response.status_code == 201
    user = response.json()
    assert (
        client.post(
            "/api/admin/users",
            json={"login": " SALES ", "password": PASSWORD, "role": "sales_admin"},
        ).status_code
        == 409
    )
    principal, token = service.login("alpha", "sales", PASSWORD, "sales-source")
    changed = client.patch("/api/admin/users/" + user["id"], json={"version": 1, "disabled": True})
    assert changed.status_code == 200
    with pytest.raises(AuthError), service.authenticated(token):
        pass
    assert (
        client.patch(
            "/api/admin/users/" + user["id"], json={"version": 1, "disabled": False}
        ).status_code
        == 409
    )
    assert (
        client.patch(
            "/api/admin/users/" + user["id"], json={"version": 2, "disabled": False}
        ).status_code
        == 200
    )
    with pytest.raises(AuthError), service.authenticated(token):
        pass
    _, token2 = service.login("alpha", "sales", PASSWORD, "sales-source")
    assert (
        client.patch(
            "/api/admin/users/" + user["id"], json={"version": 3, "revoke_sessions": True}
        ).status_code
        == 200
    )
    with pytest.raises(AuthError), service.authenticated(token2):
        pass
    other = TenantService(service.sessions).provision("UTC")
    service.bootstrap("bravo", "sales", PASSWORD, existing_tenant=other.tenant_id)
    foreign, foreign_token = service.login("bravo", "sales", PASSWORD, "other-source")
    assert foreign.context != principal.context
    assert (
        client.patch(
            f"/api/admin/users/{foreign.user_id}", json={"version": 1, "disabled": True}
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/admin/users/{uuid4()}", json={"version": 1, "disabled": True}
        ).status_code
        == 404
    )
    with service.authenticated(foreign_token):
        pass
    for field in ("tenant_id", "role", "password_hash"):
        assert (
            client.patch(
                "/api/admin/users/" + user["id"],
                json={"version": 4, "disabled": False, field: "forged"},
            ).status_code
            == 422
        )


def test_generic_failures_csrf_validation_cookie_and_no_secrets(
    client: TestClient, service: AuthService, caplog: pytest.LogCaptureFixture
) -> None:
    service.bootstrap("alpha", "admin", PASSWORD)
    bodies = []
    for org, login, password in [
        ("alpha", "admin", "wrong"),
        ("alpha", "unknown", "wrong"),
        ("unknown", "admin", PASSWORD),
    ]:
        result = client.post(
            "/api/auth/login", json={"organization": org, "login": login, "password": password}
        )
        assert result.status_code == 401
        bodies.append(result.json())
    assert bodies[0] == bodies[1] == bodies[2]
    result = client.post(
        "/api/auth/login",
        json={"organization": "alpha", "login": "admin", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert result.status_code == 403
    data = sign_in(client)
    token = str(client.cookies.get("quotepilot_dev"))
    for csrf in ("", "forged"):
        assert (
            client.post("/api/auth/refresh", json={}, headers={"x-csrf-token": csrf}).status_code
            == 403
        )
    _, other_token = service.login("alpha", "admin", PASSWORD, "other-source")
    with service.authenticated(other_token) as (_, other):
        other_csrf = other.csrf
    assert (
        client.post("/api/auth/refresh", json={}, headers={"x-csrf-token": other_csrf}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/auth/refresh", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/auth/login", json={"password": PASSWORD, "secret": token}).status_code
        == 422
    )
    assert client.post("/api/auth/login", content="x" * 4097).status_code == 413
    assert PASSWORD not in caplog.text and token not in caplog.text
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    assert str(data["csrf_token"]) != token


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("capability", list(Capability))
def test_rbac_matrix(role: Role, capability: Capability) -> None:
    expected = {
        Role.SALES_ADMIN: {"edit_draft", "generate_eligible", "audit_allowed_cases"},
        Role.SALES_MANAGER: {
            "edit_draft",
            "generate_eligible",
            "approve",
            "audit_tenant_commercial",
        },
        Role.SYSTEM_ADMIN: {
            "manage_users",
            "manage_imports",
            "manage_settings",
            "configure_policy",
            "audit_tenant_operations",
        },
    }
    principal = Principal(TenantContext(uuid4()), uuid4(), role, "test", False, uuid4(), "csrf")
    if capability.value in expected[role]:
        principal.require(capability)
    else:
        with pytest.raises(AuthError):
            principal.require(capability)


def test_expiry_and_renewal_cannot_resurrect_logout(
    client: TestClient, service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready(client, service)
    token = str(client.cookies.get("quotepilot_dev"))
    with service.authenticated(token) as (_, p):
        csrf = p.csrf

    def renew(_: int) -> str:
        try:
            with service.authenticated(token, csrf) as (session, principal):
                service.renew(session, principal)
            return "renewed"
        except AuthError:
            return "denied"

    def logout(_: int) -> str:
        with service.authenticated(token, csrf) as (session, principal):
            service.logout(session, principal)
        return "logout"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(renew, 0), pool.submit(logout, 0)]
        assert "logout" in [f.result() for f in futures]
    assert renew(0) == "denied"
    sign_in(client, password=NEW)
    timestamp = auth.now()
    monkeypatch.setattr(auth, "now", lambda: timestamp + timedelta(minutes=31))
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/auth/refresh", json={}).status_code == 401


def test_shared_limits_concurrency_and_window(
    service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def attempt(_: int) -> bool:
        try:
            service.limit([("account:unknown", 3), ("source:test", 10)])
            return True
        except AuthError as error:
            assert error.status == 429
            return False

    with ThreadPoolExecutor(6) as pool:
        assert sum(pool.map(attempt, range(6))) == 3
    timestamp = auth.now()
    monkeypatch.setattr(auth, "now", lambda: timestamp + timedelta(minutes=16))
    assert attempt(0)


def test_audit_failure_rolls_back_and_append_only(
    client: TestClient, service: AuthService, database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready(client, service)

    def fail(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("secret must never appear")

    monkeypatch.setattr(auth, "audit", fail)
    response = client.post(
        "/api/admin/users", json={"login": "rollback", "password": PASSWORD, "role": "sales_admin"}
    )
    assert response.status_code == 503 and "secret" not in response.text
    with Session(database) as session:
        assert session.scalar(select(User).where(User.login == "rollback")) is None
        events = list(session.scalars(select(BusinessAuditEvent)))
        assert {"bootstrap", "login", "password_changed"} <= {e.action for e in events}
        assert all(PASSWORD not in e.evidence for e in events)
    for sql in (
        "UPDATE business_audit_events SET outcome = 'bad'",
        "DELETE FROM business_audit_events",
        "TRUNCATE business_audit_events",
    ):
        with pytest.raises(SQLAlchemyError), database.begin() as connection:
            connection.execute(text(sql))


def test_populated_baseline_upgrade_and_fk(empty_database: Engine) -> None:
    migrate(empty_database, revision="0001_tenant_baseline")
    sessions = sessionmaker(empty_database)
    context = TenantService(sessions).provision("Asia/Singapore")
    migrate(empty_database)
    settings = TenantService(sessions).get_settings(context, context.tenant_id)
    assert settings is not None and settings.business_timezone == "Asia/Singapore"
    service = AuthService(sessions)
    service.bootstrap("alpha", "admin", PASSWORD, existing_tenant=context.tenant_id)
    other = TenantService(sessions).provision("UTC")
    with Session(empty_database) as session:
        user_id = session.scalar(select(User.id))
    with pytest.raises(IntegrityError), sessions.begin() as session:
        session.add(
            AuthSession(
                tenant_id=other.tenant_id,
                user_id=user_id,
                token_hash="x" * 64,
                csrf="x",
                created_at=auth.now(),
                idle_expires_at=auth.now(),
                absolute_expires_at=auth.now(),
            )
        )
    assert inspect(empty_database).has_table("business_audit_events")


def test_real_routes_enforce_roles_and_concurrent_creation(
    client: TestClient, service: AuthService
) -> None:
    ready(client, service)
    admin_token = str(client.cookies.get("quotepilot_dev"))
    admin_csrf = client.headers["x-csrf-token"]

    def create(_: int) -> str:
        try:
            with service.authenticated(admin_token, admin_csrf) as (session, principal):
                service.create_user(session, principal, "duplicate", PASSWORD, Role.SALES_ADMIN)
            return "created"
        except AuthError as error:
            return error.code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(create, range(2))) == ["USER_LOGIN_CONFLICT", "created"]
    for role in Role:
        with service.authenticated(admin_token, admin_csrf) as (session, principal):
            service.create_user(session, principal, role.value, PASSWORD, role)
        sign_in(client, role.value)
        response = client.post(
            "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
        )
        assert response.status_code == 200
        client.headers["x-csrf-token"] = response.json()["csrf_token"]
        expected = 200 if role == Role.SYSTEM_ADMIN else 403
        assert client.get("/api/admin/users").status_code == expected
        if expected == 403:
            assert (
                client.post(
                    "/api/admin/users",
                    json={"login": "forged", "password": PASSWORD, "role": "system_admin"},
                ).status_code
                == 403
            )
            assert (
                client.patch(
                    f"/api/admin/users/{uuid4()}", json={"version": 1, "disabled": True}
                ).status_code
                == 403
            )


def test_renewal_disable_race_and_absolute_expiry(
    client: TestClient, service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready(client, service)
    token = str(client.cookies.get("quotepilot_dev"))
    with service.authenticated(token) as (_, principal):
        user_id, csrf = principal.user_id, principal.csrf

    def renew() -> str:
        try:
            with service.authenticated(token, csrf) as (session, principal):
                service.renew(session, principal)
            return "renewed"
        except AuthError:
            return "denied"

    def disable() -> None:
        with service.authenticated(token, csrf) as (session, principal):
            service.update_user(session, principal, user_id, 2, True, False)

    with ThreadPoolExecutor(2) as pool:
        a, b = pool.submit(renew), pool.submit(disable)
        a.result()
        b.result()
    assert renew() == "denied"
    # Independent tenant for absolute expiry, renewing before each idle deadline.
    other = TenantService(service.sessions).provision("UTC")
    service.bootstrap("bravo", "admin", PASSWORD, existing_tenant=other.tenant_id)
    _, other_token = service.login("bravo", "admin", PASSWORD, "source")
    timestamp = auth.now()
    for minutes in range(20, 721, 20):
        monkeypatch.setattr(
            auth, "now", lambda minutes=minutes: timestamp + timedelta(minutes=minutes)
        )
        if minutes < 720:
            with service.authenticated(other_token) as (session, principal):
                service.renew(session, principal)
        else:
            with pytest.raises(AuthError), service.authenticated(other_token):
                pass


def test_production_cookie_and_config_fail_closed(
    client: TestClient, service: AuthService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.bootstrap("alpha", "admin", PASSWORD)
    monkeypatch.setenv("AUTH_ORIGIN", "https://example.test")
    monkeypatch.delenv("AUTH_LOCAL_HTTP", raising=False)
    response = client.post(
        "/api/auth/login",
        json={"organization": "alpha", "login": "admin", "password": PASSWORD},
        headers={"Origin": "https://example.test"},
    )
    cookie = response.headers["set-cookie"]
    assert "__Host-quotepilot=" in cookie
    assert all(part in cookie for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"))
    assert "Domain=" not in cookie
    monkeypatch.setenv("AUTH_ORIGIN", "http://example.test")
    assert client.get("/api/me").status_code == 503
    assert client.get("/health").status_code == 200
    monkeypatch.setenv("AUTH_LOCAL_HTTP", "1")
    assert client.get("/api/me").status_code == 503


def test_disabled_login_and_denied_audit_survive(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    token = str(client.cookies.get("quotepilot_dev"))
    with service.authenticated(token) as (session, principal):
        service.update_user(session, principal, principal.user_id, 2, True, False)
    for password in (NEW, "wrong-password"):
        response = client.post(
            "/api/auth/login",
            json={"organization": "alpha", "login": "admin", "password": password},
        )
        assert response.status_code == 401 and response.json()["code"] == "AUTH_INVALID_CREDENTIALS"
    with Session(database) as session:
        denied = list(
            session.scalars(
                select(BusinessAuditEvent).where(BusinessAuditEvent.outcome == "denied")
            )
        )
        assert len(denied) == 2
        assert all(e.actor_id is None and e.evidence == "invalid_credentials" for e in denied)


def test_unicode_password_change_and_identifier_policy(
    client: TestClient, service: AuthService
) -> None:
    unicode_password = "初始安全密碼-password-938!"
    unicode_replacement = "替換安全密碼-password-824!"
    service.bootstrap("alpha", "admin", unicode_password)
    sign_in(client, password=unicode_password)
    response = client.post(
        "/api/auth/password",
        json={"current_password": unicode_password, "new_password": unicode_replacement},
    )
    assert response.status_code == 200
    sign_in(client, password=unicode_replacement)
    assert client.get("/api/admin/users").status_code == 200
    for value in ("Kelvin", "Ａdmin", "adмin"):
        with pytest.raises(ValueError):
            auth.normalized_login(value)
        with pytest.raises(ValueError):
            auth.normalized_locator(value)


def test_non_ascii_csrf_is_typed_denial(client: TestClient, service: AuthService) -> None:
    ready(client, service)
    response = client.post("/api/auth/refresh", json={}, headers={b"x-csrf-token": b"\xe9"})
    assert response.status_code == 403
    assert response.json()["code"] == "AUTH_CSRF_REJECTED"


def test_session_survives_fresh_process_and_revocation(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    token = str(client.cookies.get("quotepilot_dev"))
    env = {
        **os.environ,
        "DATABASE_URL": database.url.render_as_string(hide_password=False),
        "QT_TEST_TOKEN": token,
    }
    script = """
import os
from sqlalchemy.orm import sessionmaker
from quotepilot_api.auth import AuthService, AuthError
from quotepilot_api.database import database_engine
engine = database_engine()
try:
    with AuthService(sessionmaker(engine)).authenticated(os.environ['QT_TEST_TOKEN']):
        pass
except AuthError:
    raise SystemExit(1)
finally:
    engine.dispose()
"""
    assert (
        subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, timeout=15
        ).returncode
        == 0
    )
    assert client.post("/api/auth/logout", json={}).status_code == 204
    assert (
        subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, timeout=15
        ).returncode
        == 1
    )
