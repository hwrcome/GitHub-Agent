import pytest
from sqlalchemy import select

from app.models import User


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_admin_can_read_another_users_task(integration_context):
    admin, _ = await integration_context.create_user("admin@example.com")
    _, owner_token = await integration_context.create_user("task-owner@example.com")
    submit = await integration_context.client.post(
        "/search",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={"query": "private"},
    )

    async with integration_context.session_factory() as session:
        account = await session.scalar(select(User).where(User.id == admin["id"]))
        account.role = "admin"
        await session.commit()
    login = await integration_context.client.post(
        "/auth/login", json={"email": "admin@example.com", "password": "password-123"}
    )
    response = await integration_context.client.get(
        f"/tasks/{submit.json()['task_id']}",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert response.status_code == 200
