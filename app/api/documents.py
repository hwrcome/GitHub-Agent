from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.dependencies import require_user
from app.models import User
from app.schemas.documents import DocumentCreate, DocumentSubmission
from app.services.document_service import DocumentService
from app.tasks import enqueue_document_after_commit


router = APIRouter(tags=["documents"])


@router.post("/documents", status_code=status.HTTP_202_ACCEPTED, response_model=DocumentSubmission)
async def create_document(
    payload: DocumentCreate,
    user: Annotated[User, Depends(require_user)],
    session: Annotated[AsyncSession, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> DocumentSubmission:
    submission = await DocumentService(session).create(
        user.id, payload.title, payload.content, payload.metadata, idempotency_key
    )
    if not submission.reused:
        enqueue_document_after_commit(submission.task.id)
    return DocumentSubmission(
        document_id=submission.document.id,
        task_id=submission.task.id,
        status=submission.task.status,
    )
