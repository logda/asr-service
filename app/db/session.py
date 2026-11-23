import logging
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

logger = logging.getLogger(__name__)

# create_all 不会给已存在的表补列，项目未引入 Alembic，这里维护轻量迁移清单。
# 新增列时同时改 app/models 下的模型定义和此清单。
_MIGRATION_COLUMNS = [
    ("asr_tasks", "hotword", "hotword TEXT"),
]

engine = create_async_engine(
    settings.DATABASE_URI,
    echo=False,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


def _apply_migrations(sync_conn) -> None:
    """给已存在的表补齐新增列（仅 ADD COLUMN，可重复执行）。"""
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    for table, column, ddl in _MIGRATION_COLUMNS:
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
            logger.warning(f"[migrate] added column {table}.{column}")


async def init_db() -> None:
    from app.db.base import Base
    from app.models import asr_task  # noqa: F401  确保模型已注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_migrations)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
