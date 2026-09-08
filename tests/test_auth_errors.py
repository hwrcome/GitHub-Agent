import httpx
import pytest
from httpx import ASGITransport

from app.main import create_app


@pytest.mark.asyncio
async def test_missing_auth_uses_error_envelope():
    app = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/search", json={"query": "python"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.json()["request_id"]
