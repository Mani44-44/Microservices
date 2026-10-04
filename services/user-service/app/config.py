from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "ticketflow"
    postgres_password: str = "ticketflow123"
    postgres_db: str = "ticketflow_users"

    jwt_secret_key: str = "your-super-secret-jwt-key-change-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    # Fernet key for encrypting PII columns (full_name, phone) at rest.
    # Dev-only default below; must be a urlsafe-base64 32-byte key in prod.
    fernet_key: str = "yl3INEiYtjgOsFwMfyvnqtMbucZQ3J0VdZXrz-tXP1M="

    service_name: str = "user-service"

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
