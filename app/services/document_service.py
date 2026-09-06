from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.models import Document, DocumentChunk, Task
from app.repositories.documents import get_document_by_checksum


@dataclass(frozen=True)
class DocumentSubmission:
    document: Document
    task: Task
    reused: bool


def split_document(content: str, chunk_size: int = 1000, overlap: int = 100) -> list[str]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_size must be positive and overlap smaller than chunk_size")
    if not content:
        return []
    step = chunk_size - overlap
    return [content[start : start + chunk_size] for start in range(0, len(content), step)]


def document_checksum(user_id: UUID, content: str) -> str:
    return hashlib.sha256(f"{user_id}\0{content}".encode("utf-8")).hexdigest()


class DocumentService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_id: UUID,
        title: str,
        content: str,
        metadata: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> DocumentSubmission:
        checksum = document_checksum(user_id, content)
        try:
            existing = await get_document_by_checksum(self.session, user_id, checksum)
            if existing is not None and existing.ingest_task_id is not None:
                task = await self.session.get(Task, existing.ingest_task_id)
                if task is not None:
                    await self.session.commit()
                    return DocumentSubmission(existing, task, True)

            task = Task(
                user_id=user_id,
                task_type="DOCUMENT_INGEST",
                status="PENDING",
                progress="QUEUED",
            )
            self.session.add(task)
            await self.session.flush()
            document = Document(
                user_id=user_id,
                title=title.strip(),
                content=content,
                metadata_json=metadata,
                checksum=checksum,
                status="PENDING",
                ingest_task_id=task.id,
            )
            self.session.add(document)
            await self.session.commit()
            return DocumentSubmission(document, task, False)
        except Exception:
            await self.session.rollback()
            raise


async def execute_document_task(task_id: UUID) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
                document = await session.scalar(
                    select(Document).where(Document.ingest_task_id == task_id).with_for_update()
                )
                if task is None or document is None:
                    return
                if task.status == "SUCCEEDED" and document.status == "READY":
                    return
                task.status = "RUNNING"
                task.progress = "CHUNKING"
                task.started_at = task.started_at or datetime.now(timezone.utc)

                chunks = split_document(document.content)
                await session.execute(
                    delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
                )
                session.add_all(
                    DocumentChunk(
                        document_id=document.id,
                        chunk_index=index,
                        content=chunk,
                        metadata_json=document.metadata_json,
                    )
                    for index, chunk in enumerate(chunks)
                )
                document.status = "READY"
                task.status = "SUCCEEDED"
                task.progress = "DONE"
                task.finished_at = datetime.now(timezone.utc)
    finally:
        await engine.dispose()


async def mark_document_failed(task_id: UUID, error_message: str) -> None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                task = await session.get(Task, task_id, with_for_update=True)
                document = await session.scalar(
                    select(Document).where(Document.ingest_task_id == task_id).with_for_update()
                )
                if task is not None:
                    task.status = "FAILED"
                    task.progress = "FAILED"
                    task.error_code = "DOCUMENT_INGEST_FAILED"
                    task.error_message = error_message[:500]
                    task.finished_at = datetime.now(timezone.utc)
                if document is not None:
                    document.status = "FAILED"
    finally:
        await engine.dispose()
