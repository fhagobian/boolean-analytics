"""
Espejo en Python del mapeo de zonas operativas que vive en App.jsx
(constantes LOCALIDADES_ZONA y ZONAS_OPERATIVAS). Si el mapeo de
localidades de Canelones cambia en el frontend, actualizar acá también
para que la demanda por zona sea consistente entre ambos lados.
"""

LOCALIDADES_ZONA = {
    "Canelones Metropolitana": [
        "las piedras", "la paz", "progreso", "18 de mayo", "villa felicidad",
        "barros blancos", "joaquín suárez", "suárez", "toledo", "sauce",
        "pando", "empalme olmos", "paso carrasco", "colonia nicolich",
        "nicolich", "aeroparque", "shangrilá", "san josé de carrasco",
        "lagomar", "solymar", "lomas de solymar", "el pinar", "el bosque",
        "ciudad de la costa",
    ],
    "Canelones Este": [
        "neptunia", "pinamar", "salinas", "marindia", "fortín de santa rosa",
        "villa argentina", "atlántida", "estación atlántida", "las toscas",
        "parque del plata", "las vegas", "la floresta", "estación la floresta",
        "costa azul", "bello horizonte", "guazuvirá", "san luis", "los titanes",
        "la tuna", "araminda", "santa lucía del este", "cuchilla alta",
        "santa ana", "balneario argentino", "jaureguiberry", "soca", "migues",
        "montes", "tala", "san jacinto",
    ],
    "Canelones Oeste": [
        "canelones", "santa lucía", "aguas corrientes", "los cerrillos",
        "juanicó", "san ramón", "san bautista", "santa rosa", "san antonio",
        "25 de agosto", "25 de mayo",
    ],
}


def zona_de_caso(departamento: str | None, localidad: str | None) -> str:
    """Dado departamento + localidad de un caso, devuelve su zona operativa."""
    depto = (departamento or "").strip()
    loc = (localidad or "").strip().lower()

    if depto != "Canelones":
        return depto or "Sin especificar"

    if loc:
        for zona, locs in LOCALIDADES_ZONA.items():
            if any(l in loc or loc in l for l in locs):
                return zona

    # Localidad de Canelones no mapeada: se marca como Canelones (general)
    return "Canelones (sin zona)"
