from app.config import get_settings


def test_settings_has_safe_defaults(monkeypatch):
    monkeypatch.delenv("AGENT_MODE", raising=False)
    monkeypatch.delenv("JWT_EXPIRE_SECONDS", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.agent_mode == "mock"
    assert settings.jwt_expire_seconds == 1800


def test_settings_reads_database_and_worker_urls(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://u:p@localhost/db",
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.database_url.endswith("/db")
    assert settings.redis_url.endswith("/0")
