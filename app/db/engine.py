from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


def create_engine() -> AsyncEngine:
    """Создание async-движка. Параметры пула задаются только для серверных БД:
    SQLite их не поддерживает и работает через SingletonThreadPool/DefaultPool."""
    dsn = get_settings().db_dsn
    kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
    if not dsn.startswith("sqlite"):
        kwargs.update(pool_size=20, max_overflow=10, pool_recycle=1800)
    return create_async_engine(dsn, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
