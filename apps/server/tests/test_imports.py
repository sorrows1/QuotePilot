"""QT-006 public boundary, all-or-nothing persistence and parser regression evidence."""

import base64
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from test_auth import NEW, PASSWORD, ready, sign_in

from quotepilot_api import commercial_schema as schema
from quotepilot_api.auth import AuthService
from quotepilot_api.auth_models import BusinessAuditEvent
from quotepilot_api.commercial import CommercialRepository
from quotepilot_api.commercial_inputs import CommercialInput
from quotepilot_api.import_models import ImportJob
from quotepilot_api.import_parser import ImportError, parse
from quotepilot_api.migrate import migrate
from quotepilot_api.tenants import TenantContext, TenantService

pytest_plugins = ["test_auth"]
FIXTURES = Path(__file__).parent / "fixtures" / "imports"


def xlsx(
    rows: list[list[str]],
    *,
    numeric: bool = False,
    formula: bool = False,
    extra: dict[str, str] | None = None,
) -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    body = ""
    for n, row in enumerate(rows, 1):
        cells = ""
        for c, value in enumerate(row):
            ref = chr(65 + c) + str(n)
            content = (
                f"<v>{escape(value)}</v>"
                if numeric and n > 1
                else f"<is><t>{escape(value)}</t></is>"
            )
            cell_type = "n" if numeric and n > 1 else "inlineStr"
            cells += f'<c r="{ref}" t="{cell_type}">{"<f>1+1</f>" if formula else ""}{content}</c>'
        body += f'<row r="{n}">{cells}</row>'
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as z:
        parts = {
            "xl/workbook.xml": f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets>'
            '<sheet name="Data" sheetId="1" r:id="r1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships><Relationship Id="r1" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{ns}">'
            f"<sheetData>{body}</sheetData></worksheet>",
            **(extra or {}),
        }
        for name, value in parts.items():
            z.writestr(name, value)
    return stream.getvalue()


def upload(
    client: TestClient, kind: str, data: bytes | None = None, filename: str | None = None
) -> dict[str, Any]:
    raw = data if data is not None else (FIXTURES / f"{kind}.csv").read_bytes()
    response = client.post(
        "/api/admin/imports",
        json={
            "filename": filename or f"{kind}.csv",
            "kind": kind,
            "data_base64": base64.b64encode(raw).decode(),
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def preview(client: TestClient, job: dict[str, Any], mode: str = "insert") -> dict[str, Any]:
    response = client.post(
        f"/api/admin/imports/{job['id']}/preview",
        json={
            "mapping": {h: h for h in job["headers"]},
            "mode": mode,
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def commit(client: TestClient, job: dict[str, Any], key: str | None = None) -> Any:
    return client.post(
        f"/api/admin/imports/{job['id']}/commit",
        json={
            "preview_token": job["preview_token"],
            "idempotency_key": key or job["id"],
        },
    )


def import_file(
    client: TestClient, kind: str, data: bytes | None = None, mode: str = "insert"
) -> dict[str, Any]:
    job = preview(client, upload(client, kind, data), mode)
    assert job["preview"]["valid"], job
    result = commit(client, job)
    assert result.status_code == 200, result.text
    return job


def count(database: Engine, kind: str) -> int:
    with Session(database) as session:
        return int(session.scalar(select(func.count()).select_from(schema.TABLES[kind])) or 0)


def test_csv_and_xlsx_exact_values() -> None:
    assert parse("customers.xlsx", (FIXTURES / "customers.xlsx").read_bytes()) == parse(
        "customers.csv", (FIXTURES / "customers.csv").read_bytes()
    )
    rows = [["amount"], ["999999999999999999.123456"]]
    assert parse("a.xlsx", xlsx(rows, numeric=True)) == (rows[0], rows[1:])
    assert parse("a.csv", b'\xef\xbb\xbfkey,name\r\nA,"a,b"\r\n') == (
        ["key", "name"],
        [["A", "a,b"]],
    )
    rows = [["external_key", "name"], ["SYN-CUST-100", "Synthetic customer"]]
    assert parse("a.xlsx", xlsx(rows)) == (rows[0], rows[1:])


@pytest.mark.parametrize(
    "filename,data",
    [
        ("a.txt", b"a\nb"),
        ("a.csv", b""),
        ("a.csv", b"a" * 2_000_001),
        ("a.csv", b"a,a\nx,y"),
        ("a.csv", b"a,b\nx"),
        ("a.csv", b'a\n"unterminated'),
        ("a.csv", b"a\n\xff"),
        ("a.csv", b"a\n\x00"),
        ("a.csv", b"a\n" + b"x\n" * 2001),
        ("a.csv", b"a\n" + b"x" * 4001),
        ("a.xlsx", b"PK-not-a-workbook"),
        ("a.xlsx", xlsx([["a"], ["1"]], formula=True)),
        ("a.xlsx", xlsx([["a"], ["1"]], extra={"bomb.xml": "x" * 1_000_000})),
        (
            "a.xlsx",
            xlsx(
                [["a"], ["1"]],
                extra={
                    "external.rels": '<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'
                },
            ),
        ),
        (
            "a.xlsx",
            xlsx(
                [["a"], ["1"]], extra={"entity.xml": '<!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>'}
            ),
        ),
    ],
    ids=[f"unsafe-{i}" for i in range(15)],
)
def test_parser_rejects_unsafe_files(filename: str, data: bytes) -> None:
    with pytest.raises(ImportError):
        parse(filename, data)


def test_preview_mapping_atomic_commit_and_retry(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    job = upload(client, "customers", b"Customer Code,Display Name\nSYN-CUST-100,Synthetic\n")
    response = client.post(f"/api/admin/imports/{job['id']}/preview", json={"mapping": {}})
    assert response.status_code == 422
    response = client.post(
        f"/api/admin/imports/{job['id']}/preview",
        json={
            "mapping": {"external_key": "Customer Code", "name": "Display Name"},
        },
    )
    assert response.status_code == 200
    job = response.json()
    assert job["preview"]["valid"] and count(database, "customers") == 0
    result = commit(client, job)
    assert result.status_code == 200 and result.json()["created"] == 1
    assert commit(client, job).json() == result.json()
    assert commit(client, job, "different-key").status_code == 409
    assert count(database, "customers") == 1
    other = preview(client, upload(client, "customers"))
    assert commit(client, other, job["id"]).status_code == 409
    with Session(database) as session:
        events = session.scalars(
            select(BusinessAuditEvent).where(BusinessAuditEvent.action == "import_committed")
        ).all()
        assert len(events) == 1
        assert session.get(ImportJob, UUID(job["id"])).result == result.json()  # type: ignore[union-attr]
    assert client.get(f"/api/admin/imports/{job['id']}").json()["result"] == result.json()
    assert client.get("/api/admin/imports").status_code == 200


def test_invalid_rows_download_and_unchanged_authority(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    job = preview(
        client, upload(client, "customers", (FIXTURES / "invalid_customers.csv").read_bytes())
    )
    assert {e["row"] for e in job["preview"]["errors"]} == {3, 4, 5}
    assert commit(client, job).status_code == 409
    assert count(database, "customers") == 0
    errors = client.get(f"/api/admin/imports/{job['id']}/errors")
    assert (
        errors.status_code == 200
        and "external_key" in errors.text
        and "Missing key" not in errors.text
    )
    assert errors.headers["cache-control"] == "no-store"
    good = preview(client, upload(client, "customers"))
    import_file(client, "customers")
    assert commit(client, good).status_code == 409
    assert count(database, "customers") == 2
    skipped = preview(client, upload(client, "customers"), "skip_identical")
    assert skipped["preview"]["summary"]["skipped"] == 2
    assert commit(client, skipped).status_code == 200


def test_commercial_imports_history_and_reference_revalidation(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    for kind in ("customers", "products", "pricebooks", "prices", "inventory"):
        import_file(client, kind)
    replacement = (
        b"sku,pricebook_key,pricebook_version,uom,unit_price,quantity_min,quantity_max,valid_from\n"
        b"SYN-SKU-TAPE-24MM,SYN-PB-STD-V1,1,EA,2.345600,1,,2026-09-01T00:00:00Z\n"
    )
    changed = import_file(client, "prices", replacement, "replace_effective")
    assert commit(client, changed).json()["updated"] == 1
    with Session(database) as session:
        stock = session.execute(select(schema.inventory)).mappings().all()
        assert len(stock) == 2
        assert all(s["observed_at"] == datetime(2026, 9, 9, tzinfo=UTC) for s in stock)
        assert all(s["observed_at"] <= s["imported_at"] for s in stock)
        prices = (
            session.execute(select(schema.prices).where(schema.prices.c.unit_price == Decimal("1")))
            .mappings()
            .all()
        )
        assert len(prices) == 1 and prices[0]["valid_to"] == datetime(2026, 9, 1, tzinfo=UTC)
    import_file(
        client,
        "pricebook_assignments",
        b"pricebook_key,pricebook_version,valid_from\nSYN-PB-STD-V1,1,2026-01-01T00:00:00Z\n",
    )
    import_file(
        client,
        "uom_conversions",
        b"sku,from_uom,to_uom,factor,valid_from\nSYN-SKU-TAPE-24MM,BOX,EA,12,2026-01-01T00:00:00Z\n",
    )
    import_file(
        client,
        "product_costs",
        b"sku,uom,unit_cost,valid_from\nSYN-SKU-TAPE-24MM,EA,0,2026-01-01T00:00:00Z\n",
    )
    for value in ("-1", "1.0000001", "NaN", "1e99"):
        bad = preview(
            client, upload(client, "prices", replacement.replace(b"2.345600", value.encode()))
        )
        assert not bad["preview"]["valid"]
    missing = preview(
        client,
        upload(
            client,
            "inventory",
            b"sku,uom,quantity,observed_at\nUNKNOWN,EA,1,2026-01-01T00:00:00Z\n",
        ),
    )
    assert missing["preview"]["errors"][0]["field"] == "sku"
    future = preview(
        client,
        upload(
            client,
            "inventory",
            b"sku,uom,quantity,observed_at\nSYN-SKU-TAPE-24MM,EA,1,2099-01-01T00:00:00Z\n",
        ),
    )
    assert not future["preview"]["valid"]
    pending = preview(
        client,
        upload(client, "prices", replacement.replace(b"2026-09-01", b"2026-10-01")),
        "replace_effective",
    )
    assert pending["preview"]["valid"]
    with database.begin() as connection:
        connection.execute(
            schema.products.update()
            .where(schema.products.c.sku == "SYN-SKU-TAPE-24MM")
            .values(archived=True)
        )
    assert commit(client, pending).status_code == 409


def test_tenant_and_role_boundaries(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    first = preview(client, upload(client, "customers"))
    other = TenantService(service.sessions).provision("UTC")
    service.bootstrap("beta", "admin", PASSWORD, existing_tenant=other.tenant_id)
    sign_in(client, organization="beta")
    assert (
        client.post(
            "/api/admin/imports",
            json={"filename": "a.csv", "kind": "customers", "data_base64": "YQo="},
        ).status_code
        == 403
    )
    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    assert client.get(f"/api/admin/imports/{first['id']}").status_code == 404
    assert commit(client, first).status_code == 404
    import_file(client, "customers")
    client.post(
        "/api/admin/users", json={"login": "sales", "password": PASSWORD, "role": "sales_admin"}
    )
    sign_in(client, login="sales", organization="beta")
    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    assert client.get("/api/admin/imports").status_code == 403
    assert client.get(f"/api/admin/imports/{first['id']}/errors").status_code == 403
    sign_in(client, password=NEW)
    assert commit(client, first).status_code == 200
    assert count(database, "customers") == 4
    assert (
        client.post("/api/admin/imports", content='{"x":"' + "x" * 2_800_000 + '"}').status_code
        == 413
    )


def test_concurrent_retry_and_midbatch_rollback(
    client: TestClient, service: AuthService, database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    ready(client, service)
    job = preview(client, upload(client, "customers"))
    from quotepilot_api.import_contract import CommitInput
    from quotepilot_api.imports import commit as commit_service

    token = str(client.cookies.get("quotepilot_dev"))
    body = CommitInput(preview_token=job["preview_token"], idempotency_key=job["id"])

    def attempt() -> dict[str, Any]:
        with service.authenticated(token) as (session, principal):
            return commit_service(session, principal, UUID(job["id"]), body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert results[0] == results[1] and count(database, "customers") == 2
    pending = preview(client, upload(client, "products"))
    original = CommercialRepository.add
    calls = 0

    def fail_second(
        self: CommercialRepository, context: TenantContext, record: CommercialInput
    ) -> UUID:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected failure")
        return original(self, context, record)

    with monkeypatch.context() as patch:
        patch.setattr(CommercialRepository, "add", fail_second)
        with pytest.raises(RuntimeError, match="injected failure"):
            commit(client, pending)
    assert count(database, "products") == 0
    with Session(database) as session:
        assert session.get(ImportJob, UUID(pending["id"])).result is None  # type: ignore[union-attr]
    assert commit(client, pending).status_code == 200


def test_import_migration_populated_backout(empty_database: Engine) -> None:
    migrate(empty_database, "upgrade", "0004_tenant_settings")
    service = AuthService(sessionmaker(empty_database))
    service.bootstrap("old", "admin", PASSWORD)
    migrate(empty_database)
    assert inspect(empty_database).has_table("import_jobs")
    with empty_database.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == 1
    migrate(empty_database, "downgrade", "0004_tenant_settings")
    assert not inspect(empty_database).has_table("import_jobs")
    migrate(empty_database)


def test_xlsx_upload_and_stale_mapping(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    job = preview(
        client,
        upload(
            client,
            "customers",
            xlsx([["external_key", "name"], ["SYN-CUST-100", "Synthetic customer"]]),
            "customers.xlsx",
        ),
    )
    assert job["preview"]["valid"]
    updated = preview(client, job, "skip_identical")
    assert updated["preview_token"] != job["preview_token"]
    assert commit(client, job).status_code == 409
    assert count(database, "customers") == 0
    assert commit(client, updated).status_code == 200


def test_competing_jobs_and_wrong_tenant_references(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    from quotepilot_api.import_contract import CommitInput
    from quotepilot_api.imports import commit as commit_service

    jobs = [preview(client, upload(client, "customers")) for _ in range(2)]
    token = str(client.cookies.get("quotepilot_dev"))

    def attempt(job: dict[str, Any]) -> int:
        try:
            with service.authenticated(token) as (session, principal):
                commit_service(
                    session,
                    principal,
                    UUID(job["id"]),
                    CommitInput(preview_token=job["preview_token"], idempotency_key=job["id"]),
                )
            return 200
        except ImportError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, jobs)) == [200, 409]
    assert count(database, "customers") == 2
    import_file(client, "products")
    other = TenantService(service.sessions).provision("UTC")
    service.bootstrap("beta", "admin", PASSWORD, existing_tenant=other.tenant_id)
    sign_in(client, organization="beta")
    response = client.post(
        "/api/auth/password", json={"current_password": PASSWORD, "new_password": NEW}
    )
    client.headers["x-csrf-token"] = response.json()["csrf_token"]
    job = preview(client, upload(client, "inventory"))
    assert not job["preview"]["valid"]
    assert all(e["field"] == "sku" for e in job["preview"]["errors"])
    assert count(database, "inventory") == 0
