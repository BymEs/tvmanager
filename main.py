from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from database import create_database

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)


class PlayerConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, WebSocket] = {}
        self.metadata: dict[str, dict[str, Any]] = {}

    async def connect(self, device_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        old = self.connections.get(device_id)
        if old is not None:
            try:
                await old.close(code=4001)
            except Exception:
                pass
        self.connections[device_id] = websocket
        self.metadata[device_id] = {
            "device_id": device_id,
            "online": True,
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        if self.connections.get(device_id) is websocket:
            self.connections.pop(device_id, None)
            info = self.metadata.setdefault(device_id, {"device_id": device_id})
            info["online"] = False
            info["last_seen"] = datetime.now(timezone.utc).isoformat()

    async def send(self, device_id: str, message: dict[str, Any]) -> None:
        websocket = self.connections.get(device_id)
        if websocket is None:
            raise HTTPException(status_code=404, detail="Device is offline")
        await websocket.send_json(message)

    async def broadcast(self, message: dict[str, Any]) -> int:
        sent = 0
        for device_id, websocket in list(self.connections.items()):
            try:
                await websocket.send_json(message)
                sent += 1
            except Exception:
                self.disconnect(device_id, websocket)
        return sent


manager = PlayerConnectionManager()
playlists: dict[str, dict[str, Any]] = {
    "demo": {
        "id": "demo",
        "name": "Demo Playlist",
        "items": [
            {
                "type": "image",
                "url": "https://picsum.photos/1920/1080",
                "duration": 10,
            }
        ],
    }
}


class PlaylistItem(BaseModel):
    type: str = Field(pattern="^(image|video|web|stream)$")
    url: str
    duration: int = Field(default=10, ge=1, le=86400)


class PlaylistPayload(BaseModel):
    id: str
    name: str
    items: list[PlaylistItem]


class CommandPayload(BaseModel):
    device_id: str | None = None
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await create_database()
    yield


app = FastAPI(title="MD Teknoloji TV Manager", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "MD Teknoloji TV Manager",
        "version": "0.1.0",
        "admin": "/admin",
        "player": "/player?device_id=demo-tv",
        "health": "/health",
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "time": datetime.now(timezone.utc).isoformat(),
        "online_players": len(manager.connections),
    }


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/player")
async def player_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "player.html")


@app.get("/api/devices")
async def list_devices() -> list[dict[str, Any]]:
    return sorted(manager.metadata.values(), key=lambda item: item["device_id"])


@app.get("/api/playlists")
async def list_playlists() -> list[dict[str, Any]]:
    return list(playlists.values())


@app.get("/api/playlists/{playlist_id}")
async def get_playlist(playlist_id: str) -> dict[str, Any]:
    playlist = playlists.get(playlist_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return playlist


@app.put("/api/playlists/{playlist_id}")
async def save_playlist(playlist_id: str, payload: PlaylistPayload) -> dict[str, Any]:
    if playlist_id != payload.id:
        raise HTTPException(status_code=400, detail="Playlist id mismatch")
    playlists[playlist_id] = payload.model_dump()
    return playlists[playlist_id]


@app.post("/api/command")
async def send_command(payload: CommandPayload) -> dict[str, Any]:
    message = {
        "type": "command",
        "command": payload.command,
        "payload": payload.payload,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    if payload.device_id:
        await manager.send(payload.device_id, message)
        return {"sent": 1, "device_id": payload.device_id}
    return {"sent": await manager.broadcast(message)}


@app.post("/api/devices/{device_id}/playlist/{playlist_id}")
async def assign_playlist(device_id: str, playlist_id: str) -> dict[str, Any]:
    playlist = playlists.get(playlist_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="Playlist not found")
    await manager.send(device_id, {"type": "playlist", "playlist": playlist})
    return {"ok": True, "device_id": device_id, "playlist_id": playlist_id}


@app.websocket("/ws/player/{device_id}")
async def player_socket(websocket: WebSocket, device_id: str) -> None:
    await manager.connect(device_id, websocket)
    await websocket.send_json({"type": "welcome", "device_id": device_id})
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                message = {"type": "text", "value": raw}
            info = manager.metadata.setdefault(device_id, {"device_id": device_id})
            info["online"] = True
            info["last_seen"] = datetime.now(timezone.utc).isoformat()
            if message.get("type") == "status":
                info.update({key: value for key, value in message.items() if key != "type"})
            await websocket.send_json({"type": "ack", "received": message.get("type", "unknown")})
    except WebSocketDisconnect:
        manager.disconnect(device_id, websocket)
    except Exception:
        manager.disconnect(device_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
