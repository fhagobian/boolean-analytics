import os
import asyncio
import logging
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Header, Query, Request
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import httpx

from . import db, queries, analytics, gemini, proactivo, nps, prophet_forecast, scheduler as scheduler_mod
from .fechas import ventanas_comparables, dias_habiles_transcurridos

logger = logging.getLogger("boolean-analytics")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.get_client()
    sched = scheduler_mod.iniciar_scheduler()
    yield
    sched.shutdown(wait=False)
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


@app.post("/analisis-comentarios/ejecutar")
async def ejecutar_analisis_manual(
    tipo_proceso: str = Query(..., pattern="^(INSTALACION|SERVICIO_TECNICO|RETIRO|VISITA_PROACTIVA)$"),
    desde: date | None = Query(default=None, description="Opcional — analiza esta ventana en vez de 'últimos 7 días desde hoy'"),
    hasta: date | None = Query(default=None),
    _auth: bool = Depends(verificar_token),
):
    """Dispara el análisis de comentarios manualmente. Sin desde/hasta,
    analiza los últimos 7 días desde hoy (igual que el cron automático).
    Con desde/hasta, analiza esa ventana específica — útil para correr
    el análisis sobre una semana real ya ocurrida en vez de esperar."""
    await scheduler_mod.ejecutar_analisis(tipo_proceso, desde, hasta)
    return {"status": "ok", "tipo_proceso": tipo_proceso, "desde": str(desde), "hasta": str(hasta)}


@app.get("/alertas")
async def alertas(
    _auth: bool = Depends(verificar_token),
    equipo: str | None = Query(default=None, description="Filtrar por código de equipo (Regional)"),
):
    """Bloque B — Análisis Proactivo. Corre a demanda (el frontend lo
    consulta al abrir la pestaña, y también una vez al día para el
    resumen del ticker). Liviano — solo aritmética sobre datos ya
    existentes, sin costo de IA."""
    try:
        client = await db.get_client()
        hoy = date.today()

        # 7 semanas de casos cerrados (6 de referencia + la actual)
        cerrados_7sem, tecnicos, creados_14d, cerrados_30d = await asyncio.gather(
            queries.casos_cerrados_en_rango(client, hoy - timedelta(weeks=7), hoy),
            queries.usuarios_tecnicos(client),
            queries.casos_en_rango(client, hoy - timedelta(days=14), hoy),
            queries.casos_cerrados_en_rango(client, hoy - timedelta(days=30), hoy),
        )

        if equipo:
            tecnicos = [t for t in tecnicos if t.get("empresa_codigo") == equipo]
            cerrados_7sem = [c for c in cerrados_7sem if c.get("empresa_id") == equipo]
            cerrados_30d = [c for c in cerrados_30d if c.get("empresa_id") == equipo]
            # B2 (clusters) es geográfico, no tiene dueño de equipo — se
            # deja sin filtrar y se oculta en el resumen para Regional

        b1 = proactivo.desvio_individual(cerrados_7sem, tecnicos, hoy)
        b2 = proactivo.clusters_geograficos(creados_14d, hoy)
        b3 = proactivo.casos_outlier(cerrados_30d)
        resumen_ticker = proactivo.resumen_para_ticker(b1, b2, b3, equipo=equipo)

        return {
            "generado_en": datetime.utcnow().isoformat() + "Z",
            "desvio_individual": b1,
            "clusters_geograficos": b2 if not equipo else [],
            "casos_outlier": b3,
            "resumen_ticker": resumen_ticker,
        }
    except Exception as e:
        logger.exception("Error calculando alertas del Bloque B")
        raise HTTPException(500, f"Error interno: {e}")


class EnviarNPSBody(BaseModel):
    caso_id: str
    tecnico_id: str
    telefono: str


@app.post("/encuestas-nps/enviar")
async def encuestas_nps_enviar(body: EnviarNPSBody, _auth: bool = Depends(verificar_token)):
    """Llamado por el frontend cuando el técnico carga un teléfono al
    finalizar un caso exitoso (dentro del muestreo del Director).
    Valida antifraude, manda el WhatsApp, y registra el intento."""
    client = await db.get_client()
    telefono = nps.normalizar_telefono(body.telefono)

    # Leer ventana antifraude configurada (default 60 días si no hay fila)
    dias_ventana = 60
    try:
        r = await client.get("/config_encuestas_nps", params=[("select", "dias_antifraude_mismo_telefono"), ("limit", "1")])
        cfg = r.json()
        if cfg:
            dias_ventana = cfg[0].get("dias_antifraude_mismo_telefono") or 60
    except Exception:
        pass

    if await nps.telefono_bloqueado_antifraude(client, telefono, dias_ventana):
        await client.post("/encuestas_nps", json={
            "caso_id": body.caso_id, "tecnico_id": body.tecnico_id,
            "telefono": telefono, "estado": "bloqueada_antifraude",
        }, headers={"Prefer": "return=minimal"})
        raise HTTPException(409, "Este número ya recibió una encuesta recientemente — no se puede reutilizar.")

    try:
        resultado = await nps.enviar_whatsapp(client, telefono, nps.texto_encuesta_inicial())
        await client.post("/encuestas_nps", json={
            "caso_id": body.caso_id, "tecnico_id": body.tecnico_id,
            "telefono": telefono, "estado": "enviada",
            "twilio_sid_enviado": resultado.get("sid"),
            "enviada_at": datetime.utcnow().isoformat() + "Z",
        }, headers={"Prefer": "return=minimal"})
        return {"status": "ok", "telefono": telefono}
    except Exception as e:
        logger.exception("Error enviando encuesta NPS por WhatsApp")
        await client.post("/encuestas_nps", json={
            "caso_id": body.caso_id, "tecnico_id": body.tecnico_id,
            "telefono": telefono, "estado": "error", "error_detalle": str(e),
        }, headers={"Prefer": "return=minimal"})
        raise HTTPException(502, f"No se pudo enviar el WhatsApp: {e}")


@app.post("/webhooks/twilio-whatsapp")
async def webhook_twilio_whatsapp(request: Request):
    """Endpoint PÚBLICO — Twilio manda acá la respuesta del cliente.
    No usa el token Bearer normal (Twilio no lo conoce); en cambio se
    valida la firma X-Twilio-Signature con el Auth Token, para
    asegurarnos de que el POST realmente vino de Twilio."""
    form = await request.form()
    params = dict(form)
    firma = request.headers.get("X-Twilio-Signature", "")
    url_completa = str(request.url)

    if not nps.validar_firma_twilio(url_completa, params, firma):
        raise HTTPException(403, "Firma inválida")

    from_whatsapp = params.get("From", "").replace("whatsapp:", "")
    body_texto = params.get("Body", "")
    puntaje, comentario = nps.parsear_respuesta(body_texto)

    client = await db.get_client()
    r = await client.get("/encuestas_nps", params=[
        ("telefono", f"eq.{from_whatsapp}"),
        ("estado", "eq.enviada"),
        ("select", "id"),
        ("order", "enviada_at.desc"),
        ("limit", "1"),
    ])
    pendientes = r.json()
    if pendientes:
        encuesta_id = pendientes[0]["id"]
        await client.patch(f"/encuestas_nps?id=eq.{encuesta_id}", json={
            "estado": "respondida", "puntaje": puntaje, "comentario": comentario,
            "twilio_sid_respuesta": params.get("MessageSid"),
            "respondida_at": datetime.utcnow().isoformat() + "Z",
        }, headers={"Prefer": "return=minimal"})

    return {"status": "ok"}  # Twilio espera 200, no le importa el body


@app.get("/prediccion")
async def prediccion(
    _auth: bool = Depends(verificar_token),
    equipo: str | None = Query(default=None),
    horizonte_semanas: int = Query(default=6, ge=1, le=26),
):
    """Bloque C, Fase 2 — proyección real con Prophet. Un modelo por
    tipo de proceso + uno agregado, usando el calendario de eventos
    comerciales como señal de estacionalidad."""
    try:
        client = await db.get_client()
        hoy = date.today()
        desde = date.fromordinal(hoy.toordinal() - 600)  # cubre toda la historia cargada

        casos, eventos = await asyncio.gather(
            queries.casos_en_rango(client, desde, hoy),
            queries.calendario_eventos(client),
        )
        if equipo:
            casos = [c for c in casos if c.get("empresa_id") == equipo]

        resultado = prophet_forecast.pronosticar(casos, eventos, horizonte_semanas)
        return {
            "generado_en": datetime.utcnow().isoformat() + "Z",
            "horizonte_semanas": horizonte_semanas,
            "forecast": resultado,
        }
    except Exception as e:
        logger.exception("Error calculando predicción con Prophet")
        raise HTTPException(500, f"Error interno: {e}")


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
        bloque7 = analytics.calidad_notas_por_tecnico(cerrados_actual_f, tecnicos_filtrados)
        bloque8 = await queries.ultimos_analisis_comentarios(client)

        # ── Bloque C fase 1: tendencia histórica real (no Prophet aún,
        # pero ya no es un placeholder — usa los meses reales cargados) ──
        desde_tendencia_larga = date.fromordinal(hoy.toordinal() - 400)  # ~13 meses
        casos_para_tendencia = await queries.casos_en_rango(client, desde_tendencia_larga, hoy)
        if equipo:
            casos_para_tendencia = [c for c in casos_para_tendencia if c.get("empresa_id")==equipo]
        bloque_prediccion = analytics.tendencia_historica_mensual(casos_para_tendencia, meses_atras=12)

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
            "calidad_notas_tecnico": bloque7,
            "analisis_comentarios": bloque8,
            "tendencia_historica": bloque_prediccion,
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
