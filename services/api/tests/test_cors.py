from __future__ import annotations

from api.main import app
from fastapi.testclient import TestClient


def test_browser_put_preflight_for_credentials_is_allowed() -> None:
    response = TestClient(app).options(
        "/v1/polymarket-clob-credentials",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "PUT" in response.headers["access-control-allow-methods"]
