"""
Lógica de negocio de la Radiografía Operativa (Bloque A).

IMPORTANTE — supuesto documentado sobre "resuelto en primera visita":
hoy el modelo de datos no tiene un campo explícito para esto. Se aproxima
contando eventos "ASIGNACION" en el historial del caso: si hay más de uno,
se interpreta que el caso necesitó reasignación/reapertura y por lo tanto
NO se resolvió en la primera visita. Es una aproximación razonable con los
datos actuales; se puede reemplazar por un campo explícito el día que se
agregue al modelo (ej. marcar reapertura al reabrir un caso finalizado).

IMPORTANTE — datos históricos: hasta que el usuario cargue los casos
reales de 2 años anteriores (tabla `casos_historicos`), las comparaciones
interanuales usan una SIMULACIÓN determinística basada en el valor actual
con una variación leve. Todo campo simulado se marca con "simulado": true
en la respuesta para que nunca se confunda con un dato real.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from collections import defaultdict
import hashlib
import pandas as pd

from .fechas import ventanas_comparables, dias_habiles_transcurridos, es_habil
from .zonas import zona_de_caso

TIPOS_PROCESO = ["INSTALACION", "SERVICIO_TECNICO", "RETIRO", "VISITA_PROACTIVA"]
SLA_DEFAULT = {"INSTALACION": 3, "SERVICIO_TECNICO": 2, "RETIRO": 5, "VISITA_PROACTIVA": 7}


# ─── Helpers ──────────────────────────────────────────────────────

def _resuelto_primera_visita(historial) -> bool:
    if not historial:
        return True
    try:
        eventos = historial if isinstance(historial, list) else []
        asignaciones = [e for e in eventos if e.get("tipo") == "ASIGNACION"]
        return len(asignaciones) <= 1
    except Exception:
        return True


def _tiempo_resolucion_dias_habiles(caso: dict, feriados: set[date]) -> float | None:
    creado = caso.get("created_at")
    cerrado = caso.get("updated_at")
    if not creado or not cerrado:
        return None
    d_ini = creado.date() if isinstance(creado, datetime) else creado
    d_fin = cerrado.date() if isinstance(cerrado, datetime) else cerrado
    if d_fin < d_ini:
        return None
    return float(dias_habiles_transcurridos(d_ini, d_fin, feriados))


def _sla_cumplido(caso: dict) -> bool | None:
    deadline = caso.get("sla_deadline")
    cerrado = caso.get("updated_at")
    if not deadline or not cerrado:
        return None
    return cerrado <= deadline


def _seed_deterministico(*partes: str) -> float:
    """Genera un factor 0.85-1.15 estable a partir de un string, para
    simular datos históricos de forma consistente entre llamadas
    (mismo input -> mismo resultado simulado, no random real)."""
    h = hashlib.md5("|".join(partes).encode()).hexdigest()
    n = int(h[:8], 16) / 0xFFFFFFFF  # 0..1
    return 0.85 + n * 0.30  # 0.85..1.15


def _df_casos(casos: list[dict]) -> pd.DataFrame:
    if not casos:
        return pd.DataFrame(columns=["id", "tipo_proceso", "empresa_id", "departamento",
                                      "localidad", "tecnico_id", "created_at", "updated_at",
                                      "sla_deadline", "historial"])
    return pd.DataFrame(casos)


# ─── 1. KPIs por proceso ──────────────────────────────────────────

def _dias_trabajados(casos: list[dict]) -> int:
    """Cuenta días-técnico DISTINTOS trabajados: un par (técnico, fecha)
    cuenta una sola vez, y solo si ese técnico cerró al menos un caso ese
    día. Si un técnico no cerró ningún caso un día dado, ese día no se le
    computa — a diferencia de asumir que todos trabajan todos los días
    hábiles del mes."""
    pares = set()
    for c in casos:
        tid = c.get("tecnico_id")
        upd = c.get("updated_at")
        if not tid or not upd:
            continue
        d = upd.date() if isinstance(upd, datetime) else upd
        pares.add((tid, d))
    return len(pares)


def kpis_por_proceso(
    cerrados_actual: list[dict],
    cerrados_mes_ant_comp: list[dict],
    cerrados_mes_ant_cerrado: list[dict],
    cerrados_anio_ant_comp: list[dict],
    cerrados_anio_ant_cerrado: list[dict],
    feriados: set[date],
    n_tecnicos_activos: int,
    dias_habiles_transcurridos_mes: int,
) -> dict:
    def promedio_dias(casos, tipo=None):
        vals = []
        for c in casos:
            if tipo and c.get("tipo_proceso") != tipo:
                continue
            v = _tiempo_resolucion_dias_habiles(c, feriados)
            if v is not None:
                vals.append(v)
        return (sum(vals) / len(vals)) if vals else None

    def pct_primera_visita(casos, tipo=None):
        filtrados = [c for c in casos if not tipo or c.get("tipo_proceso") == tipo]
        if not filtrados:
            return None
        ok = sum(1 for c in filtrados if _resuelto_primera_visita(c.get("historial")))
        return round(100 * ok / len(filtrados), 1)

    resultado = {}
    for tipo in TIPOS_PROCESO:
        actual = promedio_dias(cerrados_actual, tipo)
        mes_ant = promedio_dias(cerrados_mes_ant_comp, tipo)
        mes_ant_cerrado = promedio_dias(cerrados_mes_ant_cerrado, tipo)
        anio_ant = promedio_dias(cerrados_anio_ant_comp, tipo)
        anio_ant_cerrado = promedio_dias(cerrados_anio_ant_cerrado, tipo)

        simulado_mes = False
        simulado_anio = False
        if actual is not None:
            if mes_ant is None:
                factor = _seed_deterministico("mes_ant", tipo)
                mes_ant = round(actual * factor, 1)
                mes_ant_cerrado = mes_ant
                simulado_mes = True
            if anio_ant is None:
                factor = _seed_deterministico("anio_ant", tipo)
                anio_ant = round(actual * factor, 1)
                anio_ant_cerrado = anio_ant
                simulado_anio = True

        pp_mes = round((mes_ant - actual), 1) if actual is not None and mes_ant is not None else None
        pp_anio = round((anio_ant - actual), 1) if actual is not None and anio_ant is not None else None

        # Riesgo: proyección lineal de cierre de mes al ritmo actual
        riesgo = None
        if actual is not None and dias_habiles_transcurridos_mes > 0:
            proyectado = actual  # el promedio ya es representativo, se proyecta igual
            riesgo_mes = mes_ant_cerrado is not None and proyectado > mes_ant_cerrado
            riesgo_anio = anio_ant_cerrado is not None and proyectado > anio_ant_cerrado
            if riesgo_mes or riesgo_anio:
                objetivos = []
                if riesgo_mes:
                    objetivos.append(f"mes anterior ({mes_ant_cerrado:.1f}d)")
                if riesgo_anio:
                    objetivos.append(f"mismo mes año anterior ({anio_ant_cerrado:.1f}d)")
                riesgo = "⚠ riesgo: no superaría " + " ni ".join(objetivos)
            else:
                riesgo = "✓ en curso para superar ambos períodos"

        resultado[tipo] = {
            "tiempo_resolucion_dias": actual,
            "vs_mes_anterior_pp": pp_mes,
            "vs_anio_anterior_pp": pp_anio,
            "mes_anterior_simulado": simulado_mes,
            "anio_anterior_simulado": simulado_anio,
            "riesgo": riesgo,
            "primera_visita_pct": pct_primera_visita(cerrados_actual, tipo),
            "sla_objetivo_dias": SLA_DEFAULT.get(tipo),
        }

    # Casos/técnico/día — agregado de todos los procesos, usando días-técnico
    # REALMENTE trabajados como denominador (no días hábiles del calendario
    # asumiendo asistencia perfecta).
    total_cerrados = len(cerrados_actual)
    dias_trab_actual = _dias_trabajados(cerrados_actual)
    casos_tecnico_dia = round(total_cerrados / dias_trab_actual, 1) if dias_trab_actual else None

    total_mes_ant = len(cerrados_mes_ant_comp) or None
    total_anio_ant = len(cerrados_anio_ant_comp) or None
    dias_trab_mes_ant = _dias_trabajados(cerrados_mes_ant_comp)
    dias_trab_anio_ant = _dias_trabajados(cerrados_anio_ant_comp)
    ctd_mes_ant = round(total_mes_ant / dias_trab_mes_ant, 1) if total_mes_ant and dias_trab_mes_ant else None
    ctd_anio_ant = round(total_anio_ant / dias_trab_anio_ant, 1) if total_anio_ant and dias_trab_anio_ant else None

    resultado["_agregado"] = {
        "casos_tecnico_dia": casos_tecnico_dia,
        "vs_mes_anterior": round(casos_tecnico_dia - ctd_mes_ant, 1) if casos_tecnico_dia and ctd_mes_ant else None,
        "vs_anio_anterior": round(casos_tecnico_dia - ctd_anio_ant, 1) if casos_tecnico_dia and ctd_anio_ant else None,
    }
    return resultado


# ─── 2. SLA con responsables y meta correctiva ────────────────────

def sla_con_responsables(cerrados_actual: list[dict], feriados: set[date]) -> dict:
    resultado = {}
    for tipo in TIPOS_PROCESO:
        objetivo = SLA_DEFAULT.get(tipo, 3)
        del_tipo = [c for c in cerrados_actual if c.get("tipo_proceso") == tipo]
        if not del_tipo:
            resultado[tipo] = {"promedio_dias": None, "sla_objetivo_dias": objetivo,
                                "en_riesgo": False, "responsables": []}
            continue

        por_empresa = defaultdict(list)
        for c in del_tipo:
            v = _tiempo_resolucion_dias_habiles(c, feriados)
            if v is not None:
                por_empresa[c.get("empresa_id") or "Sin empresa"].append(v)

        todos_los_dias = [v for vals in por_empresa.values() for v in vals]
        if not todos_los_dias:
            resultado[tipo] = {"promedio_dias": None, "sla_objetivo_dias": objetivo,
                                "en_riesgo": False, "responsables": []}
            continue

        promedio_global = sum(todos_los_dias) / len(todos_los_dias)
        n_total = len(todos_los_dias)
        en_riesgo = promedio_global >= objetivo * 0.9  # zona de riesgo desde 90% del SLA

        responsables = []
        if promedio_global > objetivo:
            # Empresas cuyo promedio individual supera el objetivo, ordenadas
            # por cuánto "empujan" el promedio general hacia arriba.
            candidatos = []
            for emp, vals in por_empresa.items():
                prom_emp = sum(vals) / len(vals)
                if prom_emp > objetivo:
                    exceso_total = (prom_emp - objetivo) * len(vals)
                    candidatos.append((emp, prom_emp, len(vals), exceso_total))
            candidatos.sort(key=lambda x: -x[3])

            for emp, prom_emp, n_emp, _ in candidatos:
                # Meta correctiva: manteniendo fijo el resto de la muestra,
                # ¿a qué promedio debería bajar esta empresa para que el
                # promedio GLOBAL quede exactamente en el objetivo?
                suma_otros = sum(v for e, vals in por_empresa.items() if e != emp for v in vals)
                n_otros = n_total - n_emp
                meta = (objetivo * n_total - suma_otros) / n_emp if n_emp else None
                responsables.append({
                    "empresa": emp,
                    "promedio_dias": round(prom_emp, 1),
                    "casos": n_emp,
                    "meta_correctiva_dias": round(max(meta, 0), 1) if meta is not None else None,
                })

        resultado[tipo] = {
            "promedio_dias": round(promedio_global, 1),
            "sla_objetivo_dias": objetivo,
            "en_riesgo": en_riesgo,
            "responsables": responsables,
        }
    return resultado


# ─── 3. Tendencia de cumplimiento SLA ──────────────────────────────

def tendencia_sla(cerrados: list[dict], hoy: date, vista: str = "semanas") -> list[dict]:
    """vista: 'semanas' (últimas 8) o 'meses' (últimos 12)."""
    puntos = []
    if vista == "meses":
        for i in range(11, -1, -1):
            ref = (hoy.replace(day=1) - timedelta(days=1))
            # retrocede i meses desde el mes actual
            y, m = hoy.year, hoy.month
            total_meses = (y * 12 + (m - 1)) - i
            y2, m2 = divmod(total_meses, 12)
            m2 += 1
            del_periodo = [c for c in cerrados
                            if c.get("updated_at") and c["updated_at"].year == y2 and c["updated_at"].month == m2]
            puntos.append(_punto_sla(f"{y2}-{m2:02d}", del_periodo))
    else:
        for i in range(7, -1, -1):
            fin = hoy - timedelta(days=7 * i)
            ini = fin - timedelta(days=6)
            del_periodo = [c for c in cerrados
                            if c.get("updated_at") and ini <= c["updated_at"].date() <= fin]
            puntos.append(_punto_sla(f"{ini.isoformat()}", del_periodo))
    return puntos


def _punto_sla(etiqueta: str, casos: list[dict]) -> dict:
    if not casos:
        return {"periodo": etiqueta, "sla_cumplido_pct": None, "casos": 0}
    cumplidos = sum(1 for c in casos if _sla_cumplido(c))
    return {"periodo": etiqueta, "sla_cumplido_pct": round(100 * cumplidos / len(casos), 1), "casos": len(casos)}


# ─── 4. Ranking de técnicos dentro de cada equipo ──────────────────

def ranking_por_equipo(cerrados_actual: list[dict], tecnicos: list[dict],
                        dias_habiles_transcurridos_mes: int) -> dict:
    por_tecnico = defaultdict(list)
    for c in cerrados_actual:
        tid = c.get("tecnico_id")
        if tid:
            por_tecnico[tid].append(c)

    por_equipo = defaultdict(list)
    for t in tecnicos:
        tid = t.get("auth_id") or t.get("id")
        casos_t = por_tecnico.get(tid, [])
        n = len(casos_t)
        dias_trab_t = _dias_trabajados(casos_t)
        casos_dia = round(n / dias_trab_t, 1) if dias_trab_t else 0
        primera_visita = (
            round(100 * sum(1 for c in casos_t if _resuelto_primera_visita(c.get("historial"))) / n, 1)
            if n else 0
        )
        sla_ok = (
            round(100 * sum(1 for c in casos_t if _sla_cumplido(c)) / n, 1)
            if n else 0
        )
        por_equipo[t.get("empresa_codigo") or "Sin equipo"].append({
            "id": tid, "nombre": f"{t.get('nombre','')} {t.get('apellido','')}".strip(),
            "casos_dia": casos_dia, "primera_visita_pct": primera_visita, "sla_pct": sla_ok,
            "casos_totales": n,
        })

    resultado = {}
    for equipo, lista in por_equipo.items():
        if not lista:
            continue
        max_cd = max((t["casos_dia"] for t in lista), default=0) or 1
        for t in lista:
            score = (
                (t["casos_dia"] / max_cd) * 100 * 0.34
                + t["primera_visita_pct"] * 0.33
                + t["sla_pct"] * 0.33
            )
            t["score"] = round(score, 1)
        lista.sort(key=lambda t: -t["score"])
        resultado[equipo] = lista
    return resultado



# ─── 6. Reincidencia por terminal ───────────────────────────────
# Indicador líder (no de tiempo/SLA como los anteriores): detecta
# terminales que generan múltiples Servicios Técnicos en poco tiempo.
# A diferencia de los bloques 1-5 (que miden qué tan rápido se resuelve),
# este mide si se está resolviendo la CAUSA o solo el SÍNTOMA — un patrón
# de reincidencia suele señalar equipo defectuoso, falta de capacitación
# del cliente, o diagnóstico apurado en la visita anterior.

def reincidencia_terminales(casos_ventana: list[dict], hoy: date, dias_ventana: int = 30,
                             umbral: int = 2) -> list[dict]:
    """casos_ventana: casos de SERVICIO_TECNICO creados en los últimos
    `dias_ventana` días (típicamente un subconjunto de los últimos 90 días
    ya traídos para el bloque 5, filtrado acá mismo). umbral: cantidad
    mínima de ST para considerarse reincidente (default 2 = "2 o más")."""
    desde = date.fromordinal(hoy.toordinal() - dias_ventana)
    por_terminal = defaultdict(list)

    for c in casos_ventana:
        if c.get("tipo_proceso") != "SERVICIO_TECNICO":
            continue
        serie = c.get("numero_serie")
        if not serie:
            continue
        creado = c.get("created_at")
        fecha_creado = creado.date() if isinstance(creado, datetime) else creado
        if fecha_creado is None or fecha_creado < desde:
            continue
        por_terminal[serie].append(c)

    resultado = []
    for serie, casos in por_terminal.items():
        if len(casos) < umbral:
            continue
        casos_ordenados = sorted(casos, key=lambda c: c.get("created_at") or datetime.min)
        ultimo = casos_ordenados[-1]
        resultado.append({
            "numero_serie": serie,
            "razon_social": ultimo.get("razon_social") or "Sin nombre",
            "empresa": ultimo.get("empresa_id"),
            "cantidad_st": len(casos),
            "fechas": [
                (c["created_at"].date() if isinstance(c.get("created_at"), datetime) else c.get("created_at")).isoformat()
                for c in casos_ordenados if c.get("created_at")
            ],
        })

    resultado.sort(key=lambda r: -r["cantidad_st"])
    return resultado[:20]


# ─── 5. Demanda por zona × tipo de proceso (90 días) ───────────────

def demanda_por_zona(
    casos_90d: list[dict], tecnicos: list[dict], meta_por_depto: dict[str, int],
    dias_habiles_90d: int,
) -> list[dict]:
    por_zona = defaultdict(lambda: defaultdict(int))
    for c in casos_90d:
        zona = zona_de_caso(c.get("departamento"), c.get("localidad"))
        tipo = c.get("tipo_proceso") or "OTRO"
        por_zona[zona][tipo] += 1

    tecnicos_por_zona = defaultdict(int)
    for t in tecnicos:
        if not t.get("activo", True):
            continue
        for zona in (t.get("departamentos") or []):
            tecnicos_por_zona[zona] += 1

    resultado = []
    for zona, tipos in sorted(por_zona.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(tipos.values())
        n_tecnicos = tecnicos_por_zona.get(zona, 0)
        meta = meta_por_depto.get(zona, meta_por_depto.get("_default", 20))
        capacidad_diaria = n_tecnicos * meta
        demanda_diaria = round(total / dias_habiles_90d, 2) if dias_habiles_90d else 0
        cobertura_pct = round(100 * capacidad_diaria / demanda_diaria, 1) if demanda_diaria else None

        resultado.append({
            "zona": zona,
            "total_casos": total,
            "por_tipo": tipos,
            "tecnicos_asignados": n_tecnicos,
            "meta_casos_dia": meta,
            "capacidad_diaria": capacidad_diaria,
            "demanda_diaria": demanda_diaria,
            "cobertura_pct": cobertura_pct,
        })
    return resultado
