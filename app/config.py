from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+asyncpg://github_agent:github_agent@localhost:5432/github_agent"
    )
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    jwt_secret: str = "change-me-in-development-secret-32-bytes"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 1800
    agent_mode: str = "mock"
    rate_limit_per_minute: int = 10
    mcp_server_script: str = ""
    mcp_server_python: str = ""

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
