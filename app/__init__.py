# IMPORTANTE: netfix debe importarse PRIMERO, antes que cualquier otro
# módulo cree un cliente de red — parchea la resolución DNS a IPv4.
from . import netfix  # noqa: F401,E402
