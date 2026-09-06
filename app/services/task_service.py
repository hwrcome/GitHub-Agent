from __future__ import annotations

import re
import time
import json
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.agent_runner import TransientAgentError, run_search
from app.config import get_settings
from app.models import SearchRequest, SearchResult, Task
from app.schemas.agent import SearchRunResult
from app.observability import agent_duration_seconds, running_tasks
from app.redis_client import get_redis
from app.services.cache_service import CacheService
from app.services.lock_service import LockService, LockUnavailable


class InvalidTaskTransition(RuntimeError):
    pass


def _search_cache_key(request: SearchRequest) -> str:
    payload = json.dumps(
        {"query": request.query, "config": request.config},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    request_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"search:v1:{request_hash}:agent"


def sanitize_error(exc: Exception) -> str:
    message = str(exc)
    message = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", message)
    message = re.sub(r"(?i)(token|api[_ -]?key|password)\s*[:=]\s*\S+", r"\1=[REDACTED]", message)
    return f"{type(exc).__name__}: {message}"[:500]


async def transition_task(
    session: AsyncSession,
    task_id: UUID,
    from_statuses: set[str],
    to_status: str,
    progress: str | None = None,
) -> Task:
    task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
    if task is None or task.status not in from_statuses:
        current = task.status if task is not None else "MISSING"
        raise InvalidTaskTransition(f"Task {task_id} is {current}; expected {sorted(from_statuses)}")
    task.status = to_status
    if progress is not None:
        task.progress = progress
    now = datetime.now(timezone.utc)
    if to_status == "RUNNING":
        task.started_at = task.started_at or now
    if to_status in {"SUCCEEDED", "FAILED"}:
        task.finished_at = now
    await session.flush()
    return task


class TaskService:
    @staticmethod
    async def create_search_task(
        session: AsyncSession,
        user_id: UUID,
        query: str,
        config: dict[str, Any],
    ) -> Task:
        task = Task(user_id=user_id, task_type="SEARCH", status="PENDING", progress="QUEUED")
        session.add(task)
        await session.flush()
        session.add(SearchRequest(task_id=task.id, query=query, config=config))
        await session.flush()
        return task


async def _new_session() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def execute_search_task(task_id: UUID) -> None:
    engine, factory = await _new_session()
    try:
        async with factory() as session:
            async with session.begin():
                task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
                if task is None or task.status in {"SUCCEEDED", "FAILED"}:
                    return
                if task.status not in {"PENDING", "RETRYING"}:
                    return
                task.status = "RUNNING"
                task.progress = "STARTING"
                task.started_at = task.started_at or datetime.now(timezone.utc)

            search_request = await session.get(SearchRequest, task_id)
            if search_request is None:
                raise RuntimeError("search request missing")
            await session.commit()
            redis = get_redis()
            cache = CacheService(redis)
            cache_key = _search_cache_key(search_request)
            lease = None
            lock_degraded = False
            try:
                cached = await cache.get_json(cache_key)
                if cached is not None:
                    result = SearchRunResult.model_validate(cached)
                else:
                    try:
                        lease = await LockService(redis).acquire(cache_key, 900)
                    except LockUnavailable:
                        lock_degraded = True
                    if lease is None and not lock_degraded:
                        raise TransientAgentError("search lock is busy")
                    progress = []
                    running_tasks.inc()
                    started = time.perf_counter()
                    try:
                        result = await __import__("asyncio").to_thread(
                            run_search,
                            task_id,
                            mode=get_settings().agent_mode,
                            progress_callback=progress.append,
                        )
                    finally:
                        agent_duration_seconds.observe(time.perf_counter() - started)
                        running_tasks.dec()
                    await cache.set_json(cache_key, result.model_dump(mode="json"), ttl=3600)
            finally:
                if lease is not None:
                    await lease.release()
                await redis.aclose()

            async with session.begin():
                task = await session.scalar(select(Task).where(Task.id == task_id).with_for_update())
                if task is None or task.status in {"SUCCEEDED", "FAILED"}:
                    return
                task.status = "SUCCEEDED"
                task.progress = "DONE"
                task.finished_at = datetime.now(timezone.utc)
                existing = await session.get(SearchResult, task_id)
                if existing is None:
                    session.add(
                        SearchResult(
                            task_id=task_id,
                            final_results=result.final_results,
                            repositories_json=result.repositories,
                            filtered_candidates_json=result.filtered_candidates,
                            search_history_json=result.search_history,
                            metadata_json=result.metadata,
                        )
                    )
    finally:
        await engine.dispose()


async def mark_retrying(task_id: UUID, retry_count: int) -> None:
    engine, factory = await _new_session()
    try:
        async with factory() as session:
            async with session.begin():
                task = await session.get(Task, task_id, with_for_update=True)
                if task is not None and task.status not in {"SUCCEEDED", "FAILED"}:
                    task.status = "RETRYING"
                    task.retry_count = retry_count
                    task.progress = "RETRYING"
    finally:
        await engine.dispose()


async def mark_failed(task_id: UUID, error_message: str) -> None:
    engine, factory = await _new_session()
    try:
        async with factory() as session:
            async with session.begin():
                task = await session.get(Task, task_id, with_for_update=True)
                if task is not None and task.status not in {"SUCCEEDED", "FAILED"}:
                    task.status = "FAILED"
                    task.progress = "FAILED"
                    task.error_code = "AGENT_FAILED"
                    task.error_message = error_message[:500]
                    task.finished_at = datetime.now(timezone.utc)
    finally:
        await engine.dispose()


__all__ = [
    "InvalidTaskTransition",
    "TaskService",
    "TransientAgentError",
    "execute_search_task",
    "mark_failed",
    "mark_retrying",
    "sanitize_error",
    "transition_task",
]
