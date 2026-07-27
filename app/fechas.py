"""
Cálculo de ventanas de fecha COMPARABLES entre sí.

Regla de negocio acordada: cuando comparamos "lo que va del mes actual"
contra el mes anterior o el mismo mes del año anterior, NO comparamos por
fecha calendario (ej: 1-26 de julio vs 1-26 de junio), sino por CANTIDAD
DE DÍAS HÁBILES transcurridos. Así un feriado o un fin de semana distinto
entre los dos meses no distorsiona la comparación.

Ejemplo: si julio lleva 19 días hábiles transcurridos, se toman los
primeros 19 días hábiles de junio — sin importar a qué fecha calendario
de junio eso corresponda.
"""
from datetime import date, timedelta
from calendar import monthrange


def es_habil(d: date, feriados: set[date]) -> bool:
    return d.weekday() < 5 and d not in feriados


def primer_dia_mes(d: date) -> date:
    return d.replace(day=1)


def dias_habiles_transcurridos(desde: date, hasta: date, feriados: set[date]) -> int:
    """Cuenta días hábiles en [desde, hasta] inclusive."""
    if hasta < desde:
        return 0
    total = 0
    cur = desde
    while cur <= hasta:
        if es_habil(cur, feriados):
            total += 1
        cur += timedelta(days=1)
    return total


def primeros_n_habiles(desde: date, n: int, feriados: set[date]) -> tuple[date, date]:
    """Devuelve (fecha_inicio, fecha_fin) cubriendo los primeros n días hábiles
    a partir de 'desde' (inclusive). fecha_fin es el último día hábil #n."""
    if n <= 0:
        return desde, desde
    cur = desde
    contados = 0
    ultimo = desde
    while contados < n:
        if es_habil(cur, feriados):
            contados += 1
            ultimo = cur
        if contados == n:
            break
        cur += timedelta(days=1)
    return desde, ultimo


def mes_anterior(d: date) -> date:
    primero = primer_dia_mes(d)
    return primero - timedelta(days=1)  # último día del mes anterior


def ventanas_comparables(hoy: date, feriados: set[date]) -> dict:
    """
    Devuelve las 3 ventanas de fecha para comparar:
      - actual: 1º del mes en curso -> hoy
      - mes_anterior: primeros N días hábiles del mes anterior (N = hábiles
        transcurridos en el mes actual)
      - anio_anterior: primeros N días hábiles del mismo mes, año anterior
      - cierre_mes_anterior: el mes anterior completo (para el chequeo de riesgo)
      - cierre_anio_anterior: el mismo mes completo del año anterior (ídem)
    """
    inicio_actual = primer_dia_mes(hoy)
    n_habiles = dias_habiles_transcurridos(inicio_actual, hoy, feriados)

    # Mes anterior completo
    fin_mes_ant = mes_anterior(hoy)
    inicio_mes_ant = primer_dia_mes(fin_mes_ant)
    _, fin_comparable_mes_ant = primeros_n_habiles(inicio_mes_ant, n_habiles, feriados)

    # Mismo mes, año anterior
    anio_ant = hoy.year - 1
    mes_ant_num = hoy.month
    inicio_anio_ant = date(anio_ant, mes_ant_num, 1)
    ultimo_dia_anio_ant = monthrange(anio_ant, mes_ant_num)[1]
    fin_mes_anio_ant = date(anio_ant, mes_ant_num, ultimo_dia_anio_ant)
    _, fin_comparable_anio_ant = primeros_n_habiles(inicio_anio_ant, n_habiles, feriados)

    return {
        "actual": {"desde": inicio_actual, "hasta": hoy},
        "dias_habiles_transcurridos": n_habiles,
        "mes_anterior_comparable": {"desde": inicio_mes_ant, "hasta": fin_comparable_mes_ant},
        "mes_anterior_cerrado": {"desde": inicio_mes_ant, "hasta": fin_mes_ant},
        "anio_anterior_comparable": {"desde": inicio_anio_ant, "hasta": fin_comparable_anio_ant},
        "anio_anterior_cerrado": {"desde": inicio_anio_ant, "hasta": fin_mes_anio_ant},
    }
