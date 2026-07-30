"""
Programación semanal del análisis de comentarios: un tipo de proceso
por día, lunes a jueves, 3am hora Uruguay (UTC-3 todo el año, sin
horario de verano). Se reparte en 4 días distintos —no los 4 juntos—
para mantener cada corrida chica y el consumo de la API bajo control.
"""
import logging
from datetime import date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import db, queries, gemini

logger = logging.getLogger("boolean-analytics.scheduler")

# UTC-3 fijo (Uruguay no usa horario de verano) → 3am UY = 6am UTC
HORA_UTC = 6
CRONOGRAMA = {
    "mon": "SERVICIO_TECNICO",
    "tue": "INSTALACION",
    "wed": "RETIRO",
    "thu": "VISITA_PROACTIVA",
}


async def ejecutar_analisis(tipo_proceso: str):
    logger.info(f"Iniciando análisis semanal de comentarios: {tipo_proceso}")
    try:
        client = await db.get_client()
        hoy = date.today()
        desde = hoy - timedelta(days=7)
        casos = await queries.casos_con_comentarios(client, tipo_proceso, desde, hoy)
        resultado = await gemini.analizar_comentarios(client, tipo_proceso, casos)
        await queries.guardar_analisis_comentarios(
            client, tipo_proceso, resultado.get("problemas", []), len(casos)
        )
        logger.info(f"Análisis de {tipo_proceso} guardado — {len(casos)} casos evaluados, "
                     f"{len(resultado.get('problemas', []))} problemas detectados")
    except Exception:
        logger.exception(f"Error ejecutando análisis semanal de {tipo_proceso}")


def iniciar_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    for dia, tipo in CRONOGRAMA.items():
        scheduler.add_job(
            ejecutar_analisis, args=[tipo],
            trigger=CronTrigger(day_of_week=dia, hour=HORA_UTC, minute=0),
            id=f"analisis_{tipo}", replace_existing=True,
        )
    scheduler.start()
    logger.info("Scheduler de análisis semanal iniciado: " + ", ".join(
        f"{d}={t}" for d, t in CRONOGRAMA.items()
    ))
    return scheduler
