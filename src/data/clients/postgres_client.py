from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from typing import AsyncGenerator
from src.config.settings import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that provides a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def create_tables() -> None:
    """Create all ticket service tables on startup using SQLAlchemy.
    Import all models before calling so SQLAlchemy registers them with Base.

     Auth Service must have already run create_tables() first so that
        users, teams, and roles tables exist — ticket tables FK-reference them.
    """
    import src.data.models.postgres  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)