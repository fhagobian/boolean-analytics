"""
Sistema de encuestas NPS por WhatsApp vía Twilio.

Diseño:
- El Director habilita el muestreo en `config_encuestas_nps` (activa,
  rango de fechas, % de casos exitosos que entran al sorteo). Esa
  tabla se lee/escribe DIRECTO desde el frontend con supabase-js
  (no es sensible, mismo patrón que la config de SLA) — este módulo
  no la toca.
- La decisión de "¿le pido el celular al cliente en este caso?" se
  toma en el FRONTEND con un sorteo simple (Math.random() < %), en
  el momento de FINALIZAR un caso exitoso. Solo cuando el técnico
  efectivamente carga un teléfono, el frontend llama a este backend.
- Acá SÍ vive el secreto de Twilio — por eso el envío nunca puede
  pasar por el frontend.
- Antifraude: antes de enviar, se rechaza el teléfono si ya se usó
  en otra encuesta dentro de la ventana configurada (evita que un
  técnico "confirme" su propio número una y otra vez).
"""
import os
import re
import hmac
import hashlib
import base64
import httpx

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


def normalizar_telefono(telefono: str, codigo_pais_default: str = "598") -> str:
    """Normaliza a formato E.164. Si el número no trae código de país,
    asume Uruguay (598) — ajustable por parámetro para otros clientes."""
    limpio = re.sub(r"[^\d+]", "", telefono or "")
    if limpio.startswith("+"):
        return limpio
    if limpio.startswith("00"):
        return "+" + limpio[2:]
    if limpio.startswith(codigo_pais_default):
        return "+" + limpio
    # Celular uruguayo local: empieza con 09 -> se le saca el 0 y se antepone 598
    if limpio.startswith("0"):
        limpio = limpio[1:]
    return f"+{codigo_pais_default}{limpio}"


async def telefono_bloqueado_antifraude(client: httpx.AsyncClient, telefono: str, dias_ventana: int) -> bool:
    """True si este teléfono ya recibió una encuesta dentro de la
    ventana antifraude — se rechaza el nuevo envío."""
    from datetime import datetime, timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(days=dias_ventana)).isoformat()
    r = await client.get("/encuestas_nps", params=[
        ("telefono", f"eq.{telefono}"),
        ("created_at", f"gte.{desde}"),
        ("select", "id"),
        ("limit", "1"),
    ])
    r.raise_for_status()
    return len(r.json()) > 0


async def enviar_whatsapp(client: httpx.AsyncClient, telefono_destino: str, texto: str) -> dict:
    """Envía un mensaje de WhatsApp vía Twilio. Devuelve {"sid":...}
    o lanza excepción si falla."""
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    numero_origen = os.environ["TWILIO_WHATSAPP_FROM"]  # formato: whatsapp:+14155238886

    url = f"{TWILIO_API_BASE}/Accounts/{account_sid}/Messages.json"
    auth = (account_sid, auth_token)
    data = {
        "From": numero_origen,
        "To": f"whatsapp:{telefono_destino}",
        "Body": texto,
    }
    async with httpx.AsyncClient(timeout=15.0) as twilio_client:
        r = await twilio_client.post(url, auth=auth, data=data)
    r.raise_for_status()
    return r.json()


def texto_encuesta_inicial(nombre_empresa: str = "BOOLEAN") -> str:
    return (
        f"¡Hola! Somos {nombre_empresa}. Un técnico te visitó recientemente. "
        f"¿Nos ayudás calificando el servicio del 0 al 10? "
        f"Podés agregar un comentario corto si querés. Ej: '9 muy buena atención'"
    )


# ─── Parseo de la respuesta entrante ──────────────────────────────

def parsear_respuesta(texto: str) -> tuple[int | None, str]:
    """Extrae un puntaje 0-10 del mensaje del cliente y devuelve el
    resto como comentario (recortado a un largo razonable). Busca el
    primer número de 1-2 dígitos entre 0 y 10 en el mensaje."""
    texto = (texto or "").strip()
    match = re.search(r"\b(10|[0-9])\b", texto)
    puntaje = int(match.group(1)) if match else None
    comentario = texto
    if match:
        comentario = (texto[:match.start()] + texto[match.end():]).strip(" .,-—")
    comentario = comentario[:280]  # acotada, como pediste
    return puntaje, comentario


# ─── Validación de firma de Twilio (seguridad del webhook público) ─

def validar_firma_twilio(url: str, params: dict, firma_recibida: str) -> bool:
    """Twilio firma cada request al webhook con X-Twilio-Signature.
    Sin esto, cualquiera podría mandarnos POSTs falsos simulando ser
    un cliente respondiendo una encuesta."""
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    data = url
    for key in sorted(params.keys()):
        data += key + params[key]
    firma_calculada = base64.b64encode(
        hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(firma_calculada, firma_recibida or "")
