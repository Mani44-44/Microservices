from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "ticketflow"
    postgres_password: str = "ticketflow123"
    postgres_db: str = "ticketflow_travel_planner"

    rag_service_url: str = "http://localhost:8010"

    service_name: str = "travel-planner-agent"

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
