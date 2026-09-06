from __future__ import annotations

import secrets
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError


RELEASE_LOCK_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass
class LockLease:
    client: Redis
    key: str
    token: str

    async def release(self) -> bool:
        try:
            return bool(await self.client.eval(RELEASE_LOCK_SCRIPT, 1, self.key, self.token))
        except RedisError:
            return False


class LockService:
    def __init__(self, client: Redis):
        self.client = client

    async def acquire(self, key: str, ttl_seconds: int) -> LockLease | None:
        token = secrets.token_urlsafe(24)
        try:
            acquired = await self.client.set(f"lock:{key}", token, nx=True, ex=ttl_seconds)
            if not acquired:
                return None
            return LockLease(self.client, f"lock:{key}", token)
        except RedisError:
            return None
