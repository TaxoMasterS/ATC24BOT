"""
Generador de la tarjeta de bienvenida personalizada — ATC24 Español.

Compone: fondo (assets/bienvenida_fondo.png) + avatar del usuario recortado
en círculo + su nombre entre llaves ("{nombre}"), como en el ejemplo que
pasó el usuario. Devuelve bytes PNG listos para mandar como discord.File.

Si en algún momento se quiere ajustar la fuente, poné un archivo .ttf en
assets/fuente_bienvenida.ttf — se usa automáticamente si existe. Si no,
cae a una fuente del sistema (DejaVu Sans Bold, viene instalada en la
mayoría de las imágenes de Linux que usa Render) y, en el peor caso, a la
fuente por defecto de Pillow (fea pero nunca rompe).
"""

import io
import os

import aiohttp
from PIL import Image, ImageDraw, ImageFont, ImageOps

CARPETA_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
FONDO_PATH = os.path.join(CARPETA_ASSETS, "bienvenida_fondo.png")
FUENTE_CUSTOM_PATH = os.path.join(CARPETA_ASSETS, "fuente_bienvenida.ttf")

# Proporciones medidas sobre la referencia (941x529): centro del avatar al
# ~50% del ancho / ~24% del alto, radio ~19% del alto; texto centrado al
# ~62% del alto. Se recalculan sobre el tamaño real del fondo que se cargue,
# así que da igual si el PNG final tiene otra resolución.
AVATAR_CENTRO_X_FRAC = 0.50
AVATAR_CENTRO_Y_FRAC = 0.30
AVATAR_RADIO_FRAC = 0.19
TEXTO_Y_FRAC = 0.66


def _cargar_fuente(tamano: int) -> ImageFont.FreeTypeFont:
    candidatos = [
        FUENTE_CUSTOM_PATH,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for ruta in candidatos:
        if os.path.exists(ruta):
            try:
                return ImageFont.truetype(ruta, tamano)
            except Exception:
                continue
    return ImageFont.load_default()


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


async def generar_tarjeta_bienvenida(member) -> io.BytesIO:
    if not os.path.exists(FONDO_PATH):
        raise FileNotFoundError(
            f"Falta el fondo de la tarjeta en {FONDO_PATH} — subilo antes de usar esta función."
        )

    fondo = Image.open(FONDO_PATH).convert("RGBA")
    ancho, alto = fondo.size

    diametro = int(alto * AVATAR_RADIO_FRAC * 2)
    avatar = await _descargar_avatar(member)
    avatar_circular = _recortar_circulo(avatar, diametro)

    cx = int(ancho * AVATAR_CENTRO_X_FRAC)
    cy = int(alto * AVATAR_CENTRO_Y_FRAC)
    pos_avatar = (cx - diametro // 2, cy - diametro // 2)
    fondo.paste(avatar_circular, pos_avatar, avatar_circular)

    draw = ImageDraw.Draw(fondo)
    texto = member.display_name
    tamano_fuente = int(alto * 0.13)
    fuente = _cargar_fuente(tamano_fuente)

    # Si el nombre es muy largo, reducimos la fuente hasta que entre en el
    # ~90% del ancho del canvas, en vez de recortar el texto o desbordarlo.
    max_ancho = ancho * 0.90
    bbox = draw.textbbox((0, 0), texto, font=fuente)
    while (bbox[2] - bbox[0]) > max_ancho and tamano_fuente > 20:
        tamano_fuente -= 4
        fuente = _cargar_fuente(tamano_fuente)
        bbox = draw.textbbox((0, 0), texto, font=fuente)

    texto_x = (ancho - (bbox[2] - bbox[0])) / 2 - bbox[0]
    texto_y = alto * TEXTO_Y_FRAC - (bbox[3] - bbox[1]) / 2 - bbox[1]
    draw.text((texto_x, texto_y), texto, font=fuente, fill=(255, 255, 255, 255))

    salida = io.BytesIO()
    fondo.convert("RGB").save(salida, format="PNG")
    salida.seek(0)
    return salida
