"""Generador de imagen de certificado — Bloque D2 (rediseño con la plantilla
real entregada por el usuario).

El fondo real (assets/certificado_fondo.png) es la plantilla vacía sin
placeholders que mandó el usuario — mide 1400x990px. Las coordenadas de
abajo se midieron por diferencia de píxeles contra la versión con
placeholders que mandó junto a la vacía, así que son exactas para ESTA
plantilla puntual; si algún día se reemplaza el archivo de fondo por un
diseño distinto, estas coordenadas dejan de ser válidas y hay que volver a
medir.

Nota conocida (no una decisión de diseño): la plantilla trae la fecha
"17 de agosto de 2026" ya dibujada de forma fija en el PNG de fondo (quedó
así al exportarla desde la herramienta de diseño). Como cada certificado se
emite en una fecha real distinta, ese texto se tapa en tiempo de generación
con un parche muestreado del fondo inmediatamente de al lado (incluso
antes de dibujar la fecha real encima) — el resultado es muy cercano al
original pero no pixel-perfecto en esa franja puntual. Si se consigue una
versión de la plantilla sin esa fecha fija, hay que reemplazar el archivo y
se puede borrar todo el bloque de "parche de fecha" de este archivo.
"""

from __future__ import annotations

import io
import os

import aiohttp
from PIL import Image, ImageDraw, ImageFont

CARPETA_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
FONDO_PATH = os.path.join(CARPETA_ASSETS, "certificado_fondo.png")

# Tamaño nativo de la plantilla real — todas las coordenadas de abajo están
# medidas contra este tamaño exacto.
ANCHO_NATIVO, ALTO_NATIVO = 1400, 990

_NAVY = (11, 37, 69)          # BRAND_SKY_NAVY — duplicado a propósito, ver welcome_card.py
_AMBER = (255, 180, 0)        # BRAND_BEACON_AMBER
_BLANCO = (245, 247, 250)

# Coordenadas medidas por diff de píxeles entre la plantilla vacía y la
# versión con placeholders (ver certificado_zip del usuario, 1.png/2.png/3.png).
_AVATAR_BBOX = (604, 260, 795, 451)  # x0, y0, x1, y1 — círculo, diámetro 191
_USUARIO_LINEA = (434, 965)          # x0, x1 de la línea bajo el nombre — centro vertical del texto: 536
_USUARIO_Y = 536
_CURSO_Y = 651
_INSTRUCTOR_LINEA = (1085, 1270)     # x0, x1 de la línea sobre "ATC24 Español"
_INSTRUCTOR_Y = 746
_CODIGO_Y = 818
_FECHA_PARCHE_BBOX = (122, 799, 433, 848)  # región a tapar y redibujar con la fecha real (justo debajo de "FECHA UTC", que ocupa hasta y=798)
_FECHA_X = 131
_FECHA_Y = 818


def _cargar_fuente(tamano: int, *, mono: bool = False) -> ImageFont.FreeTypeFont:
    """fuente_bienvenida.ttf / fuente_mono.ttf son fuentes variables
    (Outfit / JetBrains Mono, licencia OFL) — se les pide explícitamente la
    instancia "Bold" nombrada, si no se hace, PIL las carga en su peso por
    defecto (Regular) y el diseño se ve más liviano que el original."""
    if mono:
        candidatos = [
            os.path.join(CARPETA_ASSETS, "fuente_mono.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        ]
    else:
        candidatos = [
            os.path.join(CARPETA_ASSETS, "fuente_bienvenida.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    for ruta in candidatos:
        if os.path.exists(ruta):
            try:
                fuente = ImageFont.truetype(ruta, tamano)
                try:
                    nombres = fuente.get_variation_names()
                    if b"Bold" in nombres:
                        fuente.set_variation_by_name("Bold")
                except Exception:
                    pass  # no es una fuente variable (ej. ya es un .ttf Bold estático) — se usa tal cual
                return fuente
            except Exception:
                continue
    return ImageFont.load_default()


def _fondo() -> Image.Image:
    if os.path.exists(FONDO_PATH):
        return Image.open(FONDO_PATH).convert("RGB").resize((ANCHO_NATIVO, ALTO_NATIVO))
    # Placeholder de emergencia si todavía no se copió el archivo real a
    # assets/ — nunca debería usarse en producción con la plantilla puesta.
    img = Image.new("RGB", (ANCHO_NATIVO, ALTO_NATIVO), _NAVY)
    draw = ImageDraw.Draw(img)
    draw.rectangle((20, 20, ANCHO_NATIVO - 20, ALTO_NATIVO - 20), outline=_AMBER, width=6)
    return img


async def _descargar_avatar(member) -> Image.Image:
    url = str(member.display_avatar.replace(size=256, format="png").url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.read()
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _recortar_circulo(avatar: Image.Image, diametro: int) -> Image.Image:
    avatar = avatar.resize((diametro, diametro), Image.LANCZOS)
    mascara = Image.new("L", (diametro, diametro), 0)
    ImageDraw.Draw(mascara).ellipse((0, 0, diametro, diametro), fill=255)
    resultado = Image.new("RGBA", (diametro, diametro))
    resultado.paste(avatar, (0, 0), mascara)
    return resultado


def _texto_ajustado(draw: ImageDraw.ImageDraw, texto: str, tamano_inicial: int, ancho_max: int, *, mono: bool = False) -> ImageFont.FreeTypeFont:
    """Reduce el tamaño de fuente hasta que el texto entre en ancho_max —
    necesario porque nombres de usuario/instructor tienen largo variable, a
    diferencia del resto del diseño que es fijo."""
    tamano = tamano_inicial
    while tamano > 10:
        fuente = _cargar_fuente(tamano, mono=mono)
        bbox = draw.textbbox((0, 0), texto, font=fuente)
        if bbox[2] - bbox[0] <= ancho_max:
            return fuente
        tamano -= 2
    return _cargar_fuente(10, mono=mono)


def _centrar_en(draw: ImageDraw.ImageDraw, texto: str, fuente: ImageFont.FreeTypeFont, cx: float, cy: float, color=_NAVY) -> None:
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    x = cx - (bbox[2] - bbox[0]) / 2 - bbox[0]
    y = cy - (bbox[3] - bbox[1]) / 2 - bbox[1]
    draw.text((x, y), texto, font=fuente, fill=color)


def _tapar_fecha_fija(fondo: Image.Image) -> None:
    """La plantilla trae una fecha fija dibujada en el fondo (ver docstring
    del módulo) — se tapa con un parche copiado 1:1 (sin estirar) de una
    franja limpia más abajo, en las mismas columnas, para conservar el
    degradé de la marca de agua sin amplificar restos de texto (estirar una
    franja fina termina agrandando cualquier resto de letra que haya
    quedado adentro, que es justo lo que se ve si se hace mal)."""
    x0, y0, x1, y1 = _FECHA_PARCHE_BBOX
    alto = y1 - y0
    desplazamiento = 44  # baja lo suficiente para salir del texto y no pisar el pie de página
    parche = fondo.crop((x0, y0 + desplazamiento, x1, y1 + desplazamiento))
    fondo.paste(parche, (x0, y0))


async def generar_certificado(member, curso_titulo: str, codigo: str, *, instructor_nombre: str | None = None,
                               corto: bool = False) -> io.BytesIO:
    """Genera el certificado completo, con el diseño real de la plantilla
    (avatar, usuario, curso, instructor, código y fecha de emisión). Se
    renderiza siempre a tamaño nativo (1400x990) para no perder precisión
    en las coordenadas medidas; la versión "corta" es la misma imagen
    reducida al final, no un layout distinto."""
    import datetime

    fondo = _fondo().convert("RGB")
    _tapar_fecha_fija(fondo)
    draw = ImageDraw.Draw(fondo)

    x0, y0, x1, y1 = _AVATAR_BBOX
    diametro = x1 - x0
    try:
        avatar = await _descargar_avatar(member)
        avatar_circular = _recortar_circulo(avatar, diametro)
        fondo.paste(avatar_circular, (x0, y0), avatar_circular)
    except Exception:
        pass  # si Discord no responde el avatar, el certificado se genera igual sin él

    ancho_usuario_max = (_USUARIO_LINEA[1] - _USUARIO_LINEA[0]) - 20
    fuente_usuario = _texto_ajustado(draw, member.display_name, 40, ancho_usuario_max)
    _centrar_en(draw, member.display_name, fuente_usuario, (_USUARIO_LINEA[0] + _USUARIO_LINEA[1]) / 2, _USUARIO_Y)

    fuente_curso = _texto_ajustado(draw, curso_titulo, 24, (_USUARIO_LINEA[1] - _USUARIO_LINEA[0]) - 40)
    _centrar_en(draw, curso_titulo, fuente_curso, (_USUARIO_LINEA[0] + _USUARIO_LINEA[1]) / 2, _CURSO_Y)

    if instructor_nombre:
        ancho_instructor_max = (_INSTRUCTOR_LINEA[1] - _INSTRUCTOR_LINEA[0]) - 10
        fuente_instructor = _texto_ajustado(draw, instructor_nombre, 22, ancho_instructor_max)
        _centrar_en(draw, instructor_nombre, fuente_instructor,
                    (_INSTRUCTOR_LINEA[0] + _INSTRUCTOR_LINEA[1]) / 2, _INSTRUCTOR_Y)

    fuente_codigo = _cargar_fuente(26, mono=True)
    _centrar_en(draw, codigo, fuente_codigo, (_USUARIO_LINEA[0] + _USUARIO_LINEA[1]) / 2, _CODIGO_Y)

    fecha_texto = _fecha_utc_larga(datetime.datetime.now(datetime.timezone.utc))
    fuente_fecha = _texto_ajustado(draw, fecha_texto, 26, (_FECHA_PARCHE_BBOX[2] - _FECHA_X) - 5, mono=True)
    bbox_fecha = draw.textbbox((0, 0), fecha_texto, font=fuente_fecha)
    draw.text((_FECHA_X, _FECHA_Y - (bbox_fecha[3] - bbox_fecha[1]) / 2 - bbox_fecha[1]), fecha_texto, font=fuente_fecha, fill=_NAVY)

    if corto:
        fondo = fondo.resize((700, 495), Image.LANCZOS)

    salida = io.BytesIO()
    fondo.save(salida, format="PNG")
    salida.seek(0)
    return salida


_MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _fecha_utc_larga(momento) -> str:
    return f"{momento.day} de {_MESES_ES[momento.month - 1]} de {momento.year}"


async def generar_resultado_evaluacion(member, curso_titulo: str, total: int, maximo: int, aprobado: bool) -> io.BytesIO:
    """Imagen de resultado de una evaluación (Bloque D3) — distinta del
    certificado: esta se manda siempre (apruebe o repruebe), el certificado
    solo si aprueba. No usa la plantilla del certificado (no representa un
    documento oficial), mantiene su propio fondo generado con Pillow."""
    ancho, alto = 900, 500
    fondo = Image.new("RGB", (ancho, alto), _NAVY)
    draw = ImageDraw.Draw(fondo)
    borde = max(4, ancho // 200)
    draw.rectangle((borde * 5, borde * 5, ancho - borde * 5, alto - borde * 5), outline=_AMBER, width=borde)
    color_resultado = (61, 220, 151) if aprobado else (176, 65, 62)  # BRAND_RADAR_GREEN / rojo

    def _centrar_blanco(texto, fuente, y):
        bbox = draw.textbbox((0, 0), texto, font=fuente)
        x = (ancho - (bbox[2] - bbox[0])) / 2 - bbox[0]
        draw.text((x, y), texto, font=fuente, fill=_BLANCO)

    y = alto * 0.14
    _centrar_blanco("RESULTADO DE EVALUACIÓN", _cargar_fuente(int(alto * 0.06)), y)
    y += alto * 0.14
    _centrar_blanco(member.display_name, _cargar_fuente(int(alto * 0.07)), y)
    y += alto * 0.12
    _centrar_blanco(curso_titulo, _cargar_fuente(int(alto * 0.045)), y)
    y += alto * 0.16
    fuente_puntaje = _cargar_fuente(int(alto * 0.10))
    bbox = draw.textbbox((0, 0), f"{total} / {maximo}", font=fuente_puntaje)
    x = (ancho - (bbox[2] - bbox[0])) / 2 - bbox[0]
    draw.text((x, y), f"{total} / {maximo}", font=fuente_puntaje, fill=color_resultado)
    y += alto * 0.16
    _centrar_blanco("APROBADO" if aprobado else "NO APROBADO", _cargar_fuente(int(alto * 0.06)), y)

    salida = io.BytesIO()
    fondo.save(salida, format="PNG")
    salida.seek(0)
    return salida
