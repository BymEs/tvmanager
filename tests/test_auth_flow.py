from __future__ import annotations

from fastapi.testclient import TestClient

from app import app


BOOTSTRAP_PAYLOAD = {
    "organization_name": "MD Teknoloji Test",
    "organization_slug": "md-teknoloji-test",
    "email": "admin@example.com",
    "full_name": "Test Administrator",
    "password": "StrongTestPassword123!",
}


def test_bootstrap_login_me_and_site_lifecycle() -> None:
    with TestClient(app) as client:
        status_before = client.get("/api/v1/status")
        assert status_before.status_code == 200
        assert status_before.json()["bootstrapped"] is False

        bootstrap = client.post("/api/v1/auth/bootstrap", json=BOOTSTRAP_PAYLOAD)
        assert bootstrap.status_code == 201
        bootstrap_payload = bootstrap.json()
        assert bootstrap_payload["token_type"] == "bearer"
        assert bootstrap_payload["user"]["email"] == BOOTSTRAP_PAYLOAD["email"]
        assert bootstrap_payload["user"]["role"] == "superadmin"

        second_bootstrap = client.post("/api/v1/auth/bootstrap", json=BOOTSTRAP_PAYLOAD)
        assert second_bootstrap.status_code == 409

        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": BOOTSTRAP_PAYLOAD["email"],
                "password": BOOTSTRAP_PAYLOAD["password"],
            },
        )
        assert login.status_code == 200
        access_token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        me = client.get("/api/v1/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["email"] == BOOTSTRAP_PAYLOAD["email"]
        assert me.json()["role"] == "superadmin"

        create_site = client.post(
            "/api/v1/sites",
            headers=headers,
            json={
                "name": "Istanbul Test Site",
                "address": "Istanbul",
                "timezone_name": "Europe/Istanbul",
            },
        )
        assert create_site.status_code == 201
        created_site = create_site.json()
        assert created_site["name"] == "Istanbul Test Site"
        assert created_site["timezone_name"] == "Europe/Istanbul"

        sites = client.get("/api/v1/sites", headers=headers)
        assert sites.status_code == 200
        assert any(site["id"] == created_site["id"] for site in sites.json())

        status_after = client.get("/api/v1/status")
        assert status_after.status_code == 200
        assert status_after.json()["bootstrapped"] is True


def test_protected_routes_reject_missing_or_invalid_tokens() -> None:
    with TestClient(app) as client:
        missing_token = client.get("/api/v1/me")
        assert missing_token.status_code == 401

        invalid_token = client.get(
            "/api/v1/me",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert invalid_token.status_code == 401


def test_login_rejects_invalid_credentials() -> None:
    with TestClient(app) as client:
        client.post("/api/v1/auth/bootstrap", json=BOOTSTRAP_PAYLOAD)

        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": BOOTSTRAP_PAYLOAD["email"],
                "password": "wrong-password",
            },
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"
