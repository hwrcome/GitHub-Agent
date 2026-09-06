from __future__ import annotations

import asyncio
from uuid import UUID

from app.agent_runner import TransientAgentError
from app.celery_app import celery_app
from app.services.task_service import execute_search_task, mark_failed, mark_retrying, sanitize_error
from app.services.document_service import execute_document_task, mark_document_failed


@celery_app.task(bind=True, acks_late=True, max_retries=3, name="github_agent.run_search")
def run_search_task(self, task_id: str) -> None:
    parsed_id = UUID(task_id)
    try:
        asyncio.run(execute_search_task(parsed_id))
    except TransientAgentError as exc:
        retry_count = self.request.retries + 1
        asyncio.run(mark_retrying(parsed_id, retry_count))
        raise self.retry(exc=exc, countdown=2**self.request.retries)
    except Exception as exc:
        asyncio.run(mark_failed(parsed_id, sanitize_error(exc)))
        raise


def enqueue_search_after_commit(task_id: UUID) -> None:
    run_search_task.delay(str(task_id))


@celery_app.task(bind=True, acks_late=True, max_retries=2, name="github_agent.process_document")
def process_document_task(self, task_id: str) -> None:
    parsed_id = UUID(task_id)
    try:
        asyncio.run(execute_document_task(parsed_id))
    except Exception as exc:
        asyncio.run(mark_document_failed(parsed_id, sanitize_error(exc)))
        raise


def enqueue_document_after_commit(task_id: UUID) -> None:
    process_document_task.delay(str(task_id))
