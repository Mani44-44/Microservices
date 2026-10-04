import structlog
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings
from libs.resilience import retry_with_backoff

logger = structlog.get_logger(__name__)

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Create all tables on startup (dev convenience; use Alembic in prod).

    Retries with backoff: on a cold docker-compose start, postgres may not
    be ready yet when this container's single lifespan attempt runs.
    """
    from app import models  # noqa: F401 – ensure models are imported

    async def _connect_and_create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await retry_with_backoff(_connect_and_create)
    logger.info("database_initialized", service=settings.service_name)
