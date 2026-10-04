from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ponytail: separate port (5434) from the shared Postgres (5433) — this DB
    # runs in its own pgvector/pgvector:pg16 container, not the main one.
    postgres_host: str = "localhost"
    postgres_port: int = 5434
    postgres_user: str = "ticketflow"
    postgres_password: str = "ticketflow123"
    postgres_db: str = "ticketflow_rag"
    service_name: str = "rag-service"

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
