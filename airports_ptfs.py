"""Catálogo de aeropuertos civiles de Pilot Training Flight Simulator (PTFS).

Lista real, entregada por el usuario — no agregar aeropuertos que no estén
acá. Formato: (ICAO, nombre para mostrar en el selector).
"""

AIRPORTS: list[tuple[str, str]] = [
    ("IRFD", "Greater Rockford"),
    ("IPPH", "Perth"),
    ("IZOL", "Izolirani"),
    ("ITKO", "Tokyo"),
    ("IBRD", "Bird Island Airfield"),
    ("ILAR", "Larnaca"),
    ("IIAB", "McConnell AFB"),
    ("IKFL", "Keflavik"),
    ("ITEY", "Pingeyri"),
    ("IPAP", "Paphos"),
    ("ISAU", "Sauthemptona"),
    ("IMLR", "Mellor"),
    ("IBTH", "Saint Barthélemy"),
    ("IDCS", "Saba"),
    ("ITRC", "Training Centre"),
    ("ILKL", "Lukla"),
    ("IJAF", "Al Najaf"),
    ("IGAR", "Airbase Garry"),
    ("ISCM", "RAF Scampton"),
    ("IHEN", "Henstridge Airfield"),
    ("ISKP", "Skopelos"),
    ("IBLT", "Boltic Airfield"),
    ("IBAR", "Barra"),
]


def choices():
    """Devuelve las opciones ya armadas para app_commands.choices — máximo
    25 (límite de Discord); si la lista real supera eso, hay que pasar a
    autocomplete en vez de choices fijas. El selector muestra "ICAO |
    Nombre" pero el valor guardado (Choice.value) es únicamente el ICAO —
    así todo lo que lee ese valor después (el plan de vuelo publicado,
    /vuelo, el modal de edición) muestra solo el ICAO, nunca el nombre
    completo."""
    from discord import app_commands
    return [app_commands.Choice(name=f"{icao} | {nombre}", value=icao) for icao, nombre in AIRPORTS[:25]]


def nombre_de(icao: str) -> str | None:
    icao = (icao or "").upper()
    for code, nombre in AIRPORTS:
        if code == icao:
            return nombre
    return None
