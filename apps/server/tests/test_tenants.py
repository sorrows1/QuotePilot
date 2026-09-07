from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from quotepilot_api.tenants import (
    Tenant,
    TenantContext,
    TenantRepository,
    TenantService,
    TenantSettings,
)


def test_tenant_isolation_and_timestamps(database: Engine) -> None:
    service = TenantService(sessionmaker(database))
    a = service.provision("Asia/Singapore")
    b = service.provision("Europe/London")
    before = service.get_settings(a, a.tenant_id)
    assert before is not None
    assert before.business_timezone == "Asia/Singapore"
    assert before.created_at.utcoffset() == timedelta(0)
    assert before.updated_at.utcoffset() == timedelta(0)
    assert service.get_settings(a, b.tenant_id) is None
    assert not service.update_timezone(a, b.tenant_id, "UTC")
    assert service.update_timezone(a, a.tenant_id, "UTC")
    after = service.get_settings(a, a.tenant_id)
    other = service.get_settings(b, b.tenant_id)
    assert after is not None and other is not None
    assert after.business_timezone == "UTC"
    assert after.created_at == before.created_at
    assert after.updated_at > before.updated_at
    assert other.business_timezone == "Europe/London"
    with Session(database) as session:
        repository = TenantRepository(session)
        assert repository.get(a, b.tenant_id) is None
        assert repository.get(b, a.tenant_id) is None
        assert repository.get_settings(b, a.tenant_id) is None
        assert not repository.update_timezone(b, a.tenant_id, "Europe/Paris")
    missing = TenantContext(uuid4())
    assert service.get_settings(missing, missing.tenant_id) is None
    assert not service.update_timezone(missing, missing.tenant_id, "UTC")


@pytest.mark.parametrize("invalid", [None, "", "not-a-uuid", uuid4(), {"tenant_id": uuid4()}])
def test_context_rejection_before_database_access(invalid: Any) -> None:
    # Unbound sessions prove rejection happens before any SQL/connection is attempted.
    with Session() as session:
        repository = TenantRepository(session)
        operations: list[Callable[[], object]] = [
            lambda: repository.create(invalid, "UTC"),
            lambda: repository.get(invalid, uuid4()),
            lambda: repository.get_settings(invalid, uuid4()),
            lambda: repository.update_timezone(invalid, uuid4(), "UTC"),
            lambda: TenantService(sessionmaker()).get_settings(invalid, uuid4()),
            lambda: TenantService(sessionmaker()).update_timezone(invalid, uuid4(), "UTC"),
        ]
        for operation in operations:
            with pytest.raises(ValueError, match="TenantContext"):
                operation()
        with pytest.raises(TypeError):
            repository.get_settings(tenant_id=uuid4())  # type: ignore[call-arg]


@pytest.mark.parametrize("invalid", [None, "", "not-a-uuid", str(uuid4()), UUID(int=0)])
def test_context_rejects_invalid_identity(invalid: Any) -> None:
    with pytest.raises(ValueError):
        TenantContext(invalid)


def test_failed_settings_insert_rolls_back_provisioning(database: Engine) -> None:
    def fail_insert(*args: Any) -> None:
        raise IntegrityError("injected settings failure", None, Exception("test"))

    event.listen(TenantSettings, "before_insert", fail_insert)
    try:
        with pytest.raises(IntegrityError):
            TenantService(sessionmaker(database)).provision("UTC")
    finally:
        event.remove(TenantSettings, "before_insert", fail_insert)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(Tenant)) == 0
        assert session.scalar(select(func.count()).select_from(TenantSettings)) == 0


def test_repository_does_not_commit(database: Engine) -> None:
    context = TenantContext(uuid4())
    with Session(database) as session:
        TenantRepository(session).create(context, "UTC")
        session.rollback()
    with Session(database) as session:
        assert TenantRepository(session).get(context, context.tenant_id) is None


@pytest.mark.parametrize("timezone", ["", "Mars/Olympus", "/etc/passwd", "../UTC"])
def test_invalid_timezone_leaves_settings_unchanged(database: Engine, timezone: str) -> None:
    service = TenantService(sessionmaker(database))
    context = service.provision("UTC")
    with pytest.raises(ValueError, match="IANA"):
        service.update_timezone(context, context.tenant_id, timezone)
    settings = service.get_settings(context, context.tenant_id)
    assert settings is not None and settings.business_timezone == "UTC"
