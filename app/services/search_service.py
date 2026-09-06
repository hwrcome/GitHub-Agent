from __future__ import annotations

from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Task
from app.schemas.search import SearchRequestCreate
from app.services.idempotency_service import (
    IdempotencyService,
    hash_request,
    normalize_search_request,
)
from app.services.task_service import TaskService


class SearchService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.reused = False

    async def submit(
        self,
        user_id: UUID,
        request: SearchRequestCreate,
        idempotency_key: str | None = None,
    ) -> Task:
        service = IdempotencyService(self.session)
        request_hash = hash_request(normalize_search_request(request))
        try:
            if idempotency_key:
                existing = await service.find_or_reserve(
                    user_id, "POST /search", idempotency_key, request_hash
                )
                if existing is not None:
                    task = await self.session.get(Task, existing.task_id)
                    if task is not None:
                        self.reused = True
                        await self.session.commit()
                        return task
            task = await TaskService.create_search_task(
                self.session,
                user_id,
                request.query.strip(),
                request.to_config(),
            )
            if idempotency_key:
                try:
                    await service.find_or_reserve(
                        user_id, "POST /search", idempotency_key, request_hash, task_id=task.id
                    )
                except IntegrityError:
                    # Another request may have inserted the same idempotency
                    # key between our initial lookup and task creation. The
                    # failed transaction also removes our task; re-read and
                    # return the winner's task instead of leaking a 500.
                    await self.session.rollback()
                    existing = await service.find_or_reserve(
                        user_id, "POST /search", idempotency_key, request_hash
                    )
                    if existing is None:
                        raise
                    existing_task = await self.session.get(Task, existing.task_id)
                    if existing_task is None:
                        raise
                    self.reused = True
                    await self.session.commit()
                    return existing_task
            await self.session.commit()
            return task
        except Exception:
            await self.session.rollback()
            raise
