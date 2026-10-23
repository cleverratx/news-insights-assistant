from typing import Any

import asyncpg

from app.core.config import get_settings

_pool: Any = None
POOL_SIZE = 20
ACQUIRE_TIMEOUT_SECONDS = 10


def postgres_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def get_pool() -> Any:
    """Пул создаётся при первом запросе к базе: API поднимается и без PostgreSQL. Лента и
    разбор держат соединение, пока ждут модель (15 с и дольше), поэтому пул из пяти
    соединений вешал быстрые запросы профиля на время чужих вызовов LLM."""
    global _pool
    if _pool is None:
        dsn = postgres_dsn(get_settings().database_url.get_secret_value())
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=POOL_SIZE)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
