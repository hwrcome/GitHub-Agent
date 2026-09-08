from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError


logger = logging.getLogger(__name__)
RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return {current, redis.call('TTL', KEYS[1])}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    count: int
    retry_after: int


class RateLimitService:
    def __init__(self, client: Redis):
        self.client = client

    async def check(self, scope: str, limit: int, window_seconds: int) -> RateLimitDecision:
        key = f"rl:search:{scope}"
        try:
            count, ttl = await self.client.eval(RATE_LIMIT_SCRIPT, 1, key, window_seconds)
            count, ttl = int(count), max(int(ttl), 1)
            return RateLimitDecision(count <= limit, count, ttl if count > limit else 0)
        except RedisError:
            logger.warning("redis rate limit unavailable; failing open", extra={"scope": scope})
            return RateLimitDecision(True, 0, 0)
