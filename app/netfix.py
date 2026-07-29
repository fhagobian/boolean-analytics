"""
Fuerza que TODAS las resoluciones DNS del proceso devuelvan solo
direcciones IPv4. Necesario porque Railway no tiene salida de red
IPv6: si cualquier librería (httpx incluido) intenta conectar a un
host que resuelve a IPv6, la conexión se queda colgada hasta el
timeout en vez de fallar rápido o conectar por IPv4.

Se aplica una sola vez, apenas se importa este módulo — por eso se
importa de primero en app/__init__.py, antes de que cualquier otra
parte del código cree un cliente HTTP.
"""
import socket

_getaddrinfo_original = socket.getaddrinfo


def _getaddrinfo_solo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    # Se ignora el 'family' pedido y se fuerza siempre AF_INET (IPv4)
    return _getaddrinfo_original(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _getaddrinfo_solo_ipv4
