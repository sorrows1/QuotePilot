import pytest
from fastapi.testclient import TestClient

from quotepilot_api.main import app

client = TestClient(app)


def test_health_returns_stable_liveness_response() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "quotepilot-api"}


def test_health_does_not_require_database_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "invalid")
    assert client.get("/health").json() == {"status": "ok", "service": "quotepilot-api"}
