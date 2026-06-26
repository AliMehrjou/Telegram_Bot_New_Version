import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base

# Fetch database URL from environment or fallback to local mysql docker configuration
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "mysql+aiomysql://match_bot_user:match_bot_password@localhost:3306/match_bot_db"
)

# Create an async database engine with optimal connection pool tuning
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    # باگ برطرف شده: True شدن این مقدار از خطای "MySQL server has gone away" جلوگیری می‌کند
    pool_pre_ping=True
)

# Async sessionmaker factory
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

# Base model class for SQLAlchemy
Base = declarative_base()


async def get_db_session():
    """
    Context manager style dependency helper that yields an async database session 
    and handles commit/rollback and automatic cleanup.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()