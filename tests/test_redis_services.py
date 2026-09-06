import pytest
import pytest_asyncio
from redis.asyncio import from_url

from app.services.cache_service import CacheService
from app.services.lock_service import LockService
from app.services.rate_limit_service import RateLimitService


@pytest_asyncio.fixture
async def test_redis():
    client = from_url("redis://localhost:56379/15", decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.mark.asyncio
async def test_cache_round_trips_json_and_delete(test_redis):
    cache = CacheService(test_redis)
    await cache.set_json("search:v1:hash:agent", {"repos": ["one"]}, ttl=60)
    assert await cache.get_json("search:v1:hash:agent") == {"repos": ["one"]}
    await cache.delete("search:v1:hash:agent")
    assert await cache.get_json("search:v1:hash:agent") is None


@pytest.mark.asyncio
async def test_rate_limit_allows_limit_then_rejects(test_redis):
    limiter = RateLimitService(test_redis)
    assert (await limiter.check("user:1", 2, 60)).allowed
    assert (await limiter.check("user:1", 2, 60)).allowed
    decision = await limiter.check("user:1", 2, 60)
    assert not decision.allowed
    assert decision.retry_after > 0


@pytest.mark.asyncio
async def test_lock_release_does_not_delete_another_owner(test_redis):
    lock_service = LockService(test_redis)
    first = await lock_service.acquire("search:x", 30)
    second = await lock_service.acquire("search:x", 30)
    assert first is not None and second is None

    await test_redis.set("lock:search:x", "another-owner", ex=30)
    assert not await first.release()
    assert await test_redis.get("lock:search:x") == "another-owner"
