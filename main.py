from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import update

from database import SessionLocal, create_database
from models import Device

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
MEDIA_DIR = DATA_DIR / "media"
MEDIA_FILES_DIR = MEDIA_DIR / "files"
MEDIA_INDEX_FILE = MEDIA_DIR / "index.json"
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024

STATIC_DIR.mkdir(exist_ok=True)
MEDIA_FILES_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_MEDIA_TYPES: dict[str, set[str]] = {
    "image": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"},
    "video": {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"},
    "document": {".pdf"},
    "audio": {".mp3", ".wav", ".ogg", ".m4a", ".aac"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso() -> str:
    return utc_now().isoformat()


async def persist_player_presence(device_uid: str, status: dict[str, Any] | None = None) -> None:
    values: dict[str, Any] = {
        "is_online": True,
        "last_seen_at": utc_now(),
    }
    if status:
        runtime_config = {key: value for key, value in status.items() if key != "type"}
        if runtime_config:
            values["config"] = runtime_config

    async with SessionLocal() as db:
        await db.execute(
            update(Device)
            .where(Device.device_uid == device_uid)
            .values(**values)
        )
        await db.commit()


def sanitize_filename(filename: str) -> str:
    original = Path(filename or "upload").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip(".-") or "media"
    return f"{stem[:120]}{Path(original).suffix.lower()}"


def classify_media(extension: str) -> str | None:
    for media_type, extensions in ALLOWED_MEDIA_TYPES.items():
        if extension in extensions:
            return media_type
    return None


def load_media_index() -> dict[str, dict[str, Any]]:
    if not MEDIA_INDEX_FILE.exists():
        return {}
    try:
        raw = json.loads(MEDIA_INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


media_assets: dict[str, dict[str, Any]] = load_media_index()
media_index_lock = asyncio.Lock()


async def save_media_index() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_file = MEDIA_INDEX_FILE.with_suffix(".tmp")
    payload = json.dumps(media_assets, ensure_ascii=False, indent=2)
    temporary_file.write_text(payload, encoding="utf-8")
    temporary_file.replace(MEDIA_INDEX_FILE)


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
            "connected_at": utc_iso(),
            "last_seen": utc_iso(),
        }
        await persist_player_presence(device_id)

    def disconnect(self, device_id: str, websocket: WebSocket) -> None:
        if self.connections.get(device_id) is websocket:
            self.connections.pop(device_id, None)
            info = self.metadata.setdefault(device_id, {"device_id": device_id})
            info["online"] = False
            info["last_seen"] = utc_iso()

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
    type: str = Field(pattern="^(image|video|web|stream|document|audio)$")
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
    MEDIA_FILES_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="MD Teknoloji TV Manager", version="0.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/media/files", StaticFiles(directory=MEDIA_FILES_DIR), name="media-files")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": "MD Teknoloji TV Manager",
        "version": "0.3.0",
        "admin": "/admin",
        "player": "/player?device_id=demo-tv",
        "health": "/health",
        "media": "/api/media",
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "time": utc_iso(),
        "online_players": len(manager.connections),
        "media_assets": len(media_assets),
    }


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/player")
async def player_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "player.html")


@app.get("/api/media")
async def list_media() -> list[dict[str, Any]]:
    return sorted(media_assets.values(), key=lambda item: item.get("created_at", ""), reverse=True)


@app.get("/api/media/{media_id}")
async def get_media(media_id: str) -> dict[str, Any]:
    asset = media_assets.get(media_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return asset


@app.post("/api/media", status_code=201)
async def upload_media(file: UploadFile = File(...)) -> dict[str, Any]:
    safe_name = sanitize_filename(file.filename or "upload")
    extension = Path(safe_name).suffix.lower()
    media_type = classify_media(extension)
    if media_type is None:
        supported = sorted(extension for values in ALLOWED_MEDIA_TYPES.values() for extension in values)
        raise HTTPException(status_code=415, detail={"message": "Unsupported media type", "supported": supported})

    media_id = str(uuid4())
    stored_name = f"{media_id}{extension}"
    destination = MEDIA_FILES_DIR / stored_name
    size_bytes = 0

    try:
        with destination.open("wb") as output:
            while chunk := await file.read(UPLOAD_CHUNK_BYTES):
                size_bytes += len(chunk)
                if size_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 1 GB upload limit")
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    asset = {
        "id": media_id,
        "name": safe_name,
        "stored_name": stored_name,
        "media_type": media_type,
        "content_type": file.content_type or "application/octet-stream",
        "size_bytes": size_bytes,
        "url": f"/media/files/{stored_name}",
        "created_at": utc_iso(),
    }

    async with media_index_lock:
        media_assets[media_id] = asset
        try:
            await save_media_index()
        except Exception:
            media_assets.pop(media_id, None)
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Media index could not be saved")

    return asset


@app.delete("/api/media/{media_id}")
async def delete_media(media_id: str) -> dict[str, Any]:
    async with media_index_lock:
        asset = media_assets.get(media_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="Media asset not found")

        stored_name = Path(str(asset.get("stored_name", ""))).name
        media_assets.pop(media_id, None)
        try:
            await save_media_index()
        except Exception:
            media_assets[media_id] = asset
            raise HTTPException(status_code=500, detail="Media index could not be saved")

    if stored_name:
        (MEDIA_FILES_DIR / stored_name).unlink(missing_ok=True)
    return {"ok": True, "deleted_id": media_id}


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
        "sent_at": utc_iso(),
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
            info["last_seen"] = utc_iso()
            if message.get("type") == "status":
                info.update({key: value for key, value in message.items() if key != "type"})
            await persist_player_presence(device_id, message if message.get("type") == "status" else None)
            await websocket.send_json({"type": "ack", "received": message.get("type", "unknown")})
    except WebSocketDisconnect:
        manager.disconnect(device_id, websocket)
    except Exception:
        manager.disconnect(device_id, websocket)
        try:
            await websocket.close()
        except Exception:
            pass
