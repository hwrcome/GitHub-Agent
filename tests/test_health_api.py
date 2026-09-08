import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from app.main import create_app


@pytest.mark.asyncio
async def test_liveness_does_not_require_dependencies():
    app = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_dependency_failure(monkeypatch):
    async def fake_postgres():
        return None

    async def fake_failure():
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr("app.api.health.check_postgres", fake_postgres)
    monkeypatch.setattr("app.api.health.check_redis", fake_failure)
    app = create_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"
    assert response.headers.get("x-request-id")
