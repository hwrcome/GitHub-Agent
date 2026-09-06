from uuid import uuid4

import pytest

from tests.api_fixtures import ApiContext


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_user_cannot_read_another_users_task(api_context: ApiContext):
    _, user_a_token = await api_context.create_user("user-a@example.com")
    _, user_b_token = await api_context.create_user("user-b@example.com")
    submitted = await api_context.client.post(
        "/search",
        json={"query": "private query"},
        headers={"Authorization": f"Bearer {user_b_token}"},
    )
    assert submitted.status_code == 202

    response = await api_context.client.get(
        f"/tasks/{submitted.json()['task_id']}",
        headers={"Authorization": f"Bearer {user_a_token}"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"


@pytest.mark.asyncio
async def test_task_owner_can_read_pending_task(api_context: ApiContext):
    _, token = await api_context.create_user("owner@example.com")
    submitted = await api_context.client.post(
        "/search",
        json={"query": "python inference"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = await api_context.client.get(
        f"/tasks/{submitted.json()['task_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "PENDING"
    assert response.json()["result"] is None


@pytest.mark.asyncio
async def test_missing_task_uses_error_envelope(api_context: ApiContext):
    _, token = await api_context.create_user("missing@example.com")
    response = await api_context.client.get(
        f"/tasks/{uuid4()}", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "TASK_NOT_FOUND"
