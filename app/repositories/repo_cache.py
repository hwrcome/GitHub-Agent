from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import RepoCache


class RepoCacheStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory

    async def get(self, repo_name: str) -> str | None:
        async with self.session_factory() as session:
            row = await session.scalar(select(RepoCache).where(RepoCache.repo_name == repo_name))
            if row is None or row.expires_at <= datetime.now(timezone.utc):
                return None
            return row.combined_doc

    async def save(self, repo_name: str, combined_doc: str, expires_at: datetime) -> None:
        statement = insert(RepoCache).values(
            repo_name=repo_name, combined_doc=combined_doc, expires_at=expires_at
        )
        statement = statement.on_conflict_do_update(
            index_elements=[RepoCache.repo_name],
            set_={"combined_doc": statement.excluded.combined_doc, "expires_at": statement.excluded.expires_at},
        )
        async with self.session_factory() as session:
            await session.execute(statement)
            await session.commit()
