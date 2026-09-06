import pytest

from tests.api_fixtures import ApiContext


@pytest.mark.asyncio
async def test_same_key_returns_same_task(api_context: ApiContext):
    _, token = await api_context.create_user("idempotent@example.com")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "fixed-key"}
    first = await api_context.client.post("/search", headers=headers, json={"query": "python"})
    second = await api_context.client.post("/search", headers=headers, json={"query": "python"})
    assert first.status_code == second.status_code == 202
    assert first.json()["task_id"] == second.json()["task_id"]


@pytest.mark.asyncio
async def test_same_key_with_different_payload_is_conflict(api_context: ApiContext):
    _, token = await api_context.create_user("idempotent-conflict@example.com")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "fixed-key-2"}
    await api_context.client.post("/search", headers=headers, json={"query": "python"})
    response = await api_context.client.post("/search", headers=headers, json={"query": "rust"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
