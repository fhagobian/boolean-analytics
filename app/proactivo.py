"""
Bloque B — ANÁLISIS PROACTIVO. A diferencia del Bloque A (reportes que
hay que ir a mirar), esto corre TODOS LOS DÍAS y detecta cosas que
necesitan atención sin que nadie las busque.

B1 — Desvío individual: técnico/equipo que se aparta de SU PROPIO
     promedio de las últimas 6 semanas (no comparado contra otros).
B2 — Clusters geográficos/temporales: picos anormales de un tipo de
     proceso concentrados en una zona en pocos días.
B3 — Casos outlier: casos puntuales que tardaron mucho más que el
     resto de su categoría.

Supuesto documentado: "recuperación exitosa de equipos" en RETIRO
depende de un campo (`cierre_retiro_exitoso`) que hoy NO existe en el
modelo de datos — el flujo de cierre de Retiro siempre asume éxito.
Hasta que se agregue ese campo al frontend, esa métrica específica
devuelve "sin datos suficientes" en lugar de inventar un resultado.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta
from collections import defaultdict
import statistics

from .zonas import zona_de_caso

UMBRAL_DESVIO_MODERADO = 0.35  # 35% de cambio vs el propio promedio ya se considera "moderado"
SEMANAS_VENTANA_B1 = 6
UMBRAL_CLUSTER_FACTOR = 2.5   # 2.5x lo habitual para esa zona/tipo = anómalo
UMBRAL_OUTLIER_FACTOR = 3.0   # 3x el promedio de su categoría = revisar a mano


def _semana_de(d) -> tuple:
    iso = d.isocalendar()
    return (iso[0], iso[1])  # (año, número de semana)


def _tiempo_resolucion(caso: dict) -> float | None:
    creado, cerrado = caso.get("created_at"), caso.get("updated_at")
    if not creado or not cerrado:
        return None
    delta = (cerrado - creado).total_seconds() / 86400
    return max(delta, 0)


def _contar_eventos(caso: dict, tipo_evento: str) -> int:
    hist = caso.get("historial") or []
    if not isinstance(hist, list):
        return 0
    return sum(1 for e in hist if isinstance(e, dict) and e.get("tipo") == tipo_evento)


# ─── B1 — Desvío individual ─────────────────────────────────────

def desvio_individual(casos_ult_7_semanas: list[dict], tecnicos: list[dict], hoy: date) -> list[dict]:
    """Compara la semana actual de cada técnico contra su propio
    promedio de las 6 semanas anteriores, en 4 métricas:
    tiempo de resolución, recoordinaciones, cancelaciones, y —cuando
    el dato exista— retiros no exitosos."""
    semana_actual = _semana_de(hoy)

    por_tecnico = defaultdict(list)
    for c in casos_ult_7_semanas:
        tid = c.get("tecnico_id")
        if tid:
            por_tecnico[tid].append(c)

    alertas = []
    for t in tecnicos:
        tid = t.get("auth_id") or t.get("id")
        casos_t = por_tecnico.get(tid, [])
        if len(casos_t) < 3:
            continue  # muy pocos casos para que la comparación tenga sentido

        actual = [c for c in casos_t if c.get("updated_at") and _semana_de(c["updated_at"].date()) == semana_actual]
        anteriores = [c for c in casos_t if c.get("updated_at") and _semana_de(c["updated_at"].date()) != semana_actual]
        if len(actual) < 2 or len(anteriores) < 5:
            continue

        nombre = f"{t.get('nombre','')} {t.get('apellido','')}".strip()

        # Métrica 1: tiempo de resolución
        t_actual = [v for v in (_tiempo_resolucion(c) for c in actual) if v is not None]
        t_anteriores = [v for v in (_tiempo_resolucion(c) for c in anteriores) if v is not None]
        if t_actual and t_anteriores:
            prom_actual, prom_ant = statistics.mean(t_actual), statistics.mean(t_anteriores)
            if prom_ant > 0:
                cambio = (prom_actual - prom_ant) / prom_ant
                if cambio >= UMBRAL_DESVIO_MODERADO:
                    alertas.append({
                        "tipo": "tiempo_resolucion", "tecnico": nombre, "tecnico_id": tid,
                        "empresa": t.get("empresa_codigo"),
                        "mensaje": f"{nombre} — tiempo de resolución subió de {prom_ant:.1f} a {prom_actual:.1f} días "
                                   f"esta semana (+{cambio*100:.0f}% vs su propio promedio)",
                        "severidad": "alta" if cambio >= 0.7 else "media",
                    })

        # Métrica 2 y 3: recoordinaciones y cancelaciones (por semana, normalizado por casos)
        for evento, label in [("RECOORDINACION", "recoordinaciones"), ("CANCELACION", "cancelaciones")]:
            r_actual = sum(_contar_eventos(c, evento) for c in actual) / len(actual)
            r_ant = sum(_contar_eventos(c, evento) for c in anteriores) / len(anteriores)
            if r_ant > 0 and r_actual > 0:
                cambio = (r_actual - r_ant) / r_ant
                if cambio >= UMBRAL_DESVIO_MODERADO:
                    alertas.append({
                        "tipo": label, "tecnico": nombre, "tecnico_id": tid,
                        "empresa": t.get("empresa_codigo"),
                        "mensaje": f"{nombre} — aumentaron las {label} esta semana "
                                   f"(+{cambio*100:.0f}% vs su propio promedio)",
                        "severidad": "alta" if cambio >= 0.7 else "media",
                    })
            elif r_ant == 0 and r_actual >= 1:
                alertas.append({
                    "tipo": label, "tecnico": nombre, "tecnico_id": tid,
                    "empresa": t.get("empresa_codigo"),
                    "mensaje": f"{nombre} — empezó a tener {label} esta semana (antes no tenía)",
                    "severidad": "media",
                })

        # Métrica 4: retiros exitosos — SOLO si el campo existe en los datos
        retiros_t = [c for c in casos_t if c.get("tipo_proceso") == "RETIRO"]
        tiene_dato_retiro = any(c.get("cierre_retiro_exitoso") is not None for c in retiros_t)
        if tiene_dato_retiro:
            retiros_actual = [c for c in retiros_t if c.get("updated_at") and _semana_de(c["updated_at"].date()) == semana_actual]
            if len(retiros_actual) >= 2:
                exitosos = sum(1 for c in retiros_actual if c.get("cierre_retiro_exitoso"))
                pct = 100 * exitosos / len(retiros_actual)
                if pct < 70:
                    alertas.append({
                        "tipo": "retiros_no_exitosos", "tecnico": nombre, "tecnico_id": tid,
                        "empresa": t.get("empresa_codigo"),
                        "mensaje": f"{nombre} — solo {pct:.0f}% de retiros exitosos esta semana "
                                   f"({exitosos}/{len(retiros_actual)})",
                        "severidad": "alta" if pct < 50 else "media",
                    })

    return alertas


# ─── B2 — Clusters geográficos/temporales ───────────────────────

def clusters_geograficos(casos_ult_14_dias: list[dict], hoy: date, dias_ventana: int = 3) -> list[dict]:
    """Detecta si algún tipo de proceso se concentró de forma anormal
    en una zona en los últimos `dias_ventana` días, comparado contra
    el promedio diario de esa misma zona+tipo en los 14 días previos."""
    desde_ventana = hoy - timedelta(days=dias_ventana - 1)
    desde_base = hoy - timedelta(days=14)

    por_zona_tipo_dia = defaultdict(lambda: defaultdict(int))
    for c in casos_ult_14_dias:
        creado = c.get("created_at")
        if not creado:
            continue
        d = creado.date()
        zona = zona_de_caso(c.get("departamento"), c.get("localidad"))
        tipo = c.get("tipo_proceso") or "OTRO"
        por_zona_tipo_dia[(zona, tipo)][d] += 1

    alertas = []
    for (zona, tipo), por_dia in por_zona_tipo_dia.items():
        dias_base = [d for d in por_dia if desde_base <= d < desde_ventana]
        dias_venta = [d for d in por_dia if desde_ventana <= d <= hoy]
        if not dias_venta:
            continue
        casos_ventana = sum(por_dia[d] for d in dias_venta)
        promedio_diario_base = (sum(por_dia[d] for d in dias_base) / max(len(dias_base), 1)) if dias_base else 0
        promedio_diario_ventana = casos_ventana / dias_ventana
        umbral = max(promedio_diario_base * UMBRAL_CLUSTER_FACTOR, 1.5)
        if promedio_diario_ventana >= umbral and casos_ventana >= 4:
            factor = round(promedio_diario_ventana / promedio_diario_base, 1) if promedio_diario_base else None
            alertas.append({
                "tipo": "cluster", "zona": zona, "proceso": tipo,
                "mensaje": f"⚠ {casos_ventana} casos de {tipo} en {zona} en los últimos {dias_ventana} días"
                           + (f" — {factor}x lo habitual para esa zona" if factor else " — muy por encima de lo habitual")
                           + ". Posible causa común (corte de luz, lote de hardware, problema de conectividad).",
                "casos": casos_ventana, "severidad": "alta" if (factor or 3) >= 3 else "media",
            })

    alertas.sort(key=lambda a: -a["casos"])
    return alertas


# ─── B3 — Casos outlier ──────────────────────────────────────────

def casos_outlier(cerrados_periodo: list[dict]) -> list[dict]:
    """Casos individuales cuyo tiempo de resolución fue muy superior
    al promedio de su mismo tipo de proceso — candidatos a revisión
    manual."""
    por_tipo = defaultdict(list)
    for c in cerrados_periodo:
        v = _tiempo_resolucion(c)
        if v is not None:
            por_tipo[c.get("tipo_proceso")].append((c, v))

    resultado = []
    for tipo, pares in por_tipo.items():
        if len(pares) < 5:
            continue
        valores = [v for _, v in pares]
        promedio = statistics.mean(valores)
        if promedio <= 0:
            continue
        for c, v in pares:
            if v >= promedio * UMBRAL_OUTLIER_FACTOR and v >= 1:
                resultado.append({
                    "caso_id": c.get("id"), "tipo_proceso": tipo,
                    "razon_social": c.get("razon_social"),
                    "tiempo_dias": round(v, 1), "promedio_categoria": round(promedio, 1),
                    "veces_promedio": round(v / promedio, 1),
                })

    resultado.sort(key=lambda r: -r["veces_promedio"])
    return resultado[:20]


# ─── Resumen para el ticker (Director / Regional) ────────────────

def resumen_para_ticker(b1: list[dict], b2: list[dict], b3: list[dict], equipo: str | None = None) -> list[str]:
    """Arma frases cortas para mostrar en el ticker superior. Si se
    pasa `equipo`, filtra B1 a ese equipo únicamente (para Regional)."""
    frases = []

    b1_f = [a for a in b1 if not equipo or a.get("empresa") == equipo] if equipo else b1
    altas_b1 = [a for a in b1_f if a["severidad"] == "alta"]
    if altas_b1:
        frases.append(f"⚠ {len(altas_b1)} desvío(s) de rendimiento detectado(s) hoy")

    if not equipo and b2:  # clusters son geográficos, no tienen dueño de equipo — solo Director
        top = b2[0]
        frases.append(f"📍 {top['mensaje'][:80]}")

    if not equipo and b3:
        frases.append(f"🔍 {len(b3)} caso(s) marcado(s) para revisión por demora atípica")

    return frases
