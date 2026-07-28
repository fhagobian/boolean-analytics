import os
import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Devuelve el pool de conexiones, creándolo la primera vez que se pide.
    statement_cache_size=0 porque el Transaction Pooler de Supabase
    (pgbouncer) no soporta prepared statements cacheados entre requests."""
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        _pool = await asyncpg.create_pool(
            dsn=dsn, min_size=1, max_size=5, statement_cache_size=0,
        )
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
