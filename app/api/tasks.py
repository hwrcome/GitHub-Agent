from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import require_user
from app.db import get_db
from app.errors import ApiError
from app.models import SearchResult, Task, User
from app.schemas.tasks import TaskError, TaskView


router = APIRouter(prefix="/tasks", tags=["tasks"])


async def get_task_view(session: AsyncSession, task_id: UUID, user: User) -> TaskView:
    query = select(Task).where(Task.id == task_id)
    if user.role != "admin":
        query = query.where(Task.user_id == user.id)
    task = await session.scalar(query)
    if task is None:
        raise ApiError(404, "TASK_NOT_FOUND", "Task does not exist or is not accessible")

    result = await session.get(SearchResult, task.id) if task.status == "SUCCEEDED" else None
    result_payload = None
    if result is not None:
        result_payload = {
            "final_results": result.final_results,
            "repositories": result.repositories_json,
            "filtered_candidates": result.filtered_candidates_json,
            "search_history": result.search_history_json,
            "metadata": result.metadata_json,
        }
    error = None
    if task.status == "FAILED" and task.error_code:
        error = TaskError(code=task.error_code, message=task.error_message or "Task failed")
    return TaskView(
        task_id=task.id,
        task_type=task.task_type,
        status=task.status,
        progress=task.progress,
        retry_count=task.retry_count,
        result=result_payload,
        error=error,
        created_at=task.created_at,
        updated_at=task.updated_at,
        finished_at=task.finished_at,
    )


@router.get("/{task_id}", response_model=TaskView)
async def get_task(
    task_id: UUID,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TaskView:
    return await get_task_view(session, task_id, user)
