import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Document, DocumentChunk, Task, User


TEST_DATABASE_URL = "postgresql+asyncpg://github_agent:github_agent@localhost:55432/github_agent_test"


def test_split_document_has_stable_overlap():
    from app.services.document_service import split_document

    chunks = split_document("a" * 2500, chunk_size=1000, overlap=100)
    assert [len(chunk) for chunk in chunks] == [1000, 1000, 700]
    assert chunks[0][-100:] == chunks[1][:100]


async def prepare_document_task() -> UUID:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    from app.config import get_settings

    get_settings.cache_clear()
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        user = User(email=f"doc-worker-{uuid4()}@example.com", password_hash="x")
        session.add(user)
        await session.flush()
        task = Task(user_id=user.id, task_type="DOCUMENT_INGEST", status="PENDING", progress="QUEUED")
        session.add(task)
        await session.flush()
        document = Document(
            user_id=user.id,
            title="worker doc",
            content="0123456789" * 300,
            metadata_json={},
            checksum="a" * 64,
            status="PENDING",
            ingest_task_id=task.id,
        )
        session.add(document)
        await session.commit()
        task_id = task.id
    await engine.dispose()
    return task_id


def test_document_worker_persists_chunks():
    task_id = asyncio.run(prepare_document_task())
    from app.celery_app import celery_app
    from app.tasks import process_document_task

    celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
    process_document_task.delay(str(task_id)).get()

    async def load():
        engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            task = await session.get(Task, task_id)
            document = await session.scalar(__import__("sqlalchemy").select(Document).where(Document.ingest_task_id == task_id))
            chunks = (await session.scalars(__import__("sqlalchemy").select(DocumentChunk).where(DocumentChunk.document_id == document.id))).all()
            await engine.dispose()
            return task, document, chunks

    task, document, chunks = asyncio.run(load())
    assert task.status == "SUCCEEDED"
    assert document.status == "READY"
    assert [len(chunk.content) for chunk in chunks] == [1000, 1000, 1000, 300]
