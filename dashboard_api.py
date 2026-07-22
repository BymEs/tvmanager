from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_api import current_user
from database import get_db
from models import Device, MediaAsset, Playlist, Schedule, Site, User

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


class HeartbeatPayload(BaseModel):
    device_uid: str = Field(min_length=2, max_length=160)
    capabilities: dict | None = None
    config: dict | None = None


@router.get("/dashboard/summary")
async def dashboard_summary(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    organization_id = user.organization_id
    if organization_id is None:
        raise HTTPException(status_code=403, detail="User is not assigned to an organization")

    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=2)

    site_count = await db.scalar(
        select(func.count(Site.id)).where(Site.organization_id == organization_id)
    )
    user_count = await db.scalar(
        select(func.count(User.id)).where(User.organization_id == organization_id)
    )
    device_count = await db.scalar(
        select(func.count(Device.id)).where(Device.site.has(organization_id=organization_id))
    )
    online_count = await db.scalar(
        select(func.count(Device.id)).where(
            Device.site.has(organization_id=organization_id),
            Device.is_online.is_(True),
            Device.last_seen_at.is_not(None),
            Device.last_seen_at >= stale_before,
        )
    )
    media_count = await db.scalar(
        select(func.count(MediaAsset.id)).where(MediaAsset.organization_id == organization_id)
    )
    playlist_count = await db.scalar(
        select(func.count(Playlist.id)).where(Playlist.organization_id == organization_id)
    )
    active_schedule_count = await db.scalar(
        select(func.count(Schedule.id)).where(
            Schedule.organization_id == organization_id,
            Schedule.enabled.is_(True),
        )
    )
    due_schedule_count = await db.scalar(
        select(func.count(Schedule.id)).where(
            Schedule.organization_id == organization_id,
            Schedule.enabled.is_(True),
            Schedule.next_run_at <= now,
        )
    )

    total_devices = int(device_count or 0)
    online_devices = int(online_count or 0)

    return {
        "generated_at": now,
        "organization_id": organization_id,
        "counts": {
            "sites": int(site_count or 0),
            "users": int(user_count or 0),
            "devices": total_devices,
            "online_devices": online_devices,
            "offline_devices": max(total_devices - online_devices, 0),
            "media_assets": int(media_count or 0),
            "playlists": int(playlist_count or 0),
            "active_schedules": int(active_schedule_count or 0),
            "due_schedules": int(due_schedule_count or 0),
        },
        "health": {
            "device_online_ratio": round(online_devices / total_devices, 4) if total_devices else 0.0,
            "heartbeat_timeout_seconds": 120,
        },
    }


@router.post("/devices/heartbeat")
async def device_heartbeat(
    payload: HeartbeatPayload,
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    values: dict[str, object] = {
        "is_online": True,
        "last_seen_at": now,
    }
    if payload.capabilities is not None:
        values["capabilities"] = payload.capabilities
    if payload.config is not None:
        values["config"] = payload.config

    result = await db.execute(
        update(Device)
        .where(Device.device_uid == payload.device_uid)
        .values(**values)
        .returning(Device.id, Device.site_id, Device.name)
    )
    row = result.first()
    if row is None:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Device not registered")

    await db.commit()
    return {
        "accepted": True,
        "device": {
            "id": row.id,
            "site_id": row.site_id,
            "name": row.name,
            "device_uid": payload.device_uid,
            "last_seen_at": now,
            "is_online": True,
        },
    }


@router.post("/devices/reconcile-offline")
async def reconcile_offline_devices(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if user.organization_id is None:
        raise HTTPException(status_code=403, detail="User is not assigned to an organization")

    stale_before = datetime.now(timezone.utc) - timedelta(minutes=2)
    result = await db.execute(
        update(Device)
        .where(
            Device.site.has(organization_id=user.organization_id),
            Device.is_online.is_(True),
            (Device.last_seen_at.is_(None) | (Device.last_seen_at < stale_before)),
        )
        .values(is_online=False)
    )
    await db.commit()
    return {
        "updated": int(result.rowcount or 0),
        "offline_before": stale_before,
    }
