from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379"
    jwt_secret_key: str = "your-super-secret-jwt-key-change-in-production-min-32-chars"
    jwt_algorithm: str = "HS256"

    user_service_url: str = "http://localhost:8001"
    catalog_service_url: str = "http://localhost:8002"
    booking_service_url: str = "http://localhost:8003"
    payment_service_url: str = "http://localhost:8004"

    # Rate limiting: sliding window
    rate_limit_per_user_per_minute: int = 100
    rate_limit_per_ip_per_minute: int = 300

    service_name: str = "gateway"

    model_config = SettingsConfigDict(extra="ignore", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()
