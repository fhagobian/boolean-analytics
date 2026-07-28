import os
import socket
from urllib.parse import urlparse, urlunparse
import asyncpg

_pool: asyncpg.Pool | None = None


def _dsn_forzado_ipv4(dsn: str) -> str:
    """Resuelve el hostname del DSN a su dirección IPv4 y la reemplaza en
    la URL. Necesario porque Railway no tiene salida de red IPv6, y el
    hostname de Supabase puede resolver a IPv6 primero, causando
    'Network is unreachable' aunque exista una IPv4 alcanzable."""
    parsed = urlparse(dsn)
    host = parsed.hostname
    port = parsed.port or 5432
    try:
        # AF_INET fuerza a traer solo direcciones IPv4
        info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        ipv4 = info[0][4][0]
    except Exception:
        return dsn  # si falla la resolución manual, se intenta con el DSN original

    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    netloc = f"{userinfo}{ipv4}:{port}"
    return urlunparse(parsed._replace(netloc=netloc))


async def get_pool() -> asyncpg.Pool:
    """Devuelve el pool de conexiones, creándolo la primera vez que se pide.
    statement_cache_size=0 porque el Transaction Pooler de Supabase
    (pgbouncer) no soporta prepared statements cacheados entre requests.
    Fuerza IPv4 porque Railway no tiene salida de red IPv6."""
    global _pool
    if _pool is None:
        dsn_original = os.environ["DATABASE_URL"]
        dsn = _dsn_forzado_ipv4(dsn_original)
        _pool = await asyncpg.create_pool(
            dsn=dsn, min_size=1, max_size=5, statement_cache_size=0,
            ssl="require",
        )
    return _pool


async def close_pool():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
