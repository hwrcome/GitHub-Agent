import pytest

from tests.api_fixtures import ApiContext


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_duplicate_document_is_not_reprocessed(api_context: ApiContext):
    _, token = await api_context.create_user("documents@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"title": "spec", "content": "same content", "metadata": {}}
    first = await api_context.client.post("/documents", headers=headers, json=payload)
    second = await api_context.client.post("/documents", headers=headers, json=payload)
    assert first.status_code == second.status_code == 202
    assert first.json()["document_id"] == second.json()["document_id"]
    assert first.json()["task_id"] == second.json()["task_id"]


@pytest.mark.asyncio
async def test_document_validates_title_and_content(api_context: ApiContext):
    _, token = await api_context.create_user("document-validation@example.com")
    response = await api_context.client.post(
        "/documents",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "", "content": ""},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
