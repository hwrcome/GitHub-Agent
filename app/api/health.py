from __future__ import annotations

from fastapi import APIRouter
from redis.asyncio import from_url
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.errors import ApiError


router = APIRouter(prefix="/health", tags=["health"])


async def check_postgres() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis() -> None:
    client = from_url(get_settings().redis_url, decode_responses=True)
    try:
        await client.ping()
    finally:
        await client.aclose()


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict[str, str]:
    checks = {"postgres": check_postgres, "redis": check_redis}
    failures: list[str] = []
    for name, check in checks.items():
        try:
            await check()
        except Exception:
            failures.append(name)
    if failures:
        raise ApiError(503, "NOT_READY", "Dependencies unavailable")
    return {"status": "ok"}
