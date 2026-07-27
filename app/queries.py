"""
Capa de acceso a datos. Trae casos crudos de Supabase (tabla `casos` y,
cuando exista, `casos_historicos`) para que la capa de análisis calcule
las métricas. Solo lectura — este servicio nunca escribe en la base
operativa.
"""
from datetime import date, datetime
import asyncpg


async def traer_feriados(pool: asyncpg.Pool) -> set[date]:
    rows = await pool.fetch("SELECT fecha FROM feriados WHERE activo = true")
    return {r["fecha"] for r in rows}


SELECT_CASOS_BASE = """
    SELECT
        id, tipo_proceso, estado, empresa_id, departamento, localidad,
        tecnico_id, prioridad, created_at, updated_at,
        sla_deadline, sla_dias_habiles, historial
    FROM casos
    WHERE created_at >= $1 AND created_at <= $2
"""


async def casos_en_rango(pool: asyncpg.Pool, desde: date, hasta: date) -> list[dict]:
    """Casos CREADOS en el rango [desde, hasta] (para demanda)."""
    hasta_dt = datetime.combine(hasta, datetime.max.time())
    desde_dt = datetime.combine(desde, datetime.min.time())
    rows = await pool.fetch(SELECT_CASOS_BASE, desde_dt, hasta_dt)
    return [dict(r) for r in rows]


SELECT_CASOS_CERRADOS = """
    SELECT
        id, tipo_proceso, estado, empresa_id, departamento, localidad,
        tecnico_id, prioridad, created_at, updated_at,
        sla_deadline, sla_dias_habiles, historial
    FROM casos
    WHERE estado = 'FINALIZADO' AND updated_at >= $1 AND updated_at <= $2
"""


async def casos_cerrados_en_rango(pool: asyncpg.Pool, desde: date, hasta: date) -> list[dict]:
    """Casos FINALIZADOS cuyo cierre (updated_at) cae en el rango.
    Usado para tiempos de resolución, SLA cumplido, 1ra visita, ranking."""
    hasta_dt = datetime.combine(hasta, datetime.max.time())
    desde_dt = datetime.combine(desde, datetime.min.time())
    rows = await pool.fetch(SELECT_CASOS_CERRADOS, desde_dt, hasta_dt)
    return [dict(r) for r in rows]


async def usuarios_tecnicos(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("""
        SELECT id, auth_id, nombre, apellido, rol, empresa_codigo,
               departamentos, subzonas, activo
        FROM usuarios
        WHERE rol IN ('TECNICO','SUPERVISOR')
    """)
    return [dict(r) for r in rows]


async def equipos(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("""
        SELECT codigo, nombre, departamentos, lema, escudo
        FROM empresas
    """)
    return [dict(r) for r in rows]


async def meta_productividad(pool: asyncpg.Pool) -> dict[str, int]:
    """Meta de casos/técnico/día configurada por Departamento.
    Tabla config_productividad(departamento TEXT PK, meta_casos_dia INT).
    Si no existe fila para un depto, se usa el default de 20."""
    try:
        rows = await pool.fetch("SELECT departamento, meta_casos_dia FROM config_productividad")
        return {r["departamento"]: r["meta_casos_dia"] for r in rows}
    except Exception:
        return {}


async def casos_historicos_en_rango(pool: asyncpg.Pool, desde: date, hasta: date) -> list[dict]:
    """Lee de casos_historicos si la tabla existe (se crea cuando el
    usuario cargue el Excel de los 2 años anteriores). Si no existe,
    devuelve lista vacía sin romper — el resto del sistema sigue
    funcionando solo con datos operativos."""
    try:
        hasta_dt = datetime.combine(hasta, datetime.max.time())
        desde_dt = datetime.combine(desde, datetime.min.time())
        rows = await pool.fetch("""
            SELECT tipo_proceso, estado, empresa_id, departamento, localidad,
                   tecnico_id, created_at, updated_at, sla_dias_habiles
            FROM casos_historicos
            WHERE updated_at >= $1 AND updated_at <= $2 AND estado = 'FINALIZADO'
        """, desde_dt, hasta_dt)
        return [dict(r) for r in rows]
    except Exception:
        return []
