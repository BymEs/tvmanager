from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import app


BOOTSTRAP_PAYLOAD = {
    "organization_name": "MD Teknoloji Test",
    "organization_slug": "md-teknoloji-platform-test",
    "email": "admin@example.com",
    "full_name": "Platform Administrator",
    "password": "StrongTestPassword123!",
}


def bootstrap_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/bootstrap", json=BOOTSTRAP_PAYLOAD)
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_site(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.post(
        "/api/v1/sites",
        headers=headers,
        json={
            "name": "Istanbul Merkez",
            "address": "Istanbul",
            "timezone_name": "Europe/Istanbul",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_device_record_lifecycle_and_duplicate_uid_rejection() -> None:
    with TestClient(app) as client:
        headers = bootstrap_headers(client)
        site = create_site(client, headers)

        device_payload = {
            "site_id": site["id"],
            "device_uid": "PLAYER-IST-001",
            "name": "Lobby Player",
            "group_name": "Lobby",
            "device_type": "browser",
            "capabilities": {"video": True, "image": True},
            "config": {"resolution": "1920x1080"},
        }

        create_device = client.post(
            "/api/v1/device-records",
            headers=headers,
            json=device_payload,
        )
        assert create_device.status_code == 201
        created = create_device.json()
        assert created["device_uid"] == device_payload["device_uid"]
        assert created["site_id"] == site["id"]

        duplicate = client.post(
            "/api/v1/device-records",
            headers=headers,
            json=device_payload,
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "Device UID already exists"

        devices = client.get("/api/v1/device-records", headers=headers)
        assert devices.status_code == 200
        assert any(item["id"] == created["id"] for item in devices.json())


def test_device_creation_rejects_unknown_site() -> None:
    with TestClient(app) as client:
        headers = bootstrap_headers(client)

        response = client.post(
            "/api/v1/device-records",
            headers=headers,
            json={
                "site_id": "missing-site-id",
                "device_uid": "PLAYER-MISSING-001",
                "name": "Missing Site Player",
                "device_type": "browser",
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Site not found"


def test_admin_can_create_users_and_duplicate_email_is_rejected() -> None:
    with TestClient(app) as client:
        headers = bootstrap_headers(client)
        user_payload = {
            "email": "operator@example.com",
            "full_name": "Test Operator",
            "password": "OperatorPassword123!",
            "role": "operator",
        }

        created = client.post("/api/v1/users", headers=headers, json=user_payload)
        assert created.status_code == 201
        assert created.json()["role"] == "operator"

        duplicate = client.post("/api/v1/users", headers=headers, json=user_payload)
        assert duplicate.status_code == 409

        users = client.get("/api/v1/users", headers=headers)
        assert users.status_code == 200
        assert any(item["email"] == user_payload["email"] for item in users.json())


def test_due_schedule_filtering() -> None:
    with TestClient(app) as client:
        headers = bootstrap_headers(client)
        now = datetime.now(timezone.utc)

        past_schedule = client.post(
            "/api/v1/schedules",
            headers=headers,
            json={
                "playlist_id": "playlist-past",
                "name": "Past Schedule",
                "target_type": "all",
                "next_run_at": (now - timedelta(minutes=5)).isoformat(),
                "enabled": True,
            },
        )
        assert past_schedule.status_code == 201

        future_schedule = client.post(
            "/api/v1/schedules",
            headers=headers,
            json={
                "playlist_id": "playlist-future",
                "name": "Future Schedule",
                "target_type": "all",
                "next_run_at": (now + timedelta(hours=1)).isoformat(),
                "enabled": True,
            },
        )
        assert future_schedule.status_code == 201

        disabled_schedule = client.post(
            "/api/v1/schedules",
            headers=headers,
            json={
                "playlist_id": "playlist-disabled",
                "name": "Disabled Schedule",
                "target_type": "all",
                "next_run_at": (now - timedelta(minutes=10)).isoformat(),
                "enabled": False,
            },
        )
        assert disabled_schedule.status_code == 201

        all_schedules = client.get("/api/v1/schedules", headers=headers)
        assert all_schedules.status_code == 200
        assert len(all_schedules.json()) == 3

        due = client.get("/api/v1/schedules/due", headers=headers)
        assert due.status_code == 200
        due_ids = {item["playlist_id"] for item in due.json()}
        assert "playlist-past" in due_ids
        assert "playlist-future" not in due_ids
        assert "playlist-disabled" not in due_ids
