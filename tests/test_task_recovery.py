from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task, User


pytestmark = pytest.mark.integration


TEST_DATABASE_URL = "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test"


@pytest_asyncio.fixture
async def recovery_db():
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        user = User(email=f"recovery-{uuid4()}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, task_type="SEARCH", status="PENDING", progress="QUEUED")
        session.add(task)
        await session.flush()
        task.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        await session.commit()
        yield task.id
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_enqueues_old_pending_task_once(recovery_db, monkeypatch):
    import os

    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from app.config import get_settings

    get_settings.cache_clear()
    from app.services import recovery_service

    enqueued: list[str] = []

    class FakeAsyncResult:
        id = "celery-recovery-id"

    monkeypatch.setattr(
        recovery_service.run_search_task,
        "delay",
        lambda task_id: enqueued.append(task_id) or FakeAsyncResult(),
    )
    assert await recovery_service.recover_pending_tasks() == 1
    assert await recovery_service.recover_pending_tasks() == 0
    assert enqueued == [str(recovery_db)]
