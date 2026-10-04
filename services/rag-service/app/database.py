import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings
from libs.resilience import retry_with_backoff

log = structlog.get_logger()
settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def init_db() -> None:
    """Create the pgvector extension (Postgres only) and all tables on startup.
    No Alembic in this project — create_all is the whole migration story.

    Retries with backoff: on a cold docker-compose start, postgres may not
    be ready yet when this container's single lifespan attempt runs.
    """
    async def _connect_and_create() -> None:
        async with engine.begin() as conn:
            if conn.engine.dialect.name == "postgresql":
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    await retry_with_backoff(_connect_and_create)
    log.info("database_initialized", db=settings.postgres_db)


async def get_db():
    """FastAPI dependency that yields an AsyncSession."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
