from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Organization, Site, User, UserRole
from security import create_access_token, decode_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/v1", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


class BootstrapPayload(BaseModel):
    organization_name: str = Field(min_length=2, max_length=160)
    organization_slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$")
    email: str
    full_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=8, max_length=128)


class LoginPayload(BaseModel):
    email: str
    password: str


class SitePayload(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    address: str | None = None
    timezone_name: str = "Europe/Istanbul"


async def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer), db: AsyncSession = Depends(get_db)) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    user = await db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User unavailable")
    return user


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(User.id).limit(1))
    return {"ready": True, "bootstrapped": result.scalar_one_or_none() is not None, "modules": ["auth", "multi_tenant", "sites"]}


@router.post("/auth/bootstrap", status_code=201)
async def bootstrap(payload: BootstrapPayload, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(User.id).limit(1))
    if result.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="System already bootstrapped")
    org = Organization(name=payload.organization_name, slug=payload.organization_slug)
    db.add(org)
    await db.flush()
    user = User(
        organization_id=org.id,
        email=payload.email.lower(),
        full_name=payload.full_name,
        password_hash=hash_password(payload.password),
        role=UserRole.superadmin,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return {
        "access_token": create_access_token(user.id, {"role": user.role.value, "organization_id": user.organization_id}),
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role.value, "organization_id": user.organization_id},
    }


@router.post("/auth/login")
async def login(payload: LoginPayload, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    return {
        "access_token": create_access_token(user.id, {"role": user.role.value, "organization_id": user.organization_id}),
        "token_type": "bearer",
        "user": {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role.value, "organization_id": user.organization_id},
    }


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role.value, "organization_id": user.organization_id}


@router.get("/sites")
async def list_sites(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    result = await db.execute(select(Site).where(Site.organization_id == user.organization_id).order_by(Site.name))
    return [{"id": item.id, "name": item.name, "address": item.address, "timezone_name": item.timezone_name} for item in result.scalars()]


@router.post("/sites", status_code=201)
async def create_site(payload: SitePayload, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> dict:
    if user.role not in {UserRole.superadmin, UserRole.admin, UserRole.operator}:
        raise HTTPException(status_code=403, detail="Insufficient role")
    site = Site(organization_id=user.organization_id, name=payload.name, address=payload.address, timezone_name=payload.timezone_name)
    db.add(site)
    await db.commit()
    await db.refresh(site)
    return {"id": site.id, "name": site.name, "address": site.address, "timezone_name": site.timezone_name}
