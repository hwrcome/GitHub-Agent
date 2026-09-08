from redis.asyncio import Redis, from_url

from app.config import get_settings


def get_redis() -> Redis:
    return from_url(get_settings().redis_url, decode_responses=True)
