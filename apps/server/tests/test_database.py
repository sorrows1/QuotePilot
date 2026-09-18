from uuid import UUID, uuid4

import pytest
from sqlalchemy import DateTime, Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from quotepilot_api.database import database_engine, database_url
from quotepilot_api.migrate import migrate
from quotepilot_api.tenants import Tenant, TenantSettings


def test_effective_configuration_connects_to_expected_database() -> None:
    url = database_url()
    engine = database_engine()
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_database()")) == url.database
            assert connection.scalar(text("SELECT current_user")) == url.username
            version = connection.scalar(text("SHOW server_version_num"))
            assert version is not None and int(version) // 10000 == 17
    finally:
        engine.dispose()


def test_compose_configuration_escapes_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    for key, value in {
        "DATABASE_HOST": "db",
        "POSTGRES_USER": "custom",
        "POSTGRES_PASSWORD": "p@ss%word",
        "POSTGRES_DB": "custom",
    }.items():
        monkeypatch.setenv(key, value)
    url = database_url()
    assert (url.host, url.password, url.database) == ("db", "p@ss%word", "custom")
    monkeypatch.setenv("DATABASE_URL", "postgresql://deployed:secret@remote:5433/production")
    assert database_url().host == "remote"


@pytest.mark.parametrize(
    "raw", [None, "", "bad", "sqlite:///db", "postgresql:///db", "postgresql://u@h:0/d"]
)
def test_configuration_rejects_invalid_url(
    monkeypatch: pytest.MonkeyPatch, raw: str | None
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_HOST", raising=False)
    if raw is not None:
        monkeypatch.setenv("DATABASE_URL", raw)
    with pytest.raises(ValueError, match="valid PostgreSQL DATABASE_URL"):
        database_url()


def test_url_preserves_encoded_credentials_and_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://custom:p%40ss%25word@localhost:5544/custom%20db?sslmode=require",
    )
    url = database_url()
    assert (url.username, url.password, url.host, url.port, url.database) == (
        "custom",
        "p@ss%word",
        "localhost",
        5544,
        "custom db",
    )
    assert url.drivername == "postgresql+psycopg"
    assert url.query["sslmode"] == "require"


def test_upgrade_downgrade_reupgrade(empty_database: Engine) -> None:
    assert inspect(empty_database).get_table_names() == []
    for _ in range(2):
        migrate(empty_database)
        inspector = inspect(empty_database)
        assert set(inspector.get_table_names()) == {
            "alembic_version",
            "tenants",
            "tenant_settings",
            "tenant_logins",
            "users",
            "auth_sessions",
            "business_audit_events",
            "auth_limits",
        }
        assert inspector.get_pk_constraint("tenants")["constrained_columns"] == ["id"]
        assert inspector.get_pk_constraint("tenant_settings")["constrained_columns"] == [
            "tenant_id"
        ]
        fk = inspector.get_foreign_keys("tenant_settings")[0]
        assert (fk["constrained_columns"], fk["referred_table"], fk["referred_columns"]) == (
            ["tenant_id"],
            "tenants",
            ["id"],
        )
        for table, key in [("tenants", "id"), ("tenant_settings", "tenant_id")]:
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            assert str(columns[key]["type"]) == "UUID"
            for timestamp in ["created_at", "updated_at"]:
                column_type = columns[timestamp]["type"]
                assert isinstance(column_type, DateTime) and column_type.timezone
                assert columns[timestamp]["default"] is not None
            assert all(not column["nullable"] for column in columns.values())
        with empty_database.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 0
            assert connection.scalar(text("SHOW timezone")) == "UTC"
        migrate(empty_database, "downgrade", "base")
        assert inspect(empty_database).get_table_names() == ["alembic_version"]
    migrate(empty_database)


def test_foreign_key_and_one_to_one_constraints(database: Engine) -> None:
    tenant_id = uuid4()
    with pytest.raises(IntegrityError), Session(database) as session, session.begin():
        session.add(TenantSettings(tenant_id=tenant_id, business_timezone="UTC"))
        session.flush()
    with Session(database) as session, session.begin():
        session.add(Tenant(id=tenant_id))
        session.flush()
        session.add(TenantSettings(tenant_id=tenant_id, business_timezone="UTC"))
    with pytest.raises(IntegrityError), Session(database) as session, session.begin():
        session.add(TenantSettings(tenant_id=tenant_id, business_timezone="Asia/Singapore"))
        session.flush()
    with Session(database) as session:
        assert isinstance(session.get(Tenant, tenant_id).id, UUID)  # type: ignore[union-attr]
