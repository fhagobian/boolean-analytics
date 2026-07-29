"""
Cliente HTTP hacia la API REST de Supabase (PostgREST), en lugar de una
conexión TCP directa al Postgres. Se cambió a este enfoque porque Railway
no permite salida de red por los puertos nativos de Postgres (5432/6543);
HTTPS (puerto 443) sí funciona siempre, por eso se usa el mismo mecanismo
que ya usa el frontend (supabase-js) para hablar con la base.
"""
import os
import httpx

_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        base_url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"
        key = os.environ["SUPABASE_SERVICE_KEY"]
        _client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            # Timeout corto y explícito: si algo de red falla, queremos
            # que NUESTRA app devuelva el error rápido y con detalle,
            # en vez de dejar que la plataforma corte con un 502 genérico
            # después de esperar mucho más tiempo.
            timeout=httpx.Timeout(connect=8.0, read=15.0, write=8.0, pool=8.0),
        )
    return _client


async def close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
