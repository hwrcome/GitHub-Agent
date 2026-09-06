import pytest


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_register_search_poll_and_read_result(integration_context):
    _, token = await integration_context.create_user("flow@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    submit = await integration_context.client.post(
        "/search", headers=headers, json={"query": "python inference"}
    )
    assert submit.status_code == 202
    task_id = submit.json()["task_id"]

    await integration_context.run_pending()
    result = await integration_context.poll_until_terminal(task_id, headers)

    assert result["status"] == "SUCCEEDED"
    assert result["result"]["final_results"]
    assert len(result["result"]["repositories"]) == 3
