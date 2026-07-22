from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

Path("data").mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv(
    "TVMANAGER_DATABASE_URL",
    "sqlite+aiosqlite:///./data/tvmanager.db",
)

engine = create_async_engine(
    DATABASE_URL,
    echo=os.getenv("TVMANAGER_SQL_ECHO", "0") == "1",
    pool_pre_ping=True,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def create_database() -> None:
    import models  # noqa: F401

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
