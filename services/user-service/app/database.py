from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
import structlog

from app.config import get_settings
from libs.resilience import retry_with_backoff

log = structlog.get_logger()
settings = get_settings()


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    echo=False,
    future=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables defined in the ORM models.

    Called once at application startup via the lifespan event.
    In production, prefer Alembic migrations over this approach.

    Retries with backoff: on a cold docker-compose start, postgres may not
    be ready yet when this container's single lifespan attempt runs.
    """
    # Import models so that Base.metadata is populated before create_all.
    import app.models  # noqa: F401

    async def _connect_and_create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await retry_with_backoff(_connect_and_create)
    log.info("database_initialized", database=settings.postgres_db)
