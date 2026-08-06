"""
Bloque C — Fase 2: proyección real con Prophet.

Un modelo por cada uno de los 4 tipos de proceso + uno agregado
(TOTAL), sobre series semanales de demanda (casos CREADOS por
semana — no cierres, para capturar la demanda real independiente
de cuánto tarde en resolverse). El calendario de eventos comerciales
se pasa como "holidays" de Prophet, así el modelo aprende a anticipar
los picos alrededor de esas fechas en vez de tratarlas como ruido.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from collections import defaultdict
import warnings
import pandas as pd

warnings.filterwarnings("ignore")  # Prophet es ruidoso con warnings de cmdstanpy, no son errores

TIPOS_PROCESO = ["INSTALACION", "SERVICIO_TECNICO", "RETIRO", "VISITA_PROACTIVA"]
HORIZONTE_SEMANAS_DEFAULT = 8
SEMANAS_HISTORICO_MINIMAS = 8  # menos que esto, ni se intenta ajustar Prophet


def _semana_iso_lunes(d: date) -> date:
    """Devuelve el lunes de la semana ISO a la que pertenece la fecha."""
    return d - timedelta(days=d.weekday())


def _armar_serie_semanal(casos: list[dict]) -> pd.DataFrame:
    conteo = defaultdict(int)
    for c in casos:
        creado = c.get("created_at")
        if not creado:
            continue
        d = creado.date() if isinstance(creado, datetime) else creado
        semana = _semana_iso_lunes(d)
        conteo[semana] += 1
    if not conteo:
        return pd.DataFrame(columns=["ds", "y"])
    filas = sorted(conteo.items())
    df = pd.DataFrame({"ds": [f[0] for f in filas], "y": [f[1] for f in filas]})
    df["ds"] = pd.to_datetime(df["ds"])  # asegura datetime64, no objetos date crudos
    return df


def _armar_holidays(eventos: list[dict]) -> pd.DataFrame | None:
    if not eventos:
        return None
    filas = []
    for e in eventos:
        try:
            fecha = e["fecha"]
            filas.append({
                "holiday": e.get("nombre", "evento"),
                "ds": fecha,
                "lower_window": -(e.get("dias_influencia_antes") or 0),
                "upper_window": (e.get("dias_influencia_despues") or 0),
            })
        except Exception:
            continue
    if not filas:
        return None
    return pd.DataFrame(filas)


def _confianza_por_semanas(n_semanas: int) -> str:
    if n_semanas >= 40: return "alta"
    if n_semanas >= 20: return "media"
    return "baja"


def _ajustar_y_proyectar(serie: pd.DataFrame, holidays: pd.DataFrame | None,
                          horizonte_semanas: int) -> dict | None:
    n = len(serie)
    if n < SEMANAS_HISTORICO_MINIMAS:
        return None

    from prophet import Prophet
    modelo = Prophet(
        weekly_seasonality=False,
        daily_seasonality=False,
        yearly_seasonality=(n >= 52),  # necesita al menos ~1 año para estimarla bien
        holidays=holidays,
        interval_width=0.8,
    )
    modelo.fit(serie)

    futuro = modelo.make_future_dataframe(periods=horizonte_semanas, freq="W-MON")
    forecast = modelo.predict(futuro)

    n_historico_mostrar = min(12, n)
    hist_pts = [
        {"semana": row.ds.date().isoformat(), "valor": int(row.y)}
        for row in serie.tail(n_historico_mostrar).itertuples()
    ]
    proy_pts = []
    for row in forecast.tail(horizonte_semanas).itertuples():
        proy_pts.append({
            "semana": row.ds.date().isoformat(),
            "valor": max(round(row.yhat), 0),
            "min": max(round(row.yhat_lower), 0),
            "max": max(round(row.yhat_upper), 0),
        })

    return {
        "disponible": True,
        "semanas_historico": n,
        "confianza": _confianza_por_semanas(n),
        "historico": hist_pts,
        "proyeccion": proy_pts,
    }


def pronosticar(todos_los_casos: list[dict], eventos: list[dict],
                 horizonte_semanas: int = HORIZONTE_SEMANAS_DEFAULT) -> dict:
    """Devuelve el forecast por cada tipo de proceso + el total agregado."""
    holidays = _armar_holidays(eventos)
    resultado = {}

    # Total agregado
    serie_total = _armar_serie_semanal(todos_los_casos)
    r_total = _ajustar_y_proyectar(serie_total, holidays, horizonte_semanas)
    resultado["TOTAL"] = r_total or {"disponible": False, "motivo": "Historia insuficiente"}

    # Por tipo de proceso
    for tipo in TIPOS_PROCESO:
        casos_tipo = [c for c in todos_los_casos if c.get("tipo_proceso") == tipo]
        serie = _armar_serie_semanal(casos_tipo)
        r = _ajustar_y_proyectar(serie, holidays, horizonte_semanas)
        resultado[tipo] = r or {"disponible": False, "motivo": "Historia insuficiente para este proceso"}

    return resultado
