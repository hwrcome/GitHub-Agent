from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import require_user
from app.db import get_db
from app.errors import ApiError
from app.models import User
from app.config import get_settings
from app.redis_client import get_redis
from app.services.rate_limit_service import RateLimitDecision, RateLimitService
from app.schemas.search import SearchRequestCreate
from app.schemas.tasks import TaskCreated
from app.services.search_service import SearchService
from app.services.idempotency_service import IdempotencyConflict
from app.tasks import enqueue_search_after_commit


router = APIRouter(tags=["search"])


async def check_search_rate_limit(user_id) -> RateLimitDecision:
    client = get_redis()
    try:
        return await RateLimitService(client).check(
            f"user:{user_id}", get_settings().rate_limit_per_minute, 60
        )
    finally:
        await client.aclose()


@router.post("/search", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
async def submit_search(
    payload: SearchRequestCreate,
    response: Response,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskCreated:
    decision = await check_search_rate_limit(user.id)
    if not decision.allowed:
        raise ApiError(
            429,
            "RATE_LIMITED",
            "Search rate limit exceeded",
            headers={"Retry-After": str(decision.retry_after)},
        )
    service = SearchService(session)
    try:
        task = await service.submit(user.id, payload, idempotency_key)
    except IdempotencyConflict as exc:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", str(exc)) from exc
    response.headers["Location"] = f"/tasks/{task.id}"
    if not service.reused:
        result = enqueue_search_after_commit(task.id)
        if result is not None:
            task.celery_task_id = result.id
            await session.commit()
    return TaskCreated(task_id=task.id, status=task.status, created_at=task.created_at)
