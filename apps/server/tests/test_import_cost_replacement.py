"""QT-006 regression: effective product-cost replacement may change UOM."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from test_auth import ready
from test_imports import commit, import_file, preview, upload

from quotepilot_api import commercial_schema as schema
from quotepilot_api.auth import AuthService


def test_product_cost_replacement_can_change_uom(
    client: TestClient, service: AuthService, database: Engine
) -> None:
    ready(client, service)
    import_file(client, "products")
    import_file(
        client,
        "uom_conversions",
        b"sku,from_uom,to_uom,factor,valid_from\n"
        b"SYN-SKU-TAPE-24MM,BOX,EA,12,2026-01-01T00:00:00Z\n",
    )
    import_file(
        client,
        "product_costs",
        b"sku,uom,unit_cost,valid_from\nSYN-SKU-TAPE-24MM,EA,1,2026-01-01T00:00:00Z\n",
    )

    replacement = preview(
        client,
        upload(
            client,
            "product_costs",
            b"sku,uom,unit_cost,valid_from\nSYN-SKU-TAPE-24MM,BOX,12,2026-10-01T00:00:00Z\n",
        ),
        "replace_effective",
    )
    assert replacement["preview"]["valid"]
    result = commit(client, replacement)
    assert result.status_code == 200
    assert result.json()["updated"] == 1

    with Session(database) as session:
        rows = (
            session.execute(
                select(schema.product_costs).order_by(schema.product_costs.c.valid_from)
            )
            .mappings()
            .all()
        )
        assert len(rows) == 2
        assert rows[0]["uom"] == "EA"
        assert rows[0]["valid_to"] == datetime(2026, 10, 1, tzinfo=UTC)
        assert rows[1]["uom"] == "BOX"
        assert rows[1]["valid_to"] is None
