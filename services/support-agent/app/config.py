from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "ticketflow"
    postgres_password: str = "ticketflow123"
    postgres_db: str = "ticketflow_support_agent"

    service_name: str = "support-agent"

    rag_service_url: str = "http://localhost:8010"
    booking_service_url: str = "http://localhost:8003"
    payment_service_url: str = "http://localhost:8004"
    catalog_service_url: str = "http://localhost:8002"

    http_timeout_seconds: float = 10.0

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
