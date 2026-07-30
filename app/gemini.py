"""
Análisis semanal de comentarios con Gemini (Google). Se llama a la API
REST directamente por HTTPS (no el SDK oficial) para reutilizar el mismo
cliente httpx con el patch de IPv4 ya probado — evita el riesgo de que
una librería nueva abra sus propias conexiones por fuera de ese parche
y reintroduzca el problema de red de Railway.

Modelo configurable por variable de entorno porque Google renombra y
deprecia modelos del tier gratis con cierta frecuencia; si el modelo
por defecto deja de existir, se cambia sin tocar código.
"""
import os
import json
import re
import httpx

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_COMENTARIOS_POR_CORRIDA = 150

TIPO_LABEL = {
    "INSTALACION": "Instalación", "SERVICIO_TECNICO": "Servicio Técnico",
    "RETIRO": "Retiro de Terminal", "VISITA_PROACTIVA": "Visita Proactiva",
}

PROMPT_TEMPLATE = """Sos un analista de operaciones de campo para una empresa de \
servicio técnico de terminales de pago. Te paso comentarios reales de técnicos \
sobre casos de tipo "{tipo_label}" cerrados esta semana. Cada caso tiene: \
motivo por el que se abrió, problema real encontrado, y cómo se resolvió.

Tu tarea: identificar los 3 problemas más frecuentes o importantes, y para cada \
uno proponer UNA acción correctiva concreta y accionable (algo que un Director \
de Operaciones pueda decidir hacer esta semana).

Respondé ÚNICAMENTE con un JSON válido, sin texto adicional, sin markdown, \
con este formato exacto:
{{"problemas": [
  {{"problema": "descripción breve del patrón detectado", \
"frecuencia": "alta|media|baja", \
"accion_sugerida": "acción correctiva concreta y específica"}}
]}}

Casos de la semana:
{casos_texto}
"""


def _limpiar_json(texto: str) -> str:
    """Gemini a veces envuelve el JSON en ```json ... ``` — lo saca."""
    t = texto.strip()
    t = re.sub(r"^```json\s*", "", t)
    t = re.sub(r"^```\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _armar_texto_casos(casos: list[dict]) -> str:
    lineas = []
    for i, c in enumerate(casos[:MAX_COMENTARIOS_POR_CORRIDA], 1):
        motivo = (c.get("descripcion") or "").strip()
        problema = (c.get("cierre_descripcion_problema") or "").strip()
        solucion = (c.get("cierre_como_resolvio") or "").strip()
        if not problema:
            continue  # sin contenido real, no aporta al análisis
        lineas.append(
            f"Caso {i}:\n"
            f"  Motivo de apertura: {motivo or '(sin datos)'}\n"
            f"  Problema encontrado: {problema}\n"
            f"  Cómo se resolvió: {solucion or '(sin datos)'}"
        )
    return "\n\n".join(lineas)


async def analizar_comentarios(client: httpx.AsyncClient, tipo_proceso: str,
                                 casos: list[dict]) -> dict:
    """Llama a Gemini y devuelve {"problemas": [...]} ya parseado.
    Devuelve una estructura vacía (sin llamar a la API) si no hay
    suficiente contenido real para analizar — ahorra costo y evita
    resultados inventados sobre datos vacíos."""
    casos_texto = _armar_texto_casos(casos)
    if not casos_texto.strip():
        return {"problemas": [], "motivo_vacio": "Sin comentarios con contenido suficiente esta semana"}

    api_key = os.environ["GEMINI_API_KEY"]
    prompt = PROMPT_TEMPLATE.format(
        tipo_label=TIPO_LABEL.get(tipo_proceso, tipo_proceso),
        casos_texto=casos_texto,
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
    }
    r = await client.post(url, params={"key": api_key}, json=body, timeout=60.0)
    r.raise_for_status()
    data = r.json()

    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(_limpiar_json(texto))
        problemas = parsed.get("problemas", [])[:3]
        return {"problemas": problemas}
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return {"problemas": [], "error": f"No se pudo interpretar la respuesta de Gemini: {e}"}
