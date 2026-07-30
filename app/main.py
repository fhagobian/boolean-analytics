import os
import asyncio
import logging
from datetime import date, datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

from . import db, queries, analytics
from .fechas import ventanas_comparables, dias_habiles_transcurridos

logger = logging.getLogger("boolean-analytics")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_client()
    yield
    await db.close_client()


app = FastAPI(title="BOOLEAN Analytics Engine", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://boolean-frontend.vercel.app",
        "http://localhost:5173",
    ],
    allow_methods=["GET"],
    allow_headers=["Authorization", "Content-Type"],
)


def verificar_token(authorization: str = Header(default="")):
    esperado = os.environ.get("API_SECRET", "")
    if not esperado:
        raise HTTPException(500, "API_SECRET no configurado en el servidor")
    recibido = authorization.replace("Bearer ", "").strip()
    if recibido != esperado:
        raise HTTPException(401, "Token inválido")
    return True


@app.get("/health")
async def health():
    return {"status": "ok", "service": "boolean-analytics"}


@app.get("/health/supabase")
async def health_supabase():
    """Chequeo específico de la conexión a Supabase, para diagnosticar
    problemas de configuración (SUPABASE_URL / SUPABASE_SERVICE_KEY)
    sin tener que calcular toda la radiografía."""
    try:
        client = await db.get_client()
        r = await client.get("/feriados", params=[("select", "fecha"), ("limit", "1")])
        return {
            "status": "ok" if r.status_code < 400 else "error",
            "http_status": r.status_code,
            "supabase_url_configurada": bool(os.environ.get("SUPABASE_URL")),
            "service_key_configurada": bool(os.environ.get("SUPABASE_SERVICE_KEY")),
        }
    except Exception as e:
        return {"status": "error", "detalle": str(e),
                "supabase_url_configurada": bool(os.environ.get("SUPABASE_URL")),
                "service_key_configurada": bool(os.environ.get("SUPABASE_SERVICE_KEY"))}


@app.get("/radiografia")
async def radiografia(
    _auth: bool = Depends(verificar_token),
    equipo: str | None = Query(default=None, description="Filtrar por código de equipo"),
    vista_tendencia: str = Query(default="semanas", pattern="^(semanas|meses)$"),
):
    try:
        client = await db.get_client()
        hoy = date.today()

        feriados = await queries.traer_feriados(client)
        ventanas = ventanas_comparables(hoy, feriados)
        n_habiles_mes = ventanas["dias_habiles_transcurridos"]
        desde_90 = date.fromordinal(hoy.toordinal() - 90)
        rango_dias_tendencia = 400 if vista_tendencia == "meses" else 60
        desde_tendencia = date.fromordinal(hoy.toordinal() - rango_dias_tendencia)

        # ── Todas las consultas independientes, EN PARALELO ──
        # (antes eran secuenciales: 9 round-trips uno detrás del otro
        # podían acumular tiempo suficiente para que Railway cortara
        # la conexión por timeout. En paralelo, el tiempo total es el
        # de la consulta más lenta, no la suma de todas.)
        (
            cerrados_actual,
            cerrados_mes_ant_comp,
            cerrados_mes_ant_cerrado,
            cerrados_anio_ant_comp_hist,
            cerrados_anio_ant_cerrado_hist,
            tecnicos,
            cerrados_tendencia,
            casos_90d,
            meta_por_depto,
        ) = await asyncio.gather(
            queries.casos_cerrados_en_rango(client, ventanas["actual"]["desde"], ventanas["actual"]["hasta"]),
            queries.casos_cerrados_en_rango(client, ventanas["mes_anterior_comparable"]["desde"], ventanas["mes_anterior_comparable"]["hasta"]),
            queries.casos_cerrados_en_rango(client, ventanas["mes_anterior_cerrado"]["desde"], ventanas["mes_anterior_cerrado"]["hasta"]),
            queries.casos_historicos_en_rango(client, ventanas["anio_anterior_comparable"]["desde"], ventanas["anio_anterior_comparable"]["hasta"]),
            queries.casos_historicos_en_rango(client, ventanas["anio_anterior_cerrado"]["desde"], ventanas["anio_anterior_cerrado"]["hasta"]),
            queries.usuarios_tecnicos(client),
            queries.casos_cerrados_en_rango(client, desde_tendencia, hoy),
            queries.casos_en_rango(client, desde_90, hoy),
            queries.meta_productividad(client),
        )

        # Fallback a `casos` si todavía no existe casos_historicos con datos
        cerrados_anio_ant_comp = cerrados_anio_ant_comp_hist
        cerrados_anio_ant_cerrado = cerrados_anio_ant_cerrado_hist
        if not cerrados_anio_ant_comp or not cerrados_anio_ant_cerrado:
            fallback_comp, fallback_cerrado = await asyncio.gather(
                queries.casos_cerrados_en_rango(client, ventanas["anio_anterior_comparable"]["desde"], ventanas["anio_anterior_comparable"]["hasta"]),
                queries.casos_cerrados_en_rango(client, ventanas["anio_anterior_cerrado"]["desde"], ventanas["anio_anterior_cerrado"]["hasta"]),
            )
            if not cerrados_anio_ant_comp:
                cerrados_anio_ant_comp = fallback_comp
            if not cerrados_anio_ant_cerrado:
                cerrados_anio_ant_cerrado = fallback_cerrado

        if equipo:
            tecnicos_filtrados = [t for t in tecnicos if t.get("empresa_codigo") == equipo]
            cerrados_actual_f = [c for c in cerrados_actual if c.get("empresa_id") == equipo]
            cerrados_mes_ant_comp_f = [c for c in cerrados_mes_ant_comp if c.get("empresa_id") == equipo]
            cerrados_mes_ant_cerrado_f = [c for c in cerrados_mes_ant_cerrado if c.get("empresa_id") == equipo]
            cerrados_anio_ant_comp_f = [c for c in cerrados_anio_ant_comp if c.get("empresa_id") == equipo]
            cerrados_anio_ant_cerrado_f = [c for c in cerrados_anio_ant_cerrado if c.get("empresa_id") == equipo]
            cerrados_tendencia_f = [c for c in cerrados_tendencia if c.get("empresa_id") == equipo]
            casos_90d_f = [c for c in casos_90d if c.get("empresa_id") == equipo]
        else:
            tecnicos_filtrados = tecnicos
            cerrados_actual_f = cerrados_actual
            cerrados_mes_ant_comp_f = cerrados_mes_ant_comp
            cerrados_mes_ant_cerrado_f = cerrados_mes_ant_cerrado
            cerrados_anio_ant_comp_f = cerrados_anio_ant_comp
            cerrados_anio_ant_cerrado_f = cerrados_anio_ant_cerrado
            cerrados_tendencia_f = cerrados_tendencia
            casos_90d_f = casos_90d

        n_tecnicos_activos = sum(1 for t in tecnicos_filtrados if t.get("activo", True))

        bloque1 = analytics.kpis_por_proceso(
            cerrados_actual_f, cerrados_mes_ant_comp_f, cerrados_mes_ant_cerrado_f,
            cerrados_anio_ant_comp_f, cerrados_anio_ant_cerrado_f,
            feriados, n_tecnicos_activos, n_habiles_mes,
        )
        # Bloque 2 (SLA con responsables): si hay un equipo seleccionado, se
        # calcula SOLO con sus datos — no tiene sentido "culpar" a otros
        # equipos cuando el usuario ya pidió ver uno en particular.
        bloque2 = analytics.sla_con_responsables(cerrados_actual_f, feriados)
        bloque3 = analytics.tendencia_sla(cerrados_tendencia_f, hoy, vista_tendencia)
        bloque4 = analytics.ranking_por_equipo(cerrados_actual_f, tecnicos_filtrados, n_habiles_mes)
        dias_habiles_90d = dias_habiles_transcurridos(desde_90, hoy, feriados)
        bloque5 = analytics.demanda_por_zona(casos_90d_f, tecnicos_filtrados, meta_por_depto, dias_habiles_90d)
        bloque6 = analytics.reincidencia_terminales(casos_90d_f, hoy)

        return {
            "generado_en": datetime.utcnow().isoformat() + "Z",
            "ventana_actual": {"desde": ventanas["actual"]["desde"].isoformat(),
                                "hasta": ventanas["actual"]["hasta"].isoformat(),
                                "dias_habiles_transcurridos": n_habiles_mes},
            "kpis_por_proceso": bloque1,
            "sla_responsables": bloque2,
            "tendencia_sla": {"vista": vista_tendencia, "puntos": bloque3},
            "ranking_por_equipo": bloque4,
            "demanda_por_zona": {"ventana_dias": 90, "zonas": bloque5},
            "reincidencia_terminales": {"ventana_dias": 30, "terminales": bloque6},
        }

    except httpx.HTTPStatusError as e:
        logger.exception("Error HTTP consultando Supabase")
        raise HTTPException(
            502,
            f"Error consultando Supabase ({e.response.status_code}): revisar SUPABASE_URL / "
            f"SUPABASE_SERVICE_KEY en Railway. Detalle: {e.response.text[:300]}"
        )
    except Exception as e:
        logger.exception("Error inesperado calculando la radiografía")
        raise HTTPException(500, f"Error interno: {e}")
