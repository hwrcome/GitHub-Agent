import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Task, User


TEST_DATABASE_URL = "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test"


@pytest_asyncio.fixture
async def task_db_session():
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def create_pending_task(session: AsyncSession) -> Task:
    user = User(email="task-service@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    task = Task(user_id=user.id, task_type="SEARCH", status="PENDING", progress="QUEUED")
    session.add(task)
    await session.commit()
    return task


@pytest.mark.asyncio
async def test_task_transitions_are_conditional(task_db_session: AsyncSession):
    from app.services.task_service import InvalidTaskTransition, transition_task

    task = await create_pending_task(task_db_session)
    updated = await transition_task(
        task_db_session, task.id, {"PENDING"}, "RUNNING", "QUERY_ANALYZED"
    )
    assert updated.status == "RUNNING"
    assert updated.progress == "QUERY_ANALYZED"
    with pytest.raises(InvalidTaskTransition):
        await transition_task(task_db_session, task.id, {"PENDING"}, "RUNNING")


def test_error_sanitization_does_not_leak_credentials():
    from app.services.task_service import sanitize_error

    message = sanitize_error(RuntimeError("Authorization: Bearer secret-token"))
    assert "secret-token" not in message
    assert len(message) <= 500
