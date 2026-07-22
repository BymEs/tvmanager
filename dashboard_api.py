from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth_api import current_user
from database import get_db
from models import Device, MediaAsset, MonitoringSetting, Playlist, Schedule, Site, User, UserRole

router = APIRouter(prefix="/api/v1", tags=["dashboard"])
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 120


class HeartbeatPayload(BaseModel):
    device_uid: str = Field(min_length=2, max_length=160)
    capabilities: dict | None = None
    config: dict | None = None


class MonitoringSettingsUpdate(BaseModel):
    heartbeat_timeout_seconds: int = Field(ge=10, le=86400)


def require_admin(user: User) -> None:
    if user.role not in {UserRole.superadmin, UserRole.admin}:
        raise HTTPException(status_code=403, detail="Admin role required")


async def get_heartbeat_timeout(db: AsyncSession, organization_id: str) -> int:
    configured = await db.scalar(
        select(MonitoringSetting.heartbeat_timeout_seconds).where(
            MonitoringSetting.organization_id == organization_id
        )
    )
    return int(configured or DEFAULT_HEARTBEAT_TIMEOUT_SECONDS)


@router.get("/monitoring/settings")
async def get_monitoring_settings(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    if user.organization_id is None:
        raise HTTPException(status_code=403, detail="User is not assigned to an organization")

    timeout_seconds = await get_heartbeat_timeout(db, user.organization_id)
    return {
        "organization_id": user.organization_id,
        "heartbeat_timeout_seconds": timeout_seconds,
        "heartbeat_timeout_minutes": round(timeout_seconds / 60, 2),
        "uses_default": await db.get(MonitoringSetting, user.organization_id) is None,
    }


@router.put("/monitoring/settings")
async def update_monitoring_settings(
    payload: MonitoringSettingsUpdate,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    require_admin(user)
    if user.organization_id is None:
        raise HTTPException(status_code=403, detail="User is not assigned to an organization")

    item = await db.get(MonitoringSetting, user.organization_id)
    if item is None:
        item = MonitoringSetting(
            organization_id=user.organization_id,
            heartbeat_timeout_seconds=payload.heartbeat_timeout_seconds,
        )
        db.add(item)
    else:
        item.heartbeat_timeout_seconds = payload.heartbeat_timeout_seconds

    await db.commit()
    await db.refresh(item)
    return {
        "organization_id": item.organization_id,
        "heartbeat_timeout_seconds": item.heartbeat_timeout_seconds,
        "heartbeat_timeout_minutes": round(item.heartbeat_timeout_seconds / 60, 2),
        "updated_at": item.updated_at,
    }


@router.get("/dashboard/summary")
async def dashboard_summary(
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    organization_id = user.organization_id
    if organization_id is None:
        raise HTTPException(status_code=403, detail="User is not assigned to an organization")

    now = datetime.now(timezone.utc)
    timeout_seconds = await get_heartbeat_timeout(db, organization_id)
    stale_before = now - timedelta(seconds=timeout_seconds)

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
            "heartbeat_timeout_seconds": timeout_seconds,
            "heartbeat_timeout_minutes": round(timeout_seconds / 60, 2),
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

    timeout_seconds = await get_heartbeat_timeout(db, user.organization_id)
    stale_before = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
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
        "heartbeat_timeout_seconds": timeout_seconds,
        "offline_before": stale_before,
    }
