"""Rúbricas de evaluación por rango — Bloque D3.

PLACEHOLDER a propósito (decisión ya conversada): cada rango tiene 10
criterios de EJEMPLO. La estructura (10 criterios, 1 a 10 puntos cada uno,
100 puntos máximo, 75 para aprobar) ya está lista — para cargar el
contenido real de instrucción alcanza con editar RUBRICAS aquí abajo, sin
tocar ningún otro archivo del bot.

Los criterios deben ser DISTINTOS por rango (una evaluación de PPA no debe
compartir los mismos criterios que PCA o S1) — por eso es un diccionario
por rango y no una lista única reutilizada.
"""

PUNTAJE_MAXIMO = 100
PUNTAJE_APROBACION = 75

_CRITERIOS_EJEMPLO = [
    "Conocimiento teórico general",
    "Aplicación práctica de procedimientos",
    "Comunicación y fraseología",
    "Toma de decisiones bajo presión",
    "Manejo de situaciones no estándar",
    "Cumplimiento de la normativa",
    "Coordinación con otras posiciones o tripulaciones",
    "Precisión técnica",
    "Gestión del tiempo",
    "Actitud profesional",
]

# Rango -> lista de 10 (nombre, descripción). Todos parten del mismo
# ejemplo genérico — reemplazar por rango a medida que se defina el
# contenido real de instrucción.
RUBRICAS = {
    rango: [(nombre, "Descripción pendiente de definir.") for nombre in _CRITERIOS_EJEMPLO]
    for rango in ("APA", "PPA", "PCA", "ATO", "S1", "S2", "S3", "C1", "C3")
}


def criterios_para(rango: str) -> list:
    return RUBRICAS.get(rango, [])


def calcular_resultado(puntajes: list) -> tuple:
    """Devuelve (total, aprobado)."""
    total = sum(puntajes)
    return total, total >= PUNTAJE_APROBACION
