from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document


async def get_document_by_checksum(
    session: AsyncSession, user_id: UUID, checksum: str
) -> Document | None:
    return await session.scalar(
        select(Document).where(Document.user_id == user_id, Document.checksum == checksum)
    )
