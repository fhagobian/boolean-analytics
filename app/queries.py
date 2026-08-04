"""
Capa de acceso a datos vía la API REST de Supabase (PostgREST). Solo
lectura — este servicio nunca escribe en la base operativa. Los filtros
usan la sintaxis de PostgREST (eq., gte., lte., in.).
"""
from datetime import date, datetime, timezone
import httpx

CAMPOS_CASO = ("id,tipo_proceso,estado,empresa_id,departamento,localidad,"
               "tecnico_id,prioridad,created_at,updated_at,sla_deadline,"
               "sla_dias_habiles,historial,numero_serie,razon_social,"
               "descripcion,observaciones,"
               "cierre_descripcion_problema,cierre_como_resolvio")


def _dt_iso(d: date, fin_del_dia: bool = False) -> str:
    t = datetime.max.time() if fin_del_dia else datetime.min.time()
    return datetime.combine(d, t, tzinfo=timezone.utc).isoformat()


def _parse_dt(v):
    if not v:
        return None
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


def _parse_fila_caso(row: dict) -> dict:
    row = dict(row)
    row["created_at"] = _parse_dt(row.get("created_at"))
    row["updated_at"] = _parse_dt(row.get("updated_at"))
    row["sla_deadline"] = _parse_dt(row.get("sla_deadline"))
    return row


async def _get(client: httpx.AsyncClient, path: str, params: list[tuple[str, str]]) -> list[dict]:
    """Trae resultados de PostgREST. Si el llamador pidió un `limit` chico
    (<1000, ej. chequeos puntuales), se respeta tal cual. Si no hay límite
    o es grande (se pidió "traer todo"), se pagina de verdad — sigue
    pidiendo páginas hasta recibir un resultado vacío, sin importar qué
    tope de filas tenga configurado el proyecto de Supabase (Settings →
    API → Max Rows). Esto evita que una consulta se trunque en silencio
    cuando el volumen de datos supera ese tope."""
    limit_pedido = None
    for k, v in params:
        if k == "limit":
            limit_pedido = int(v)

    if limit_pedido is not None and limit_pedido < 1000:
        r = await client.get(path, params=params)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return r.json()

    resultados = []
    offset = 0
    tam_pagina = 1000
    while True:
        pag_params = [(k, v) for k, v in params if k not in ("limit", "offset")]
        pag_params += [("limit", str(tam_pagina)), ("offset", str(offset))]
        r = await client.get(path, params=pag_params)
        if r.status_code == 404:
            return resultados
        r.raise_for_status()
        pagina = r.json()
        if not pagina:
            break
        resultados.extend(pagina)
        offset += len(pagina)
    return resultados


async def traer_feriados(client: httpx.AsyncClient) -> set[date]:
    rows = await _get(client, "/feriados", [("activo", "eq.true"), ("select", "fecha")])
    resultado = set()
    for r in rows:
        v = r.get("fecha")
        resultado.add(date.fromisoformat(v) if isinstance(v, str) else v)
    return resultado


async def casos_en_rango(client: httpx.AsyncClient, desde: date, hasta: date) -> list[dict]:
    """Casos CREADOS en el rango [desde, hasta] (para demanda)."""
    params = [
        ("created_at", f"gte.{_dt_iso(desde)}"),
        ("created_at", f"lte.{_dt_iso(hasta, True)}"),
        ("select", CAMPOS_CASO),
        ("limit", "5000"),
    ]
    rows = await _get(client, "/casos", params)
    return [_parse_fila_caso(r) for r in rows]


async def casos_cerrados_en_rango(client: httpx.AsyncClient, desde: date, hasta: date) -> list[dict]:
    """Casos FINALIZADOS cuyo cierre (updated_at) cae en el rango."""
    params = [
        ("estado", "eq.FINALIZADO"),
        ("updated_at", f"gte.{_dt_iso(desde)}"),
        ("updated_at", f"lte.{_dt_iso(hasta, True)}"),
        ("select", CAMPOS_CASO),
        ("limit", "5000"),
    ]
    rows = await _get(client, "/casos", params)
    return [_parse_fila_caso(r) for r in rows]


async def usuarios_tecnicos(client: httpx.AsyncClient) -> list[dict]:
    params = [
        ("rol", "in.(TECNICO,SUPERVISOR)"),
        ("select", "id,auth_id,nombre,apellido,rol,empresa_codigo,departamentos,subzonas,activo"),
        ("limit", "1000"),
    ]
    return await _get(client, "/usuarios", params)


async def equipos(client: httpx.AsyncClient) -> list[dict]:
    params = [("select", "codigo,nombre,departamentos,lema,escudo")]
    return await _get(client, "/empresas", params)


async def meta_productividad(client: httpx.AsyncClient) -> dict[str, int]:
    """Meta de casos/técnico/día por Departamento. Si la tabla
    config_productividad no existe todavía, devuelve {} (se usa el
    default de 20 en la capa de análisis)."""
    rows = await _get(client, "/config_productividad",
                       [("select", "departamento,meta_casos_dia")])
    return {r["departamento"]: r["meta_casos_dia"] for r in rows}


async def casos_con_comentarios(client: httpx.AsyncClient, tipo_proceso: str,
                                  desde: date, hasta: date) -> list[dict]:
    """Casos FINALIZADOS de un tipo específico, cerrados en el rango,
    con los campos de texto necesarios para el análisis de comentarios
    (motivo de apertura + problema real + cómo se resolvió)."""
    params = [
        ("tipo_proceso", f"eq.{tipo_proceso}"),
        ("estado", "eq.FINALIZADO"),
        ("updated_at", f"gte.{_dt_iso(desde)}"),
        ("updated_at", f"lte.{_dt_iso(hasta, True)}"),
        ("select", "id,descripcion,cierre_descripcion_problema,cierre_como_resolvio"),
        ("limit", "500"),
    ]
    return await _get(client, "/casos", params)


async def guardar_analisis_comentarios(client: httpx.AsyncClient, tipo_proceso: str,
                                         problemas: list[dict], casos_analizados: int) -> None:
    """Guarda el resultado de una corrida semanal de análisis de
    comentarios. Única escritura del servicio — a una tabla propia de
    resultados, nunca a la tabla operativa `casos`."""
    from datetime import datetime, timezone
    body = {
        "tipo_proceso": tipo_proceso,
        "problemas": problemas,
        "casos_analizados": casos_analizados,
        "generado_en": datetime.now(timezone.utc).isoformat(),
    }
    r = await client.post("/analisis_comentarios", json=body,
                           headers={"Prefer": "return=minimal"})
    r.raise_for_status()


async def ultimos_analisis_comentarios(client: httpx.AsyncClient) -> dict[str, dict]:
    """Trae el análisis más reciente guardado por tipo de proceso."""
    params = [
        ("select", "tipo_proceso,problemas,casos_analizados,generado_en"),
        ("order", "generado_en.desc"),
        ("limit", "40"),
    ]
    rows = await _get(client, "/analisis_comentarios", params)
    resultado = {}
    for r in rows:
        tp = r["tipo_proceso"]
        if tp not in resultado:  # el primero que aparece es el más reciente (ya viene ordenado)
            resultado[tp] = r
    return resultado


async def casos_historicos_en_rango(client: httpx.AsyncClient, desde: date, hasta: date) -> list[dict]:
    """Lee de casos_historicos si la tabla existe (se crea cuando se
    cargue el Excel de los 2 años anteriores). Si no existe, devuelve
    lista vacía sin romper el resto del cálculo."""
    params = [
        ("estado", "eq.FINALIZADO"),
        ("updated_at", f"gte.{_dt_iso(desde)}"),
        ("updated_at", f"lte.{_dt_iso(hasta, True)}"),
        ("select", "tipo_proceso,estado,empresa_id,departamento,localidad,"
                   "tecnico_id,created_at,updated_at,sla_dias_habiles"),
        ("limit", "5000"),
    ]
    rows = await _get(client, "/casos_historicos", params)
    return [_parse_fila_caso(r) for r in rows]
