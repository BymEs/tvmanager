from __future__ import annotations

from fastapi.testclient import TestClient

from app import app


def test_application_starts_and_health_is_ok() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert isinstance(payload["online_players"], int)
    assert isinstance(payload["media_assets"], int)


def test_enterprise_api_root_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "MD Teknoloji TV Manager Enterprise API"
    assert payload["docs"] == "/docs"
    assert "authentication" in payload["modules"]
    assert "player_websocket" in payload["modules"]


def test_platform_status_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ready"] is True
    assert "auth" in payload["modules"]


def test_core_pages_exist() -> None:
    with TestClient(app) as client:
        admin_response = client.get("/admin")
        player_response = client.get("/player")

    assert admin_response.status_code == 200
    assert player_response.status_code == 200
