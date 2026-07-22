from __future__ import annotations

from auth_api import router as auth_router
from main import app
from platform_api import router as platform_router

app.include_router(auth_router)
app.include_router(platform_router)


@app.get("/api/v1")
async def enterprise_api_root() -> dict:
    return {
        "name": "MD Teknoloji TV Manager Enterprise API",
        "version": "0.3.0",
        "docs": "/docs",
        "status":