import pytest
from sqlalchemy import func, select

from app.models import SearchRequest, Task
from app.services.rate_limit_service import RateLimitDecision
from tests.api_fixtures import ApiContext


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_search_returns_202_and_task_location(api_context: ApiContext):
    _, token = await api_context.create_user("search@example.com")
    response = await api_context.client.post(
        "/search",
        json={"query": "python inference"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    assert response.headers["location"] == f"/tasks/{task_id}"
    async with api_context.session_factory() as session:
        assert await session.scalar(select(func.count(Task.id))) == 1
        assert await session.get(SearchRequest, task_id) is not None


@pytest.mark.asyncio
async def test_search_requires_authentication_and_validates_query(api_context: ApiContext):
    missing_auth = await api_context.client.post("/search", json={"query": "python"})
    assert missing_auth.status_code == 401
    _, token = await api_context.create_user("validation@example.com")
    invalid = await api_context.client.post(
        "/search",
        json={"query": "", "max_results": 1000},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_search_rate_limit_returns_429(api_context: ApiContext, monkeypatch):
    _, token = await api_context.create_user("rate-limited@example.com")

    async def reject_limit(_user_id):
        return RateLimitDecision(allowed=False, count=11, retry_after=42)

    monkeypatch.setattr("app.api.search.check_search_rate_limit", reject_limit)
    response = await api_context.client.post(
        "/search",
        json={"query": "python inference"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.json()["error"]["code"] == "RATE_LIMITED"
