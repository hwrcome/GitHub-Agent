from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.schemas.search import SearchRequestCreate
from app.services.task_service import TaskService


class SearchService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def submit(
        self,
        user_id: UUID,
        request: SearchRequestCreate,
        idempotency_key: str | None = None,
    ) -> Task:
        try:
            task = await TaskService.create_search_task(
                self.session,
                user_id,
                request.query.strip(),
                request.to_config(),
            )
            await self.session.commit()
            return task
        except Exception:
            await self.session.rollback()
            raise
