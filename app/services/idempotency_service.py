from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdempotencyKey


class IdempotencyConflict(RuntimeError):
    pass


def normalize_search_request(request: Any) -> str:
    if hasattr(request, "model_dump"):
        value = request.model_dump(exclude_none=True)
    elif isinstance(request, dict):
        value = request
    else:
        value = vars(request)
    if "query" in value:
        value = {**value, "query": str(value["query"]).strip()}
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_request(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class IdempotencyService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_or_reserve(
        self,
        user_id: UUID,
        endpoint: str,
        key: str,
        request_hash: str,
        task_id: UUID | None = None,
    ) -> IdempotencyKey | None:
        record = await self.session.scalar(
            select(IdempotencyKey)
            .where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
            .with_for_update()
        )
        now = datetime.now(timezone.utc)
        if record is not None and record.expires_at > now:
            if record.request_hash != request_hash:
                raise IdempotencyConflict("idempotency key was already used for another request")
            return record
        if record is not None:
            await self.session.execute(
                delete(IdempotencyKey).where(
                    IdempotencyKey.user_id == user_id,
                    IdempotencyKey.endpoint == endpoint,
                    IdempotencyKey.key == key,
                )
            )
            await self.session.flush()
        if task_id is None:
            return None
        record = IdempotencyKey(
            user_id=user_id,
            endpoint=endpoint,
            key=key,
            request_hash=request_hash,
            task_id=task_id,
            expires_at=now + timedelta(hours=24),
        )
        self.session.add(record)
        await self.session.flush()
        return record
