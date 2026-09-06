from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Task
from app.tasks import run_search_task


async def recover_pending_tasks(older_than_seconds: int = 60) -> int:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    recovered = 0
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    try:
        async with factory() as session:
            tasks = (
                await session.scalars(
                    select(Task)
                    .where(
                        Task.status == "PENDING",
                        Task.celery_task_id.is_(None),
                        Task.created_at <= cutoff,
                    )
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for task in tasks:
                result = run_search_task.delay(str(task.id))
                task.celery_task_id = result.id
                recovered += 1
            await session.commit()
    finally:
        await engine.dispose()
    return recovered
