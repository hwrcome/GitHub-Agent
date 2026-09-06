from __future__ import annotations

import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self, client: Redis):
        self.client = client

    async def get_json(self, key: str) -> Any | None:
        try:
            value = await self.client.get(key)
            return json.loads(value) if value is not None else None
        except (RedisError, ValueError, TypeError):
            logger.warning("redis cache read failed", extra={"cache_key": key})
            return None

    async def set_json(self, key: str, value: Any, ttl: int) -> bool:
        try:
            await self.client.set(key, json.dumps(value, separators=(",", ":"), ensure_ascii=False), ex=ttl)
            return True
        except (RedisError, TypeError, ValueError):
            logger.warning("redis cache write failed", extra={"cache_key": key})
            return False

    async def delete(self, key: str) -> bool:
        try:
            return bool(await self.client.delete(key))
        except RedisError:
            logger.warning("redis cache delete failed", extra={"cache_key": key})
            return False
