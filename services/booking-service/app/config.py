from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_user: str = "ticketflow"
    postgres_password: str = "ticketflow123"
    postgres_db: str = "ticketflow_booking"

    redis_url: str = "redis://localhost:6379"

    jwt_secret_key: str = "your-super-secret-jwt-key-change-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"

    payment_service_url: str = "http://localhost:8004"
    catalog_service_url: str = "http://localhost:8002"

    seat_lock_ttl_seconds: int = 300  # 5 minutes
    max_seats_per_booking: int = 10
    expiry_check_interval_seconds: int = 30

    kafka_brokers: str = "localhost:9092"
    outbox_poll_interval_seconds: int = 2
    outbox_batch_size: int = 50

    service_name: str = "booking-service"

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
