from fastapi.testclient import TestClient

from quotepilot_api.main import app

client = TestClient(app)


def test_health_returns_stable_ready_response() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "quotepilot-api"}
