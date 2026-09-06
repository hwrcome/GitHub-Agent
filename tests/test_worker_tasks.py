import asyncio
import os
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, SearchRequest, Task, User


TEST_DATABASE_URL = "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test"


async def prepare_task() -> UUID:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        user = User(email="worker@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, task_type="SEARCH", status="PENDING", progress="QUEUED")
        session.add(task)
        await session.flush()
        session.add(SearchRequest(task_id=task.id, query="python inference", config={}))
        await session.commit()
        task_id = task.id
    await engine.dispose()
    return task_id


def load_task(task_id: UUID) -> Task:
    async def _load():
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            task = await session.get(Task, task_id)
            await engine.dispose()
            return task

    return asyncio.run(_load())


@pytest.fixture
def task_id():
    value = asyncio.run(prepare_task())
    yield value
    async def _cleanup():
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()
    asyncio.run(_cleanup())


@pytest.fixture
def eager_celery():
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from app.config import get_settings

    get_settings.cache_clear()
    from app.celery_app import celery_app

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
    return celery_app


def test_worker_mock_mode_persists_success(eager_celery, task_id):
    from app.tasks import run_search_task

    run_search_task.delay(str(task_id)).get()
    task = load_task(task_id)
    assert task.status == "SUCCEEDED"
