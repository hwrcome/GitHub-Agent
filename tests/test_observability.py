import httpx
import pytest
from httpx import ASGITransport

from app.main import create_app


@pytest.mark.asyncio
async def test_request_id_is_returned():
    app = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live", headers={"X-Request-ID": "req-123"})
    assert response.headers["X-Request-ID"] == "req-123"


@pytest.mark.asyncio
async def test_metrics_endpoint_contains_http_counter():
    app = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
