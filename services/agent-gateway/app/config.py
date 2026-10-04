from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "ticketflow"
    postgres_password: str = "ticketflow123"
    postgres_db: str = "ticketflow_agent_gateway"

    redis_url: str = "redis://localhost:6379"

    jwt_secret_key: str = "your-super-secret-jwt-key-change-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"

    rag_service_url: str = "http://localhost:8010"
    guardrail_service_url: str = "http://localhost:8011"
    eval_service_url: str = "http://localhost:8012"
    travel_planner_url: str = "http://localhost:8008"
    support_agent_url: str = "http://localhost:8009"

    rate_limit_per_hour: int = 20
    downstream_timeout_seconds: float = 10.0

    service_name: str = "agent-gateway"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = SettingsConfigDict(extra="ignore", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
