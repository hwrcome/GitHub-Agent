from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_db
from app.main import create_app
from app.models import Base


TEST_DATABASE_URL = "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test"


@dataclass
class ApiContext:
    client: httpx.AsyncClient
    session_factory: async_sessionmaker[AsyncSession]

    async def create_user(self, email: str) -> tuple[dict, str]:
        register = await self.client.post(
            "/auth/register", json={"email": email, "password": "password-123"}
        )
        assert register.status_code == 201
        login = await self.client.post(
            "/auth/login", json={"email": email, "password": "password-123"}
        )
        assert login.status_code == 200
        return register.json(), login.json()["access_token"]


@pytest_asyncio.fixture
async def api_context(monkeypatch) -> AsyncIterator[ApiContext]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    monkeypatch.setattr("app.api.search.enqueue_search_after_commit", lambda task_id: None)
    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield ApiContext(client=client, session_factory=session_factory)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()
