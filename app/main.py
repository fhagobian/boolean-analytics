import os
from datetime import date, datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware

from . import db, queries, analytics
from .fechas import ventanas_comparables, dias_habiles_transcurridos


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_pool()
    yield
    await db.close_pool()


app = FastAPI(title="BOOLEAN Analytics Engine", lifespan=lifespan)

# CORS: solo el frontend de producción (y localhost para pruebas)
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


@app.get("/radiografia")
async def radiografia(
    _auth: bool = Depends(verificar_token),
    equipo: str | None = Query(default=None, description="Filtrar por código de equipo"),
    vista_tendencia: str = Query(default="semanas", pattern="^(semanas|meses)$"),
):
    pool = await db.get_pool()
    hoy = date.today()

    feriados = await queries.traer_feriados(pool)
    ventanas = ventanas_comparables(hoy, feriados)

    # ── Traer casos cerrados de las 3 ventanas comparables ──
    cerrados_actual = await queries.casos_cerrados_en_rango(
        pool, ventanas["actual"]["desde"], ventanas["actual"]["hasta"])
    cerrados_mes_ant_comp = await queries.casos_cerrados_en_rango(
        pool, ventanas["mes_anterior_comparable"]["desde"], ventanas["mes_anterior_comparable"]["hasta"])
    cerrados_mes_ant_cerrado = await queries.casos_cerrados_en_rango(
        pool, ventanas["mes_anterior_cerrado"]["desde"], ventanas["mes_anterior_cerrado"]["hasta"])

    # Año anterior: primero intenta casos_historicos, si no hay usa `casos` (por si ya tiene antigüedad)
    cerrados_anio_ant_comp = await queries.casos_historicos_en_rango(
        pool, ventanas["anio_anterior_comparable"]["desde"], ventanas["anio_anterior_comparable"]["hasta"])
    if not cerrados_anio_ant_comp:
        cerrados_anio_ant_comp = await queries.casos_cerrados_en_rango(
            pool, ventanas["anio_anterior_comparable"]["desde"], ventanas["anio_anterior_comparable"]["hasta"])
    cerrados_anio_ant_cerrado = await queries.casos_historicos_en_rango(
        pool, ventanas["anio_anterior_cerrado"]["desde"], ventanas["anio_anterior_cerrado"]["hasta"])
    if not cerrados_anio_ant_cerrado:
        cerrados_anio_ant_cerrado = await queries.casos_cerrados_en_rango(
            pool, ventanas["anio_anterior_cerrado"]["desde"], ventanas["anio_anterior_cerrado"]["hasta"])

    tecnicos = await queries.usuarios_tecnicos(pool)
    if equipo:
        tecnicos_filtrados = [t for t in tecnicos if t.get("empresa_codigo") == equipo]
        cerrados_actual_f = [c for c in cerrados_actual if c.get("empresa_id") == equipo]
    else:
        tecnicos_filtrados = tecnicos
        cerrados_actual_f = cerrados_actual

    n_tecnicos_activos = sum(1 for t in tecnicos_filtrados if t.get("activo", True))
    n_habiles_mes = ventanas["dias_habiles_transcurridos"]

    # ── Bloque 1: KPIs por proceso ──
    bloque1 = analytics.kpis_por_proceso(
        cerrados_actual_f, cerrados_mes_ant_comp, cerrados_mes_ant_cerrado,
        cerrados_anio_ant_comp, cerrados_anio_ant_cerrado,
        feriados, n_tecnicos_activos, n_habiles_mes,
    )

    # ── Bloque 2: SLA con responsables (siempre global, no filtra por equipo
    #    porque necesita comparar equipos entre sí) ──
    bloque2 = analytics.sla_con_responsables(cerrados_actual, feriados)

    # ── Bloque 3: Tendencia ──
    hasta_tendencia = hoy - date.resolution * 0  # hoy
    desde_tendencia = hoy.replace(day=1) if vista_tendencia == "meses" else hoy
    rango_dias = 400 if vista_tendencia == "meses" else 60
    cerrados_tendencia = await queries.casos_cerrados_en_rango(
        pool, date.fromordinal(hoy.toordinal() - rango_dias), hoy)
    if equipo:
        cerrados_tendencia = [c for c in cerrados_tendencia if c.get("empresa_id") == equipo]
    bloque3 = analytics.tendencia_sla(cerrados_tendencia, hoy, vista_tendencia)

    # ── Bloque 4: Ranking por equipo ──
    bloque4 = analytics.ranking_por_equipo(cerrados_actual, tecnicos, n_habiles_mes)

    # ── Bloque 5: Demanda por zona (90 días) ──
    desde_90 = date.fromordinal(hoy.toordinal() - 90)
    casos_90d = await queries.casos_en_rango(pool, desde_90, hoy)
    if equipo:
        casos_90d = [c for c in casos_90d if c.get("empresa_id") == equipo]
    meta_por_depto = await queries.meta_productividad(pool)
    dias_habiles_90d = dias_habiles_transcurridos(desde_90, hoy, feriados)
    bloque5 = analytics.demanda_por_zona(casos_90d, tecnicos, meta_por_depto, dias_habiles_90d)

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
    }
