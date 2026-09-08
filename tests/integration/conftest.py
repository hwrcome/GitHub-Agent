import os
import asyncio
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task, User
from app.main import create_app
from app.db import get_db
from httpx import ASGITransport, AsyncClient


TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test",
)


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    yield engine
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def user(db_session):
    account = User(email="fixture@example.com", password_hash="hashed")
    db_session.add(account)
    await db_session.commit()
    return account


@dataclass
class IntegrationContext:
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]

    async def create_user(self, email: str):
        register = await self.client.post(
            "/auth/register", json={"email": email, "password": "password-123"}
        )
        assert register.status_code == 201
        login = await self.client.post(
            "/auth/login", json={"email": email, "password": "password-123"}
        )
        assert login.status_code == 200
        return register.json(), login.json()["access_token"]

    async def run_pending(self):
        from app.tasks import run_search_task

        async with self.session_factory() as session:
            task_ids = [task.id for task in (await session.scalars(select(Task).where(Task.status == "PENDING"))).all()]
        for task_id in task_ids:
            await asyncio.to_thread(lambda: run_search_task.delay(str(task_id)).get())

    async def poll_until_terminal(self, task_id, headers, timeout=5):
        elapsed = 0.0
        while elapsed < timeout:
            response = await self.client.get(f"/tasks/{task_id}", headers=headers)
            payload = response.json()
            if payload.get("status") in {"SUCCEEDED", "FAILED"}:
                return payload
            await asyncio.sleep(0.05)
            elapsed += 0.05
        raise AssertionError("task did not reach a terminal state")


@pytest_asyncio.fixture
async def integration_context(api_context):
    import os

    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from app.config import get_settings

    get_settings.cache_clear()
    from app.celery_app import celery_app

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
    yield IntegrationContext(api_context.client, api_context.session_factory)
