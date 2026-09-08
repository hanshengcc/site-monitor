"""Database connection and session management."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from backend.app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=30,
    max_overflow=20,
    pool_timeout=60,
    pool_recycle=1800,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
