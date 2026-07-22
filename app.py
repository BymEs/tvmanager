from __future__ import annotations

from auth_api import router as auth_router
from dashboard_api import router as dashboard_router
from main import app
from platform_api import router as platform_router

# Keep the existing media/player application and mount the enterprise API on it.
# This makes app:app the single production entrypoint without duplicating FastAPI state.
app.include_router(auth_router)
app.include_router(platform_router)
app.include_router(dashboard_router)


@app.get("/api/v1", tags=["platform"])
async def enterprise_api_root() -> dict[str, object]:
    return {
        "name": "MD Teknoloji TV Manager Enterprise API",
        "version": "0.4.0",
        "docs": "/docs",
        "status": "/api/v1/status",
        "modules": [
            "authentication",
            "organizations",
            "sites",
            "users",
            "device_records",
            "device_heartbeat",
            "monitoring_settings",
            "dashboard",
            "schedules",
            "media",
            "playlists",
            "player_websocket",
        ],
    }
