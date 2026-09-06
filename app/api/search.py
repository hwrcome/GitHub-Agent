from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import require_user
from app.db import get_db
from app.errors import ApiError
from app.models import User
from app.schemas.search import SearchRequestCreate
from app.schemas.tasks import TaskCreated
from app.services.search_service import SearchService
from app.services.idempotency_service import IdempotencyConflict
from app.tasks import enqueue_search_after_commit


router = APIRouter(tags=["search"])


@router.post("/search", status_code=status.HTTP_202_ACCEPTED, response_model=TaskCreated)
async def submit_search(
    payload: SearchRequestCreate,
    response: Response,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TaskCreated:
    service = SearchService(session)
    try:
        task = await service.submit(user.id, payload, idempotency_key)
    except IdempotencyConflict as exc:
        raise ApiError(409, "IDEMPOTENCY_KEY_REUSED", str(exc)) from exc
    response.headers["Location"] = f"/tasks/{task.id}"
    if not service.reused:
        enqueue_search_after_commit(task.id)
    return TaskCreated(task_id=task.id, status=task.status, created_at=task.created_at)
