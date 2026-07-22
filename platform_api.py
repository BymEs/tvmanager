from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_api import current_user
from database import get_db
from models import Device, DeviceType, Schedule, User, UserRole
from security import hash_password

router = APIRouter(prefix="/api/v1", tags=["platform"])


class UserCreate(BaseModel):
    email: str
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.viewer


class DeviceCreate(BaseModel):
    site_id: str
    device_uid: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=2, max_length=160)
    group_name: str = "Genel"
    device_type: DeviceType = DeviceType.browser
    capabilities: dict | None = None
    config: dict | None = None


class ScheduleCreate(BaseModel):
    playlist_id: str
    name: str = Field(min_length=2, max_length=160)
    target_type: str = Field(default="all", pattern="^(all|site|group|device)$")
    target_value: str | None = None
    next_run_at: datetime
    enabled: bool = True


def require_editor(user: User) -> None:
    if user.role not in {UserRole.superadmin, UserRole.admin, UserRole.operator}:
        raise HTTPException(status_code=403, detail="Insufficient role")


def require_admin(user: User) -> None:
    if user.role not in {UserRole.superadmin, UserRole.admin}:
        raise HTTPException(status_code=403, detail="Admin role required")


@router.get("/users")
async def list_users(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    require_admin(user)
    result = await db.execute(select(User).where(User.organization_id == user.organization_id).order_by(User.full_name))
    return [
        {"id": item.id, "email": item.email, "full_name": item.full_name, "role": item.role.value, "is_active": item.is_active, "last_login_at": item.last_login_at}
        for item in result.scalars()
    ]


@router.post("/users", status_code=201)
async def create_user(payload: UserCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    require_admin(user)
    email = payload.email.lower().strip()
    existing = await db.execute(select(User.id).where(User.organization_id == user.organization_id, User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Email already exists")
    item = User(
        organization_id=user.organization_id,
        email=email,
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id, "email": item.email, "full_name": item.full_name, "role": item.role.value, "is_active": item.is_active}


@router.get("/device-records")
async def list_device_records(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(
        select(Device).where(Device.site.has(organization_id=user.organization_id)).order_by(Device.name)
    )
    return [
        {
            "id": item.id, "site_id": item.site_id, "device_uid": item.device_uid, "name": item.name,
            "group_name": item.group_name, "device_type": item.device_type.value, "capabilities": item.capabilities,
            "config": item.config, "is_online": item.is_online, "last_seen_at": item.last_seen_at,
        }
        for item in result.scalars()
    ]


@router.post("/device-records", status_code=201)
async def create_device_record(payload: DeviceCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    require_editor(user)
    from models import Site
    site = await db.get(Site, payload.site_id)
    if site is None or site.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Site not found")
    existing = await db.execute(select(Device.id).where(Device.device_uid == payload.device_uid))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Device UID already exists")
    item = Device(**payload.model_dump())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id, "device_uid": item.device_uid, "name": item.name, "site_id": item.site_id, "group_name": item.group_name, "device_type": item.device_type.value}


@router.get("/schedules")
async def list_schedules(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(Schedule).where(Schedule.organization_id == user.organization_id).order_by(Schedule.next_run_at))
    return [
        {"id": item.id, "playlist_id": item.playlist_id, "name": item.name, "target_type": item.target_type,
         "target_value": item.target_value, "next_run_at": item.next_run_at, "enabled": item.enabled}
        for item in result.scalars()
    ]


@router.post("/schedules", status_code=201)
async def create_schedule(payload: ScheduleCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    require_editor(user)
    next_run = payload.next_run_at
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=timezone.utc)
    item = Schedule(organization_id=user.organization_id, **payload.model_dump(exclude={"next_run_at"}), next_run_at=next_run)
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": item.id, "playlist_id": item.playlist_id, "name": item.name, "target_type": item.target_type, "target_value": item.target_value, "next_run_at": item.next_run_at, "enabled": item.enabled}


@router.get("/schedules/due")
async def due_schedules(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Schedule).where(Schedule.organization_id == user.organization_id, Schedule.enabled.is_(True), Schedule.next_run_at <= now).order_by(Schedule.next_run_at)
    )
    return [{"id": item.id, "playlist_id": item.playlist_id, "name": item.name, "target_type": item.target_type, "target_value": item.target_value, "next_run_at": item.next_run_at} for item in result.scalars()]
