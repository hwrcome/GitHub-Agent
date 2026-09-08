from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import get_db
from app.main import create_app
from app.models import Base


pytestmark = pytest.mark.integration


TEST_DATABASE_URL = "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.mark.asyncio
async def test_register_login_and_duplicate_email(client: httpx.AsyncClient):
    response = await client.post("/auth/register", json={"email": "A@Example.com", "password": "password-123"})
    assert response.status_code == 201
    assert response.json()["email"] == "a@example.com"
    assert "password_hash" not in response.json()

    duplicate = await client.post(
        "/auth/register", json={"email": "a@example.com", "password": "password-123"}
    )
    assert duplicate.status_code == 409

    login = await client.post("/auth/login", json={"email": "a@example.com", "password": "password-123"})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"
    assert login.json()["user"]["email"] == "a@example.com"


@pytest.mark.asyncio
async def test_login_rejects_invalid_credentials(client: httpx.AsyncClient):
    await client.post("/auth/register", json={"email": "a@example.com", "password": "password-123"})
    response = await client.post("/auth/login", json={"email": "a@example.com", "password": "wrong-pass"})
    assert response.status_code == 401
