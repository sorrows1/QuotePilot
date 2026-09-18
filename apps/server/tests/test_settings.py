"""QT-004 observable API, isolation, exact values, history and concurrency regression tests."""

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from test_auth import NEW, PASSWORD, ready, sign_in

from quotepilot_api import settings
from quotepilot_api.auth import AuthError, AuthService
from quotepilot_api.auth_models import BusinessAuditEvent, User
from quotepilot_api.migrate import migrate
from quotepilot_api.settings import NumberAllocator, SettingsError, read_logo, sanitize_logo
from quotepilot_api.settings_models import BrandAsset, QuoteNumber, SettingsRevision
from quotepilot_api.tenants import TenantContext, TenantService, TenantSettings

pytest_plugins = ["test_auth"]

COMPLETE: dict[str, object] = {
    "company_name": " Example Company ",
    "company_address": "One Street\r\nSingapore",
    "tax_enabled": False,
    "inventory_max_age_minutes": 60,
    "default_quote_validity_days": 30,
    "quote_number_prefix": " qt ",
    "discount_approval_enabled": False,
    "quote_margin_control_enabled": False,
    "line_margin_control_enabled": False,
    "quote_value_approval_enabled": False,
}


def patch(client: TestClient, values: dict[str, object]) -> Any:
    version = client.get("/api/admin/settings").json()["edit_version"]
    return client.patch("/api/admin/settings", json={"expected_edit_version": version, **values})


def complete(client: TestClient) -> dict[str, Any]:
    result = patch(client, COMPLETE)
    assert result.status_code == 200, result.text
    result = client.post(
        "/api/admin/settings/complete",
        json={"expected_edit_version": result.json()["edit_version"]},
    )
    assert result.status_code == 200, result.text
    return dict(result.json())


def test_setup_revision_conflict_noop_and_audit(
    client: TestClient, service: AuthService, database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready(client, service)
    assert client.get("/api/me").json()["setup_complete"] is False
    result = patch(client, {"company_name": "First"})
    assert result.status_code == 200
    assert result.json()["active_settings_revision"] is None
    assert client.get("/api/admin/settings").json()["company_name"] == "First"
    result = client.post("/api/admin/settings/complete", json={"expected_edit_version": 2})
    assert result.status_code == 422
    value = complete(client)
    assert value["active_settings_revision"] == 1
    context = TenantContext(UUID(client.get("/api/me").json()["tenant_id"]))
    assert not TenantService(service.sessions).update_timezone(
        context, context.tenant_id, "Asia/Singapore"
    )
    with Session(database) as session:
        assert settings.read_revision(session, context, 1).company_name == "Example Company"
        with pytest.raises(AuthError):
            settings.read_revision(session, TenantContext(uuid4()), 1)
    assert client.get("/api/me").json()["setup_complete"] is True
    assert "company_name" not in client.get("/api/me").json()
    tenant = UUID(client.get("/api/me").json()["tenant_id"])
    with Session(database) as session:
        history = session.get(SettingsRevision, (tenant, 1))
        assert history is not None and history.company_name == "Example Company"
        count = session.scalar(select(func.count()).select_from(BusinessAuditEvent))
    assert (
        patch(client, {"company_name": "Example Company"}).json()["edit_version"]
        == value["edit_version"]
    )
    assert (
        client.post(
            "/api/admin/settings/complete", json={"expected_edit_version": value["edit_version"]}
        ).json()["active_settings_revision"]
        == 1
    )
    with Session(database) as session:
        assert count == session.scalar(select(func.count()).select_from(BusinessAuditEvent))
    changed = patch(client, {"company_name": "Second"}).json()
    assert changed["active_settings_revision"] == 2
    conflict = client.patch(
        "/api/admin/settings",
        json={"expected_edit_version": value["edit_version"], "company_name": "Lost"},
    )
    assert conflict.status_code == 409 and conflict.json()["code"] == "SETTINGS_VERSION_CONFLICT"
    assert patch(client, {"company_name": None}).status_code == 422
    with Session(database) as session:
        assert session.get(SettingsRevision, (tenant, 1)).company_name == "Example Company"  # type: ignore[union-attr]
        for audit in session.scalars(
            select(BusinessAuditEvent).where(BusinessAuditEvent.action.like("settings%"))
        ):
            assert "Second" not in audit.evidence and "Street" not in audit.evidence

    def fail(*args: Any, **kwargs: Any) -> None:
        raise SQLAlchemyError("forced audit failure")

    monkeypatch.setattr(settings, "audit", fail)
    assert patch(client, {"company_name": "Must rollback"}).status_code == 503
    assert client.get("/api/admin/settings").json()["company_name"] == "Second"
    with database.begin() as connection, pytest.raises(SQLAlchemyError):
        connection.execute(text("UPDATE settings_revisions SET company_name = 'tampered'"))


@pytest.mark.parametrize(
    "key,value",
    [
        ("tax_rate", 0.09),
        ("tax_rate", "0.0000001"),
        ("tax_rate", "1.1"),
        ("tax_rate", "NaN"),
        ("tax_rate", "1e-2"),
        ("tax_rate", "-0.1"),
        ("minimum_line_margin_threshold", 0),
        ("minimum_margin_threshold", "0.1234567"),
        ("quote_value_approval_threshold", "1.001"),
        ("quote_value_approval_threshold", "1000000000000000000"),
        ("default_quote_validity_days", 0),
        ("default_quote_validity_days", 366),
        ("default_quote_validity_days", 1.5),
        ("default_quote_validity_days", True),
        ("inventory_max_age_minutes", 0),
        ("inventory_max_age_minutes", -1),
        ("inventory_max_age_minutes", 1.5),
        ("inventory_max_age_minutes", 2147483648),
        ("quote_number_prefix", "A-B"),
        ("quote_number_prefix", "ß"),
        ("company_name", "\u0000"),
        ("company_name", " " * 3),
        ("currency_code", "USD"),
        ("tenant_id", str(uuid4())),
    ],
)
def test_strict_inputs(client: TestClient, service: AuthService, key: str, value: object) -> None:
    ready(client, service)
    assert patch(client, {key: value}).status_code == 422


def test_conditional_and_boundary_values(client: TestClient, service: AuthService) -> None:
    ready(client, service)
    for validity in (1, 365):
        assert patch(client, {"default_quote_validity_days": validity}).status_code == 200
    assert patch(client, {"inventory_max_age_minutes": 2147483647}).status_code == 200
    assert patch(client, {"tax_enabled": True}).status_code == 200  # partial drafts allowed
    for rate in ("0", "1", "0.123456"):
        assert patch(client, {"tax_rate": rate, "freight_taxable": False}).status_code == 200
    for enabled, threshold in settings.CONTROLS.items():
        for value in ("0", "1"):
            assert patch(client, {enabled: True, threshold: value}).status_code == 200
        result = patch(client, {enabled: False})
        assert result.json()[threshold] is None
        assert patch(client, {threshold: "0"}).status_code == 422
    result = patch(client, {"tax_enabled": False}).json()
    assert result["tax_rate"] is None and result["freight_taxable"] is None
    complete(client)
    assert patch(client, {"tax_enabled": True}).status_code == 422
    assert (
        patch(
            client, {"tax_enabled": True, "tax_rate": "0.09", "freight_taxable": False}
        ).status_code
        == 200
    )


@pytest.mark.parametrize("role", ["sales_admin", "sales_manager"])
def test_rbac_setup_signal_and_csrf(client: TestClient, service: AuthService, role: str) -> None:
    ready(client, service)
    created = client.post(
        "/api/admin/users", json={"login": "sales", "password": PASSWORD, "role": role}
    )
    assert created.status_code == 201
    # A separate incomplete tenant must not inherit the completed tenant signal.
    complete(client)
    other = TenantService(service.sessions).provision("UTC")
    service.bootstrap("beta", "admin", PASSWORD, existing_tenant=other.tenant_id)
    sign_in(client, organization="beta")
    assert client.get("/api/me").json()["setup_complete"] is False
    sign_in(client, login="sales")
    changed = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    client.headers["x-csrf-token"] = changed.json()["csrf_token"]
    assert client.get("/api/me").json()["setup_complete"] is True
    for method, path, body in [
        ("GET", "", None),
        ("PATCH", "", {"expected_edit_version": 1}),
        ("POST", "/complete", {"expected_edit_version": 1}),
        ("DELETE", "/logo", {"expected_edit_version": 1}),
        ("GET", "/logo", None),
    ]:
        assert client.request(method, "/api/admin/settings" + path, json=body).status_code == 403
    sign_in(client, password=NEW)
    client.headers["x-csrf-token"] = "bad"
    assert client.patch("/api/admin/settings", json={"expected_edit_version": 1}).status_code == 403


def logo_bytes(format: str = "PNG", size: tuple[int, int] = (32, 32)) -> bytes:
    target = io.BytesIO()
    image = Image.new("RGB", size, "red")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private", "must not survive")
    image.save(target, format=format, pnginfo=metadata)
    return target.getvalue()


def test_logo_sanitation_and_history(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    complete(client)
    tenant = TenantContext(UUID(client.get("/api/me").json()["tenant_id"]))
    for format, mime in [("PNG", "image/png"), ("JPEG", "image/jpeg")]:
        version = client.get("/api/admin/settings").json()["edit_version"]
        result = client.put(
            "/api/admin/settings/logo",
            json={
                "expected_edit_version": version,
                "media_type": mime,
                "data_base64": base64.b64encode(logo_bytes(format)).decode(),
            },
        )
        assert result.status_code == 200, result.text
        asset_id = UUID(result.json()["current_logo_asset_id"])
        response = client.get("/api/admin/settings/logo")
        assert response.headers["content-type"] == "image/png"
        with Image.open(io.BytesIO(response.content)) as image:
            assert "private" not in image.info
        with Session(database) as session:
            assert read_logo(session, tenant, asset_id).byte_size == len(response.content)
            with pytest.raises(AuthError):
                read_logo(session, TenantContext(uuid4()), asset_id)
    result = client.request(
        "DELETE",
        "/api/admin/settings/logo",
        json={"expected_edit_version": result.json()["edit_version"]},
    )
    assert result.status_code == 200
    assert client.get("/api/admin/settings/logo").status_code == 404
    with Session(database) as session:
        assert read_logo(session, tenant, asset_id) is not None
        assert (session.scalar(select(func.count()).select_from(BrandAsset)) or 0) >= 1
        assert (session.scalar(select(func.count()).select_from(SettingsRevision)) or 0) >= 3


@pytest.mark.parametrize(
    "data,mime",
    [
        (b"<svg/>", "image/png"),
        (b"broken", "image/png"),
        (b"x" * (2097152 + 1), "image/png"),
        (logo_bytes("PNG", (15, 32)), "image/png"),
        (logo_bytes("PNG", (2049, 16)), "image/png"),
        (logo_bytes(), "image/jpeg"),
        (logo_bytes("GIF"), "image/png"),
    ],
    ids=["svg", "malformed", "oversize", "too-small", "too-large", "mismatch", "gif"],
)
def test_bad_logos(data: bytes, mime: str) -> None:
    with pytest.raises(SettingsError):
        sanitize_logo(data, mime)


def test_number_allocation_and_concurrent_writers(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    tenant = TenantContext(UUID(client.get("/api/me").json()["tenant_id"]))
    allocator = NumberAllocator(service.sessions)
    with pytest.raises(SettingsError):
        allocator.allocate(tenant, uuid4())
    value = complete(client)
    subject = uuid4()
    with ThreadPoolExecutor(max_workers=6) as pool:
        assert set(pool.map(lambda _: allocator.allocate(tenant, subject), range(12))) == {
            "QT-000001"
        }
        results = list(pool.map(lambda _: allocator.allocate(tenant, uuid4()), range(12)))
    assert len(set(results)) == 12
    assert sorted(int(number.split("-")[1]) for number in results) == list(range(2, 14))
    token = str(client.cookies.get("quotepilot_dev"))

    def write(name: str) -> str:
        try:
            with service.authenticated(token) as (session, principal):
                settings.mutate(session, principal, value["edit_version"], {"company_name": name})
            return "ok"
        except SettingsError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ["First", "Second"])) == ["SETTINGS_VERSION_CONFLICT", "ok"]
    assert patch(client, {"quote_number_prefix": "new"}).status_code == 200
    assert allocator.allocate(tenant, subject) == "QT-000001"
    assert allocator.allocate(tenant, uuid4()) == "NEW-000014"
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(QuoteNumber)) == 14


def test_migration_populated_baseline_and_disposable_backout(empty_database: Engine) -> None:
    from sqlalchemy.orm import sessionmaker

    migrate(empty_database, revision="0003_auth_commercial_merge")
    tenant, user = uuid4(), uuid4()
    with empty_database.begin() as connection:
        connection.execute(text("INSERT INTO tenants (id) VALUES (:id)"), {"id": tenant})
        connection.execute(
            text("INSERT INTO tenant_settings (tenant_id,business_timezone) VALUES (:id,'UTC')"),
            {"id": tenant},
        )
        connection.execute(
            text(
                "INSERT INTO users (id,tenant_id,login,password_hash,role,disabled,"
                "must_change_password,version) VALUES "
                "(:user,:tenant,'admin','not-a-password','system_admin',false,true,1)"
            ),
            {"user": user, "tenant": tenant},
        )
    product_id = uuid4()
    with empty_database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO products (id,tenant_id,sku,name,description) "
                "VALUES (:id,:tenant,'PRESERVED','Existing product','Before QT-004')"
            ),
            {"id": product_id, "tenant": tenant},
        )
    migrate(empty_database)
    with sessionmaker(empty_database).begin() as session:
        assert (
            session.scalar(text("SELECT name FROM products WHERE id=:id"), {"id": product_id})
            == "Existing product"
        )
        assert (
            session.scalar(
                text("SELECT next_sequence FROM quote_number_counters WHERE tenant_id=:id"),
                {"id": tenant},
            )
            == 1
        )
        assert session.get(User, user) is not None
        assert session.get(TenantSettings, tenant).business_timezone == "UTC"  # type: ignore[union-attr]
        assert session.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [
            "0005_imports"
        ]
    migrate(empty_database, "downgrade", "0003_auth_commercial_merge")
    migrate(empty_database)


def test_animation_and_decompression_dimensions() -> None:
    target = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(
        target,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (32, 32), "blue")],
        duration=100,
    )
    with pytest.raises(SettingsError):
        sanitize_logo(target.getvalue(), "image/png")
    # Oversized IHDR is rejected before allocating pixels, even when compressed data is tiny.
    import struct
    import zlib

    data = bytearray(logo_bytes())
    data[16:24] = struct.pack(">II", 30000, 30000)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]))
    with pytest.raises(SettingsError):
        sanitize_logo(bytes(data), "image/png")
    with pytest.raises(SettingsError):
        sanitize_logo(logo_bytes()[:40], "image/png")


def test_number_tenant_isolation_growth_and_immutability(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    complete(client)
    context = TenantContext(UUID(client.get("/api/me").json()["tenant_id"]))
    subject = uuid4()
    allocator = NumberAllocator(service.sessions)
    assert allocator.allocate(context, subject) == "QT-000001"
    other = TenantService(service.sessions).provision("UTC")
    service.bootstrap("beta", "admin", PASSWORD, existing_tenant=other.tenant_id)
    sign_in(client, organization="beta")
    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    complete(client)
    assert allocator.allocate(other, subject) == "QT-000001"
    with database.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO quote_numbers "
                "(tenant_id,subject_id,sequence,number,prefix,settings_revision) "
                "VALUES (:tenant,:subject,999999,'QT-999999','QT',1)"
            ),
            {"tenant": other.tenant_id, "subject": uuid4()},
        )
    with database.begin() as connection:
        connection.execute(
            text("UPDATE quote_number_counters SET next_sequence=1000000 WHERE tenant_id=:tenant"),
            {"tenant": other.tenant_id},
        )
    assert allocator.allocate(other, uuid4()) == "QT-1000000"
    for table in ("quote_numbers", "brand_assets", "settings_revisions"):
        with pytest.raises(SQLAlchemyError), database.begin() as connection:
            connection.execute(text(f"DELETE FROM {table}"))
    # Composite FK enforces tenant ownership even for a trusted caller's accidental bad reference.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError), database.begin() as connection:
        connection.execute(
            text("UPDATE tenant_settings SET current_logo_asset_id=:id WHERE tenant_id=:tenant"),
            {"id": uuid4(), "tenant": context.tenant_id},
        )


def test_standalone_model_registration() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from quotepilot_api.tenants import Base; "
            "print([t.name for t in Base.metadata.sorted_tables])",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "settings_revisions" in result.stdout and "users" in result.stdout
