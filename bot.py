#!/usr/bin/env python3
"""
Bot de verificación por botón — ATC24 Español

Publica un mensaje (Components V2) con un botón "Acepto y confirmo".
Cuando alguien lo presiona:
  1. Se le retira el rol NV | No Verificado
  2. Se le otorga el rol  V | Verificado
  3. Recibe una respuesta privada (solo él la ve) confirmando el cambio

Este botón SOLO confirma que la persona aceptó las reglas — no revisa
si ya completó la verificación de Roblox con Bloxlink. Si quieres que
también valide Bloxlink antes de dar el rol, dímelo y lo agrego.

REQUISITOS ANTES DE CORRERLO
-----------------------------
1. pip install discord.py aiohttp
2. En el Portal de Desarrolladores de Discord, tu bot debe tener el
   permiso "Manage Roles" (Gestionar roles) al invitarlo al servidor.
3. MUY IMPORTANTE: en Ajustes del servidor → Roles, el rol de tu BOT
   debe estar POR ENCIMA de los roles V y NV en la lista. Si el bot
   está más abajo que esos roles, Discord le va a negar el permiso
   para asignarlos, aunque tenga "Manage Roles" activado.
4. El token NO va escrito en este archivo — se lee de la variable de
   entorno DISCORD_BOT_TOKEN. Esto es a propósito: así puedes subir
   este código a GitHub sin exponer tu token en el repo.

   Antes de correr el script, defínela:

     Windows (cmd):        set DISCORD_BOT_TOKEN=tu_token_aqui
     Windows (PowerShell):  $env:DISCORD_BOT_TOKEN="tu_token_aqui"
     Linux/Mac/VPS:          export DISCORD_BOT_TOKEN="tu_token_aqui"

5. Asegúrate de que la carpeta "payloads/" (con verificacion.json y
   guia.json) esté junto a este script.

CÓMO PUBLICAR EL MENSAJE
-------------------------
Corre el bot normalmente (python bot.py). Una vez
conectado, escribe en el canal donde quieres el botón:

    !publicar-verificacion

(solo funciona para quien tenga permiso de Administrador). El bot
publica el mensaje ahí y borra tu comando. Después de publicarlo una
vez, no hace falta volver a escribirlo — el bot debe seguir
corriendo 24/7 para poder reaccionar a los clics futuros.

Mismo mecanismo para el resto de los mensajes fijos — un !comando por
cada archivo en payloads/ (ver COMANDOS_PUBLICAR más abajo):

    !publicar-guia               guía general de la web y el bot
    !publicar-guia-bloxlink      guía de verificación con Bloxlink
    !publicar-guia-vuelo         guía de cómo presentar un plan de vuelo
    !publicar-guia-atis          guía de cómo leer el ATIS

HOSTING EN RENDER (GRATIS, SIN TARJETA)
-----------------------------------------
Este script además levanta un mini servidor web (puerto tomado de la
variable de entorno PORT, que Render define solo). Esto es SOLO para
que Render detecte que el servicio está "vivo" — Render exige que un
"Web Service" escuche en un puerto, si no, lo apaga. El servidor no
hace nada más que responder "OK" a quien lo visite.

Como el plan gratis de Render duerme el servicio tras 15 min sin
tráfico HTTP, hay que configurar un ping externo gratuito (UptimeRobot)
que visite esa URL cada 5 minutos para mantenerlo despierto. Ver
GUIA-hosting-render.md para el paso a paso completo.
"""

from __future__ import annotations

import ast
import asyncio
import datetime
import json
import operator
import os
import random
import re

import aiohttp
import discord
from discord import app_commands
from aiohttp import web

import atc_core
import moderation_core
import academy_core
import sessions_core
import bot_state
import airports_ptfs
import evaluation_criteria
import certificate_card

CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))

# En Render las variables de entorno ya están puestas en el dashboard, pero
# para correr el bot en una PC local no hay dashboard — esto carga el
# archivo .env (si existe, junto a este script) antes de leer nada con
# os.environ, sin que haga falta exportar cada variable a mano en la
# terminal. No hace nada si no encuentra un .env (ej. en Render).
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(CARPETA_SCRIPT, ".env"))
except ImportError:
    pass

# ─────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
PORT = int(os.environ.get("PORT", "10000"))  # Render inyecta PORT automáticamente

# Si está definida, los slash commands se sincronizan solo en este server
# (instantáneo). Sin ella, la sincronización es global y puede tardar ~1h
# en propagarse la primera vez.
GUILD_ID = os.environ.get("DISCORD_GUILD_ID")

# API key de Bloxlink (dashboard de Bloxlink → tu servidor → API). Se usa
# para leer el nombre REAL de Roblox ya verificado y usarlo como base del
# apodo, en vez del username de Discord. Si no está configurada, el apodo
# sigue funcionando igual mostrando el nombre de Discord (no rompe nada).
BLOXLINK_API_KEY = os.environ.get("BLOXLINK_API_KEY")

# Canal de Discord donde el bot publica/edita los mensajes de plan de vuelo
# (Components V2 + nombre real de Roblox vía Bloxlink) — /vuelo publica aquí
# directo, motor propio (atc_core), sin depender de la web. ID de canal.
DISCORD_CHANNEL_FLIGHTS = os.environ.get("DISCORD_CHANNEL_FLIGHTS") or "1538774998620315730"
# Mismo patrón para anuncios de posición ATC abierta y para el ATIS.
DISCORD_CHANNEL_ATC = os.environ.get("DISCORD_CHANNEL_ATC") or "1238796826727944199"
DISCORD_CHANNEL_ATIS = os.environ.get("DISCORD_CHANNEL_ATIS") or "1501733916128575621"
# Canal donde vive el panel de "Sesiones agendadas" de Academia y se publican
# las sesiones en vivo (Fase C, sistema de sesiones). Opcional — si no está
# configurado, "Agendar sesión" (en /academia) sigue guardando la sesión pero no hay panel.
DISCORD_CHANNEL_ACADEMIA_SESIONES = os.environ.get("DISCORD_CHANNEL_ACADEMIA_SESIONES")
# Canal interno donde llegan las solicitudes de clase individual (Bloque
# C1/C2) para que un instructor las reclame.
DISCORD_CHANNEL_SOLICITUDES_CLASE = os.environ.get("DISCORD_CHANNEL_SOLICITUDES_CLASE") or "1238796826526748755"
# Canal donde se publica la felicitación pública al aprobar una evaluación (Bloque D3).
DISCORD_CHANNEL_ANUNCIOS_ACADEMIA = os.environ.get("DISCORD_CHANNEL_ANUNCIOS_ACADEMIA") or "1238796826317164632"

# Canales de ejecución exclusiva (Bloque A): /vuelo y /atc solo pueden
# correr en su canal designado — cualquier otro mensaje ahí se borra con un
# aviso. Con default a los IDs reales del servidor, configurable por env var
# igual que el resto de los canales.
DISCORD_CHANNEL_VUELO_CMD = os.environ.get("DISCORD_CHANNEL_VUELO_CMD") or "1238796826727944200"
DISCORD_CHANNEL_ATC_CMD = os.environ.get("DISCORD_CHANNEL_ATC_CMD") or "1238796826727944199"

# ─── Sistema de tickets de soporte ─────────────────────────────────────────
# Rol que puede ver/gestionar TODOS los tickets, además de Liderazgo. Opcional
# — si no está configurado, solo Liderazgo ve los tickets nuevos.
SOPORTE_ROLE_ID = int(os.environ["SOPORTE_ROLE_ID"]) if os.environ.get("SOPORTE_ROLE_ID") else None
# Categoría de Discord donde se crean los canales de ticket. Configurable por
# variable de entorno; si no está seteada, cae a la categoría real del
# servidor.
TICKETS_CATEGORY_ID = int(os.environ["TICKETS_CATEGORY_ID"]) if os.environ.get("TICKETS_CATEGORY_ID") else 1238796826317164625
# Canal donde se registra cada caso de moderación (/advertir, /timeout,
# /kick, /ban), además de quedar guardado en la base propia del bot.
# Opcional — si no está configurado, solo queda el registro en la base y la
# respuesta ephemeral al moderador.
DISCORD_CHANNEL_MOD_LOG = os.environ.get("DISCORD_CHANNEL_MOD_LOG")
# Canal donde llegan las apelaciones (Bloque B7) enviadas desde el botón
# "Apelar" del DM de sanción. Si no está configurado, cae al canal de
# mod-log.
DISCORD_CHANNEL_APELACIONES = os.environ.get("DISCORD_CHANNEL_APELACIONES") or "1238796825796939888"
# Rol de mute adicional que se aplica junto con el timeout nativo de
# Discord (Bloque B2) — con default al rol real del servidor.
DISCORD_ROLE_MUTE = int(os.environ.get("DISCORD_ROLE_MUTE") or "1538720899224707222")

# Canal del juego de conteo (estilo countingbot.com) y canal de "foto de la
# semana". Configurables por variable de entorno; si no están seteadas, caen
# a los canales reales del servidor.
DISCORD_CHANNEL_CONTEO = os.environ.get("DISCORD_CHANNEL_CONTEO") or "1406796261817979001"
DISCORD_CHANNEL_FOTO_SEMANA = os.environ.get("DISCORD_CHANNEL_FOTO_SEMANA") or "1238796825960386617"

# Canal adicional donde también se avisa cuando alguien solicita que se abra
# una posición ATC (además del canal ATC normal).
CANAL_SOLICITUD_CONTROL_EXTRA = os.environ.get("CANAL_SOLICITUD_CONTROL_EXTRA") or "1535448653236404265"

# ─── Paleta de marca (ATC24 Español) — usada en accent_color de todos los
# mensajes Components V2 y en los discord.Embed, para que se vea consistente
# en vez de colores genéricos de Discord.
BRAND_SKY_NAVY = 0x0B2545
BRAND_RADAR_GREEN = 0x3DDC97
BRAND_BEACON_AMBER = 0xFFB400
BRAND_RUNWAY_WHITE = 0xF5F7FA

# ─── Carpeta de datos persistentes (contador de tickets, conteo, foto de la
# semana) — sobrevive reinicios normales del proceso; se pierde si Render
# hace un redeploy completo (disco efímero), igual que el resto del bot no
# garantiza persistencia entre despliegues.
CARPETA_DATOS = os.path.join(CARPETA_SCRIPT, "data")
os.makedirs(CARPETA_DATOS, exist_ok=True)

# Banner fijo de bienvenida (2 contenedores Components V2: imagen + texto,
# ver _payload_bienvenida). NO es el mismo archivo que assets/bienvenida_fondo.png
# (ese es el fondo de la tarjeta con avatar, generada por welcome_card.py —
# sistema anterior que este banner reemplaza en el mensaje de ingreso). Si
# el archivo todavía no existe, on_member_join manda solo el contenedor de
# texto y avisa por consola — no rompe nada, pero falta la imagen real.
RUTA_BANNER_BIENVENIDA = os.path.join(CARPETA_SCRIPT, "assets", "banner_bienvenida.png")

# ─── Motor de datos propio del bot (Fase A del rediseño) — SQLite embebida,
# reemplaza la dependencia de la web para vuelos y posiciones ATC. OJO: en
# Render el disco es efímero salvo que se agregue un "persistent disk" al
# servicio — sin eso, esta base se reinicia vacía en cada redeploy.
ATC_DB_PATH = os.path.join(CARPETA_DATOS, "atc24.db")
db: "aiosqlite.Connection" = None  # se asigna en setup_hook

# Cuánto tiempo puede quedar un canal de voz de posición ATC vacío antes de
# que el bot cierre la posición solo — reemplaza el viejo mecanismo (roto)
# de la web, que dependía de que el socket del navegador siguiera "trackeando"
# la posición. Aquí la señal es la MEJOR posible en Discord: si no hay nadie
# en el canal de voz de la posición, no se está controlando de verdad.
ATC_VACIO_GRACIA_SEG = 10 * 60  # 10 minutos


def _leer_json(nombre: str, default: dict) -> dict:
    ruta = os.path.join(CARPETA_DATOS, nombre)
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(default)


def _guardar_json(nombre: str, data: dict) -> None:
    ruta = os.path.join(CARPETA_DATOS, nombre)
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Emojis reales del servidor (mismos que ya se usan del lado de la web) —
# se usan en vez de emojis Unicode genéricos en todos los mensajes.
E = {
    "avion": "<:Avion:1534071045865341019>",
    "check": "<:Check:1534080132526903369>",
    "cruz": "<:Cruz:1534080002864451736>",
    "flecha": "<:flecha:1534071380625195048>",
    "brujula": "<:brujula:1534077767958663329>",
    "antena": "<:Antena:1534070807792324659>",
    "atc": "<:ATC:1534071009060061214>",
    "reloj": "<:reloj:1534077832245018664>",
    "chat": "<:chat:1534077571279360141>",
    "libro": "<:libro:1534075380405764227>",
    "microfono": "<:microfono:1534077647494316137>",
    "verificado": "<:Verificado:1534081412536205422>",
}

V_ROLE_ID  = 1508568101770367156   # V  | Verificado
NV_ROLE_ID = 1532919695827665057   # NV | No Verificado

FLT_ROLE_ID = 1238796825381834760   # FLT | Piloto (rol base)
ATC_ROLE_ID = 1532224008555204669   # ATC | Controlador de Tráfico Aéreo (rol base)

LLEGADAS_CHANNEL_ID = 1238796825415389294

CUSTOM_ID = "atc24_verificar_aceptacion"
ARCHIVO_MENSAJE = os.path.join(CARPETA_SCRIPT, "payloads", "verificacion.json")
ARCHIVO_GUIA = os.path.join(CARPETA_SCRIPT, "payloads", "guia.json")
ARCHIVO_GUIA_BLOXLINK = os.path.join(CARPETA_SCRIPT, "payloads", "guia_bloxlink.json")
ARCHIVO_GUIA_VUELO = os.path.join(CARPETA_SCRIPT, "payloads", "guia_plan_vuelo.json")
ARCHIVO_GUIA_ATIS = os.path.join(CARPETA_SCRIPT, "payloads", "guia_atis.json")

# ─── Jerarquía de roles y prefijos (guía oficial de ATC24 Español) ────────
# Cada lista va del rango MÁS ALTO al más bajo dentro de su categoría.
# "Un solo prefijo por categoría": si un miembro tiene más de un rol de una
# misma lista, solo el primero (más alto) se usa en el apodo, y el resto se
# retira automáticamente (ver enforce_single_rank_per_category).

STF_ROLE_ID = 1238796825415389286
PM_ROLE_ID = 1238796825390092358

LIDERAZGO_ORDER = [
    1238796825415389288,  # CEO
    1238796825415389287,  # EXO
    1238796825402544150,  # DEV
    STF_ROLE_ID,           # STF
    PM_ROLE_ID,             # PM
]

# No son jerárquicos entre sí (un instructor puede tener varios a la vez);
# el orden aquí solo desempata cuál se muestra si tiene más de uno.
INSTRUCTOR_ORDER = [
    1238796825402544154,  # CTI
    1238796825402544153,  # CFI
    1238796825402544152,  # GTI
]

ATC_ORDER = [
    1488656415232102460,  # C3
    1238796825390092356,  # C1
    1238796825390092355,  # S3
    1238796825390092354,  # S2
    1238796825390092353,  # S1
    1238796825390092351,  # ATO
]

PILOTO_ORDER = [
    1238796825381834762,  # PCA
    1238796825381834761,  # PPA
    1238796825381834759,  # APA
]

GC_ORDER = [
    1495560972759466124,  # ADT
    1238796825381834755,  # ETG
    1238796825381834754,  # EET
]

# Categorías con "un solo rango a la vez" — se les aplica auto-limpieza.
# Liderazgo NO va aquí: sus roles se pueden combinar libremente (alguien puede
# ser DEV + STF + PM a la vez); LIDERAZGO_ORDER solo define cuál se muestra
# primero en el apodo cuando tiene varios, no cuáles puede tener asignados.
RANKED_CATEGORIES = {
    "ATC": ATC_ORDER,
    "PILOTO": PILOTO_ORDER,
    "GC": GC_ORDER,
}

PREFIX_LABELS = {
    1238796825415389288: "CEO",
    1238796825415389287: "EXO",
    1238796825402544150: "DEV",
    1238796825415389286: "STF",
    1238796825390092358: "PM",
    1238796825402544154: "CTI",
    1238796825402544153: "CFI",
    1238796825402544152: "GTI",
    1488656415232102460: "C3",
    1238796825390092356: "C1",
    1238796825390092355: "S3",
    1238796825390092354: "S2",
    1238796825390092353: "S1",
    1238796825390092351: "ATO",
    1238796825381834762: "PCA",
    1238796825381834761: "PPA",
    1238796825381834759: "APA",
    1495560972759466124: "ADT",
    1238796825381834755: "ETG",
    1238796825381834754: "EET",
}


def _highest(role_ids: set, order: list):
    for rid in order:
        if rid in role_ids:
            return rid
    return None


def _strip_prefix(nombre: str) -> str:
    """Si el nombre ya tiene un prefijo (`X | nombre` o `X / Y | nombre`),
    devuelve solo la parte real del nombre."""
    if " | " in nombre:
        return nombre.split(" | ", 1)[1]
    return nombre


_bloxlink_cache = {}  # discord_id -> (nombre_roblox, expira_en)
BLOXLINK_CACHE_TTL = 300  # 5 minutos — evita golpear la API de Bloxlink en /apodo-todos
_bloxlink_avisado_sin_guild_id = False  # para loguear el aviso de GUILD_ID faltante una sola vez


async def _roblox_username(discord_id: int):
    """Nombre real de Roblox ya verificado en Bloxlink para este Discord ID,
    o None si no está configurado / no se pudo resolver (nunca rompe el
    cálculo del apodo — solo cae a usar el nombre de Discord)."""
    if not BLOXLINK_API_KEY:
        return None
    if not GUILD_ID:
        # Hace falta SÍ o SÍ, aparte de la key — la API de Bloxlink es por
        # servidor. Se loguea una sola vez (no en cada llamada) para que no
        # quede en absoluto silencio por qué nunca aparece el nombre real.
        global _bloxlink_avisado_sin_guild_id
        if not _bloxlink_avisado_sin_guild_id:
            _bloxlink_avisado_sin_guild_id = True
            print("Aviso: BLOXLINK_API_KEY está configurada pero falta DISCORD_GUILD_ID — sin eso no se puede consultar Bloxlink, así que el apodo/los mensajes de vuelo siguen mostrando el nombre de Discord.")
        return None
    ahora = asyncio.get_running_loop().time()
    cacheado = _bloxlink_cache.get(discord_id)
    if cacheado and cacheado[1] > ahora:
        return cacheado[0]
    url = f"https://api.blox.link/v4/public/guilds/{GUILD_ID}/discord-to-roblox/{discord_id}"
    headers = {"Authorization": BLOXLINK_API_KEY}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    detalle = await resp.text()
                    print(f"Aviso: Bloxlink respondió {resp.status} al consultar {discord_id}: {detalle}")
                    return None
                data = await resp.json()

            roblox_id = (data or {}).get("robloxID")
            if not roblox_id:
                print(f"Aviso: Bloxlink respondió OK para {discord_id} pero sin robloxID (¿el usuario no se verificó con Bloxlink?): {data}")
                return None

            # Bloxlink solo devuelve el ID numérico de Roblox — el nombre
            # real hay que resolverlo aparte contra la API pública de Roblox
            # (antes el código asumía un formato de respuesta de Bloxlink
            # que incluía el nombre directo — resolved.roblox.name — pero la
            # API real solo manda {"robloxID": "..."}, así que esto nunca
            # devolvía nada).
            async with session.get(f"https://users.roblox.com/v1/users/{roblox_id}") as resp2:
                if resp2.status != 200:
                    detalle = await resp2.text()
                    print(f"Aviso: la API de Roblox respondió {resp2.status} al resolver el ID {roblox_id}: {detalle}")
                    return None
                datos_roblox = await resp2.json()
    except Exception as err:
        print(f"Aviso: no pude consultar Bloxlink/Roblox para {discord_id}: {err}")
        return None

    nombre = datos_roblox.get("name") or datos_roblox.get("displayName")
    if not nombre:
        return None
    _bloxlink_cache[discord_id] = (nombre, ahora + BLOXLINK_CACHE_TTL)
    return nombre


def build_nickname(member: discord.Member, base_name: str = None):
    """Calcula el apodo con prefijo según la guía de jerarquía. Devuelve
    None si el miembro no tiene ningún rol de instructor/liderazgo/rango
    operativo (no hay nada que anteponer). `base_name` permite pasar el
    nombre real de Roblox (Bloxlink) en vez de derivarlo del apodo actual de
    Discord — ver build_nickname_async()."""
    role_ids = {r.id for r in member.roles}

    instructor = _highest(role_ids, INSTRUCTOR_ORDER)
    liderazgo = _highest(role_ids, LIDERAZGO_ORDER)
    atc = _highest(role_ids, ATC_ORDER)
    piloto = _highest(role_ids, PILOTO_ORDER)
    gc = _highest(role_ids, GC_ORDER)

    slot1 = instructor or liderazgo
    operativas = [c for c in (atc, piloto, gc) if c]  # ya en orden ATC>Piloto>GC

    if slot1:
        slots = [slot1] + operativas[:1]
    else:
        slots = operativas[:2]

    if not slots:
        return None

    prefijo = " / ".join(PREFIX_LABELS[rid] for rid in slots)
    nombre_base = base_name if base_name is not None else _strip_prefix(member.display_name)
    return f"{prefijo} | {nombre_base}"


async def build_nickname_async(member: discord.Member):
    """Versión que prioriza el nombre real de Roblox (Bloxlink) como base
    del apodo, con fallback automático al nombre de Discord."""
    roblox_name = await _roblox_username(member.id)
    base = roblox_name if roblox_name else _strip_prefix(member.display_name)
    return build_nickname(member, base_name=base)


async def enforce_base_tags(member: discord.Member) -> bool:
    """El rol base ATC (Controlador de Tráfico Aéreo) y FLT (Piloto) sigue a
    la rama: si tiene cualquier rango de esa rama (desde estudiante ATO/APA
    en adelante), debe tener el rol base sí o sí; si no tiene ningún rango
    de esa rama, no debe tenerlo. Devuelve True si cambió algo."""
    role_ids = {r.id for r in member.roles}

    tiene_rango_atc = any(rid in role_ids for rid in ATC_ORDER)
    tiene_rango_piloto = any(rid in role_ids for rid in PILOTO_ORDER)

    a_agregar = []
    a_quitar = []

    if tiene_rango_atc and ATC_ROLE_ID not in role_ids:
        a_agregar.append(ATC_ROLE_ID)
    elif not tiene_rango_atc and ATC_ROLE_ID in role_ids:
        a_quitar.append(ATC_ROLE_ID)

    if tiene_rango_piloto and FLT_ROLE_ID not in role_ids:
        a_agregar.append(FLT_ROLE_ID)
    elif not tiene_rango_piloto and FLT_ROLE_ID in role_ids:
        a_quitar.append(FLT_ROLE_ID)

    if not a_agregar and not a_quitar:
        return False

    if a_agregar:
        roles_obj = [r for r in (member.guild.get_role(rid) for rid in a_agregar) if r is not None]
        if roles_obj:
            await member.add_roles(*roles_obj, reason="Rol base sigue al rating (ATC/FLT)")
    if a_quitar:
        roles_obj = [r for r in (member.guild.get_role(rid) for rid in a_quitar) if r is not None]
        if roles_obj:
            await member.remove_roles(*roles_obj, reason="Sin rating en esa rama — se retira el rol base")
    return True


async def enforce_single_rank_per_category(member: discord.Member) -> bool:
    """Si el miembro tiene más de un rango dentro de la misma categoría
    (ej. S1 y S2 a la vez), retira todos menos el más alto. Devuelve True
    si removió algo."""
    role_ids = {r.id for r in member.roles}
    a_remover = []
    for orden in RANKED_CATEGORIES.values():
        presentes = [rid for rid in orden if rid in role_ids]
        if len(presentes) > 1:
            a_remover.extend(presentes[1:])  # se queda con presentes[0], el más alto

    if not a_remover:
        return False

    roles_obj = [member.guild.get_role(rid) for rid in a_remover]
    roles_obj = [r for r in roles_obj if r is not None]
    if roles_obj:
        await member.remove_roles(*roles_obj, reason="Un solo prefijo por categoría — se conserva el rango más alto")
    return True


def has_any_role(member: discord.Member, role_ids: list) -> bool:
    ids = {r.id for r in member.roles}
    return any(rid in ids for rid in role_ids)


# ─── Permisos de moderación centralizados (Fase B) — antes cada comando
# repetía su propia lista de roles inline; ahora hay un solo lugar que
# decide quién puede advertir/silenciar (staff normal) vs. quién puede
# expulsar/banear (solo Liderazgo, acciones más graves e irreversibles).
def es_staff_moderacion(member: discord.Member) -> bool:
    return has_any_role(member, LIDERAZGO_ORDER + INSTRUCTOR_ORDER)


def es_staff_senior(member: discord.Member) -> bool:
    return has_any_role(member, LIDERAZGO_ORDER)


def _categoria_de_rol(role_id: int):
    if role_id in LIDERAZGO_ORDER:
        return "LIDERAZGO"
    if role_id in INSTRUCTOR_ORDER:
        return "INSTRUCTOR"
    if role_id in ATC_ORDER:
        return "ATC"
    if role_id in PILOTO_ORDER:
        return "PILOTO"
    if role_id in GC_ORDER:
        return "GC"
    return None


def _puede_ascender(invocador: discord.Member, categoria: str) -> bool:
    """Liderazgo puede otorgar cualquier categoría. Cada instructor solo
    puede otorgar rangos de SU rama (CTI→ATC, CFI→Piloto, GTI→Equipo de
    Tierra). Instructor/Liderazgo como categoría de DESTINO solo lo puede
    otorgar Liderazgo."""
    if has_any_role(invocador, LIDERAZGO_ORDER):
        return True
    if categoria == "ATC":
        return INSTRUCTOR_ORDER[0] in {r.id for r in invocador.roles}  # CTI
    if categoria == "PILOTO":
        return INSTRUCTOR_ORDER[1] in {r.id for r in invocador.roles}  # CFI
    if categoria == "GC":
        return INSTRUCTOR_ORDER[2] in {r.id for r in invocador.roles}  # GTI
    return False


def _puede_ascender_alguna(invocador: discord.Member) -> bool:
    """True si el invocador puede ascender a alguien en AL MENOS una
    categoría (Liderazgo, o instructor de alguna rama) — usado para decidir
    si se muestra el botón "Ascender" dentro de /academia."""
    if has_any_role(invocador, LIDERAZGO_ORDER):
        return True
    ids = {r.id for r in invocador.roles}
    return any(rid in ids for rid in INSTRUCTOR_ORDER)



BRANCH_LABEL = {"atc": "🛫 ATC", "pilot": "✈️ Piloto", "gc": "🧰 Equipo Terrestre"}
BRANCH_ORDER = ["atc", "pilot", "gc"]

# Banners de Academia (uno por rama, con 2 variantes cada uno que se eligen
# al azar en cada envío) — no hay persistencia de cuál se mandó la vez
# anterior, a diferencia del sistema de imagen de embeds formales (que es
# un pedido aparte y todavía no se construye): acá alcanza con variar.
BRANCH_BANNERS = {
    "atc": ["banner_academia_atc_1.png", "banner_academia_atc_2.png"],
    "pilot": ["banner_academia_pilotos_1.png", "banner_academia_pilotos_2.png"],
    "gc": ["banner_academia_groundcrew_1.png", "banner_academia_groundcrew_2.png"],
}


def _nombre_banner_academia(rama: str, semilla=None) -> str | None:
    """Con semilla (ej. el uuid de una sesión) elige siempre la misma
    variante para esa semilla — necesario para los paneles Components V2 de
    sesiones en vivo, que se editan in-place más de una vez (join/salir) y
    tienen que seguir referenciando el mismo archivo ya adjunto al mensaje
    original en vez de "cambiar" de foto en cada edición."""
    variantes = BRANCH_BANNERS.get(rama)
    if not variantes:
        return None
    if semilla is not None:
        return random.Random(str(semilla)).choice(variantes)
    return random.choice(variantes)


def _archivo_banner_academia(rama: str, semilla=None) -> discord.File | None:
    nombre = _nombre_banner_academia(rama, semilla)
    if not nombre:
        return None
    ruta = os.path.join(CARPETA_SCRIPT, "assets", nombre)
    if not os.path.exists(ruta):
        return None
    return discord.File(ruta, filename=nombre)


# ─── Imagen de identidad institucional — REGLA GLOBAL para embeds formales ──
# Se aplica a TODO embed formal/institucional (info del servidor, anuncios,
# moderación, soporte, verificación, sistemas de administración, info de
# ATC/vuelos, confirmaciones, comunicados, dashboards institucionales) —
# EXCLUYE explícitamente todo lo de Academia (clases, evaluaciones,
# certificados, sesiones, panel de instructor), que tiene su propia
# identidad separada (ver BRANCH_BANNERS arriba). No reemplaza ni rediseña
# ningún embed existente — solo le agrega la imagen respetando su
# estructura actual.
FORMAL_EMBED_IMAGES = ["formal_embed_1.png", "formal_embed_2.png"]


def _ruta_imagen_formal(nombre: str) -> str:
    return os.path.join(CARPETA_SCRIPT, "assets", nombre)


async def _nombre_imagen_formal_siguiente() -> str | None:
    """Alternancia balanceada real (round-robin, no al azar) y persistida en
    bot_state — para embeds formales que se mandan UNA sola vez y no se
    vuelven a editar después. Un reinicio del bot no reinicia el contador:
    sigue desde donde quedó."""
    variantes = [n for n in FORMAL_EMBED_IMAGES if os.path.exists(_ruta_imagen_formal(n))]
    if not variantes:
        return None
    ultimo = await bot_state.get(db, "formal_embed_image_last")
    try:
        indice_anterior = variantes.index(ultimo)
    except ValueError:
        indice_anterior = -1
    siguiente = variantes[(indice_anterior + 1) % len(variantes)]
    await bot_state.set(db, "formal_embed_image_last", siguiente)
    return siguiente


def _nombre_imagen_formal_determinista(semilla) -> str | None:
    """Para embeds formales que SÍ se editan más de una vez después de
    enviados (paneles privados de vuelo/ATC, el menú de !help) — la misma
    semilla elige siempre la misma variante, para no romper la referencia
    attachment:// en la próxima edición (esa imagen ya no cambia más para
    ESE mensaje puntual, pero sigue siendo una de las dos oficiales)."""
    variantes = [n for n in FORMAL_EMBED_IMAGES if os.path.exists(_ruta_imagen_formal(n))]
    if not variantes:
        return None
    return random.Random(str(semilla)).choice(variantes)


def _archivo_imagen_formal(nombre: str | None) -> discord.File | None:
    if not nombre:
        return None
    ruta = _ruta_imagen_formal(nombre)
    if not os.path.exists(ruta):
        return None
    return discord.File(ruta, filename=nombre)


async def aplicar_imagen_formal(embed: discord.Embed) -> discord.File | None:
    """Función central pedida en la especificación (equivalente a
    getFormalEmbedImage()) para embeds que se mandan una sola vez. Aplica la
    imagen al embed (mutación in-place) y devuelve el discord.File que hay
    que pasar en el `file=`/`files=` del envío — si no hay imagen
    disponible, no toca el embed y devuelve None."""
    nombre = await _nombre_imagen_formal_siguiente()
    archivo = _archivo_imagen_formal(nombre)
    if archivo:
        embed.set_image(url=f"attachment://{archivo.filename}")
    return archivo


def aplicar_imagen_formal_determinista(embed: discord.Embed, semilla) -> discord.File | None:
    """Misma idea que aplicar_imagen_formal pero para embeds que se van a
    editar más de una vez (ver _nombre_imagen_formal_determinista)."""
    nombre = _nombre_imagen_formal_determinista(semilla)
    archivo = _archivo_imagen_formal(nombre)
    if archivo:
        embed.set_image(url=f"attachment://{archivo.filename}")
    return archivo

COURSE_STATE_LABEL = {
    "locked": f"{E['cruz']} Bloqueado",
    "in_progress": f"{E['libro']} En progreso",
    "theory_done": f"{E['libro']} Teoría completada",
    "completed": f"{E['check']} Completado",
}
EVAL_STATE_LABEL = {
    "locked": "Evaluación bloqueada",
    "available": "Evaluación disponible",
    "pending": f"{E['reloj']} Evaluación en revisión",
    "approved": f"{E['check']} Evaluación aprobada",
    "rejected": f"{E['cruz']} Evaluación rechazada",
}
CERT_TYPE_LABEL = {"final": f"{E['verificado']} Certificado final", "theory": f"{E['libro']} Certificado de teoría"}
CERT_TYPE_ORDEN = {"final": 0, "theory": 1}


def _agrupar_por_rama(items: list, campo_rama: str = "branch") -> dict:
    grupos = {b: [] for b in BRANCH_ORDER}
    for it in items:
        grupos.setdefault(it.get(campo_rama), []).append(it)
    return grupos


async def _certificado_en_rama(discord_id: str, branch: str) -> bool:
    """Motor propio de Academia (Fase C) — antes le pegaba a la web."""
    return await academy_core.has_final_certificate(db, str(discord_id), branch)


# Todas las categorías ascendibles por comando (Liderazgo/Instructor incluidos,
# para que el staff tenga un solo comando en vez de asignar roles a mano).
RANGO_CHOICES = [
    app_commands.Choice(name=PREFIX_LABELS[rid], value=str(rid))
    for rid in (LIDERAZGO_ORDER + INSTRUCTOR_ORDER + ATC_ORDER + PILOTO_ORDER + GC_ORDER)
]


async def _target_nickname(member: discord.Member):
    """Nombre final que debería tener el miembro: con prefijo si le
    corresponde, o su nombre limpio (sin prefijo) si no tiene ningún rol
    que lo amerite. Ya usa el nombre real de Roblox (Bloxlink) si está
    disponible."""
    nuevo = await build_nickname_async(member)
    if nuevo is not None:
        return nuevo
    roblox_name = await _roblox_username(member.id)
    return roblox_name if roblox_name else _strip_prefix(member.display_name)


async def actualizar_apodo(member: discord.Member):
    """Sigue el flujo pedido: BORRAR apodo actual -> CALCULAR el nuevo ->
    CAMBIAR. Si el miembro ya tiene exactamente el apodo que le corresponde,
    no hace ninguna llamada a la API (evita rate-limit innecesario en
    /apodo-todos). Devuelve (nombre_final, hubo_cambio)."""
    objetivo = await _target_nickname(member)
    if objetivo == member.display_name:
        return objetivo, False

    if member.nick is not None:
        await member.edit(nick=None, reason="Actualización de apodo — paso 1/2: borrar")

    nuevo = await build_nickname_async(member)
    if nuevo:
        await member.edit(nick=nuevo, reason="Actualización de apodo — paso 2/2: cambiar")

    return objetivo, True


# ─────────────────────────────────────────────────────────────
# El flujo "Elige tu rama" (BienvenidaView + _asignar_rol_bienvenida +
# _mandar_eleccion_de_rama_por_dm) se retiró por completo a pedido
# explícito del usuario — ya no hay inscripción automática en Academia
# disparada desde la verificación. Un mecanismo nuevo para inscribirse se
# va a definir y configurar más adelante; mientras tanto, la inscripción
# manual sigue disponible más adelante en el flujo de /academia una vez
# que se decida cómo reemplazar esto.

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # necesario para poder modificar roles de miembros

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


async def _ping(_request):
    """Endpoint que UptimeRobot visita para mantener el servicio despierto."""
    return web.Response(text="ATC24 Español — bot de verificación activo.")


async def iniciar_servidor_web():
    """Levanta el mini servidor HTTP que Render necesita para no apagar el
    servicio. Desde la Fase E ya NO recibe webhooks de la web (ese canal se
    cortó junto con server.js) — solo queda el keep-alive que usa UptimeRobot."""
    app = web.Application()
    app.router.add_get("/", _ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"Mini servidor web escuchando en el puerto {PORT} (para Render/UptimeRobot).")


_presencia_iniciada = False  # evita arrancar el loop dos veces si on_ready se dispara de nuevo (reconexión)
_foto_semana_iniciada = False  # mismo motivo, para el loop de votación semanal
# Unix epoch en milisegundos — formato que espera Discord en timestamps.start
# (documentado), más seguro que pasar un datetime como kwarg suelto.
_BOT_START_MS = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)


def _plural(n, singular, plural):
    """'1 vuelo activo' en vez de '1 vuelos activos' — detalle chico pero se nota."""
    return f"{n} {singular if n == 1 else plural}"


# Easter eggs: variantes fijas, sin datos en vivo, que aparecen de vez en
# cuando en vez de la rotación normal — probabilidad baja a propósito para
# que sigan siendo un hallazgo, no lo que se ve la mayoría del tiempo.
_PRESENCIA_EASTER_EGGS = [
    (discord.ActivityType.listening, "el ruido de la torre de control"),
    (discord.ActivityType.watching, "un aterrizaje perfecto"),
    (discord.ActivityType.playing, "con el squawk 7700 (es broma)"),
    (discord.ActivityType.competing, "contra la niebla en IRFD"),
    (discord.ActivityType.watching, "cómo despega el sol"),
]
_PRESENCIA_EASTER_EGG_PROBABILIDAD = 0.12  # ~1 de cada 8 rotaciones


async def _actualizar_presencia_loop():
    """Rota entre varios estados en vez de mostrar siempre el mismo texto fijo
    — sigue siendo texto simple (límite real de Discord para bots, ver charla
    sobre rich presence de usuario), pero se siente más vivo. Desde la Fase A
    los conteos salen del motor propio del bot (atc_core), ya no de la web —
    esto además deja de depender de que atc24espanol.lat esté arriba."""
    indice = 0
    while True:
        try:
            vuelos = await atc_core.count_active_flights(db)
            controladores = len(await atc_core.get_active_atc(db))
            rol_v = client.guilds[0].get_role(V_ROLE_ID) if client.guilds else None
            verificados = len(rol_v.members) if rol_v else 0

            variantes = [
                (discord.ActivityType.watching,
                    _plural(vuelos, "vuelo activo", "vuelos activos") if vuelos else "el radar en silencio"),
                (discord.ActivityType.watching,
                    _plural(controladores, "controlador en línea", "controladores en línea") if controladores else "el espacio aéreo sin control"),
                (discord.ActivityType.competing, "ATC24 Español"),
                (discord.ActivityType.listening, "/academia y /ascender"),
                (discord.ActivityType.watching, _plural(verificados, "piloto o controlador verificado", "pilotos y controladores verificados")),
                (discord.ActivityType.playing, "Volando contigo de la mano"),
            ]
            if random.random() < _PRESENCIA_EASTER_EGG_PROBABILIDAD:
                tipo, texto = random.choice(_PRESENCIA_EASTER_EGGS)
            else:
                tipo, texto = variantes[indice % len(variantes)]
                indice += 1
            # timestamps.start hace que Discord muestre "hace X tiempo" y lo
            # vaya actualizando solo en el cliente de cada usuario — no hace
            # falta que nosotros recalculemos ningún texto de tiempo a mano.
            actividad = discord.Activity(type=tipo, name=texto, timestamps={"start": _BOT_START_MS})
            await client.change_presence(activity=actividad)
        except Exception as err:
            print(f"Aviso: no pude actualizar el rich presence: {err}")
        await asyncio.sleep(20)  # rota cada 20s; los easter eggs se intercalan al azar


# ─── Foto de la semana ─────────────────────────────────────────────────────
# Ciclo semanal, anclado a un instante EXACTO en UTC (viernes 00:00 UTC), no
# a un sondeo cada tanto sobre la fecha local del sistema:
#   1. Nominación: cualquiera reacciona con ⭐ a una foto en el canal
#      correspondiente durante la semana — se guarda en "nominadas".
#   2. En cada viernes 00:00 UTC, "_ejecutar_reset_semanal_foto" hace dos
#      cosas en el mismo instante: cierra la votación de la semana anterior
#      (si había una abierta) y anuncia a la ganadora con su imagen, y abre
#      la votación formal de esta semana — pero solo si hay al menos 3
#      nominadas; si hay menos de 3, las nominadas quedan pendientes para la
#      semana siguiente en vez de forzar una votación con pocas opciones.
#   3. La votación es por botón (Components V2), un voto por persona — el
#      usuario puede cambiar su voto mientras la votación sigue abierta.
_estado_foto_semana = _leer_json("foto_semana.json", {
    "nominadas": [],       # [{message_id, author_id, jump_url}, ...] — semana en curso, sin votación abierta todavía
    "votacion": None,      # {"message_id", "opciones": [...], "votos": {user_id: message_id_opcion}} o None
    "ultimo_reset_utc": None,  # ISO del último viernes 00:00 UTC ya procesado — evita reprocesar el mismo boundary
})


def _proximo_viernes_00_utc(desde: "datetime.datetime") -> "datetime.datetime":
    dias_hasta_viernes = (4 - desde.weekday()) % 7  # 4 = viernes
    candidato = datetime.datetime.combine(
        desde.date(), datetime.time(0, 0), tzinfo=datetime.timezone.utc
    ) + datetime.timedelta(days=dias_hasta_viernes)
    if candidato <= desde:
        candidato += datetime.timedelta(days=7)
    return candidato


def _viernes_00_utc_anterior(desde: "datetime.datetime") -> "datetime.datetime":
    return _proximo_viernes_00_utc(desde) - datetime.timedelta(days=7)


@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == client.user.id:
        return
    if str(payload.channel_id) != str(DISCORD_CHANNEL_FOTO_SEMANA):
        return
    if str(payload.emoji) != "⭐":
        return

    if any(n["message_id"] == payload.message_id for n in _estado_foto_semana["nominadas"]):
        return  # ya estaba nominada

    try:
        canal = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
        mensaje = await canal.fetch_message(payload.message_id)
    except discord.HTTPException:
        return

    imagen_url = None
    for a in mensaje.attachments:
        if (a.content_type or "").startswith("image/"):
            imagen_url = a.url
            break
    if imagen_url is None:
        for e in mensaje.embeds:
            if e.type == "image" and e.url:
                imagen_url = e.url
                break
    if imagen_url is None:
        return  # el mensaje reaccionado no tiene ninguna imagen — no es una foto válida

    _estado_foto_semana["nominadas"].append({
        "message_id": mensaje.id,
        "author_id": mensaje.author.id,
        "jump_url": mensaje.jump_url,
        "imagen_url": imagen_url,
    })
    _guardar_json("foto_semana.json", _estado_foto_semana)


def _construir_payload_votacion_foto_semana(opciones: list) -> dict:
    lineas = [
        "# Votación — Foto de la semana",
        "",
        "Elige tu foto favorita de las nominadas esta semana. Un voto por persona; "
        "podés cambiar tu voto mientras la votación siga abierta.",
        "",
    ]
    for i, o in enumerate(opciones, start=1):
        votos = o.get("votos", 0)
        lineas.append(f"**{i}.** <@{o['author_id']}> — {_plural(votos, 'voto', 'votos')} — {o['jump_url']}")

    filas = []
    fila_actual = []
    for i, o in enumerate(opciones, start=1):
        fila_actual.append({
            "type": 2,
            "style": 1,
            "label": f"Votar {i}",
            "custom_id": f"foto_voto:{o['message_id']}",
        })
        if len(fila_actual) == 5:
            filas.append({"type": 1, "components": fila_actual})
            fila_actual = []
    if fila_actual:
        filas.append({"type": 1, "components": fila_actual})

    return {
        "flags": 32768,
        "allowed_mentions": {"parse": []},
        "components": [
            {
                "type": 17,
                "accent_color": BRAND_BEACON_AMBER,
                "components": [{"type": 10, "content": "\n".join(lineas)}, *filas],
            },
        ],
    }


async def _abrir_votacion_foto_semana():
    """Si hay al menos 3 nominadas, publica la votación formal y limpia la
    lista de nominadas (pasan a ser las opciones de la votación). Con menos
    de 3, no abre nada — las nominadas quedan pendientes para la próxima
    semana."""
    nominadas = _estado_foto_semana["nominadas"]
    if len(nominadas) < 3 or not DISCORD_CHANNEL_FOTO_SEMANA:
        return

    opciones = [
        {"message_id": n["message_id"], "author_id": n["author_id"], "jump_url": n["jump_url"],
         "imagen_url": n["imagen_url"], "votos": 0}
        for n in nominadas
    ]
    payload = _construir_payload_votacion_foto_semana(opciones)
    try:
        mensaje = await _publicar_payload_crudo(int(DISCORD_CHANNEL_FOTO_SEMANA), payload)
    except Exception as err:
        print(f"ERROR al publicar la votación de foto de la semana: {err}")
        return

    _estado_foto_semana["nominadas"] = []
    _estado_foto_semana["votacion"] = {"message_id": mensaje["id"], "opciones": opciones, "votos": {}}
    _guardar_json("foto_semana.json", _estado_foto_semana)


async def _procesar_voto_foto_semana(interaction: discord.Interaction, message_id_opcion: str):
    votacion = _estado_foto_semana.get("votacion")
    if not votacion or str(interaction.message.id) != str(votacion["message_id"]):
        await interaction.response.send_message("Esta votación ya cerró.", ephemeral=True)
        return

    votacion["votos"][str(interaction.user.id)] = message_id_opcion
    for o in votacion["opciones"]:
        o["votos"] = sum(1 for elegido in votacion["votos"].values() if elegido == str(o["message_id"]))
    _guardar_json("foto_semana.json", _estado_foto_semana)

    # interaction.response.edit_message() de discord.py no entiende
    # Components V2 crudo (mismo motivo que el resto de los paneles de este
    # bot) — se edita por REST directo y se responde el clic aparte.
    payload = _construir_payload_votacion_foto_semana(votacion["opciones"])
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    await _discord_request(
        "PATCH",
        f"https://discord.com/api/v10/channels/{interaction.channel_id}/messages/{interaction.message.id}",
        headers=headers, json_body=payload,
    )
    await interaction.response.send_message("Tu voto quedó registrado.", ephemeral=True)


async def _cerrar_votacion_y_anunciar_ganadora():
    votacion = _estado_foto_semana.get("votacion")
    if not votacion or not DISCORD_CHANNEL_FOTO_SEMANA:
        _estado_foto_semana["votacion"] = None
        return

    opciones = votacion["opciones"]
    if not opciones:
        _estado_foto_semana["votacion"] = None
        return

    ganadora = max(opciones, key=lambda o: o.get("votos", 0))
    payload = {
        "flags": 32768,
        "allowed_mentions": {"parse": []},
        "components": [
            {
                "type": 17,
                "accent_color": BRAND_BEACON_AMBER,
                "components": [
                    {"type": 10, "content": "# 🏆 Foto de la semana"},
                    {"type": 14, "divider": True, "spacing": 1},
                    {
                        "type": 10,
                        "content": (
                            f"Con {_plural(ganadora.get('votos', 0), 'voto', 'votos')}, la foto de la semana es de "
                            f"<@{ganadora['author_id']}>.\n{ganadora['jump_url']}"
                        ),
                    },
                    {"type": 12, "items": [{"media": {"url": ganadora["imagen_url"]}}]},
                ],
            },
        ],
    }
    try:
        await _publicar_payload_crudo(int(DISCORD_CHANNEL_FOTO_SEMANA), payload)
    except Exception as err:
        print(f"ERROR al anunciar la ganadora de foto de la semana: {err}")

    _estado_foto_semana["votacion"] = None


async def _ejecutar_reset_semanal_foto():
    await _cerrar_votacion_y_anunciar_ganadora()
    await _abrir_votacion_foto_semana()
    _guardar_json("foto_semana.json", _estado_foto_semana)


async def _foto_semana_loop():
    await client.wait_until_ready()

    # Si el bot estuvo apagado durante un viernes 00:00 UTC, ese boundary se
    # procesa apenas arranca en vez de esperar hasta el próximo — así el
    # reset semanal no se salta una semana entera por una caída/reinicio.
    ahora = datetime.datetime.now(datetime.timezone.utc)
    ultimo_boundary = _viernes_00_utc_anterior(ahora)
    if _estado_foto_semana.get("ultimo_reset_utc") != ultimo_boundary.isoformat():
        try:
            await _ejecutar_reset_semanal_foto()
        except Exception as err:
            print(f"Aviso: fallo en el reset semanal (recuperación al arrancar) de foto de la semana: {err}")
        _estado_foto_semana["ultimo_reset_utc"] = ultimo_boundary.isoformat()
        _guardar_json("foto_semana.json", _estado_foto_semana)

    while True:
        ahora = datetime.datetime.now(datetime.timezone.utc)
        objetivo = _proximo_viernes_00_utc(ahora)
        await asyncio.sleep(max((objetivo - ahora).total_seconds(), 1))
        try:
            await _ejecutar_reset_semanal_foto()
        except Exception as err:
            print(f"Aviso: fallo en el reset semanal de foto de la semana: {err}")
        _estado_foto_semana["ultimo_reset_utc"] = objetivo.isoformat()
        _guardar_json("foto_semana.json", _estado_foto_semana)


_mantenimiento_atc_iniciado = False  # mismo motivo que los otros flags "_iniciada"
_sesiones_academia_iniciado = False  # mismo motivo
_paneles_permanentes_iniciado = False  # mismo motivo — reconciliación de message_id persistidos


async def _reconciliar_atc_al_arrancar():
    """Al reiniciar el proceso se pierden los timers de auto-cierre en
    memoria (_atc_close_timers) — sin esto, una posición cuyo canal de voz
    quedó vacío justo antes de un reinicio se quedaría abierta para siempre.
    Recorre las posiciones activas y rearma el timer para las que ya están
    vacías ahora mismo."""
    if not GUILD_ID:
        return
    guild = client.get_guild(int(GUILD_ID))
    if not guild:
        return
    for fila in await atc_core.get_active_atc(db):
        voice_id = fila.get("voice_channel_id")
        if not voice_id:
            continue
        canal = guild.get_channel(int(voice_id))
        if canal is None:
            # El canal ya no existe (se borró a mano) — cierra la posición.
            cerrada = await atc_core.close_atc(db, fila["uuid"], reason="Canal de voz ya no existe")
            if cerrada:
                print(f"Posición ATC {cerrada['airport']}_{cerrada['position_type']} cerrada: su canal de voz ya no existía.")
            continue
        if not any(not m.bot for m in canal.members) and fila["uuid"] not in _atc_close_timers:
            _atc_close_timers[fila["uuid"]] = client.loop.create_task(
                _cerrar_atc_por_inactividad(fila["uuid"], voice_id)
            )


async def _reconciliar_message_id_guardado(clave: str, channel_id) -> str | None:
    """Recupera un message_id guardado en bot_state y confirma con la API
    de Discord que el mensaje todavía existe antes de darlo por bueno — si
    lo borraron a mano mientras el bot estaba apagado, se limpia el estado
    guardado para que el próximo repost cree uno nuevo en vez de intentar
    editar un mensaje que ya no está."""
    guardado = await bot_state.get(db, clave)
    if not guardado or not channel_id:
        return None
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    status, _data, _texto = await _discord_request(
        "GET", f"https://discord.com/api/v10/channels/{channel_id}/messages/{guardado}", headers=headers,
    )
    if status == 200:
        return guardado
    await bot_state.set(db, clave, None)
    return None


async def _reconciliar_paneles_permanentes_al_arrancar():
    """Bloque de persistencia entre reinicios: retoma la tabla ATC y el
    panel de sesiones agendadas guardados en bot_state (si siguen existiendo
    en Discord) en vez de perder la referencia y crear paneles duplicados en
    el próximo repost."""
    global _tabla_atc_message_id, _sesiones_message_id
    _tabla_atc_message_id = await _reconciliar_message_id_guardado("tabla_atc_message_id", DISCORD_CHANNEL_ATC)
    if _tabla_atc_message_id:
        print(f"Tabla ATC: retomo el panel existente ({_tabla_atc_message_id}) tras el reinicio.")
    _sesiones_message_id = await _reconciliar_message_id_guardado(
        "sesiones_agendadas_message_id", DISCORD_CHANNEL_ACADEMIA_SESIONES
    )
    if _sesiones_message_id:
        print(f"Sesiones agendadas: retomo el panel existente ({_sesiones_message_id}) tras el reinicio.")


async def _mantenimiento_atc_loop():
    """Bloque A5: reemplaza el viejo barrido de 8h por el temporizador de
    inactividad real (14 min sin cambios → aviso con 1 min de gracia → +5
    min si confirma → cierre automático si no)."""
    while True:
        await asyncio.sleep(60)
        try:
            for fila in await atc_core.flights_para_avisar(db):
                await atc_core.marcar_flight_avisado(db, fila["uuid"])
                try:
                    usuario = await client.fetch_user(int(fila["owner_id"]))
                    await usuario.send(
                        f"Tu plan de vuelo **{fila['callsign']}** lleva {atc_core.INACTIVIDAD_AVISO_MIN} minutos "
                        f"sin cambios. Se va a finalizar automáticamente en {atc_core.INACTIVIDAD_GRACIA_MIN} "
                        "minuto si no confirmas que sigues en vuelo.",
                        view=ConfirmarVueloView(fila["uuid"]),
                    )
                except (discord.Forbidden, discord.NotFound):
                    pass

            for fila in await atc_core.flights_para_finalizar_por_inactividad(db):
                cerrado = await atc_core.close_flight(db, fila["uuid"], atc_core.FLIGHT_EXPIRADO)
                if not cerrado:
                    continue
                await _cerrar_dashboard_vuelo(cerrado)
                try:
                    usuario = await client.fetch_user(int(cerrado["owner_id"]))
                    await usuario.send(f"Tu plan de vuelo **{cerrado['callsign']}** se finalizó automáticamente por inactividad.")
                except (discord.Forbidden, discord.NotFound):
                    pass

            # Bloque A7: cierres de ATC programados desde el panel privado
            # (botón "Programar cierre") cuya hora ya llegó.
            for fila in await atc_core.atc_pendientes_de_cierre_programado(db):
                cerrada = await atc_core.close_atc(db, fila["uuid"], reason="Cierre programado desde el panel")
                if not cerrada:
                    continue
                await _borrar_aviso_cierre_programado(fila)
                _cancelar_timer_cierre_atc(cerrada["uuid"])
                await _borrar_canal_atc(cerrada)
                await _cerrar_dashboard_atc(cerrada)
                try:
                    await _repostear_tabla_atc(await _activos_para_tabla())
                except Exception as err:
                    print(f"ERROR al actualizar la tabla ATC tras un cierre programado: {err}")
        except Exception as err:
            print(f"Aviso: fallo en el loop de mantenimiento ATC: {err}")


@client.event
async def on_ready():
    print(f"Conectado como {client.user} (id {client.user.id})")
    print("Esperando el comando !publicar-verificacion en algún canal…")
    print("El bot debe seguir corriendo para poder reaccionar a los clics del botón.\n")

    global _presencia_iniciada, _foto_semana_iniciada, _mantenimiento_atc_iniciado, _sesiones_academia_iniciado
    global _paneles_permanentes_iniciado
    if not _paneles_permanentes_iniciado:
        _paneles_permanentes_iniciado = True
        try:
            await _reconciliar_paneles_permanentes_al_arrancar()
        except Exception as err:
            print(f"Aviso: no pude reconciliar los paneles permanentes al arrancar: {err}")
    if not _presencia_iniciada:
        _presencia_iniciada = True
        client.loop.create_task(_actualizar_presencia_loop())
    if not _foto_semana_iniciada:
        _foto_semana_iniciada = True
        client.loop.create_task(_foto_semana_loop())
    if not _mantenimiento_atc_iniciado:
        _mantenimiento_atc_iniciado = True
        await _reconciliar_atc_al_arrancar()
        client.loop.create_task(_mantenimiento_atc_loop())
    if not _sesiones_academia_iniciado:
        _sesiones_academia_iniciado = True
        client.loop.create_task(_sesiones_academia_loop())


@client.event
async def setup_hook():
    global db
    db = await atc_core.init_db(ATC_DB_PATH)
    await moderation_core.init_schema(db)
    await academy_core.init_schema(db)
    await sessions_core.init_schema(db)
    await bot_state.init_schema(db)
    if not await academy_core.has_courses(db):
        print(
            "Aviso: la Academia todavía no tiene cursos cargados en la base propia del bot. "
            "Corré `python migrar_academia.py <ruta a ATC24Espanol/data.db>` una vez para traer "
            "el catálogo y el progreso de alumnos ya existentes."
        )
    print(f"Motor de datos propio (SQLite) listo en {ATC_DB_PATH}")

    # Se ejecuta antes de conectar a Discord — arrancamos el mini servidor
    # web aquí para que Render vea el puerto abierto cuanto antes.
    await iniciar_servidor_web()

    client.add_view(TicketPanelView())  # botón "Abrir ticket" del panel fijo
    client.add_view(TicketCanalView())  # botón "Cerrar ticket" dentro de cada canal de ticket
    client.add_view(StaffDashboardView())  # Bloque B5: botones del Dashboard de Staff (!staff)

    # Antes esto no tenía try/except: si Discord (o Cloudflare delante de
    # Discord, bajo tráfico/rate-limit) respondía con un error transitorio aquí,
    # el bot se caía ENTERO al arrancar — y como Render lo reinicia solo, eso
    # arma un crash-loop que se auto-alimenta (cada reintento de sync hace más
    # probable el próximo bloqueo). Un fallo de sync no debe tumbar el bot: los
    # comandos ya registrados de una sincronización previa siguen funcionando
    # igual, sólo no se actualizan hasta el próximo arranque exitoso.
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=int(GUILD_ID))
            tree.copy_global_to(guild=guild_obj)
            await tree.sync(guild=guild_obj)
            # Purga cualquier comando global viejo (de antes de configurar
            # GUILD_ID, o de un despliegue anterior) — si no se limpia,
            # Discord muestra ESE comando global además del que acabamos de
            # sincronizar al servidor, y aparece duplicado en el selector "/".
            tree.clear_commands(guild=None)
            await tree.sync()
            print(f"Slash commands sincronizados al servidor {GUILD_ID} (instantáneo) y comandos globales viejos purgados.")
        else:
            await tree.sync()
            print("Slash commands sincronizados globalmente (puede tardar ~1h en propagarse).")
    except Exception as err:
        print(f"Aviso: no pude sincronizar los slash commands al arrancar (sigo con los que ya estaban registrados): {err}")


def _payload_bienvenida_texto(member: discord.Member) -> dict:
    """Contenedor 2 (texto) del mensaje de bienvenida — Components V2.
    Contenido fijo pedido por el usuario; <@usuario> se sustituye por la
    mención real de quien se une."""
    return {
        "type": 17,
        "accent_color": BRAND_BEACON_AMBER,
        "components": [
            {"type": 10, "content": "# Bienvenido a ATC24 Español"},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": f"¡Hola, {member.mention}!"},
            {"type": 10, "content": (
                "Has llegado a **ATC24 Español**, una comunidad creada para quienes comparten la misma "
                "pasión: la aviación virtual."
            )},
            {"type": 10, "content": (
                "Aquí encontrarás un espacio para **pilotos, controladores y entusiastas de la aviación**, "
                "donde podrás aprender, practicar, participar en operaciones y mejorar tus habilidades "
                "dentro de ATC24."
            )},
            {"type": 10, "content": (
                "✈️ **Pilota.** Perfecciona tus procedimientos y operaciones de vuelo.\n"
                "📡 **Controla.** Aprende y practica comunicaciones y control de tránsito aéreo.\n"
                "🎓 **Aprende.** Forma parte de nuestra academia y progresa junto a la comunidad.\n"
                "🌎 **Participa.** Únete a vuelos, eventos y operaciones especiales."
            )},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": (
                "Antes de comenzar, revisa la información y las reglas del servidor y completa el proceso "
                "de verificación."
            )},
            {"type": 10, "content": "**Tu operación comienza aquí.**"},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": "### ATC24 Español\n**Aviación virtual, formación y operaciones.**"},
        ],
    }


def _payload_bienvenida_imagen() -> dict:
    """Contenedor 1 — exclusivamente visual (Media Gallery, type 12) con el
    banner fijo de bienvenida. Requiere que RUTA_BANNER_BIENVENIDA exista;
    se referencia como attachment:// porque se manda junto con el archivo
    en el mismo POST multipart (ver _publicar_payload_con_archivo)."""
    return {
        "type": 17,
        "accent_color": BRAND_BEACON_AMBER,
        "components": [
            {"type": 12, "items": [{"media": {"url": "attachment://banner_bienvenida.png"}}]},
        ],
    }


async def _enviar_bienvenida(member: discord.Member, canal: discord.TextChannel) -> None:
    """Mensaje de bienvenida en 2 contenedores Components V2 apilados
    (banner + texto) en un solo mensaje — reemplaza a la tarjeta con avatar
    generada por welcome_card.py. Si todavía no existe el banner real
    (RUTA_BANNER_BIENVENIDA), se manda solo el contenedor de texto y se
    avisa por consola, sin romper el flujo de bienvenida."""
    payload_texto = _payload_bienvenida_texto(member)
    if os.path.exists(RUTA_BANNER_BIENVENIDA):
        payload = {
            "flags": 32768, "allowed_mentions": {"parse": []},
            "components": [_payload_bienvenida_imagen(), payload_texto],
        }
        try:
            await _publicar_payload_con_archivo(
                canal.id, payload, ruta_archivo=RUTA_BANNER_BIENVENIDA, nombre_archivo="banner_bienvenida.png"
            )
            return
        except Exception as err:
            print(f"ERROR al publicar la bienvenida con banner: {err}")
    else:
        print(f"Aviso: falta {RUTA_BANNER_BIENVENIDA} — mando la bienvenida solo con el contenedor de texto.")

    payload = {"flags": 32768, "allowed_mentions": {"parse": []}, "components": [payload_texto]}
    try:
        await _publicar_payload_crudo(canal.id, payload)
    except Exception as err:
        print(f"ERROR al publicar la bienvenida: {err}")


@client.event
async def on_member_join(member: discord.Member):
    # Se une por primera vez o vuelve a unirse tras haberse ido — en ambos
    # casos Discord lo deja sin roles, así que hay que darle NV explícito.
    nv_role = member.guild.get_role(NV_ROLE_ID)
    if nv_role is not None:
        try:
            await member.add_roles(nv_role, reason="Ingreso al servidor — pendiente de verificación")
        except discord.Forbidden:
            print(f"ERROR 403: no pude darle NV a {member} al unirse — revisa la jerarquía del bot.")
    else:
        print("ERROR: no se encontró el rol NV_ROLE_ID en este servidor.")

    if LLEGADAS_CHANNEL_ID is not None:
        canal = member.guild.get_channel(LLEGADAS_CHANNEL_ID)
        if canal is None:
            print(f"ERROR: no encontré el canal de llegadas {LLEGADAS_CHANNEL_ID}")
        else:
            await _enviar_bienvenida(member, canal)

    # El DM para elegir rama se manda recién cuando se verifica de verdad
    # (aprieta "Acepto y confirmo" en el mensaje de !publicar-verificacion),
    # no aquí — ver on_interaction más abajo.


@client.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if before.roles == after.roles:
        return
    try:
        await enforce_single_rank_per_category(after)
        await enforce_base_tags(after)
        await actualizar_apodo(after)  # sigue el flujo borrar->calcular->cambiar
    except discord.Forbidden:
        print(f"No pude actualizar roles/apodo de {after} — jerarquía del bot insuficiente o es el dueño del servidor.")
    except discord.HTTPException as err:
        print(f"Error de Discord actualizando a {after}: {err}")


async def _procesar_ascenso(interaction: discord.Interaction, miembro: discord.Member, role_id: int):
    """Lógica de ascenso compartida entre el (desactivado) /ascender y el
    flujo de botón "Ascender" dentro de /academia. Asume que la interacción
    ya fue diferida (defer) por el llamador antes de invocar esto."""
    categoria = _categoria_de_rol(role_id)
    if categoria is None:
        await interaction.followup.send("Rango desconocido.", ephemeral=True)
        return

    if not _puede_ascender(interaction.user, categoria):
        await interaction.followup.send(
            "No tienes permiso para otorgar ese rango (necesitas ser Instructor de esa rama o Staff).",
            ephemeral=True,
        )
        return

    if categoria in ("ATC", "PILOTO"):
        branch = "atc" if categoria == "ATC" else "pilot"
        try:
            certificado = await _certificado_en_rama(str(miembro.id), branch)
        except Exception as err:
            await interaction.followup.send(f"No pude verificar Academia en la web: {err}", ephemeral=True)
            print(f"ERROR ascenso consultando la web: {err}")
            return
        if not certificado:
            await interaction.followup.send(
                f"{miembro.mention} todavía no tiene el certificado de **{branch.upper()}** en Academia "
                "(teoría + examen final) — no se puede ascender hasta que lo complete.",
                ephemeral=True,
            )
            return

    rol = interaction.guild.get_role(role_id)
    if rol is None:
        await interaction.followup.send("No encontré ese rol en el servidor — avisa al staff.", ephemeral=True)
        return
    try:
        await miembro.add_roles(rol, reason=f"Ascenso otorgado por {interaction.user} (vía /academia)")
    except discord.Forbidden:
        await interaction.followup.send(
            "No pude asignar el rol — la jerarquía de roles del bot es insuficiente.",
            ephemeral=True,
        )
        return

    # No hace falta llamar enforce_single_rank_per_category/actualizar_apodo aquí:
    # el add_roles de arriba dispara on_member_update, que ya se encarga.
    await interaction.followup.send(
        f"Listo — {miembro.mention} ahora tiene **{PREFIX_LABELS.get(role_id, rol.name)}**.",
        ephemeral=True,
    )


# Desactivado como comando de nivel superior a pedido del usuario — el mismo
# flujo ahora vive dentro de /academia (botón "Ascender", visible solo para
# quien puede ascender a alguien). Se deja comentado, no borrado, por si se
# quiere reactivar como comando independiente más adelante.
# @tree.command(name="ascender", description="Otorga un rango a un usuario")
# @app_commands.describe(miembro="Usuario a ascender", rango="Rango a otorgar")
# @app_commands.choices(rango=RANGO_CHOICES)
# async def ascender(interaction: discord.Interaction, miembro: discord.Member, rango: app_commands.Choice[str]):
#     role_id = int(rango.value)
#     categoria = _categoria_de_rol(role_id)
#     if categoria is None:
#         await interaction.response.send_message("Rango desconocido.", ephemeral=True)
#         return
#     if not _puede_ascender(interaction.user, categoria):
#         await interaction.response.send_message(
#             "No tienes permiso para otorgar ese rango (necesitas ser Instructor de esa rama o Staff).",
#             ephemeral=True,
#         )
#         return
#     await interaction.response.defer(ephemeral=True)
#     await _procesar_ascenso(interaction, miembro, role_id)


@tree.command(name="apodo", description="Recalcula y actualiza tu propio apodo según tu rango y roles actuales")
async def apodo(interaction: discord.Interaction):
    member = interaction.user
    try:
        nuevo, cambio = await actualizar_apodo(member)
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiarte el apodo — el bot no tiene permisos suficientes sobre tu rol más alto, "
            "o eres el dueño del servidor (Discord no permite cambiarle el apodo al dueño).",
            ephemeral=True,
        )
        return

    if not nuevo:
        await interaction.response.send_message(
            "No tienes ningún rol operativo, de instructor o liderazgo — te dejé sin prefijo.",
            ephemeral=True,
        )
    elif cambio:
        await interaction.response.send_message(f"Listo, tu apodo ahora es **{nuevo}**.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Tu apodo ya estaba correcto: **{nuevo}**.", ephemeral=True)


@tree.command(name="apodo-miembro", description="Recalcula el apodo de otro miembro según su jerarquía de roles (Staff)")
@app_commands.default_permissions()
@app_commands.describe(miembro="Miembro cuyo apodo se va a recalcular")
async def apodo_miembro(interaction: discord.Interaction, miembro: discord.Member):
    if not has_any_role(interaction.user, LIDERAZGO_ORDER):
        await interaction.response.send_message("Este comando es solo para Staff.", ephemeral=True)
        return

    try:
        nuevo, cambio = await actualizar_apodo(miembro)
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiar ese apodo — jerarquía de roles del bot insuficiente, o es el dueño del servidor.",
            ephemeral=True,
        )
        return

    if not nuevo:
        await interaction.response.send_message(
            f"{miembro.mention} no tiene ningún rol operativo, de instructor o liderazgo — quedó sin prefijo.",
            ephemeral=True,
        )
    elif cambio:
        await interaction.response.send_message(f"Apodo de {miembro.mention} actualizado a **{nuevo}**.", ephemeral=True)
    else:
        await interaction.response.send_message(f"El apodo de {miembro.mention} ya estaba correcto: **{nuevo}**.", ephemeral=True)


# Desactivado a pedido del usuario (por ahora) — decorador comentado para que
# no se registre como slash command. La función queda intacta para reactivar
# solo con descomentar la línea de abajo.
# @tree.command(name="apodo-todos", description="Recalcula el apodo de todos los miembros")
async def apodo_todos(interaction: discord.Interaction):
    if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Este comando es solo para el dueño del servidor.", ephemeral=True)
        return

    await interaction.response.send_message("Actualizando apodos de todo el servidor, esto puede tardar…", ephemeral=True)

    actualizados = 0
    fallidos = 0
    async for member in interaction.guild.fetch_members(limit=None):
        try:
            _, cambio = await actualizar_apodo(member)  # borrar -> calcular -> cambiar; no-op si ya está bien
        except (discord.Forbidden, discord.HTTPException):
            fallidos += 1
            continue
        if cambio:
            actualizados += 1
            await asyncio.sleep(0.5)  # solo pausamos cuando de verdad hubo llamadas a la API

    await interaction.followup.send(
        f"Listo. Apodos actualizados: {actualizados}. No se pudieron cambiar: {fallidos} "
        "(probablemente su rol más alto está por encima del bot, o son el dueño del servidor).",
        ephemeral=True,
    )


# Desactivado a pedido del usuario (por ahora) — decorador comentado para que
# no se registre como slash command. La función queda intacta para reactivar
# solo con descomentar la línea de abajo.
# @tree.command(name="apodo-borrartodos", description="Borra el apodo de TODOS los miembros (vuelven a su nombre de usuario)")
async def apodo_borrar_todos(interaction: discord.Interaction):
    if interaction.guild is None or interaction.user.id != interaction.guild.owner_id:
        await interaction.response.send_message("Este comando es solo para el dueño del servidor.", ephemeral=True)
        return

    await interaction.response.send_message("Borrando el apodo de todo el servidor, esto puede tardar…", ephemeral=True)

    borrados = 0
    fallidos = 0
    async for member in interaction.guild.fetch_members(limit=None):
        if member.nick is None:
            continue  # ya no tiene apodo, nada que hacer
        try:
            await member.edit(nick=None, reason=f"Borrado masivo de apodos por {interaction.user}")
            borrados += 1
        except (discord.Forbidden, discord.HTTPException):
            fallidos += 1
            continue
        await asyncio.sleep(0.5)  # evitar rate limit de Discord

    await interaction.followup.send(
        f"Listo. Apodos borrados: {borrados}. No se pudieron borrar: {fallidos} "
        "(probablemente su rol más alto está por encima del bot, o son el dueño del servidor).",
        ephemeral=True,
    )


async def _embed_progreso(usuario) -> discord.Embed:
    data = await academy_core.user_state(db, str(usuario.id))
    embed = discord.Embed(title="Tu progreso en Academia", color=BRAND_RADAR_GREEN)

    if not data.get("enrollments"):
        embed.description = f"{E['libro']} Todavía no te inscribiste en ninguna rama de Academia. Consulta con el Staff cómo inscribirte."
        return embed
    embed.description = E["libro"]

    cursos_por_rama = _agrupar_por_rama(data.get("courseProgress", []))
    for rama in BRANCH_ORDER:
        cursos = cursos_por_rama.get(rama, [])
        if not cursos:
            continue
        lineas = []
        for curso in cursos:
            estado = COURSE_STATE_LABEL.get(curso["state"], curso["state"])
            eval_estado = EVAL_STATE_LABEL.get(curso.get("evalState"), None)
            linea = f"**{curso['courseTitle']}** — {estado}"
            if eval_estado:
                linea += f"\n{eval_estado}"
            lineas.append(linea)
        embed.add_field(name=BRANCH_LABEL[rama], value="\n\n".join(lineas), inline=False)

    certs = sorted(data.get("certificates", []), key=lambda c: CERT_TYPE_ORDEN.get(c["type"], 9))
    certs_por_rama = _agrupar_por_rama(certs)
    for rama in BRANCH_ORDER:
        items = certs_por_rama.get(rama, [])
        if not items:
            continue
        texto = "\n".join(f"{CERT_TYPE_LABEL.get(c['type'], c['type'])} — {c['courseTitle']}" for c in items)
        embed.add_field(name=f"{BRANCH_LABEL[rama]} · Certificados", value=texto, inline=False)

    return embed


def _fecha_ms(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).strftime("%d/%m/%Y")


async def _embed_certificados(objetivo):
    """Devuelve (embed, texto_si_vacio) — solo uno de los dos viene con valor."""
    items = (await academy_core.user_state(db, str(objetivo.id)))["certificates"]
    if not items:
        return None, f"{objetivo.mention} todavía no tiene certificados en Academia."

    items = sorted(items, key=lambda c: CERT_TYPE_ORDEN.get(c["type"], 9))
    por_rama = _agrupar_por_rama(items)
    embed = discord.Embed(title=f"Certificados de {objetivo.display_name}", description=E["verificado"], color=BRAND_BEACON_AMBER)
    for rama in BRANCH_ORDER:
        certs = por_rama.get(rama, [])
        if not certs:
            continue
        texto = "\n".join(f"{CERT_TYPE_LABEL.get(c['type'], c['type'])} — {c['courseTitle']} ({_fecha_ms(c['issuedAt'])})" for c in certs)
        embed.add_field(name=BRANCH_LABEL[rama], value=texto, inline=False)
    return embed, None


async def _embed_cola(rama_valor=None):
    """Devuelve (embed, texto_si_vacio, evaluaciones, inscripciones) — junta
    la cola de evaluaciones de curso CON la cola de inscripciones nuevas
    (antes eran dos pantallas separadas en la web; aquí comparten un solo
    panel con dos selects para aprobar/rechazar)."""
    evaluaciones = await academy_core.pending_evaluations(db, rama_valor)
    inscripciones = await academy_core.pending_enrollments(db, rama_valor)
    if not evaluaciones and not inscripciones:
        return None, f"{E['check']} No hay nada pendiente de revisar ahora mismo.", [], []

    embed = discord.Embed(title="Cola de Academia", description=E["chat"], color=BRAND_BEACON_AMBER)
    if inscripciones:
        por_rama = _agrupar_por_rama(inscripciones)
        for r in BRANCH_ORDER:
            pendientes = por_rama.get(r, [])
            if not pendientes:
                continue
            texto = "\n".join(f"• <@{it['user_id']}>" for it in pendientes)
            embed.add_field(name=f"{BRANCH_LABEL[r]} · Inscripciones nuevas", value=texto, inline=False)
    if evaluaciones:
        por_rama = _agrupar_por_rama(evaluaciones)
        for r in BRANCH_ORDER:
            pendientes = por_rama.get(r, [])
            if not pendientes:
                continue
            texto = "\n".join(
                f"• <@{it['userId']}> — {it['courseTitle']} ({EVAL_STATE_LABEL.get(it['evalState'], it['evalState'])})"
                for it in pendientes
            )
            embed.add_field(name=f"{BRANCH_LABEL[r]} · Evaluaciones", value=texto, inline=False)
    return embed, None, evaluaciones, inscripciones


class InscripcionAccionView(discord.ui.View):
    def __init__(self, enrollment_uuid: str):
        super().__init__(timeout=180)
        self.enrollment_uuid = enrollment_uuid

    @discord.ui.button(label="Aprobar", style=discord.ButtonStyle.success, emoji="✅")
    async def aprobar(self, interaction: discord.Interaction, button: discord.ui.Button):
        fila = await academy_core.resolve_enrollment(db, self.enrollment_uuid, True, str(interaction.user.id))
        if not fila:
            await interaction.response.send_message("Esa inscripción ya no está pendiente.", ephemeral=True)
            return
        await interaction.response.send_message(f"Inscripción aprobada para <@{fila['user_id']}>. ✅", ephemeral=True)
        try:
            alumno = await client.fetch_user(int(fila["user_id"]))
            embed = discord.Embed(
                title=f"¡Bienvenido a la {BRANCH_LABEL.get(fila['branch'], fila['branch'])}!",
                description="Tu inscripción fue aprobada. Ya puedes empezar con el primer curso de la rama.",
                color=BRAND_RADAR_GREEN,
            )
            archivo = _archivo_banner_academia(fila["branch"])
            if archivo:
                embed.set_image(url=f"attachment://{archivo.filename}")
                await alumno.send(embed=embed, file=archivo)
            else:
                await alumno.send(embed=embed)
        except Exception as err:
            print(f"Aviso: no pude mandar el DM de bienvenida de Academia a {fila['user_id']}: {err}")

    @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.danger, emoji="❌")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        fila = await academy_core.resolve_enrollment(db, self.enrollment_uuid, False, str(interaction.user.id))
        if not fila:
            await interaction.response.send_message("Esa inscripción ya no está pendiente.", ephemeral=True)
            return
        await interaction.response.send_message(f"Inscripción rechazada para <@{fila['user_id']}>.", ephemeral=True)


class InscripcionesSelect(discord.ui.Select):
    def __init__(self, items: list):
        opciones = [
            discord.SelectOption(
                label=f"{BRANCH_LABEL.get(it['branch'], it['branch'])} — {it['user_id']}"[:100],
                value=it["uuid"],
            )
            for it in items[:25]
        ]
        super().__init__(placeholder="Aprobar/rechazar una inscripción nueva…", options=opciones)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "¿Qué quieres hacer con esta inscripción?", view=InscripcionAccionView(self.values[0]), ephemeral=True,
        )


class EvaluacionModal2(discord.ui.Modal, title="Calificar (2/2)"):
    def __init__(self, user_id: str, course_uuid: str, curso_titulo: str, criterios: list, puntajes_1a5: list):
        super().__init__()
        self.user_id = user_id
        self.course_uuid = course_uuid
        self.curso_titulo = curso_titulo
        self.puntajes_1a5 = puntajes_1a5
        self.campos = []
        for nombre, _desc in criterios[5:10]:
            campo = discord.ui.TextInput(label=nombre[:45], max_length=2, placeholder="1-10")
            self.campos.append(campo)
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        puntajes_6a10 = []
        for campo in self.campos:
            valor = str(campo)
            if not valor.isdigit() or not (1 <= int(valor) <= 10):
                await interaction.followup.send(f"Puntaje inválido en \"{campo.label}\" — debe ser un número de 1 a 10.", ephemeral=True)
                return
            puntajes_6a10.append(int(valor))
        puntajes = self.puntajes_1a5 + puntajes_6a10
        total, aprobado = evaluation_criteria.calcular_resultado(puntajes)

        resultado = await academy_core.resolve_evaluation(db, self.user_id, self.course_uuid, aprobado, str(interaction.user.id))
        if not resultado:
            await interaction.followup.send("Esa evaluación ya no está pendiente.", ephemeral=True)
            return

        try:
            alumno = await client.fetch_user(int(self.user_id))
        except discord.NotFound:
            alumno = None
        if alumno:
            try:
                imagen_resultado = await certificate_card.generar_resultado_evaluacion(
                    alumno, self.curso_titulo, total, evaluation_criteria.PUNTAJE_MAXIMO, aprobado
                )
                await alumno.send(file=discord.File(imagen_resultado, filename="resultado.png"))
            except Exception as err:
                print(f"Aviso: no pude mandar la imagen de resultado a {self.user_id}: {err}")
            if aprobado and resultado.get("certificateCode"):
                instructor_nombre = None
                try:
                    instructor_usuario = await client.fetch_user(int(resultado["instructorId"]))
                    instructor_nombre = instructor_usuario.display_name
                except Exception:
                    pass  # el certificado se genera igual sin el nombre del instructor si no se pudo resolver
                try:
                    imagen_cert = await certificate_card.generar_certificado(
                        alumno, self.curso_titulo, resultado["certificateCode"], instructor_nombre=instructor_nombre
                    )
                    await alumno.send(
                        content="¡Felicitaciones! Aquí está tu certificado. ✅",
                        file=discord.File(imagen_cert, filename="certificado.png"),
                    )
                except Exception as err:
                    print(f"Aviso: no pude mandar el certificado a {self.user_id}: {err}")

        if aprobado and DISCORD_CHANNEL_ANUNCIOS_ACADEMIA:
            canal = client.get_channel(int(DISCORD_CHANNEL_ANUNCIOS_ACADEMIA))
            if canal:
                try:
                    embed = discord.Embed(
                        description=f"🎉 <@{self.user_id}> aprobó **{self.curso_titulo}** con "
                                    f"{total}/{evaluation_criteria.PUNTAJE_MAXIMO}. ¡Felicitaciones!",
                        color=BRAND_RADAR_GREEN,
                    )
                    archivo = _archivo_banner_academia(resultado.get("branch"))
                    if archivo:
                        embed.set_image(url=f"attachment://{archivo.filename}")
                        await canal.send(embed=embed, file=archivo)
                    else:
                        await canal.send(embed=embed)
                except Exception as err:
                    print(f"Aviso: no pude publicar la felicitación en anuncios de Academia: {err}")

        veredicto = "aprobado" if aprobado else "no aprobado"
        await interaction.followup.send(
            f"Evaluación calificada: <@{self.user_id}> quedó **{veredicto}** con {total}/{evaluation_criteria.PUNTAJE_MAXIMO}.",
            ephemeral=True,
        )


class EvaluacionModal1(discord.ui.Modal, title="Calificar (1/2)"):
    def __init__(self, user_id: str, course_uuid: str, curso_titulo: str, criterios: list):
        super().__init__()
        self.user_id = user_id
        self.course_uuid = course_uuid
        self.curso_titulo = curso_titulo
        self.criterios = criterios
        self.campos = []
        for nombre, _desc in criterios[0:5]:
            campo = discord.ui.TextInput(label=nombre[:45], max_length=2, placeholder="1-10")
            self.campos.append(campo)
            self.add_item(campo)

    async def on_submit(self, interaction: discord.Interaction):
        puntajes = []
        for campo in self.campos:
            valor = str(campo)
            if not valor.isdigit() or not (1 <= int(valor) <= 10):
                await interaction.response.send_message(f"Puntaje inválido en \"{campo.label}\" — debe ser un número de 1 a 10.", ephemeral=True)
                return
            puntajes.append(int(valor))
        await interaction.response.send_modal(
            EvaluacionModal2(self.user_id, self.course_uuid, self.curso_titulo, self.criterios, puntajes)
        )


class RangoEvaluacionSelect(discord.ui.Select):
    def __init__(self, user_id: str, course_uuid: str, curso_titulo: str):
        opciones = [discord.SelectOption(label=rango, value=rango) for rango in evaluation_criteria.RUBRICAS]
        super().__init__(placeholder="Elige la rúbrica (rango) a evaluar…", options=opciones)
        self.user_id = user_id
        self.course_uuid = course_uuid
        self.curso_titulo = curso_titulo

    async def callback(self, interaction: discord.Interaction):
        criterios = evaluation_criteria.criterios_para(self.values[0])
        await interaction.response.send_modal(
            EvaluacionModal1(self.user_id, self.course_uuid, self.curso_titulo, criterios)
        )


class EvaluacionAccionView(discord.ui.View):
    def __init__(self, user_id: str, course_uuid: str, curso_titulo: str):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.course_uuid = course_uuid
        self.add_item(RangoEvaluacionSelect(user_id, course_uuid, curso_titulo))

    @discord.ui.button(label="Rechazar sin calificar", style=discord.ButtonStyle.danger, emoji="❌", row=1)
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        resultado = await academy_core.resolve_evaluation(db, self.user_id, self.course_uuid, False, str(interaction.user.id))
        if not resultado:
            await interaction.response.send_message("Esa evaluación ya no está pendiente.", ephemeral=True)
            return
        await interaction.response.send_message(f"Rechazado: <@{self.user_id}>. Puede volver a intentarlo.", ephemeral=True)


class EvaluacionesSelect(discord.ui.Select):
    def __init__(self, items: list):
        opciones = [
            discord.SelectOption(
                label=it["courseTitle"][:100], description=f"Alumno {it['userId']}"[:100],
                value=f"{it['userId']}|{it['courseUuid']}",
            )
            for it in items[:25]
        ]
        super().__init__(placeholder="Calificar o rechazar una evaluación…", options=opciones)
        self._titulos = {f"{it['userId']}|{it['courseUuid']}": it["courseTitle"] for it in items}

    async def callback(self, interaction: discord.Interaction):
        user_id, course_uuid = self.values[0].split("|", 1)
        curso_titulo = self._titulos.get(self.values[0], "Curso")
        await interaction.response.send_message(
            f"¿Qué quieres hacer con la evaluación de <@{user_id}>? Elige la rúbrica para calificar con los 10 criterios, "
            "o rechazá directamente sin calificar.",
            view=EvaluacionAccionView(user_id, course_uuid, curso_titulo), ephemeral=True,
        )


class ColaAcademiaView(discord.ui.View):
    def __init__(self, evaluaciones: list, inscripciones: list):
        super().__init__(timeout=180)
        if inscripciones:
            self.add_item(InscripcionesSelect(inscripciones))
        if evaluaciones:
            self.add_item(EvaluacionesSelect(evaluaciones))


class AscenderRangoSelect(discord.ui.Select):
    def __init__(self, miembro: discord.Member):
        self.miembro = miembro
        opciones = [
            discord.SelectOption(label=PREFIX_LABELS[rid], value=str(rid))
            for rid in (LIDERAZGO_ORDER + INSTRUCTOR_ORDER + ATC_ORDER + PILOTO_ORDER + GC_ORDER)
        ]
        super().__init__(placeholder=f"Rango a otorgar a {miembro.display_name}", min_values=1, max_values=1, options=opciones)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)  # la consulta a Academia puede tardar más de 3s
        await _procesar_ascenso(interaction, self.miembro, int(self.values[0]))


class AscenderMiembroSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Elige el usuario a ascender", min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        miembro = self.values[0]
        if not isinstance(miembro, discord.Member):
            await interaction.response.send_message("No pude resolver ese usuario en este servidor.", ephemeral=True)
            return
        vista = discord.ui.View(timeout=180)
        vista.add_item(AscenderRangoSelect(miembro))
        await interaction.response.send_message(f"Elige el rango a otorgar a {miembro.mention}:", view=vista, ephemeral=True)


class AscenderInicioView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(AscenderMiembroSelect())


class AgendarSesionModal(discord.ui.Modal, title="Agendar sesión de Academia"):
    categoria = discord.ui.TextInput(label="Categoría/tema (ej. Basic Operations Theory)", max_length=100)
    curso = discord.ui.TextInput(label="Nombre del curso/sesión (ej. RPL Course)", max_length=100)
    en_minutos = discord.ui.TextInput(label="En cuántos minutos empieza", max_length=6, placeholder="ej. 60")
    cupo = discord.ui.TextInput(label="Cupo máximo de alumnos", required=False, default="10", max_length=3)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        minutos_txt = str(self.en_minutos)
        if not minutos_txt.isdigit() or int(minutos_txt) <= 0:
            await interaction.followup.send("Ingresa un número de minutos válido.", ephemeral=True)
            return
        cupo_txt = str(self.cupo) or "10"
        cupo_val = int(cupo_txt) if cupo_txt.isdigit() and int(cupo_txt) > 0 else 10
        programado_ms = int(
            (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=int(minutos_txt))).timestamp() * 1000
        )
        await sessions_core.create_session(
            db, category=str(self.categoria), course_title=str(self.curso), instructor_id=str(interaction.user.id),
            scheduled_at_ms=programado_ms, max_students=cupo_val,
        )
        try:
            await _repostear_sesiones_agendadas()
        except Exception as err:
            print(f"ERROR al actualizar el panel de sesiones agendadas: {err}")
        ts = int(programado_ms / 1000)
        await interaction.followup.send(f"Sesión **{self.curso}** agendada para <t:{ts}:R>. ✅", ephemeral=True)


async def _cerrar_mensaje_sesion_en_vivo(sesion: dict) -> None:
    if not sesion.get("channel_id") or not sesion.get("message_id"):
        return
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    try:
        await _discord_request(
            "DELETE", f"https://discord.com/api/v10/channels/{sesion['channel_id']}/messages/{sesion['message_id']}",
            headers=headers,
        )
    except Exception:
        pass


class AnuncioSesionModal(discord.ui.Modal, title="Anunciar sesión"):
    texto = discord.ui.TextInput(label="Mensaje", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, sesion: dict):
        super().__init__()
        self.sesion = sesion

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not DISCORD_CHANNEL_ACADEMIA_SESIONES:
            await interaction.followup.send("No hay un canal de sesiones de Academia configurado.", ephemeral=True)
            return
        canal = client.get_channel(int(DISCORD_CHANNEL_ACADEMIA_SESIONES))
        if not canal:
            await interaction.followup.send("No pude ubicar el canal de sesiones.", ephemeral=True)
            return
        await canal.send(f"📢 **{self.sesion['course_title']}** ({self.sesion['category']}): {str(self.texto)}")
        await interaction.followup.send("Anuncio publicado. ✅", ephemeral=True)


class PanelInstructorAccionView(discord.ui.View):
    """Bloque D1 — panel de instructor: crear/cerrar/atrasar/adelantar/
    cancelar/iniciar/anunciar clases. "Crear" vive en AgendarSesionModal
    (botón "Agendar sesión" de /academia); aquí van las acciones sobre una
    sesión ya existente."""

    def __init__(self, sesion: dict):
        super().__init__(timeout=180)
        self.sesion = sesion
        if sesion["state"] != sessions_core.SCHEDULED:
            self.remove_item(self.atrasar_btn)
            self.remove_item(self.adelantar_btn)
            self.remove_item(self.iniciar_btn)

    @discord.ui.button(label="Atrasar 15 min", style=discord.ButtonStyle.secondary, emoji="⏪")
    async def atrasar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        nuevo_ts = self.sesion["scheduled_at"] + 15 * 60 * 1000
        actualizada = await sessions_core.reschedule(db, self.sesion["uuid"], nuevo_ts)
        if not actualizada:
            await interaction.followup.send("Esa sesión ya no se puede reprogramar.", ephemeral=True)
            return
        await _repostear_sesiones_agendadas()
        await interaction.followup.send(f"Sesión atrasada 15 min — ahora empieza <t:{nuevo_ts // 1000}:R>.", ephemeral=True)

    @discord.ui.button(label="Adelantar 15 min", style=discord.ButtonStyle.secondary, emoji="⏩")
    async def adelantar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        ahora_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        nuevo_ts = max(ahora_ms, self.sesion["scheduled_at"] - 15 * 60 * 1000)
        actualizada = await sessions_core.reschedule(db, self.sesion["uuid"], nuevo_ts)
        if not actualizada:
            await interaction.followup.send("Esa sesión ya no se puede reprogramar.", ephemeral=True)
            return
        await _repostear_sesiones_agendadas()
        await interaction.followup.send(f"Sesión adelantada — ahora empieza <t:{nuevo_ts // 1000}:R>.", ephemeral=True)

    @discord.ui.button(label="Iniciar ahora", style=discord.ButtonStyle.success, emoji="▶️")
    async def iniciar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        ahora_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        actualizada = await sessions_core.reschedule(db, self.sesion["uuid"], ahora_ms)
        if not actualizada:
            await interaction.followup.send("Esa sesión ya no se puede iniciar.", ephemeral=True)
            return
        try:
            await _publicar_sesion_en_vivo(actualizada)
        except Exception as err:
            print(f"ERROR al iniciar la sesión desde el panel de instructor: {err}")
        await _repostear_sesiones_agendadas()
        await interaction.followup.send("Sesión iniciada. ✅", ephemeral=True)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger, emoji="🗑️", row=1)
    async def cancelar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await sessions_core.set_state(db, self.sesion["uuid"], sessions_core.CANCELLED)
        if self.sesion["state"] == sessions_core.LIVE:
            await _cerrar_mensaje_sesion_en_vivo(self.sesion)
        await _repostear_sesiones_agendadas()
        await interaction.followup.send("Sesión cancelada.", ephemeral=True)

    @discord.ui.button(label="Cerrar (completar)", style=discord.ButtonStyle.secondary, emoji="✅", row=1)
    async def cerrar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await sessions_core.set_state(db, self.sesion["uuid"], sessions_core.COMPLETED)
        await _cerrar_mensaje_sesion_en_vivo(self.sesion)
        await interaction.followup.send("Sesión marcada como completada.", ephemeral=True)

    @discord.ui.button(label="Anunciar", style=discord.ButtonStyle.primary, emoji="📢", row=1)
    async def anunciar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnuncioSesionModal(self.sesion))


class SesionInstructorSelect(discord.ui.Select):
    def __init__(self, sesiones: list):
        opciones = [
            discord.SelectOption(
                label=f"{s['course_title']} ({s['category']})"[:100],
                description=("En vivo" if s["state"] == sessions_core.LIVE else "Agendada")[:100],
                value=s["uuid"],
            )
            for s in sesiones[:25]
        ]
        super().__init__(placeholder="Elige una sesión para gestionar…", options=opciones)
        self._sesiones = {s["uuid"]: s for s in sesiones}

    async def callback(self, interaction: discord.Interaction):
        sesion = self._sesiones[self.values[0]]
        await interaction.response.send_message(
            f"Gestionando **{sesion['course_title']}**:", view=PanelInstructorAccionView(sesion), ephemeral=True,
        )


class PanelInstructorView(discord.ui.View):
    def __init__(self, sesiones: list):
        super().__init__(timeout=180)
        if sesiones:
            self.add_item(SesionInstructorSelect(sesiones))


class AcademiaView(discord.ui.View):
    def __init__(self, invocador: discord.Member):
        super().__init__(timeout=180)
        if not _puede_ascender_alguna(invocador):
            self.remove_item(self.ascender_btn)
        if not es_staff_moderacion(invocador):
            self.remove_item(self.agendar_btn)
            self.remove_item(self.panel_instructor_btn)

    @discord.ui.button(label="Mi progreso", style=discord.ButtonStyle.primary, emoji="📚")
    async def progreso_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            embed = await _embed_progreso(interaction.user)
        except Exception as err:
            await interaction.followup.send(f"No pude consultar tu progreso: {err}", ephemeral=True)
            return
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Mis certificados", style=discord.ButtonStyle.success, emoji="🎓")
    async def certificados_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            embed, texto = await _embed_certificados(interaction.user)
        except Exception as err:
            await interaction.followup.send(f"No pude consultar tus certificados: {err}", ephemeral=True)
            return
        await interaction.followup.send(embed=embed, content=texto, ephemeral=True)

    @discord.ui.button(label="Cola de evaluaciones", style=discord.ButtonStyle.secondary, emoji="📋")
    async def cola_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_staff_moderacion(interaction.user):
            await interaction.response.send_message("Esta opción es solo para Instructores/Staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            embed, texto, evaluaciones, inscripciones = await _embed_cola()
        except Exception as err:
            await interaction.followup.send(f"No pude consultar la cola: {err}", ephemeral=True)
            return
        vista = ColaAcademiaView(evaluaciones, inscripciones) if (evaluaciones or inscripciones) else None
        await interaction.followup.send(embed=embed, content=texto, view=vista, ephemeral=True)

    @discord.ui.button(label="Ascender", style=discord.ButtonStyle.danger)
    async def ascender_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # No hace falta re-chequear permiso aquí: si el botón está visible es
        # porque _puede_ascender_alguna ya dio true en __init__, y el permiso
        # exacto por categoría se vuelve a validar en _procesar_ascenso.
        await interaction.response.send_message(
            "Elige al usuario a ascender:", view=AscenderInicioView(), ephemeral=True,
        )

    @discord.ui.button(label="Agendar sesión", style=discord.ButtonStyle.secondary, emoji="🗓️")
    async def agendar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Bloque C4: reemplaza a /academia-agendar como comando aparte —
        # la re-validación de permiso (por si el rol cambió después de
        # abrirse el panel) queda igual que en el resto de los botones
        # sensibles de esta vista.
        if not es_staff_moderacion(interaction.user):
            await interaction.response.send_message("Esta opción es solo para Instructores/Staff.", ephemeral=True)
            return
        await interaction.response.send_modal(AgendarSesionModal())

    @discord.ui.button(label="Panel de instructor", style=discord.ButtonStyle.secondary, emoji="🧑‍🏫")
    async def panel_instructor_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_staff_moderacion(interaction.user):
            await interaction.response.send_message("Esta opción es solo para Instructores/Staff.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        sesiones = await sessions_core.sessions_by_instructor(db, str(interaction.user.id))
        if not sesiones:
            await interaction.followup.send("No tienes sesiones propias agendadas o en vivo ahora mismo.", ephemeral=True)
            return
        await interaction.followup.send("Tus sesiones:", view=PanelInstructorView(sesiones), ephemeral=True)


@tree.command(name="academia", description="Abre tu panel de Academia: progreso, certificados, cola de evaluaciones y ascensos")
async def academia(interaction: discord.Interaction):
    await interaction.response.send_message(
        "Elige qué quieres ver:", view=AcademiaView(interaction.user), ephemeral=True,
    )


class EcoModal(discord.ui.Modal, title="Mandar mensaje"):
    def __init__(self, destino):
        super().__init__()
        self.destino = destino

    texto = discord.ui.TextInput(
        label="Mensaje (admite markdown completo)",
        style=discord.TextStyle.paragraph,
        placeholder="Escribe el mensaje tal como quieres que se vea, incluido markdown…",
        max_length=4000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Bloque B8: antes se llamaba a destino.send() ANTES de responder la
        # interacción — si el envío tardaba (canal ocupado, rate limit, MD
        # que tarda en abrirse), se superaban los 3s que da Discord para
        # confirmar la interacción y quedaba como "la aplicación no
        # respondió", aunque el mensaje sí se hubiera mandado. Con el
        # defer() de entrada, la interacción queda confirmada de inmediato y
        # el resultado real se informa después con followup, sin ese límite.
        await interaction.response.defer(ephemeral=True)
        try:
            await self.destino.send(str(self.texto))
        except discord.Forbidden:
            await interaction.followup.send(
                "No pude mandar el mensaje ahí — el bot no tiene permisos, o el usuario tiene los MD cerrados.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"Mensaje enviado a {self.destino.mention}. ✅", ephemeral=True)
        print(f"{interaction.user} usó /eco hacia {self.destino}")


# ─── Dashboard privado de vuelo por MD (Bloque A4) ────────────────────────
def _embed_dashboard_vuelo(fila: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"Panel de tu plan de vuelo — {fila.get('callsign') or '—'}",
        description=_texto_plan_vuelo(fila, None).rsplit(f"\n{_ZWS}", 1)[0],
        color=BRAND_SKY_NAVY,
    )
    embed.set_footer(text="Usa los botones para editar, cambiar el squawk, retirar o finalizar tu plan.")
    # Semilla = uuid del vuelo: este panel se edita varias veces (cambios de
    # datos, squawk) — necesita elegir siempre la misma variante para no
    # romper la referencia attachment:// ya adjunta al mensaje original.
    aplicar_imagen_formal_determinista(embed, fila["uuid"])
    return embed


async def _enviar_dashboard_vuelo(usuario: discord.abc.User, fila: dict) -> None:
    archivo = _archivo_imagen_formal(_nombre_imagen_formal_determinista(fila["uuid"]))
    try:
        if archivo:
            mensaje = await usuario.send(embed=_embed_dashboard_vuelo(fila), view=VueloDashboardView(fila["uuid"]), file=archivo)
        else:
            mensaje = await usuario.send(embed=_embed_dashboard_vuelo(fila), view=VueloDashboardView(fila["uuid"]))
    except discord.Forbidden:
        return  # MD cerrados — el plan sigue activo, solo sin panel
    await atc_core.set_flight_dm(db, fila["uuid"], str(mensaje.channel.id), str(mensaje.id))


async def _actualizar_dashboard_vuelo(fila: dict) -> None:
    if not fila.get("dm_channel_id") or not fila.get("dm_message_id"):
        return
    try:
        usuario = await client.fetch_user(int(fila["owner_id"]))
        dm = usuario.dm_channel or await usuario.create_dm()
        mensaje = await dm.fetch_message(int(fila["dm_message_id"]))
        await mensaje.edit(embed=_embed_dashboard_vuelo(fila), view=VueloDashboardView(fila["uuid"]))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as err:
        print(f"Aviso: no pude actualizar el panel de vuelo de {fila['owner_id']}: {err}")


async def _cerrar_dashboard_vuelo(fila: dict) -> None:
    """Se borra al finalizar o retirar el plan, a pedido explícito."""
    if not fila.get("dm_channel_id") or not fila.get("dm_message_id"):
        return
    try:
        usuario = await client.fetch_user(int(fila["owner_id"]))
        dm = usuario.dm_channel or await usuario.create_dm()
        mensaje = await dm.fetch_message(int(fila["dm_message_id"]))
        await mensaje.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


class EditarVueloModal(discord.ui.Modal, title="Editar plan de vuelo"):
    def __init__(self, flight_uuid: str, fila: dict):
        super().__init__()
        self.flight_uuid = flight_uuid
        self.aeronave = discord.ui.TextInput(label="Aeronave", default=fila.get("aircraft_type") or "", max_length=50)
        self.nivel = discord.ui.TextInput(label="Nivel de vuelo", default=fila.get("level") or "", max_length=20)
        self.alterno = discord.ui.TextInput(label="Alterno (ICAO)", default=fila.get("alternate") or "", max_length=10)
        self.ruta = discord.ui.TextInput(label="Ruta (DCT si se deja vacía)", default=fila.get("route") or "", required=False, max_length=200)
        self.observaciones = discord.ui.TextInput(
            label="Observaciones", style=discord.TextStyle.paragraph, default=fila.get("remarks") or "", max_length=500,
        )
        for item in (self.aeronave, self.nivel, self.alterno, self.ruta, self.observaciones):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        fila = await atc_core.edit_flight(
            db, self.flight_uuid, aircraft_type=str(self.aeronave), level=str(self.nivel),
            alternate=str(self.alterno).upper(), route=str(self.ruta), remarks=str(self.observaciones),
        )
        if not fila:
            await interaction.followup.send("Ese plan ya no está activo.", ephemeral=True)
            return
        try:
            await _enviar_o_editar_vuelo(fila)
        except Exception as err:
            print(f"ERROR al editar mensaje de vuelo: {err}")
        await _actualizar_dashboard_vuelo(fila)
        await interaction.followup.send("Plan actualizado. ✅", ephemeral=True)


class SquawkModal(discord.ui.Modal, title="Cambiar squawk"):
    squawk = discord.ui.TextInput(label="Nuevo squawk (4 dígitos)", min_length=4, max_length=4)

    def __init__(self, flight_uuid: str):
        super().__init__()
        self.flight_uuid = flight_uuid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        valor = str(self.squawk)
        if not valor.isdigit():
            await interaction.followup.send("El squawk debe ser un número de 4 dígitos.", ephemeral=True)
            return
        fila = await atc_core.set_squawk(db, self.flight_uuid, valor)
        if not fila:
            await interaction.followup.send("Ese plan ya no está activo.", ephemeral=True)
            return
        try:
            await _enviar_o_editar_vuelo(fila)
        except Exception as err:
            print(f"ERROR al editar mensaje de vuelo: {err}")
        await _actualizar_dashboard_vuelo(fila)
        await interaction.followup.send(f"Squawk actualizado a **{valor}**. ✅", ephemeral=True)


class VueloDashboardView(discord.ui.View):
    def __init__(self, flight_uuid: str):
        super().__init__(timeout=None)
        self.flight_uuid = flight_uuid

    @discord.ui.button(label="Editar plan", style=discord.ButtonStyle.primary, emoji="✏️")
    async def editar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        fila = await atc_core.get_flight(db, self.flight_uuid)
        if not fila or fila["state"] != atc_core.FLIGHT_ACTIVO:
            await interaction.response.send_message("Ese plan ya no está activo.", ephemeral=True)
            return
        await interaction.response.send_modal(EditarVueloModal(self.flight_uuid, fila))

    @discord.ui.button(label="Cambiar squawk", style=discord.ButtonStyle.secondary, emoji="🔢")
    async def squawk_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(SquawkModal(self.flight_uuid))

    @discord.ui.button(label="Retirar", style=discord.ButtonStyle.danger, emoji="🚫")
    async def retirar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        fila = await atc_core.close_flight(db, self.flight_uuid, atc_core.FLIGHT_CANCELADO)
        if not fila:
            await interaction.followup.send("Ese plan ya no estaba activo.", ephemeral=True)
            return
        await _cerrar_dashboard_vuelo(fila)
        await interaction.followup.send(f"Plan **{fila['callsign']}** retirado.", ephemeral=True)

    @discord.ui.button(label="Finalizar", style=discord.ButtonStyle.success, emoji="🏁")
    async def finalizar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        fila = await atc_core.close_flight(db, self.flight_uuid, atc_core.FLIGHT_COMPLETADO)
        if not fila:
            await interaction.followup.send("Ese plan ya no estaba activo.", ephemeral=True)
            return
        await _cerrar_dashboard_vuelo(fila)
        await interaction.followup.send(f"Plan **{fila['callsign']}** finalizado. ✅", ephemeral=True)


class ConfirmarVueloView(discord.ui.View):
    def __init__(self, flight_uuid: str):
        super().__init__(timeout=90)
        self.flight_uuid = flight_uuid

    @discord.ui.button(label="Sigo aquí", style=discord.ButtonStyle.success, emoji="✅")
    async def confirmar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        fila = await atc_core.confirmar_flight_activo(db, self.flight_uuid)
        if not fila:
            await interaction.response.send_message("Ya no llegaste a tiempo, o el plan ya se cerró.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Perfecto — tu plan **{fila['callsign']}** sigue activo unos minutos más.", ephemeral=True
        )


@tree.command(name="vuelo", description="Presenta un plan de vuelo y lo publica en el canal de vuelos")
@app_commands.describe(
    callsign="Callsign de tu aeronave (ej. AEA1234)", aircraft="Tipo de aeronave (ej. A320, B738)",
    salida="Aeródromo de salida", llegada="Aeródromo de llegada", nivel="Nivel de vuelo (ej. FL350)",
    reglas="Reglas de vuelo", alterno="Aeródromo alterno", squawk="Código squawk de 4 dígitos (ej. 2000)",
    observaciones="Información adicional para control", ruta="Ruta de vuelo (opcional — se usa DCT si se deja vacía)",
)
@app_commands.choices(
    reglas=[app_commands.Choice(name="IFR", value="IFR"), app_commands.Choice(name="VFR", value="VFR")],
    salida=airports_ptfs.choices(), llegada=airports_ptfs.choices(), alterno=airports_ptfs.choices(),
)
async def vuelo(
    interaction: discord.Interaction,
    callsign: str, aircraft: str, salida: app_commands.Choice[str], llegada: app_commands.Choice[str],
    nivel: str, reglas: app_commands.Choice[str], alterno: app_commands.Choice[str],
    squawk: str, observaciones: str, ruta: str = None,
):
    if interaction.channel_id != int(DISCORD_CHANNEL_VUELO_CMD):
        await interaction.response.send_message(
            f"Este comando solo se puede usar en <#{DISCORD_CHANNEL_VUELO_CMD}>.", ephemeral=True
        )
        return
    if not squawk.isdigit() or len(squawk) != 4:
        await interaction.response.send_message("El squawk debe ser un número de 4 dígitos.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    fila = await atc_core.create_flight(
        db, owner_id=str(interaction.user.id), callsign=callsign, aircraft_type=aircraft,
        departure=salida.value, destination=llegada.value, level=nivel, flight_rules=reglas.value,
        route=ruta or "", alternate=alterno.value, remarks=observaciones, squawk=squawk,
    )
    try:
        roblox_name = await _roblox_username(interaction.user.id)
    except Exception:
        roblox_name = None
    try:
        await _enviar_o_editar_vuelo(fila, roblox_name)
    except Exception as err:
        print(f"ERROR al publicar plan de vuelo: {err}")
    await _enviar_dashboard_vuelo(interaction.user, fila)
    await interaction.followup.send(
        f"Plan de vuelo **{callsign}** presentado. Revisa tu panel por mensaje directo para editarlo, "
        "cambiar el squawk, retirarlo o finalizarlo. ✅",
        ephemeral=True,
    )


async def _fijar_estado_canal_voz(voice_channel_id: str, texto: str) -> None:
    """El "estado" de un canal de voz (lo que se ve debajo del nombre en la
    lista de canales) es una función relativamente nueva de Discord —
    discord.py no siempre trae un método de alto nivel para setearlo, así
    que se llama directo a la API REST (mismo patrón que el resto de las
    llamadas crudas de este bot). Se re-aplica cada vez que detectamos que
    alguien lo cambió (on_voice_state_update), porque cualquier miembro con
    permiso de enviar mensajes en el canal de voz puede tocarlo desde el
    cliente de Discord — no hay forma de bloquearlo del todo sin negarle ese
    permiso a todo el mundo, lo cual rompería el chat de texto del canal."""
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    try:
        await _discord_request(
            "PUT", f"https://discord.com/api/v10/channels/{voice_channel_id}/voice-status",
            headers=headers, json_body={"status": texto[:500]},
        )
    except Exception as err:
        print(f"Aviso: no pude fijar el estado del canal de voz {voice_channel_id}: {err}")


# ─── Posiciones ATC — motor propio (Fase A), canales de voz nativos ───────
def _atc_row_to_op(row: dict) -> dict:
    return {
        "airport": row["airport"], "positionType": row["position_type"],
        "frequency": row["frequency"], "ownerId": row["owner_id"],
        "controllerName": row["controller_name"], "voiceChannelId": row.get("voice_channel_id"),
    }


async def _activos_para_tabla() -> list:
    filas = await atc_core.get_active_atc(db)
    return [_atc_row_to_op(f) for f in filas]


async def _crear_canal_atc(fila: dict) -> tuple[str | None, str | None]:
    """Crea (o reutiliza) la categoría del aeródromo y el canal de voz de esta
    posición usando la API nativa de discord.py — reemplaza los llamados HTTP
    crudos que antes hacía discordVoiceChannels.js (repo web)."""
    if not GUILD_ID:
        return None, None
    guild = client.get_guild(int(GUILD_ID))
    if not guild:
        return None, None
    icao = fila["airport"]
    categoria = None
    category_id = await atc_core.get_category(db, icao)
    if category_id:
        categoria = guild.get_channel(int(category_id))
    if categoria is None:
        categoria = discord.utils.get(guild.categories, name=icao)
    if categoria is None:
        try:
            categoria = await guild.create_category(icao, reason="Nuevo aeródromo con control activo")
        except discord.Forbidden:
            print(f"Aviso: sin permiso para crear la categoría de voz de {icao}")
            return None, None
        try:
            await guild.create_voice_channel(atc_core.UNICOM_NAME, category=categoria, reason="Canal UNICOM fijo")
        except discord.Forbidden:
            print(f"Aviso: sin permiso para crear el canal UNICOM de {icao}")
    await atc_core.save_category(db, icao, str(categoria.id))
    nombre = f"{icao}_{fila['position_type']} | {fila['frequency']}"
    try:
        canal = await guild.create_voice_channel(nombre, category=categoria, reason="Nueva posición ATC abierta")
    except discord.Forbidden:
        print(f"Aviso: sin permiso para crear el canal de voz {nombre}")
        return str(categoria.id), None
    return str(categoria.id), str(canal.id)


async def _borrar_canal_atc(fila: dict) -> None:
    if not GUILD_ID:
        return
    guild = client.get_guild(int(GUILD_ID))
    if not guild:
        return
    voice_id = fila.get("voice_channel_id")
    if voice_id:
        canal = guild.get_channel(int(voice_id))
        if canal:
            try:
                await canal.delete(reason="Posición ATC cerrada")
            except (discord.Forbidden, discord.NotFound):
                pass
    icao = fila["airport"]
    if await atc_core.count_active_atc(db, icao) > 0:
        return  # sigue habiendo otra posición activa en este aeródromo
    category_id = await atc_core.get_category(db, icao)
    if not category_id:
        return
    categoria = guild.get_channel(int(category_id))
    if categoria:
        for canal in list(categoria.channels):
            try:
                await canal.delete(reason="Aeródromo sin controladores activos")
            except (discord.Forbidden, discord.NotFound):
                pass
        try:
            await categoria.delete(reason="Aeródromo sin controladores activos")
        except (discord.Forbidden, discord.NotFound):
            pass
    await atc_core.clear_category(db, icao)


# uuid de posición ATC -> Task que la cerrará sola si el canal sigue vacío
_atc_close_timers: dict = {}


def _cancelar_timer_cierre_atc(op_uuid: str) -> None:
    tarea = _atc_close_timers.pop(op_uuid, None)
    if tarea and not tarea.done():
        tarea.cancel()


async def _cerrar_atc_por_inactividad(op_uuid: str, voice_channel_id: str) -> None:
    """Reemplaza el viejo mecanismo de auto-cierre por desconexión de socket
    del navegador (la causa del bug de canales borrados en plena frecuencia).
    Aquí la señal es la presencia real en el canal de voz de la posición: si
    después del margen de gracia sigue sin nadie, recién ahí se cierra."""
    try:
        await asyncio.sleep(ATC_VACIO_GRACIA_SEG)
    except asyncio.CancelledError:
        return
    guild = client.get_guild(int(GUILD_ID)) if GUILD_ID else None
    canal = guild.get_channel(int(voice_channel_id)) if guild else None
    if canal and any(not m.bot for m in canal.members):
        return  # alguien volvió a entrar mientras esperábamos
    fila = await atc_core.close_atc(db, op_uuid, reason="Cierre automático: canal de voz vacío")
    if not fila:
        return
    await _borrar_aviso_cierre_programado(fila)
    await _borrar_canal_atc(fila)
    await _cerrar_dashboard_atc(fila)
    try:
        await _repostear_tabla_atc(await _activos_para_tabla())
    except Exception as err:
        print(f"ERROR al actualizar la tabla ATC tras un cierre automático: {err}")
    print(f"Posición ATC {fila['airport']}_{fila['position_type']} cerrada sola por inactividad (canal de voz vacío).")
    _atc_close_timers.pop(op_uuid, None)


@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if before.channel == after.channel:
        return
    for canal in filter(None, {before.channel, after.channel}):
        fila = await atc_core.get_atc_by_voice_channel(db, str(canal.id))
        if not fila:
            continue
        vacio = not any(not m.bot for m in canal.members)
        if vacio:
            if fila["uuid"] not in _atc_close_timers:
                _atc_close_timers[fila["uuid"]] = client.loop.create_task(
                    _cerrar_atc_por_inactividad(fila["uuid"], str(canal.id))
                )
        else:
            _cancelar_timer_cierre_atc(fila["uuid"])
            # Reafirma el estado "ATC: {controlador}" — cualquiera con
            # permiso de chat en el canal de voz puede cambiarlo desde el
            # cliente, así que se re-aplica cada vez que hay actividad.
            await _fijar_estado_canal_voz(str(canal.id), f"ATC: {fila['controller_name'] or fila['owner_id']}")


# ─── Dashboard privado de ATC por MD (Bloque A7) ──────────────────────────
async def _borrar_aviso_cierre_programado(fila: dict) -> None:
    canal_id = fila.get("close_announcement_channel_id")
    msg_id = fila.get("close_announcement_message_id")
    if not canal_id or not msg_id:
        return
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    try:
        await _discord_request(
            "DELETE", f"https://discord.com/api/v10/channels/{canal_id}/messages/{msg_id}", headers=headers
        )
    except Exception:
        pass


def _embed_dashboard_atc(fila: dict) -> discord.Embed:
    embed = discord.Embed(
        title=f"Panel de tu posición ATC — {fila['airport']}_{fila['position_type']}", color=BRAND_SKY_NAVY
    )
    lineas = [f"**Frecuencia:** {fila['frequency']}"]
    if fila.get("voice_channel_id"):
        lineas.append(f"**Canal de voz:** <#{fila['voice_channel_id']}>")
    if fila.get("close_scheduled_at"):
        ts = int(datetime.datetime.fromisoformat(fila["close_scheduled_at"]).timestamp())
        lineas.append(f"**Cierre programado:** <t:{ts}:R>")
    embed.description = "\n".join(lineas)
    embed.set_footer(text="Usa los botones para anunciar, programar/hacer el cierre, o publicar el ATIS.")
    aplicar_imagen_formal_determinista(embed, fila["uuid"])
    return embed


async def _enviar_dashboard_atc(usuario: discord.abc.User, fila: dict) -> None:
    archivo = _archivo_imagen_formal(_nombre_imagen_formal_determinista(fila["uuid"]))
    try:
        if archivo:
            mensaje = await usuario.send(embed=_embed_dashboard_atc(fila), view=ATCDashboardView(fila["uuid"]), file=archivo)
        else:
            mensaje = await usuario.send(embed=_embed_dashboard_atc(fila), view=ATCDashboardView(fila["uuid"]))
    except discord.Forbidden:
        return  # MD cerrados — la posición sigue abierta, solo sin panel
    await atc_core.set_atc_dm(db, fila["uuid"], str(mensaje.channel.id), str(mensaje.id))


async def _actualizar_dashboard_atc(fila: dict) -> None:
    if not fila or not fila.get("dm_channel_id") or not fila.get("dm_message_id"):
        return
    try:
        usuario = await client.fetch_user(int(fila["owner_id"]))
        dm = usuario.dm_channel or await usuario.create_dm()
        mensaje = await dm.fetch_message(int(fila["dm_message_id"]))
        await mensaje.edit(embed=_embed_dashboard_atc(fila), view=ATCDashboardView(fila["uuid"]))
    except (discord.Forbidden, discord.NotFound, discord.HTTPException) as err:
        print(f"Aviso: no pude actualizar el panel ATC de {fila['owner_id']}: {err}")


async def _cerrar_dashboard_atc(fila: dict) -> None:
    if not fila.get("dm_channel_id") or not fila.get("dm_message_id"):
        return
    try:
        usuario = await client.fetch_user(int(fila["owner_id"]))
        dm = usuario.dm_channel or await usuario.create_dm()
        mensaje = await dm.fetch_message(int(fila["dm_message_id"]))
        await mensaje.delete()
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass


class AnuncioATCDashboardModal(discord.ui.Modal, title="Anuncio rápido a ATC"):
    texto = discord.ui.TextInput(
        label="Mensaje", style=discord.TextStyle.paragraph, max_length=1000,
        placeholder="Se publica en el canal ATC y se borra solo en 5 a 10 minutos.",
    )

    def __init__(self, atc_uuid: str):
        super().__init__()
        self.atc_uuid = atc_uuid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not DISCORD_CHANNEL_ATC:
            await interaction.followup.send("No hay un canal ATC configurado.", ephemeral=True)
            return
        payload = {
            "flags": 32768,
            "allowed_mentions": {"parse": [], "roles": [str(ATC_ROLE_ID)]},
            "components": [
                {"type": 17, "accent_color": BRAND_BEACON_AMBER,
                 "components": [{"type": 10, "content": f"<@&{ATC_ROLE_ID}> {str(self.texto)}"}]},
            ],
        }
        try:
            mensaje = await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATC), payload)
        except Exception as err:
            await interaction.followup.send(f"No pude publicar el anuncio: {err}", ephemeral=True)
            return

        async def _autoborrar(mensaje_id: str):
            await asyncio.sleep(random.randint(300, 600))  # 5-10 min
            headers = {"Authorization": f"Bot {BOT_TOKEN}"}
            try:
                await _discord_request(
                    "DELETE", f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ATC}/messages/{mensaje_id}",
                    headers=headers,
                )
            except Exception:
                pass

        asyncio.create_task(_autoborrar(mensaje["id"]))
        await interaction.followup.send("Anuncio publicado — se borra solo en 5 a 10 minutos. ✅", ephemeral=True)


class ProgramarCierreModal(discord.ui.Modal, title="Programar cierre de posición"):
    minutos = discord.ui.TextInput(label="En cuántos minutos cierra", max_length=4, placeholder="ej. 10")

    def __init__(self, atc_uuid: str):
        super().__init__()
        self.atc_uuid = atc_uuid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        valor = str(self.minutos)
        if not valor.isdigit() or int(valor) <= 0:
            await interaction.followup.send("Ingresa un número de minutos mayor a 0.", ephemeral=True)
            return
        minutos = int(valor)
        fila = await atc_core.get_atc(db, self.atc_uuid)
        if not fila or fila["state"] == atc_core.ATC_FINALIZADA:
            await interaction.followup.send("Esa posición ya no está activa.", ephemeral=True)
            return
        if not DISCORD_CHANNEL_ATC:
            await interaction.followup.send("No hay un canal ATC configurado para el aviso.", ephemeral=True)
            return
        ts = int((datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutos)).timestamp())
        payload = {
            "flags": 32768, "allowed_mentions": {"parse": []},
            "components": [
                {"type": 17, "accent_color": BRAND_BEACON_AMBER, "components": [
                    {"type": 10, "content": f"{fila['airport']}_{fila['position_type']} cerrará <t:{ts}:R>."},
                ]},
            ],
        }
        try:
            mensaje = await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATC), payload)
        except Exception as err:
            await interaction.followup.send(f"No pude publicar el aviso: {err}", ephemeral=True)
            return
        await atc_core.schedule_atc_close(
            db, self.atc_uuid, minutes=minutos, channel_id=DISCORD_CHANNEL_ATC, message_id=str(mensaje["id"])
        )
        await _actualizar_dashboard_atc(await atc_core.get_atc(db, self.atc_uuid))
        await interaction.followup.send(f"Cierre programado para dentro de {minutos} minuto(s). ✅", ephemeral=True)


class ATISModal2(discord.ui.Modal, title="ATIS (2/2) — nubes, QNH, RMKs"):
    nubes = discord.ui.TextInput(label="Nubes (ej. SKC, FEW020)", max_length=30)
    qnh = discord.ui.TextInput(label="QNH (ej. 1013)", max_length=10)
    nivel_transicion = discord.ui.TextInput(label="Nivel de transición (ej. FL60)", max_length=10)
    rmks = discord.ui.TextInput(label="RMKs (opcional)", style=discord.TextStyle.paragraph, required=False, max_length=300)

    def __init__(self, atc_uuid: str, ident: str, pista_salida: str, pista_llegada: str, viento: str, visibilidad: str):
        super().__init__()
        self.atc_uuid = atc_uuid
        self.ident = ident
        self.pista_salida = pista_salida
        self.pista_llegada = pista_llegada
        self.viento = viento
        self.visibilidad = visibilidad

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        fila = await atc_core.get_atc(db, self.atc_uuid)
        if not fila or fila["state"] == atc_core.ATC_FINALIZADA:
            await interaction.followup.send("Esa posición ya no está activa.", ephemeral=True)
            return
        if not DISCORD_CHANNEL_ATIS:
            await interaction.followup.send("No hay un canal de ATIS configurado.", ephemeral=True)
            return
        payload = _construir_payload_atis({
            "airport": fila["airport"], "ident": self.ident,
            "at": int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000),
            "depRwy": self.pista_salida, "arrRwy": self.pista_llegada or self.pista_salida,
            "wind": self.viento, "visibility": self.visibilidad,
            "clouds": str(self.nubes), "qnh": str(self.qnh), "trl": str(self.nivel_transicion),
            "remarks": str(self.rmks),
        })
        try:
            await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATIS), payload)
        except Exception as err:
            await interaction.followup.send(f"No pude publicar el ATIS: {err}", ephemeral=True)
            return
        await interaction.followup.send(f"ATIS {fila['airport']} {self.ident} publicado. ✅", ephemeral=True)


class ATISModal1(discord.ui.Modal, title="ATIS (1/2) — pistas, viento, visibilidad"):
    ident = discord.ui.TextInput(label="Letra de información (ej. A, B, C)", max_length=2)
    pista_salida = discord.ui.TextInput(label="Pista de salida (ej. 09L)", max_length=10)
    pista_llegada = discord.ui.TextInput(label="Pista de llegada (vacío = igual a salida)", required=False, max_length=10)
    viento = discord.ui.TextInput(label="Viento (ej. 250/12)", max_length=20)
    visibilidad = discord.ui.TextInput(label="Visibilidad (ej. CAVOK, 8000m)", max_length=20)

    def __init__(self, atc_uuid: str):
        super().__init__()
        self.atc_uuid = atc_uuid

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ATISModal2(
            self.atc_uuid, str(self.ident), str(self.pista_salida), str(self.pista_llegada),
            str(self.viento), str(self.visibilidad),
        ))


class ATCDashboardView(discord.ui.View):
    def __init__(self, atc_uuid: str):
        super().__init__(timeout=None)
        self.atc_uuid = atc_uuid

    @discord.ui.button(label="Anuncio rápido", style=discord.ButtonStyle.primary, emoji="📢")
    async def anuncio_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnuncioATCDashboardModal(self.atc_uuid))

    @discord.ui.button(label="ATIS", style=discord.ButtonStyle.secondary, emoji="🌦️")
    async def atis_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ATISModal1(self.atc_uuid))

    @discord.ui.button(label="Programar cierre", style=discord.ButtonStyle.secondary, emoji="⏳")
    async def programar_cierre_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ProgramarCierreModal(self.atc_uuid))

    @discord.ui.button(label="Cerrar ahora", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cerrar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        fila = await atc_core.close_atc(db, self.atc_uuid, reason="Cierre manual del controlador (panel)")
        if not fila:
            await interaction.followup.send("Esa posición ya no estaba activa.", ephemeral=True)
            return
        _cancelar_timer_cierre_atc(fila["uuid"])
        await _borrar_aviso_cierre_programado(fila)
        await _borrar_canal_atc(fila)
        try:
            await _repostear_tabla_atc(await _activos_para_tabla())
        except Exception as err:
            print(f"ERROR al actualizar la tabla ATC tras cerrar posición: {err}")
        await _cerrar_dashboard_atc(fila)
        await interaction.followup.send(f"Posición **{fila['airport']}_{fila['position_type']}** cerrada. ✅", ephemeral=True)


@tree.command(name="atc", description="Abre una posición de control ATC y crea su canal de voz")
@app_commands.describe(
    aeropuerto="ICAO del aeródromo a controlar (ej. SBGR)", posicion="Posición que vas a abrir",
    frecuencia="Frecuencia de radio (ej. 118.200)",
)
@app_commands.choices(posicion=[
    app_commands.Choice(name="Delivery", value="DEL"),
    app_commands.Choice(name="Suelo", value="GND"),
    app_commands.Choice(name="Torre", value="TWR"),
    app_commands.Choice(name="Aproximación", value="APP"),
    app_commands.Choice(name="Centro", value="CTR"),
])
async def atc(interaction: discord.Interaction, aeropuerto: str, posicion: app_commands.Choice[str], frecuencia: str):
    if interaction.channel_id != int(DISCORD_CHANNEL_ATC_CMD):
        await interaction.response.send_message(
            f"Este comando solo se puede usar en <#{DISCORD_CHANNEL_ATC_CMD}>.", ephemeral=True
        )
        return
    if not (has_any_role(interaction.user, ATC_ORDER) or has_any_role(interaction.user, LIDERAZGO_ORDER)):
        await interaction.response.send_message("Este comando es solo para controladores (rol ATC).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    icao = aeropuerto.upper()
    try:
        fila = await atc_core.open_atc(
            db, owner_id=str(interaction.user.id), controller_name=interaction.user.display_name,
            airport=icao, position_type=posicion.value, frequency=frecuencia,
        )
    except atc_core.PositionAlreadyOpen:
        await interaction.followup.send(f"Ya hay alguien controlando {icao}_{posicion.value}.", ephemeral=True)
        return

    category_id, voice_id = await _crear_canal_atc(fila)
    if category_id or voice_id:
        await atc_core.set_atc_channel(db, fila["uuid"], category_id, voice_id)
    if voice_id:
        await _fijar_estado_canal_voz(voice_id, f"ATC: {interaction.user.display_name}")
    fila = await atc_core.get_atc(db, fila["uuid"])

    try:
        payload = _construir_payload_atc_abierto(_atc_row_to_op(fila), str(interaction.user.id))
        if DISCORD_CHANNEL_ATC:
            await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATC), payload)
    except Exception as err:
        print(f"ERROR al anunciar apertura de posición ATC: {err}")
    try:
        await _repostear_tabla_atc(await _activos_para_tabla())
    except Exception as err:
        print(f"ERROR al actualizar la tabla ATC tras abrir posición: {err}")
    await _enviar_dashboard_atc(interaction.user, fila)

    canal_txt = f" — canal de voz: <#{voice_id}>" if voice_id else ""
    await interaction.followup.send(
        f"Posición **{icao}_{posicion.value}** abierta en {frecuencia}.{canal_txt} "
        f"Revisa tu panel por mensaje directo para anunciar, cerrar o publicar el ATIS "
        f"(o se cierra sola si el canal de voz queda vacío {ATC_VACIO_GRACIA_SEG // 60} min). ✅",
        ephemeral=True,
    )


def _formatear_minutos(total_minutos: int) -> str:
    horas, minutos = divmod(total_minutos, 60)
    return f"{horas}h {minutos}min" if horas else f"{minutos} min"


@tree.command(name="rankings", description="Muestra los rankings de pilotos, controladores y actividad de la red")
async def rankings(interaction: discord.Interaction):
    await interaction.response.defer()
    pilotos = await atc_core.top_pilots(db, limit=10)
    controladores = await atc_core.top_controllers(db, limit=10)
    actividad = await atc_core.top_actividad(db, limit=10)
    embed = discord.Embed(title="Rankings de ATC24 Español", description=E["brujula"], color=BRAND_BEACON_AMBER)

    def _campo(nombre: str, items: list, vacio: str):
        if not items:
            embed.add_field(name=nombre, value=vacio, inline=False)
            return
        texto = "\n".join(
            f"**{i + 1}.** <@{it['owner_id']}> — {_formatear_minutos(it['total_minutos'])}"
            for i, it in enumerate(items)
        )
        embed.add_field(name=nombre, value=texto, inline=False)

    _campo("Pilotos con más minutos volados", pilotos, "Todavía no hay vuelos completados.")
    _campo("Controladores con más minutos en posición", controladores, "Todavía no hay posiciones ATC cerradas.")
    _campo("Actividad total (vuelos + control)", actividad, "Todavía no hay actividad registrada.")
    archivo = await aplicar_imagen_formal(embed)
    if archivo:
        await interaction.followup.send(embed=embed, file=archivo)
    else:
        await interaction.followup.send(embed=embed)


@tree.command(name="servidor", description="Muestra el estado en vivo de la red: vuelos, controladores y verificados")
async def servidor(interaction: discord.Interaction):
    await interaction.response.defer()
    vuelos = await atc_core.count_active_flights(db)
    posiciones = await atc_core.get_active_atc(db)
    verificados = totales = 0
    if interaction.guild:
        rol_v = interaction.guild.get_role(V_ROLE_ID)
        verificados = len(rol_v.members) if rol_v else 0
        totales = interaction.guild.member_count or 0
    embed = discord.Embed(title="Estado de ATC24 Español", description=E["antena"], color=BRAND_SKY_NAVY)
    embed.add_field(name="Vuelos activos", value=str(vuelos), inline=True)
    embed.add_field(name="Controladores en línea", value=str(len(posiciones)), inline=True)
    embed.add_field(name="Verificados", value=f"{verificados} / {totales}", inline=True)
    archivo = await aplicar_imagen_formal(embed)
    if archivo:
        await interaction.followup.send(embed=embed, file=archivo)
    else:
        await interaction.followup.send(embed=embed)


@tree.command(name="certificado-verificar", description="Comprueba la autenticidad de un certificado por su código")
@app_commands.describe(codigo="Código de verificación de 6 caracteres que aparece en el certificado")
async def certificado_verificar(interaction: discord.Interaction, codigo: str):
    await interaction.response.defer(ephemeral=True)
    cert = await academy_core.get_certificate_by_code(db, codigo.strip())
    if not cert:
        await interaction.followup.send("No encontré ningún certificado con ese código.", ephemeral=True)
        return
    revocado = cert["status"] == "REVOCADO"
    embed = discord.Embed(
        title="Certificado revocado ❌" if revocado else "Certificado verificado ✅",
        color=(0xB0413E if revocado else BRAND_RADAR_GREEN),
    )
    embed.add_field(name="Titular", value=f"<@{cert['user_id']}>", inline=True)
    embed.add_field(name="Curso", value=cert["course_title"], inline=True)
    embed.add_field(name="Rama", value=cert["branch"], inline=True)
    embed.add_field(name="Emitido", value=_fecha_ms(cert["issued_at"]), inline=True)
    embed.add_field(name="Estado", value="REVOCADO" if revocado else "VÁLIDO", inline=True)
    embed.add_field(name="Registro", value=f"`{academy_core.compact_record(cert)}`", inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


@tree.command(name="certificado-revocar", description="Revoca un certificado por su código (Staff)")
@app_commands.describe(codigo="Código de verificación de 6 caracteres del certificado a revocar")
async def certificado_revocar(interaction: discord.Interaction, codigo: str):
    if not has_any_role(interaction.user, LIDERAZGO_ORDER):
        await interaction.response.send_message("Este comando es solo para Staff.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    revocado = await academy_core.revoke_certificate(db, codigo.strip(), str(interaction.user.id))
    if not revocado:
        await interaction.followup.send(
            "No encontré ningún certificado vigente con ese código (o ya estaba revocado).", ephemeral=True
        )
        return
    await interaction.followup.send(
        f"Certificado `{revocado['verify_code']}` de <@{revocado['user_id']}> revocado. "
        f"Registro: `{academy_core.compact_record(revocado)}`",
        ephemeral=True,
    )


_MOD_LOG_COLORES = {
    moderation_core.WARN: 0xB0413E,
    moderation_core.TIMEOUT: BRAND_BEACON_AMBER,
    moderation_core.KICK: 0xB0413E,
    moderation_core.BAN: 0x8B0000,
    moderation_core.UNBAN: BRAND_RADAR_GREEN,
}


async def _registrar_caso_moderacion(*, miembro: discord.abc.User, moderador: discord.abc.User, accion: str,
                                      motivo: str, duracion_minutos: int = None) -> dict:
    """Punto único donde se crea un caso Y se loguea — antes cada comando
    (solo /advertir existía) repetía su propia lógica de log inline; ahora
    /timeout, /kick y /ban comparten el mismo formato de registro."""
    caso = await moderation_core.create_case(
        db, user_id=str(miembro.id), moderator_id=str(moderador.id), moderator_name=str(moderador),
        action=accion, reason=motivo, duration_minutes=duracion_minutos,
    )
    if DISCORD_CHANNEL_MOD_LOG:
        canal_log = client.get_channel(int(DISCORD_CHANNEL_MOD_LOG))
        if canal_log:
            etiqueta = moderation_core.ACTION_LABELS.get(accion, accion)
            embed_log = discord.Embed(title=f"Caso #{caso['id']} — {etiqueta}", color=_MOD_LOG_COLORES.get(accion, BRAND_SKY_NAVY))
            embed_log.add_field(name="Usuario", value=miembro.mention, inline=True)
            embed_log.add_field(name="Por", value=moderador.mention, inline=True)
            if duracion_minutos:
                embed_log.add_field(name="Duración", value=f"{duracion_minutos} min", inline=True)
            embed_log.add_field(name="Motivo", value=motivo or "—", inline=False)
            archivo = await aplicar_imagen_formal(embed_log)
            if archivo:
                await canal_log.send(embed=embed_log, file=archivo)
            else:
                await canal_log.send(embed=embed_log)
    return caso


class ApelacionModal(discord.ui.Modal, title="Apelar una sanción"):
    motivo = discord.ui.TextInput(
        label="Explicá por qué apelás esta sanción", style=discord.TextStyle.paragraph, max_length=1000,
    )

    def __init__(self, case_id: int):
        super().__init__()
        self.case_id = case_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        canal_id = DISCORD_CHANNEL_APELACIONES or DISCORD_CHANNEL_MOD_LOG
        canal = client.get_channel(int(canal_id)) if canal_id else None
        if not canal:
            await interaction.followup.send(
                "No hay un canal de apelaciones configurado — comunicate con el Staff directamente.", ephemeral=True
            )
            return
        embed = discord.Embed(title=f"Apelación — Caso #{self.case_id}", color=BRAND_BEACON_AMBER)
        embed.add_field(name="Usuario", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Explicación", value=str(self.motivo), inline=False)
        archivo = await aplicar_imagen_formal(embed)
        if archivo:
            await canal.send(embed=embed, file=archivo)
        else:
            await canal.send(embed=embed)
        await interaction.followup.send(
            "Tu apelación fue enviada al Staff. Vas a recibir una respuesta a la brevedad.", ephemeral=True
        )


class ApelacionView(discord.ui.View):
    def __init__(self, case_id: int):
        super().__init__(timeout=None)
        self.case_id = case_id

    @discord.ui.button(label="Apelar", style=discord.ButtonStyle.secondary, emoji="📝")
    async def apelar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ApelacionModal(self.case_id))


async def _avisar_por_dm(usuario: discord.abc.User, *, titulo: str, descripcion: str, motivo: str, color: int,
                          caso: dict = None) -> None:
    try:
        embed_dm = discord.Embed(title=titulo, description=descripcion, color=color)
        embed_dm.add_field(name="Motivo", value=motivo or "—", inline=False)
        vista = None
        if caso:
            embed_dm.set_footer(text="Si no estás de acuerdo con esta sanción, puedes apelarla con el botón de abajo.")
            vista = ApelacionView(caso["id"])
        else:
            embed_dm.set_footer(text="Ante cualquier duda, comunicate con el Staff del servidor.")
        archivo = await aplicar_imagen_formal(embed_dm)
        if archivo:
            await usuario.send(embed=embed_dm, view=vista, file=archivo)
        else:
            await usuario.send(embed=embed_dm, view=vista)
    except discord.Forbidden:
        pass  # MD cerrados — no rompe el flujo, el caso ya quedó registrado


async def _ejecutar_advertencia(*, moderador: discord.abc.User, miembro: discord.Member, motivo: str) -> dict:
    """Lógica compartida entre /advertir y el dashboard de Staff (Bloque B5)."""
    caso = await _registrar_caso_moderacion(miembro=miembro, moderador=moderador, accion=moderation_core.WARN, motivo=motivo)
    await _avisar_por_dm(
        miembro, titulo=f"{E['cruz']} Recibiste una advertencia",
        descripcion="Se registró una advertencia formal a tu nombre en **ATC24 Español**. Te pedimos que evites que se repita.",
        motivo=motivo, color=0xB0413E, caso=caso,
    )
    return caso


async def _ejecutar_timeout(*, moderador: discord.abc.User, miembro: discord.Member, minutos: int, motivo: str) -> tuple[dict | None, str | None]:
    """Devuelve (caso, error) — error no es None si Discord rechazó la acción."""
    try:
        await miembro.timeout(datetime.timedelta(minutes=minutos), reason=motivo)
    except discord.Forbidden:
        return None, "No hay permiso para silenciar a ese usuario (su rol podría estar por encima del rol del bot)."
    except discord.HTTPException as err:
        return None, f"Discord rechazó el timeout: {err}"
    rol_mute = miembro.guild.get_role(DISCORD_ROLE_MUTE)
    if rol_mute:
        try:
            await miembro.add_roles(rol_mute, reason=motivo)
        except discord.Forbidden:
            print(f"Aviso: no pude asignar el rol de mute a {miembro} (jerarquía insuficiente).")
    caso = await _registrar_caso_moderacion(
        miembro=miembro, moderador=moderador, accion=moderation_core.TIMEOUT, motivo=motivo, duracion_minutos=minutos
    )
    await _avisar_por_dm(
        miembro, titulo="Fuiste silenciado temporalmente",
        descripcion=f"Se te aplicó un timeout de {minutos} minutos en **ATC24 Español**.",
        motivo=motivo, color=BRAND_BEACON_AMBER, caso=caso,
    )
    return caso, None


async def _ejecutar_kick(*, moderador: discord.abc.User, miembro: discord.Member, motivo: str) -> tuple[dict | None, str | None]:
    caso = await _registrar_caso_moderacion(miembro=miembro, moderador=moderador, accion=moderation_core.KICK, motivo=motivo)
    await _avisar_por_dm(
        miembro, titulo="Fuiste expulsado",
        descripcion="Se te expulsó de **ATC24 Español**. Puedes volver a unirte al servidor si corresponde.",
        motivo=motivo, color=0xB0413E, caso=caso,
    )
    try:
        await miembro.kick(reason=motivo)
    except discord.Forbidden:
        return caso, "El caso quedó registrado, pero no hay permiso para expulsar a ese usuario (jerarquía insuficiente)."
    return caso, None


async def _ejecutar_ban(*, moderador: discord.abc.User, miembro: discord.Member, motivo: str, borrar_mensajes_dias: int = 0) -> tuple[dict | None, str | None]:
    caso = await _registrar_caso_moderacion(miembro=miembro, moderador=moderador, accion=moderation_core.BAN, motivo=motivo)
    await _avisar_por_dm(
        miembro, titulo="Fuiste baneado", descripcion="Se te baneó de **ATC24 Español**.",
        motivo=motivo, color=0x8B0000, caso=caso,
    )
    try:
        await miembro.ban(reason=motivo, delete_message_seconds=borrar_mensajes_dias * 86400)
    except discord.Forbidden:
        return caso, "El caso quedó registrado, pero no hay permiso para banear a ese usuario (jerarquía insuficiente)."
    return caso, None


@tree.command(name="advertir", description="Registra una advertencia formal a un usuario (Instructores/Staff)")
@app_commands.default_permissions()
@app_commands.describe(miembro="Usuario a advertir", motivo="Motivo detallado — queda registrado y se le manda por MD")
async def advertir(interaction: discord.Interaction, miembro: discord.Member, motivo: str):
    if not es_staff_moderacion(interaction.user):
        await interaction.response.send_message("Este comando es solo para Instructores/Staff.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    caso = await _ejecutar_advertencia(moderador=interaction.user, miembro=miembro, motivo=motivo)
    total = await moderation_core.count_active_warns(db, str(miembro.id))
    await interaction.followup.send(
        f"{E['cruz']} Advertencia (caso **#{caso['id']}**) registrada para {miembro.mention}. Ahora tiene **{total}** advertencia(s) activa(s).",
        ephemeral=True,
    )


@tree.command(name="timeout", description="Aplica un timeout (silencio temporal) a un usuario y registra el caso")
@app_commands.default_permissions()
@app_commands.describe(
    miembro="Usuario a silenciar", minutos="Duración en minutos (máx. 40320 = 28 días)",
    motivo="Motivo del timeout — queda registrado y se le manda por MD",
)
async def timeout_cmd(interaction: discord.Interaction, miembro: discord.Member, minutos: app_commands.Range[int, 1, 40320], motivo: str):
    if not es_staff_moderacion(interaction.user):
        await interaction.response.send_message("Este comando es solo para Instructores/Staff.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    caso, error = await _ejecutar_timeout(moderador=interaction.user, miembro=miembro, minutos=minutos, motivo=motivo)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    await interaction.followup.send(f"{miembro.mention} silenciado por {minutos} min (caso **#{caso['id']}**). ✅", ephemeral=True)


@tree.command(name="kick", description="Expulsa a un usuario del servidor — puede volver a unirse (solo Liderazgo)")
@app_commands.default_permissions()
@app_commands.describe(miembro="Usuario a expulsar", motivo="Motivo de la expulsión — se le manda por MD antes de expulsarlo")
async def kick_cmd(interaction: discord.Interaction, miembro: discord.Member, motivo: str):
    if not es_staff_senior(interaction.user):
        await interaction.response.send_message("Este comando es solo para Liderazgo.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    caso, error = await _ejecutar_kick(moderador=interaction.user, miembro=miembro, motivo=motivo)
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    await interaction.followup.send(f"{miembro.mention} expulsado (caso **#{caso['id']}**). ✅", ephemeral=True)


@tree.command(name="ban", description="Banea a un usuario del servidor de forma permanente (solo Liderazgo)")
@app_commands.default_permissions()
@app_commands.describe(
    miembro="Usuario a banear", motivo="Motivo del ban — se le manda por MD antes de banearlo",
    borrar_mensajes_dias="Borrar también sus mensajes de los últimos N días (0-7, opcional)",
)
async def ban_cmd(interaction: discord.Interaction, miembro: discord.Member, motivo: str,
                   borrar_mensajes_dias: app_commands.Range[int, 0, 7] = 0):
    if not es_staff_senior(interaction.user):
        await interaction.response.send_message("Este comando es solo para Liderazgo.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    caso, error = await _ejecutar_ban(
        moderador=interaction.user, miembro=miembro, motivo=motivo, borrar_mensajes_dias=borrar_mensajes_dias
    )
    if error:
        await interaction.followup.send(error, ephemeral=True)
        return
    await interaction.followup.send(f"{miembro.mention} baneado (caso **#{caso['id']}**). ✅", ephemeral=True)


@tree.command(name="advertencias", description="Consulta el historial de moderación propio, o el de otro usuario si es Instructor o Staff")
@app_commands.describe(usuario="Usuario a consultar (opcional; en blanco muestra el historial propio)")
async def advertencias(interaction: discord.Interaction, usuario: discord.Member = None):
    objetivo = usuario or interaction.user
    if usuario and usuario.id != interaction.user.id and not es_staff_moderacion(interaction.user):
        await interaction.response.send_message(
            "Esta información solo está disponible para Instructores o Staff cuando se consulta a otro usuario.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)
    casos = await moderation_core.cases_for_user(db, str(objetivo.id))
    if not casos:
        await interaction.followup.send(
            f"{objetivo.mention} no registra casos de moderación en el sistema.", ephemeral=True
        )
        return

    activos = [c for c in casos if c["active"]]
    revocados = [c for c in casos if not c["active"]]
    embed = discord.Embed(title=f"Historial de moderación — {objetivo.display_name}", color=0xB0413E)
    embed.description = (
        f"**Total de casos:** {len(casos)}  ·  **Activos:** {len(activos)}  ·  **Revocados:** {len(revocados)}"
    )

    def _agregar_casos(nombre_seccion: str, lista: list):
        if not lista:
            return
        texto = "\n".join(
            f"**Caso #{c['id']}** — {moderation_core.ACTION_LABELS.get(c['action'], c['action'])} — "
            f"{datetime.datetime.fromisoformat(c['created_at']).strftime('%d/%m/%Y')}\n"
            f"Motivo: {c.get('reason') or 'No especificado'}"
            for c in lista[:10]
        )
        embed.add_field(name=nombre_seccion, value=texto, inline=False)

    _agregar_casos("Casos activos", activos)
    _agregar_casos("Casos revocados", revocados)

    if len(casos) > 20:
        embed.set_footer(text="Se muestran únicamente los casos más recientes de cada categoría.")
    archivo = await aplicar_imagen_formal(embed)
    if archivo:
        await interaction.followup.send(embed=embed, file=archivo, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)


class ModPanelRevocarSelect(discord.ui.Select):
    def __init__(self, opciones: list):
        super().__init__(placeholder="Revocar un caso activo…", options=opciones, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        vista: "ModPanelView" = self.view
        if interaction.user.id != vista.autor_id:
            await interaction.response.send_message("Solo quien abrió este panel puede usarlo.", ephemeral=True)
            return
        caso = await moderation_core.revoke_case(db, int(self.values[0]))
        if caso:
            await interaction.response.send_message(f"Caso **#{caso['id']}** revocado. ✅", ephemeral=True)
        else:
            await interaction.response.send_message("No encontré ese caso.", ephemeral=True)


class ModPanelView(discord.ui.View):
    def __init__(self, casos: list, autor_id: int):
        super().__init__(timeout=180)
        self.autor_id = autor_id
        opciones = [
            discord.SelectOption(
                label=f"Caso #{c['id']} — {moderation_core.ACTION_LABELS.get(c['action'], c['action'])}"[:100],
                description=(c.get("reason") or "—")[:100],
                value=str(c["id"]),
            )
            for c in casos if c["active"]
        ][:25]
        if opciones:
            self.add_item(ModPanelRevocarSelect(opciones))


async def _enviar_panel_moderacion_usuario(interaction: discord.Interaction, miembro: discord.Member) -> None:
    """Bloque B11: lógica compartida entre el antiguo /panel-moderacion
    (retirado como comando independiente) y el botón "Consultar historial"
    del Dashboard de Staff (Bloque B5)."""
    casos = await moderation_core.cases_for_user(db, str(miembro.id))
    embed = discord.Embed(title=f"Panel de moderación — {miembro.display_name}", color=BRAND_SKY_NAVY)
    if not casos:
        embed.description = "Sin casos registrados."
    else:
        activas = sum(1 for c in casos if c["action"] == moderation_core.WARN and c["active"])
        embed.description = f"{len(casos)} caso(s) en total · {activas} advertencia(s) activa(s)"
        for c in casos[:10]:
            fecha = datetime.datetime.fromisoformat(c["created_at"]).strftime("%d/%m/%Y")
            etiqueta = moderation_core.ACTION_LABELS.get(c["action"], c["action"])
            estado = "" if c["active"] else " _(revocado)_"
            embed.add_field(name=f"Caso #{c['id']} — {etiqueta} — {fecha}{estado}", value=c.get("reason") or "—", inline=False)
    archivo = await aplicar_imagen_formal(embed)
    if archivo:
        await interaction.followup.send(embed=embed, view=ModPanelView(casos, interaction.user.id), file=archivo, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, view=ModPanelView(casos, interaction.user.id), ephemeral=True)


# Bloque B11: /panel-moderacion retirado como comando independiente — la
# misma función (_enviar_panel_moderacion_usuario) ahora se dispara desde el
# botón "Consultar historial" del Dashboard de Staff (!staff).
# @tree.command(name="panel-moderacion", description="Abre el panel de moderación de un usuario: historial completo y revocar casos")
@app_commands.default_permissions()
@app_commands.describe(miembro="Usuario cuyo historial de moderación se va a revisar")
async def panel_moderacion(interaction: discord.Interaction, miembro: discord.Member):
    if not es_staff_moderacion(interaction.user):
        await interaction.response.send_message("Este comando es solo para Instructores/Staff.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await _enviar_panel_moderacion_usuario(interaction, miembro)


# ─── Dashboard de Staff (Bloque B5) ────────────────────────────────────────
# Panel permanente (publicado con "!staff"), exclusivo para Director
# Ejecutivo y Subdirector Ejecutivo (CEO/EXO — los dos primeros de
# LIDERAZGO_ORDER), que centraliza advertir/timeout/kick/ban/historial/
# reglas en un solo lugar en vez de que cada quien tenga que recordar un
# comando distinto para cada acción.
STAFF_DASHBOARD_ROLE_IDS = LIDERAZGO_ORDER[:2]  # CEO, EXO

REGLAS_STAFF_TEXTO = (
    "1. Toda sanción debe registrarse con un motivo claro y verificable.\n"
    "2. Las expulsiones y los baneos requieren nivel Liderazgo.\n"
    "3. Toda apelación debe responderse en un plazo razonable.\n"
    "4. El Staff debe mantener un trato respetuoso y profesional en todo momento.\n"
    "5. Ante cualquier duda sobre un caso, se consulta con otro miembro de Liderazgo antes de actuar.\n\n"
    "_(Texto de referencia — reemplazar por el reglamento real de Staff cuando esté definido.)_"
)


def _puede_usar_dashboard_staff(member: discord.Member) -> bool:
    return has_any_role(member, STAFF_DASHBOARD_ROLE_IDS)


class AdvertirMotivoModal(discord.ui.Modal, title="Advertir usuario"):
    motivo = discord.ui.TextInput(label="Motivo", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, miembro: discord.Member):
        super().__init__()
        self.miembro = miembro

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        caso = await _ejecutar_advertencia(moderador=interaction.user, miembro=self.miembro, motivo=str(self.motivo))
        await interaction.followup.send(f"Advertencia registrada para {self.miembro.mention} (caso **#{caso['id']}**). ✅", ephemeral=True)


class TimeoutMotivoModal(discord.ui.Modal, title="Aplicar timeout"):
    minutos = discord.ui.TextInput(label="Duración en minutos", max_length=6, placeholder="ej. 60")
    motivo = discord.ui.TextInput(label="Motivo", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, miembro: discord.Member):
        super().__init__()
        self.miembro = miembro

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        valor = str(self.minutos)
        if not valor.isdigit() or int(valor) <= 0:
            await interaction.followup.send("Ingresa una duración válida en minutos.", ephemeral=True)
            return
        caso, error = await _ejecutar_timeout(moderador=interaction.user, miembro=self.miembro, minutos=int(valor), motivo=str(self.motivo))
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"{self.miembro.mention} silenciado (caso **#{caso['id']}**). ✅", ephemeral=True)


class KickMotivoModal(discord.ui.Modal, title="Expulsar usuario"):
    motivo = discord.ui.TextInput(label="Motivo", style=discord.TextStyle.paragraph, max_length=500)

    def __init__(self, miembro: discord.Member):
        super().__init__()
        self.miembro = miembro

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        caso, error = await _ejecutar_kick(moderador=interaction.user, miembro=self.miembro, motivo=str(self.motivo))
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"{self.miembro.mention} expulsado (caso **#{caso['id']}**). ✅", ephemeral=True)


class BanMotivoModal(discord.ui.Modal, title="Banear usuario"):
    motivo = discord.ui.TextInput(label="Motivo", style=discord.TextStyle.paragraph, max_length=500)
    borrar_dias = discord.ui.TextInput(
        label="Borrar mensajes de los últimos N días (0-7)", required=False, default="0", max_length=1,
    )

    def __init__(self, miembro: discord.Member):
        super().__init__()
        self.miembro = miembro

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        dias_txt = str(self.borrar_dias) or "0"
        dias = int(dias_txt) if dias_txt.isdigit() and 0 <= int(dias_txt) <= 7 else 0
        caso, error = await _ejecutar_ban(
            moderador=interaction.user, miembro=self.miembro, motivo=str(self.motivo), borrar_mensajes_dias=dias
        )
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        await interaction.followup.send(f"{self.miembro.mention} baneado (caso **#{caso['id']}**). ✅", ephemeral=True)


class StaffAccionSelect(discord.ui.UserSelect):
    _ETIQUETAS = {"advertir": "advertir", "timeout": "silenciar", "kick": "expulsar", "ban": "banear", "historial": "consultar"}

    def __init__(self, accion: str):
        super().__init__(placeholder=f"Elige el usuario a {self._ETIQUETAS[accion]}…", min_values=1, max_values=1)
        self.accion = accion

    async def callback(self, interaction: discord.Interaction):
        miembro = self.values[0]
        if not isinstance(miembro, discord.Member):
            await interaction.response.send_message("No pude resolver ese usuario en este servidor.", ephemeral=True)
            return
        if self.accion == "advertir":
            await interaction.response.send_modal(AdvertirMotivoModal(miembro))
        elif self.accion == "timeout":
            await interaction.response.send_modal(TimeoutMotivoModal(miembro))
        elif self.accion == "kick":
            await interaction.response.send_modal(KickMotivoModal(miembro))
        elif self.accion == "ban":
            await interaction.response.send_modal(BanMotivoModal(miembro))
        elif self.accion == "historial":
            await interaction.response.defer(ephemeral=True)
            await _enviar_panel_moderacion_usuario(interaction, miembro)


class StaffAccionView(discord.ui.View):
    def __init__(self, accion: str):
        super().__init__(timeout=120)
        self.add_item(StaffAccionSelect(accion))


class StaffDashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistente

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not _puede_usar_dashboard_staff(interaction.user):
            await interaction.response.send_message(
                "Este panel es exclusivo para Director Ejecutivo y Subdirector Ejecutivo.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Advertir", style=discord.ButtonStyle.secondary, emoji="⚠️", custom_id="atc24:staff:advertir", row=0)
    async def advertir_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Elige el usuario a advertir:", view=StaffAccionView("advertir"), ephemeral=True)

    @discord.ui.button(label="Timeout", style=discord.ButtonStyle.secondary, emoji="🔇", custom_id="atc24:staff:timeout", row=0)
    async def timeout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Elige el usuario a silenciar:", view=StaffAccionView("timeout"), ephemeral=True)

    @discord.ui.button(label="Kick", style=discord.ButtonStyle.danger, emoji="🚪", custom_id="atc24:staff:kick", row=0)
    async def kick_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Elige el usuario a expulsar:", view=StaffAccionView("kick"), ephemeral=True)

    @discord.ui.button(label="Ban", style=discord.ButtonStyle.danger, emoji="🔨", custom_id="atc24:staff:ban", row=0)
    async def ban_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Elige el usuario a banear:", view=StaffAccionView("ban"), ephemeral=True)

    @discord.ui.button(label="Consultar historial", style=discord.ButtonStyle.primary, emoji="📋", custom_id="atc24:staff:historial", row=1)
    async def historial_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Elige el usuario a consultar:", view=StaffAccionView("historial"), ephemeral=True)

    @discord.ui.button(label="Reglas de Staff", style=discord.ButtonStyle.secondary, emoji="📖", custom_id="atc24:staff:reglas", row=1)
    async def reglas_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(title="Reglas de Staff", description=REGLAS_STAFF_TEXTO, color=BRAND_SKY_NAVY)
        archivo = await aplicar_imagen_formal(embed)
        if archivo:
            await interaction.response.send_message(embed=embed, file=archivo, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


async def _embed_dashboard_staff() -> tuple[discord.Embed, discord.File | None]:
    embed = discord.Embed(
        title="Dashboard de Staff — ATC24 Español",
        description=(
            "Panel centralizado de moderación y gestión, disponible únicamente para Director Ejecutivo y "
            "Subdirector Ejecutivo.\n\nUsa los botones de abajo para advertir, silenciar, expulsar o banear "
            "a un usuario, consultar su historial de moderación, o revisar las reglas de Staff."
        ),
        color=BRAND_SKY_NAVY,
    )
    archivo = await aplicar_imagen_formal(embed)
    return embed, archivo


@tree.command(name="purgar", description="Borra en bloque los mensajes más recientes de este canal")
@app_commands.default_permissions()
@app_commands.describe(cantidad="Cuántos mensajes borrar (entre 1 y 100; Discord no permite borrar los de más de 14 días)")
async def purgar(interaction: discord.Interaction, cantidad: app_commands.Range[int, 1, 100]):
    if not es_staff_moderacion(interaction.user):
        await interaction.response.send_message("Este comando es solo para Instructores/Staff.", ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message("Esto solo funciona en canales de texto normales.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        # Discord no permite borrado masivo de mensajes con más de 14 días —
        # purge() los salta solos en vez de fallar, así que puede borrar
        # menos de lo pedido si el canal está poco activo.
        borrados = await interaction.channel.purge(limit=cantidad)
    except discord.Forbidden:
        await interaction.followup.send(
            "No pude borrar mensajes — al bot le falta el permiso \"Gestionar mensajes\" en este canal.",
            ephemeral=True,
        )
        return
    except Exception as err:
        await interaction.followup.send(f"No pude borrar los mensajes: {err}", ephemeral=True)
        return

    await interaction.followup.send(f"Borré {len(borrados)} mensaje(s) de este canal.", ephemeral=True)


# ─────────────────────────────────────────────────────────────
# Sistema de tickets de soporte — canal privado por ticket, creado a demanda
# desde el botón "Apelar" de una sanción o desde el botón del panel fijo
# (!panel-soporte). Solo lo ve quien lo abrió y el staff (SOPORTE_ROLE_ID, o Liderazgo si no hay rol
# configurado). El seguimiento de "quién tiene un ticket abierto" vive en
# memoria — sobrevive mientras el bot está corriendo, se resetea al
# reiniciar (peor caso: alguien puede abrir un segundo ticket si reinició
# justo en el medio, no rompe nada).
TICKET_PANEL_CUSTOM_ID = "atc24:ticket:abrir"
TICKET_CERRAR_CUSTOM_ID = "atc24:ticket:cerrar"

_tickets_abiertos = {}  # discord_id -> channel_id


def _ticket_autor(channel_id: int):
    for uid, cid in _tickets_abiertos.items():
        if cid == channel_id:
            return uid
    return None


def _slug_canal(nombre: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", nombre.lower()).strip("-")
    return (s or "usuario")[:80]


def _siguiente_numero_ticket() -> int:
    """Contador global persistente — cada ticket nuevo (de cualquier
    usuario) se lleva el siguiente número de la secuencia, sin importar
    cuántos tickets tenga abiertos o cerrados cada quien."""
    estado = _leer_json("tickets_contador.json", {"siguiente": 1})
    numero = estado["siguiente"]
    estado["siguiente"] = numero + 1
    _guardar_json("tickets_contador.json", estado)
    return numero


async def _categoria_tickets(guild: discord.Guild):
    if TICKETS_CATEGORY_ID:
        cat = guild.get_channel(TICKETS_CATEGORY_ID)
        if isinstance(cat, discord.CategoryChannel):
            return cat
    for c in guild.categories:
        if c.name.lower() == "tickets":
            return c
    try:
        return await guild.create_category("Tickets", reason="Categoría automática para tickets de soporte")
    except discord.Forbidden:
        return None


async def _crear_ticket(interaction: discord.Interaction, motivo: str, descripcion: str):
    guild = interaction.guild or (client.get_guild(int(GUILD_ID)) if GUILD_ID else None)
    if guild is None:
        await interaction.response.send_message("No pude ubicar el servidor — avisa al staff.", ephemeral=True)
        return

    existente_id = _tickets_abiertos.get(interaction.user.id)
    if existente_id:
        canal_existente = guild.get_channel(existente_id)
        if canal_existente:
            await interaction.response.send_message(f"Ya tienes un ticket abierto: {canal_existente.mention}", ephemeral=True)
            return
        _tickets_abiertos.pop(interaction.user.id, None)  # el canal ya no existe — se limpia el registro viejo

    await interaction.response.defer(ephemeral=True)

    categoria = await _categoria_tickets(guild)
    rol_soporte = guild.get_role(SOPORTE_ROLE_ID) if SOPORTE_ROLE_ID else None
    rol_stf = guild.get_role(STF_ROLE_ID)

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if rol_soporte:
        overwrites[rol_soporte] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    else:
        # Public Manager (PM) queda excluido a propósito — no debe ver
        # tickets aunque forme parte de Liderazgo.
        for rid in LIDERAZGO_ORDER:
            if rid == PM_ROLE_ID:
                continue
            rol = guild.get_role(rid)
            if rol:
                overwrites[rol] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    if rol_stf:
        overwrites[rol_stf] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

    numero_ticket = _siguiente_numero_ticket()
    # Bloque B6: el nombre del canal usa el username real de Discord
    # (interaction.user.name), no el apodo del servidor — el apodo puede
    # tener prefijos de rango que cambian y no identifica a la persona de
    # forma estable como sí lo hace su username.
    nombre_canal = f"{_slug_canal(interaction.user.name)}_{numero_ticket:03d}"
    try:
        canal = await guild.create_text_channel(
            nombre_canal, category=categoria, overwrites=overwrites,
            reason=f"Ticket de soporte abierto por {interaction.user}",
        )
    except discord.Forbidden:
        await interaction.followup.send(
            "No pude crear el canal del ticket — al bot le falta el permiso \"Gestionar canales\".",
            ephemeral=True,
        )
        return

    _tickets_abiertos[interaction.user.id] = canal.id

    embed = discord.Embed(title=f"{E['chat']} Ticket de soporte #{numero_ticket:03d}", description=descripcion, color=BRAND_SKY_NAVY)
    embed.add_field(name="Abierto por", value=f"{interaction.user.mention} ({interaction.user.name})", inline=True)
    embed.add_field(name="Motivo", value=motivo, inline=True)
    embed.set_footer(text="ATC24 Español")

    mencion_staff = rol_soporte.mention if rol_soporte else " ".join(f"<@&{rid}>" for rid in LIDERAZGO_ORDER[:1])
    mencion_stf = rol_stf.mention if rol_stf else f"<@&{STF_ROLE_ID}>"
    archivo = await aplicar_imagen_formal(embed)
    if archivo:
        await canal.send(
            content=f"{interaction.user.mention} {mencion_staff} {mencion_stf}",
            embed=embed, view=TicketCanalView(), file=archivo,
        )
    else:
        await canal.send(
            content=f"{interaction.user.mention} {mencion_staff} {mencion_stf}",
            embed=embed, view=TicketCanalView(),
        )
    await canal.send(f"{E['reloj']} Gracias por escribirnos — un miembro del Staff te va a atender en breve.")
    await interaction.followup.send(f"Listo, tu ticket quedó en {canal.mention}.", ephemeral=True)


class TicketModal(discord.ui.Modal, title="Abrir ticket de soporte"):
    motivo = discord.ui.TextInput(label="Motivo (breve)", max_length=100, required=True)
    descripcion = discord.ui.TextInput(
        label="Descripción",
        style=discord.TextStyle.paragraph,
        placeholder="Cuéntanos qué pasó, con el mayor detalle posible…",
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await _crear_ticket(interaction, str(self.motivo), str(self.descripcion))


class TicketCanalView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistente

    @discord.ui.button(label="Cerrar ticket", style=discord.ButtonStyle.danger, custom_id=TICKET_CERRAR_CUSTOM_ID)
    async def cerrar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        autor_id = _ticket_autor(interaction.channel.id)
        es_staff = has_any_role(interaction.user, LIDERAZGO_ORDER) or (SOPORTE_ROLE_ID and has_any_role(interaction.user, [SOPORTE_ROLE_ID]))
        if not es_staff and interaction.user.id != autor_id:
            await interaction.response.send_message("Solo el autor del ticket o el staff pueden cerrarlo.", ephemeral=True)
            return

        await interaction.response.send_message(f"Cerrando este ticket en 5 segundos — lo cierra {interaction.user.mention}.")
        if autor_id is not None:
            _tickets_abiertos.pop(autor_id, None)
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason=f"Ticket cerrado por {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("No pude borrar el canal — al bot le falta el permiso \"Gestionar canales\".")


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)  # persistente

    @discord.ui.button(label="Abrir ticket", style=discord.ButtonStyle.primary, custom_id=TICKET_PANEL_CUSTOM_ID)
    async def abrir_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal())


# Bloque B6: /panel-soporte deja de ser un comando slash — pasa a
# publicarse con el comando de texto "!panel-soporte" (ver el bloque de
# comandos "!" más abajo), igual que el resto de los paneles/guías fijas.
async def _embed_panel_soporte() -> tuple[discord.Embed, discord.File | None]:
    embed = discord.Embed(
        title="Soporte ATC24 Español",
        description=(
            f"{E['chat']} ¿Tienes un problema, una duda o algo para reportar? Presiona el botón de abajo "
            "y se va a crear un canal privado — solo lo van a poder ver tú y el Staff.\n\n"
            "**Motivos habituales para abrir un ticket:**\n"
            "• Reportar un error o mal funcionamiento del bot.\n"
            "• Pedir ayuda con la verificación de Bloxlink.\n"
            "• Consultar o disputar tu rango, rama o apodo.\n"
            "• Reportar a otro usuario por una falta de conducta.\n"
            "• Cualquier duda sobre Academia, evaluaciones o ascensos.\n"
            "• Apelar una sanción de moderación.\n"
            "• Cualquier otra situación que prefieras tratar en privado con el Staff.\n\n"
            "Contanos con el mayor detalle posible qué pasó — así el Staff puede ayudarte más rápido."
        ),
        color=BRAND_SKY_NAVY,
    )
    archivo = await aplicar_imagen_formal(embed)
    return embed, archivo


# Bloque B8: /reportar retirado como comando independiente — el mismo
# formulario (TicketModal) ahora se abre desde el botón "Apelar" de una
# sanción (ApelacionModal) o desde el panel fijo de soporte
# (TicketPanelView, /panel-soporte). Se deja la función intacta.
# @tree.command(name="reportar", description="Abre un ticket privado con el staff para reportar un problema o hacer una consulta")
async def reportar(interaction: discord.Interaction):
    await interaction.response.send_modal(TicketModal())


@tree.command(name="eco", description="Publica un anuncio formal en nombre del bot — en un canal, por MD, o aquí mismo")
@app_commands.default_permissions()
@app_commands.describe(
    canal="Canal donde publicarlo (si se deja vacío junto con usuario, se manda en este mismo canal)",
    usuario="Usuario a quien mandárselo por MD en vez de publicarlo en un canal",
)
async def eco(
    interaction: discord.Interaction,
    canal: discord.TextChannel = None,
    usuario: discord.Member = None,
):
    if not has_any_role(interaction.user, LIDERAZGO_ORDER + INSTRUCTOR_ORDER):
        await interaction.response.send_message("Este comando es solo para Instructores/Staff.", ephemeral=True)
        return
    if canal and usuario:
        await interaction.response.send_message("Elige solo uno: un canal O un usuario, no ambos.", ephemeral=True)
        return

    # Si no se especifica nada, se manda en el mismo canal donde se ejecutó el comando.
    destino = usuario or canal or interaction.channel
    await interaction.response.send_modal(EcoModal(destino))


# ─── Guardia de rate limit para las llamadas REST crudas a Discord ─────────
# discord.py maneja el rate limit solo para las llamadas que pasan por su
# propio client.http — pero varias partes de este bot le pegan a la API de
# Discord directo con aiohttp (ver más abajo) para no depender de la firma
# interna de discord.py, que cambia entre versiones sin aviso. Esas llamadas
# crudas NO tenían ningún manejo de 429: si Discord respondía "esperá X
# segundos", el código lo ignoraba y seguía pegándole igual en el próximo
# evento. Según la documentación de Discord (developers.discord.com/
# developers/topics/rate-limits), una IP que acumula 10.000 respuestas
# inválidas (401/403/429) en 10 minutos queda baneada por Cloudflare — eso es
# el Error 1015 que vimos en producción, y ESTA era la causa real: no un bug
# puntual, sino que ninguna llamada cruda respetaba el rate limit, y varias
# de ellas (la tabla de ATC Online sobre todo) se disparan muy seguido.
#
# _discord_request centraliza TODAS esas llamadas: reutiliza una sola sesión
# (en vez de abrir una por request), respeta Retry-After reintentando una
# vez, y lleva la cuenta de respuestas inválidas en una ventana de 10
# minutos. Si esa cuenta se acerca al límite real de Discord, el bot se
# pausa SOLO — dejo de mandar llamadas crudas por un rato — en vez de
# esperar a que Cloudflare lo bloquee. Es el freno de mano propio antes del
# de ellos.
import collections as _collections
import time as _time

_RATE_GUARD_WINDOW = 600      # 10 min — misma ventana que usa Discord/Cloudflare
_RATE_GUARD_THRESHOLD = 5000  # nos frenamos a la mitad del límite real de Discord (10.000)
_RATE_GUARD_PAUSE = 120       # cuánto dura la pausa una vez que nos frenamos
_invalid_responses = _collections.deque()  # timestamps (monotonic) de 401/403/429
_breaker_paused_until = 0.0
_http_session: aiohttp.ClientSession | None = None


class DiscordPausado(Exception):
    """Se lanza cuando el freno de mano propio está activo — la llamada ni
    se intenta, para no seguir sumando respuestas inválidas mientras
    esperamos a que la ventana de 10 minutos se limpie sola."""


def _rate_guard_registrar_invalido(status: int):
    global _breaker_paused_until
    ahora = _time.monotonic()
    _invalid_responses.append(ahora)
    while _invalid_responses and ahora - _invalid_responses[0] > _RATE_GUARD_WINDOW:
        _invalid_responses.popleft()
    print(f"AVISO: Discord respondió {status} en una llamada cruda ({len(_invalid_responses)} inválidas en los últimos {_RATE_GUARD_WINDOW}s).")
    if len(_invalid_responses) >= _RATE_GUARD_THRESHOLD and ahora >= _breaker_paused_until:
        _breaker_paused_until = ahora + _RATE_GUARD_PAUSE
        print(
            f"FRENO DE MANO: {len(_invalid_responses)} respuestas inválidas de Discord en los "
            f"últimos {_RATE_GUARD_WINDOW}s — pausando TODAS las llamadas REST crudas por "
            f"{_RATE_GUARD_PAUSE}s para no llegar al baneo de Cloudflare (10.000 en 10 min)."
        )


def _rate_guard_chequear():
    ahora = _time.monotonic()
    if ahora < _breaker_paused_until:
        restante = round(_breaker_paused_until - ahora, 1)
        raise DiscordPausado(f"pausado por rate limit propio, quedan {restante}s")


async def _get_http_session() -> aiohttp.ClientSession:
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session


async def _discord_request(method: str, url: str, *, headers=None, json_body=None, reintentos_429: int = 1):
    """Wrapper único para TODAS las llamadas REST crudas a Discord — ver el
    comentario grande arriba de esta sección para el porqué. Devuelve
    (status, data_json_o_None, texto_o_None). Lanza DiscordPausado si el
    freno de mano propio está activo — el llamador decide cómo manejarlo
    (normalmente: loguear y no hacer nada, como cualquier otro fallo
    best-effort de estas llamadas)."""
    _rate_guard_chequear()
    session = await _get_http_session()
    intentos = 0
    while True:
        async with session.request(method, url, headers=headers, json=json_body) as resp:
            if resp.status == 429 and intentos < reintentos_429:
                cuerpo = {}
                try:
                    cuerpo = await resp.json()
                except Exception:
                    pass
                espera = cuerpo.get("retry_after")
                if espera is None:
                    espera = float(resp.headers.get("Retry-After", 1))
                espera = min(float(espera), 10)  # nunca bloqueamos más de 10s de una
                _rate_guard_registrar_invalido(429)
                intentos += 1
                await asyncio.sleep(espera)
                continue
            if resp.status in (401, 403, 429):
                _rate_guard_registrar_invalido(resp.status)
            data, texto = None, None
            if resp.status != 204:
                try:
                    data = await resp.json()
                except Exception:
                    texto = await resp.text()
            return resp.status, data, texto


async def _publicar_payload_crudo(channel_id: int, payload: dict) -> dict:
    """Publica un mensaje Components V2 llamando directamente a la API REST
    de Discord (en vez de usar client.http.send_message, cuya firma interna
    cambia entre versiones de discord.py sin previo aviso). Devuelve el
    mensaje creado (para quien necesite su id, ej. para autoborrarlo)."""
    headers = {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    status, data, texto = await _discord_request(
        "POST", f"https://discord.com/api/v10/channels/{channel_id}/messages",
        headers=headers, json_body=payload,
    )
    if status >= 300:
        raise RuntimeError(f"Discord respondió {status}: {texto or data}")
    return data


async def _publicar_payload_con_archivo(channel_id: int, payload: dict, *, ruta_archivo: str, nombre_archivo: str) -> dict:
    """Igual que _publicar_payload_crudo, pero además adjunta un archivo
    local — necesario para un contenedor de imagen (type 12, Media Gallery)
    que referencia "attachment://{nombre_archivo}" en vez de una URL externa.
    _discord_request no sirve acá porque manda JSON puro; esto arma el
    multipart/form-data a mano (payload_json + el archivo), como pide la API
    de Discord para mensajes con adjuntos."""
    with open(ruta_archivo, "rb") as f:
        datos_archivo = f.read()
    form = aiohttp.FormData()
    form.add_field("payload_json", json.dumps(payload), content_type="application/json")
    form.add_field("files[0]", datos_archivo, filename=nombre_archivo, content_type="image/png")
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    _rate_guard_chequear()
    session = await _get_http_session()
    async with session.post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages", headers=headers, data=form
    ) as resp:
        if resp.status >= 300:
            texto = await resp.text()
            raise RuntimeError(f"Discord respondió {resp.status}: {texto}")
        return await resp.json()


# ─── Mensajes de plan de vuelo (Bloque A: texto plano, NO embed) ──────────
# El plan publicado en #vuelo es un mensaje de texto normal — se edita SOLO
# cuando cambian datos del plan o el squawk, nunca al finalizar (sin
# "Plan de vuelo finalizado" ni mostrar estado, a pedido explícito). Un
# carácter invisible (zero-width space) al final separa visualmente un plan
# del siguiente en el canal.
_flight_message_ids = {}  # flight uuid -> message_id (en memoria; se pierde si el bot reinicia)
_flight_message_locks: dict = {}
_ZWS = "​"


def _lock_para_vuelo(op_uuid: str) -> asyncio.Lock:
    lock = _flight_message_locks.get(op_uuid)
    if lock is None:
        lock = asyncio.Lock()
        _flight_message_locks[op_uuid] = lock
    return lock


def _texto_plan_vuelo(fila: dict, roblox_name: str | None) -> str:
    lineas = [f"**Discord:** <@{fila['owner_id']}>"]
    if roblox_name:
        lineas.append(f"**Roblox:** {roblox_name}")
    lineas.extend([
        f"**Callsign:** {fila.get('callsign') or '—'}",
        f"**Aircraft:** {fila.get('aircraft_type') or '—'}",
        f"**Flight Rules:** {fila.get('flight_rules') or '—'}",
        f"**Departing:** {fila.get('departure') or '—'}",
        f"**Arriving:** {fila.get('destination') or '—'}",
        f"**Alternate:** {fila.get('alternate') or '—'}",
        f"**Route:** {fila.get('route') or 'DCT'}",
        f"**Flight Level:** {fila.get('level') or '—'}",
        f"**Squawk:** {fila.get('squawk') or '—'}",
    ])
    if fila.get("remarks"):
        lineas.append(f"**Remarks:** {fila['remarks']}")
    return "\n".join(lineas) + f"\n{_ZWS}"


async def _enviar_o_editar_vuelo(fila: dict, roblox_name: str | None = None) -> None:
    op_uuid = fila["uuid"]
    async with _lock_para_vuelo(op_uuid):
        headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        payload = {"content": _texto_plan_vuelo(fila, roblox_name)}
        mensaje_id = _flight_message_ids.get(op_uuid)
        if mensaje_id:
            url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_FLIGHTS}/messages/{mensaje_id}"
            status, data, texto = await _discord_request("PATCH", url, headers=headers, json_body=payload)
            if status < 300:
                return
            if status != 404:
                raise RuntimeError(f"Discord respondió {status} al editar: {texto or data}")
            # 404 = lo borraron a mano — cae a crear uno nuevo abajo.
            _flight_message_ids.pop(op_uuid, None)

        url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_FLIGHTS}/messages"
        status, data, texto = await _discord_request("POST", url, headers=headers, json_body=payload)
        if status >= 300:
            raise RuntimeError(f"Discord respondió {status} al crear: {texto or data}")
        _flight_message_ids[op_uuid] = data["id"]


# ─── Anuncio de posición ATC abierta (Components V2, mención de rol) ──────
def _construir_payload_atc_abierto(op: dict, actor_id: str):
    lineas = [
        f"{E['antena']} <@&{V_ROLE_ID}> Nueva posición ATC abierta.",
        "",
        f"**Aeropuerto:** {op.get('airport') or '----'}",
        f"**Posición:** {op.get('positionType') or '---'}",
        f"**Frecuencia:** {op.get('frequency') or '---.---'}",
        f"**Controla:** <@{actor_id}>",
    ]
    if op.get("voiceChannelId"):
        lineas.append(f"**Canal de voz:** <#{op['voiceChannelId']}>")
    lineas.extend([
        "",
        "¡Únanse a volar! https://www.roblox.com/share?code=b8ff9e346139a142a3d1f42c0d9398a9&type=Server",
    ])
    return {
        "flags": 32768,
        "allowed_mentions": {"parse": [], "roles": [str(V_ROLE_ID)]},
        "components": [
            {"type": 17, "accent_color": BRAND_RADAR_GREEN, "components": [{"type": 10, "content": "\n".join(lineas)}]},
        ],
    }


# ─── Tabla "ATC Online" — un único mensaje, siempre el último del canal ───
# Desde la Fase A, /atc y el botón "Cerrar ahora" del panel privado llaman a _repostear_tabla_atc()
# directamente (in-process) cada vez que cambia una posición — el bot borra
# el mensaje anterior (si existe) y publica uno nuevo. Además, cualquier
# mensaje humano nuevo en el canal ATC dispara el mismo repost (ver
# on_message) para que la tabla quede siempre como el último mensaje del
# canal — por eso se guarda también la última lista de activos, para poder
# reconstruir el mismo payload sin volver a consultar la base.
_tabla_atc_message_id = None
_tabla_atc_ultimos_activos: list = []
# Sin este lock, dos reposts casi simultáneos (ej. dos mensajes seguidos en
# el canal, o un mensaje justo cuando la web también avisó un cambio) pueden
# leer el mismo _tabla_atc_message_id ANTES de que ninguno lo actualice, y
# terminan publicando dos mensajes nuevos en vez de uno — la tabla se ve
# duplicada. El lock serializa los reposts para que eso no pase.
_tabla_atc_lock = asyncio.Lock()
# IDs de mensajes viejos de la tabla que no se pudieron borrar (ej. rate
# limit de Discord al borrar) — se reintenta en el PRÓXIMO repost, para que
# no queden mensajes duplicados stackeados para siempre en el canal.
_tabla_atc_pendientes_borrar: list = []

SOLICITAR_CONTROL_CUSTOM_ID = "atc24:solicitar_control"


def _construir_payload_tabla_atc(activos: list):
    ahora_ts = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    if not activos:
        cuerpo = "_No hay ninguna posición ATC abierta ahora mismo._"
    else:
        por_aeropuerto = {}
        for op in activos:
            ap = op.get("airport") or "----"
            por_aeropuerto.setdefault(ap, []).append(op)
        bloques = []
        for ap in sorted(por_aeropuerto):
            filas = [f"**{ap}**"]
            for op in sorted(por_aeropuerto[ap], key=lambda o: o.get("positionType") or ""):
                pos = op.get("positionType") or "---"
                owner = op.get("ownerId")
                quien = f"<@{owner}>" if owner else (op.get("controllerName") or "—")
                freq = op.get("frequency") or "---.---"
                canal_txt = f" · <#{op['voiceChannelId']}>" if op.get("voiceChannelId") else ""
                filas.append(f"`{ap}_{pos:<4}` {quien} · **{freq}**{canal_txt}")
            bloques.append("\n".join(filas))
        cuerpo = "\n\n".join(bloques)
    lineas = [
        f"{E['atc']} **ATC24 Español · Controladores en línea**",
        f"-# {len(activos)} posición(es) abierta(s) · actualizado <t:{ahora_ts}:R>",
    ]
    return {
        "flags": 32768,
        "allowed_mentions": {"parse": []},
        "components": [
            {
                "type": 17, "accent_color": BRAND_RADAR_GREEN,
                "components": [
                    {"type": 10, "content": "\n".join(lineas)},
                    {"type": 14, "divider": True, "spacing": 1},
                    {"type": 10, "content": cuerpo},
                    {
                        "type": 1,
                        "components": [
                            {
                                "type": 2,
                                "style": 3,
                                "label": "Solicitar apertura de posición",
                                "custom_id": SOLICITAR_CONTROL_CUSTOM_ID,
                            }
                        ],
                    },
                ],
            },
        ],
    }


async def _repostear_tabla_atc(activos: list):
    """Borra el mensaje anterior de la tabla (si existe) y publica uno
    nuevo — usado tanto por el endpoint que llama la web (cuando cambia una
    posición) como por on_message (cuando llega cualquier mensaje nuevo al
    canal, para que la tabla quede siempre última)."""
    global _tabla_atc_message_id, _tabla_atc_ultimos_activos, _tabla_atc_pendientes_borrar
    async with _tabla_atc_lock:
        _tabla_atc_ultimos_activos = activos
        headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
        payload = _construir_payload_tabla_atc(activos)

        a_borrar = list(_tabla_atc_pendientes_borrar)
        if _tabla_atc_message_id:
            a_borrar.append(_tabla_atc_message_id)
        _tabla_atc_pendientes_borrar = []

        # Antes esto no chequeaba el resultado del delete — si Discord
        # devolvía un error (ej. 429 por rate limit, muy probable aquí porque
        # se repostea en CADA mensaje del canal), el mensaje viejo quedaba
        # sin borrar y el nuevo se publicaba igual, dejando dos tablas
        # visibles a la vez. Ahora, si falla, se reintenta en el próximo
        # repost en vez de abandonarlo — y _discord_request ya se encarga de
        # respetar Retry-After en vez de seguir de largo pegándole a Discord.
        for msg_id in a_borrar:
            try:
                status, data_del, texto = await _discord_request(
                    "DELETE", f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ATC}/messages/{msg_id}",
                    headers=headers,
                )
                if status not in (204, 404):
                    print(f"Aviso: no pude borrar la tabla ATC vieja ({msg_id}): {status} {texto or data_del}")
                    _tabla_atc_pendientes_borrar.append(msg_id)
            except DiscordPausado as err:
                print(f"Aviso: freno de mano activo, no borro la tabla ATC vieja ({msg_id}) por ahora: {err}")
                _tabla_atc_pendientes_borrar.append(msg_id)
            except Exception as err:
                print(f"Aviso: no pude borrar la tabla ATC vieja ({msg_id}): {err}")
                _tabla_atc_pendientes_borrar.append(msg_id)

        status, data, texto = await _discord_request(
            "POST", f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ATC}/messages",
            headers=headers, json_body=payload,
        )
        if status >= 300:
            raise RuntimeError(f"Discord respondió {status}: {texto or data}")
        _tabla_atc_message_id = data["id"]
        await bot_state.set(db, "tabla_atc_message_id", _tabla_atc_message_id)


# ─── Sesiones agendadas de Academia (Components V2) ───────────────────────
# Un instructor agenda desde el botón "Agendar sesión" de /academia (categoría + curso libres, no
# ligado al catálogo formal de academy_core — mantiene el agendado simple).
# Panel "Sesiones agendadas" = 1 solo mensaje que se EDITA in-place (a
# diferencia de la tabla ATC, no hace falta que sea el último mensaje del
# canal). Cuando llega la hora, se publica un anuncio nuevo con Unirse/
# Salir/Ver alumnos — los botones se enrutan por custom_id dinámico
# (academy_session:<accion>:<uuid>) desde on_interaction, igual que el resto
# de los paneles Components V2 de este bot.
SOLICITAR_SESION_CUSTOM_ID = "atc24:solicitar_sesion"
_sesiones_message_id = None
_sesiones_lock = asyncio.Lock()
_ultima_solicitud_sesion = {}  # discord_id -> timestamp de asyncio loop


def _construir_payload_sesiones_agendadas(sesiones: list):
    if not sesiones:
        cuerpo = "_No hay sesiones agendadas por ahora. Un instructor puede agendar una desde `/academia`._"
    else:
        por_categoria: dict = {}
        orden = []
        for s in sesiones:
            cat = s["category"]
            if cat not in por_categoria:
                por_categoria[cat] = []
                orden.append(cat)
            por_categoria[cat].append(s)
        bloques = []
        for cat in orden:
            filas = [f"**{cat}**"]
            for s in por_categoria[cat]:
                ts = int(s["scheduled_at"] / 1000)
                cupo_max = s["max_students"] if s["max_students"] is not None else 10
                titulo = f"`Clase {s['course_title']}`"
                if s.get("channel_id") and s.get("message_id"):
                    url = f"https://discord.com/channels/{GUILD_ID}/{s['channel_id']}/{s['message_id']}"
                    titulo = f"[Clase {s['course_title']}]({url})"
                filas.append(f"{titulo} ({s['_inscritos']}/{cupo_max}) — <t:{ts}:R> — <@{s['instructor_id']}>")
            bloques.append("\n".join(filas))
        cuerpo = "\n\n".join(bloques)
    return {
        "flags": 32768,
        "allowed_mentions": {"parse": []},
        "components": [
            {
                "type": 17, "accent_color": BRAND_BEACON_AMBER,
                "components": [
                    {"type": 10, "content": f"{E['libro']} **ATC24 Español — Sesiones de Academia agendadas**"},
                    {"type": 14, "divider": True, "spacing": 1},
                    {"type": 10, "content": cuerpo},
                    {
                        "type": 1,
                        "components": [
                            {"type": 2, "style": 3, "label": "Solicitar una sesión", "custom_id": SOLICITAR_SESION_CUSTOM_ID},
                        ],
                    },
                ],
            },
        ],
    }


async def _repostear_sesiones_agendadas():
    """Edita in-place el panel de sesiones agendadas (crea uno si todavía no
    existe). A diferencia de la tabla ATC no hace falta que sea el último
    mensaje del canal, así que editar alcanza — no hay que borrar/recrear."""
    global _sesiones_message_id
    if not DISCORD_CHANNEL_ACADEMIA_SESIONES:
        return
    async with _sesiones_lock:
        sesiones = await sessions_core.upcoming_sessions(db)
        for s in sesiones:
            s["_inscritos"] = await sessions_core.count_signups(db, s["uuid"])
        payload = _construir_payload_sesiones_agendadas(sesiones)
        headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}

        if _sesiones_message_id:
            status, data, texto = await _discord_request(
                "PATCH", f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ACADEMIA_SESIONES}/messages/{_sesiones_message_id}",
                headers=headers, json_body=payload,
            )
            if status < 300:
                return
            if status != 404:
                print(f"Aviso: no pude editar el panel de sesiones agendadas: {status} {texto or data}")
                return
            _sesiones_message_id = None  # lo borraron a mano — cae a crear uno nuevo abajo
            await bot_state.set(db, "sesiones_agendadas_message_id", None)

        status, data, texto = await _discord_request(
            "POST", f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ACADEMIA_SESIONES}/messages",
            headers=headers, json_body=payload,
        )
        if status >= 300:
            print(f"Aviso: no pude publicar el panel de sesiones agendadas: {status} {texto or data}")
            return
        _sesiones_message_id = data["id"]
        await bot_state.set(db, "sesiones_agendadas_message_id", _sesiones_message_id)


# Bloque C1/C2: ratings seleccionables al pedir una clase individual — mismos
# códigos que ya usa el sistema de rangos (PREFIX_LABELS), sin duplicar la
# jerarquía de roles aquí.
RATINGS_ACADEMIA = [
    (1238796825381834759, "Piloto"), (1238796825381834761, "Piloto"), (1238796825381834762, "Piloto"),
    (1238796825390092351, "ATC"), (1238796825390092353, "ATC"), (1238796825390092354, "ATC"),
    (1238796825390092355, "ATC"), (1238796825390092356, "ATC"), (1488656415232102460, "ATC"),
    (1495560972759466124, "Equipo Terrestre"), (1238796825381834755, "Equipo Terrestre"),
    (1238796825381834754, "Equipo Terrestre"),
]

# Rating (ej. "S1", "PPA", "ETG") -> rama de Academia ("atc"/"pilot"/"gc") —
# se arma una sola vez a partir de a qué jerarquía de roles pertenece cada
# rating, para no duplicar la lista de IDs de nuevo.
_RATING_A_RAMA = {}
for _rid in ATC_ORDER:
    _RATING_A_RAMA[PREFIX_LABELS[_rid]] = "atc"
for _rid in PILOTO_ORDER:
    _RATING_A_RAMA[PREFIX_LABELS[_rid]] = "pilot"
for _rid in GC_ORDER:
    _RATING_A_RAMA[PREFIX_LABELS[_rid]] = "gc"


class ConfirmarDisponibilidadView(discord.ui.View):
    def __init__(self, alumno_id: int, rating: str, instructor_id: int):
        super().__init__(timeout=900)  # 15 min para confirmar
        self.alumno_id = alumno_id
        self.rating = rating
        self.instructor_id = instructor_id

    @discord.ui.button(label="Sigo disponible", style=discord.ButtonStyle.success, emoji="✅")
    async def confirmar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.alumno_id:
            await interaction.response.send_message("Este botón es para quien solicitó la clase.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        guild = client.get_guild(int(GUILD_ID)) if GUILD_ID else None
        canal_voz = None
        if guild:
            categoria = discord.utils.get(guild.categories, name="Academia — Sesiones")
            if categoria is None:
                try:
                    categoria = await guild.create_category("Academia — Sesiones")
                except discord.Forbidden:
                    categoria = None
            try:
                canal_voz = await guild.create_voice_channel(
                    f"{self.rating}-{interaction.user.display_name}"[:100], category=categoria,
                    reason="Salón de sesión individual de Academia",
                )
            except discord.Forbidden:
                canal_voz = None
        ahora_ms = int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
        sesion = await sessions_core.create_session(
            db, category=self.rating, course_title=f"Sesión {self.rating}",
            instructor_id=str(self.instructor_id), scheduled_at_ms=ahora_ms, max_students=1,
        )
        await sessions_core.sign_up(db, sesion["uuid"], str(self.alumno_id))
        try:
            await _publicar_sesion_en_vivo(sesion)
        except Exception as err:
            print(f"ERROR al publicar la sesión individual en vivo: {err}")
        salon_txt = f" Salón de voz: {canal_voz.mention}" if canal_voz else ""
        await interaction.followup.send(f"¡Listo! Tu sesión de {self.rating} está en marcha.{salon_txt}", ephemeral=True)

    @discord.ui.button(label="Ya no puedo", style=discord.ButtonStyle.danger, emoji="🚫")
    async def cancelar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.alumno_id:
            await interaction.response.send_message("Este botón es para quien solicitó la clase.", ephemeral=True)
            return
        await interaction.response.send_message("Avisale al instructor cuando quieras reagendar.", ephemeral=True)


class SolicitudClaseView(discord.ui.View):
    def __init__(self, solicitante_id: int, rating: str):
        super().__init__(timeout=None)
        self.solicitante_id = solicitante_id
        self.rating = rating

    @discord.ui.button(label="Reclamar", style=discord.ButtonStyle.primary, emoji="🙋")
    async def reclamar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not es_staff_moderacion(interaction.user):
            await interaction.response.send_message("Esta acción es solo para Instructores/Staff.", ephemeral=True)
            return
        button.disabled = True
        button.label = f"Reclamado por {interaction.user.display_name}"
        await interaction.response.edit_message(view=self)
        try:
            solicitante = await client.fetch_user(self.solicitante_id)
            await solicitante.send(
                f"{interaction.user.mention} va a dar tu sesión de **{self.rating}**. ¿Sigues disponible ahora?",
                view=ConfirmarDisponibilidadView(self.solicitante_id, self.rating, interaction.user.id),
            )
        except (discord.Forbidden, discord.NotFound):
            pass


class RatingSelect(discord.ui.Select):
    def __init__(self):
        vistos = set()
        opciones = []
        for rid, _grupo in RATINGS_ACADEMIA:
            etiqueta = PREFIX_LABELS.get(rid, str(rid))
            if etiqueta in vistos:
                continue
            vistos.add(etiqueta)
            opciones.append(discord.SelectOption(label=etiqueta, value=etiqueta))
        super().__init__(placeholder="Elige el rating para tu sesión…", options=opciones)

    async def callback(self, interaction: discord.Interaction):
        rating = self.values[0]
        await interaction.response.send_message("Tu solicitud fue enviada a los instructores.", ephemeral=True)
        if not DISCORD_CHANNEL_SOLICITUDES_CLASE:
            return
        canal = client.get_channel(int(DISCORD_CHANNEL_SOLICITUDES_CLASE))
        if not canal:
            return
        embed = discord.Embed(
            title="Nueva solicitud de clase", color=BRAND_BEACON_AMBER,
            description=f"{interaction.user.mention} solicita una sesión de **{rating}**.",
        )
        await canal.send(embed=embed, view=SolicitudClaseView(interaction.user.id, rating))


class RatingSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(RatingSelect())


async def _procesar_solicitud_sesion(interaction: discord.Interaction):
    ahora = asyncio.get_running_loop().time()
    anterior = _ultima_solicitud_sesion.get(interaction.user.id)
    if anterior and ahora - anterior < SOLICITUD_CONTROL_COOLDOWN:
        restante = int(SOLICITUD_CONTROL_COOLDOWN - (ahora - anterior))
        await interaction.response.send_message(
            f"Ya pediste una sesión hace poco. Puedes volver a hacerlo en {restante} segundos.", ephemeral=True,
        )
        return
    _ultima_solicitud_sesion[interaction.user.id] = ahora
    await interaction.response.send_message("Elige el rating para tu sesión:", view=RatingSelectView(), ephemeral=True)


def _construir_payload_sesion_en_vivo(sesion: dict, inscritos: int):
    ts = int(sesion["scheduled_at"] / 1000)
    cupo = f"{inscritos}/{sesion['max_students']}" if sesion["max_students"] is not None else str(inscritos)
    lineas = [
        f"**{sesion['course_title']}: en curso**",
        "",
        f"<@{sesion['instructor_id']}> está dando una sesión de **{sesion['course_title']}** "
        f"(agendada <t:{ts}:R>) enfocada en **{sesion['category']}**. "
        f"Hay actualmente **{cupo}** alumno(s) anotados. Usa **Ver alumnos** para ver quién está anotado.",
    ]
    componentes_container = []
    # El nombre se elige con el uuid de la sesión como semilla — así una
    # edición posterior (join/salir) recalcula el mismo nombre de archivo
    # en vez de "cambiar" la imagen ya adjunta al mensaje original.
    rama = _RATING_A_RAMA.get(sesion["category"])
    nombre_banner = _nombre_banner_academia(rama, semilla=sesion["uuid"]) if rama else None
    if nombre_banner:
        componentes_container.append({"type": 12, "items": [{"media": {"url": f"attachment://{nombre_banner}"}}]})
    componentes_container.append({"type": 10, "content": "\n".join(lineas)})
    componentes_container.append({
        "type": 1,
        "components": [
            {"type": 2, "style": 3, "label": "Unirse", "custom_id": f"academy_session:join:{sesion['uuid']}"},
            {"type": 2, "style": 4, "label": "Salir", "custom_id": f"academy_session:leave:{sesion['uuid']}"},
            {"type": 2, "style": 2, "label": "Ver alumnos", "custom_id": f"academy_session:students:{sesion['uuid']}"},
        ],
    })
    return {
        "flags": 32768,
        "allowed_mentions": {"parse": []},
        "components": [{"type": 17, "accent_color": BRAND_RADAR_GREEN, "components": componentes_container}],
    }, nombre_banner


async def _publicar_sesion_en_vivo(sesion: dict):
    if not DISCORD_CHANNEL_ACADEMIA_SESIONES:
        return
    inscritos = await sessions_core.count_signups(db, sesion["uuid"])
    payload, nombre_banner = _construir_payload_sesion_en_vivo(sesion, inscritos)
    if nombre_banner:
        ruta = os.path.join(CARPETA_SCRIPT, "assets", nombre_banner)
        if os.path.exists(ruta):
            try:
                data = await _publicar_payload_con_archivo(
                    int(DISCORD_CHANNEL_ACADEMIA_SESIONES), payload, ruta_archivo=ruta, nombre_archivo=nombre_banner
                )
                await sessions_core.set_live(db, sesion["uuid"], DISCORD_CHANNEL_ACADEMIA_SESIONES, data["id"])
                return
            except Exception as err:
                print(f"Aviso: no pude publicar la sesión en vivo {sesion['uuid']} con banner, la mando sin imagen: {err}")
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    status, data, texto = await _discord_request(
        "POST", f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ACADEMIA_SESIONES}/messages",
        headers=headers, json_body=payload,
    )
    if status >= 300:
        print(f"Aviso: no pude publicar la sesión en vivo {sesion['uuid']}: {status} {texto or data}")
        return
    await sessions_core.set_live(db, sesion["uuid"], DISCORD_CHANNEL_ACADEMIA_SESIONES, data["id"])


async def _actualizar_mensaje_sesion_en_vivo(sesion: dict):
    if not sesion.get("channel_id") or not sesion.get("message_id"):
        return
    inscritos = await sessions_core.count_signups(db, sesion["uuid"])
    # El banner no se vuelve a subir acá — se recalcula el mismo nombre de
    # archivo (misma semilla) y se referencia otra vez, dejando el adjunto
    # que ya está en el mensaje sin tocar (el PATCH no manda "attachments",
    # así que Discord no lo borra).
    payload, _nombre_banner = _construir_payload_sesion_en_vivo(sesion, inscritos)
    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    status, data, texto = await _discord_request(
        "PATCH", f"https://discord.com/api/v10/channels/{sesion['channel_id']}/messages/{sesion['message_id']}",
        headers=headers, json_body=payload,
    )
    if status >= 300 and status != 404:
        print(f"Aviso: no pude actualizar el panel de la sesión en vivo {sesion['uuid']}: {status} {texto or data}")


async def _sesiones_academia_loop():
    while True:
        await asyncio.sleep(60)
        try:
            vencidas = await sessions_core.due_sessions(db)
            for s in vencidas:
                await _publicar_sesion_en_vivo(s)
            if vencidas:
                await _repostear_sesiones_agendadas()
        except Exception as err:
            print(f"Aviso: fallo en el loop de sesiones de Academia: {err}")


async def _procesar_boton_sesion(interaction: discord.Interaction, accion: str, session_uuid: str):
    sesion = await sessions_core.get_session(db, session_uuid)
    if not sesion or sesion["state"] != sessions_core.LIVE:
        await interaction.response.send_message("Esta sesión ya no está activa.", ephemeral=True)
        return

    if accion == "join":
        resultado = await sessions_core.sign_up(db, session_uuid, str(interaction.user.id))
        if resultado == "already":
            await interaction.response.send_message("Ya estás anotado en esta sesión.", ephemeral=True)
            return
        if resultado == "full":
            await interaction.response.send_message("Esta sesión ya no tiene cupo disponible.", ephemeral=True)
            return
        await interaction.response.send_message(f"Te anotaste a **{sesion['course_title']}**. ✅", ephemeral=True)
        await _actualizar_mensaje_sesion_en_vivo(await sessions_core.get_session(db, session_uuid))
        try:
            await interaction.user.send(f"Confirmado: te anotaste a la sesión de **{sesion['course_title']}**. ✅")
        except discord.Forbidden:
            pass
        return

    if accion == "leave":
        if not await sessions_core.leave(db, session_uuid, str(interaction.user.id)):
            await interaction.response.send_message("No estabas anotado en esta sesión.", ephemeral=True)
            return
        await interaction.response.send_message("Saliste de la sesión.", ephemeral=True)
        await _actualizar_mensaje_sesion_en_vivo(await sessions_core.get_session(db, session_uuid))
        try:
            await interaction.user.send(f"Confirmado: saliste de la sesión de **{sesion['course_title']}**.")
        except discord.Forbidden:
            pass
        return

    if accion == "students":
        alumnos = await sessions_core.list_signups(db, session_uuid)
        if not alumnos:
            await interaction.response.send_message("Todavía no hay alumnos anotados.", ephemeral=True)
            return
        texto = "\n".join(f"• <@{uid}>" for uid in alumnos)
        await interaction.response.send_message(f"**Alumnos anotados a {sesion['course_title']}:**\n{texto}", ephemeral=True)


# Bloque C4: /academia-agendar retirado como comando independiente — la
# misma lógica ahora vive en AgendarSesionModal, detrás del botón "Agendar
# sesión" de /academia (visible solo para Instructores/Staff).
# @tree.command(name="academia-agendar", description="Agenda una nueva sesión de clase y la publica en el panel de sesiones")
@app_commands.describe(
    categoria="Categoría/tema de la sesión (ej. Basic Operations Theory)",
    curso="Nombre del curso/sesión (ej. RPL Course)",
    en_minutos="En cuántos minutos empieza (ej. 60 para dentro de una hora)",
    cupo="Cupo máximo de alumnos (opcional, sin límite si se deja vacío)",
)
async def academia_agendar(
    interaction: discord.Interaction, categoria: str, curso: str,
    en_minutos: app_commands.Range[int, 1, 43200], cupo: app_commands.Range[int, 1, 200] = None,
):
    if not es_staff_moderacion(interaction.user):
        await interaction.response.send_message("Este comando es solo para Instructores/Staff.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    programado_ms = int(
        (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=en_minutos)).timestamp() * 1000
    )
    await sessions_core.create_session(
        db, category=categoria, course_title=curso, instructor_id=str(interaction.user.id),
        scheduled_at_ms=programado_ms, max_students=cupo,
    )
    try:
        await _repostear_sesiones_agendadas()
    except Exception as err:
        print(f"ERROR al actualizar el panel de sesiones agendadas: {err}")
    ts = int(programado_ms / 1000)
    await interaction.followup.send(f"Sesión **{curso}** agendada para <t:{ts}:R>. ✅", ephemeral=True)


# ─── Solicitud formal de apertura de posición (botón en la tabla ATC) ─────
_ultima_solicitud_control = {}  # discord_id -> timestamp de asyncio loop
SOLICITUD_CONTROL_COOLDOWN = 300  # 5 minutos, evita spam del botón


async def _procesar_solicitud_control(interaction: discord.Interaction):
    ahora = asyncio.get_running_loop().time()
    anterior = _ultima_solicitud_control.get(interaction.user.id)
    if anterior and ahora - anterior < SOLICITUD_CONTROL_COOLDOWN:
        restante = int(SOLICITUD_CONTROL_COOLDOWN - (ahora - anterior))
        await interaction.response.send_message(
            f"Ya solicitaste la apertura de una posición hace poco. Puedes volver a hacerlo en {restante} segundos.",
            ephemeral=True,
        )
        return

    _ultima_solicitud_control[interaction.user.id] = ahora
    await interaction.response.send_message(
        "Tu solicitud fue enviada a los controladores disponibles.", ephemeral=True
    )

    payload = {
        "flags": 32768,
        "allowed_mentions": {"parse": [], "roles": [str(ATC_ROLE_ID)]},
        "components": [
            {
                "type": 17,
                "accent_color": BRAND_BEACON_AMBER,
                "components": [
                    {
                        "type": 10,
                        "content": f"<@&{ATC_ROLE_ID}> {interaction.user.mention} quiere que se abra una posición ATC.",
                    },
                ],
            }
        ],
    }
    # Solo al canal dedicado a esto — el de "Anuncios ATC" (DISCORD_CHANNEL_ATC)
    # queda reservado para la tabla de controladores en línea y los anuncios
    # de posición abierta, sin mezclarlo con las solicitudes.
    if CANAL_SOLICITUD_CONTROL_EXTRA:
        try:
            await _publicar_payload_crudo(int(CANAL_SOLICITUD_CONTROL_EXTRA), payload)
        except Exception as err:
            print(f"ERROR al notificar solicitud de control en el canal {CANAL_SOLICITUD_CONTROL_EXTRA}: {err}")


# ─── Panel de ATC — comando /panel-atc, solo visible/usable para ATCs ─────
ENCUESTA_CONTROL_CUSTOM_SI = "atc24:encuesta_control:si"
ENCUESTA_CONTROL_CUSTOM_NO = "atc24:encuesta_control:no"

_votos_encuesta_control = {}  # message_id -> {"si": set(discord_id), "no": set(discord_id)}


def _construir_payload_encuesta_control(votos_si: int = 0, votos_no: int = 0):
    return {
        "flags": 32768,
        "allowed_mentions": {"parse": ["roles"]},
        "components": [
            {
                "type": 17,
                "accent_color": BRAND_SKY_NAVY,
                "components": [
                    {
                        "type": 10,
                        "content": (
                            f"<@&{ATC_ROLE_ID}> **¿Quieren que se abra control ahora?** Votá abajo.\n\n"
                            f"Sí: {votos_si} · No: {votos_no}"
                        ),
                    },
                    {
                        "type": 1,
                        "components": [
                            {"type": 2, "style": 3, "label": "Sí", "custom_id": ENCUESTA_CONTROL_CUSTOM_SI},
                            {"type": 2, "style": 4, "label": "No", "custom_id": ENCUESTA_CONTROL_CUSTOM_NO},
                        ],
                    },
                ],
            }
        ],
    }


async def _procesar_voto_control(interaction: discord.Interaction, voto: str):
    msg_id = interaction.message.id
    registro = _votos_encuesta_control.setdefault(msg_id, {"si": set(), "no": set()})
    otro = "no" if voto == "si" else "si"
    registro[otro].discard(interaction.user.id)
    registro[voto].add(interaction.user.id)

    await interaction.response.send_message(
        f"Tu voto (\"{'Sí' if voto == 'si' else 'No'}\") quedó registrado.", ephemeral=True
    )

    headers = {"Authorization": f"Bot {BOT_TOKEN}", "Content-Type": "application/json"}
    payload = _construir_payload_encuesta_control(len(registro["si"]), len(registro["no"]))
    try:
        await _discord_request(
            "PATCH", f"https://discord.com/api/v10/channels/{interaction.channel_id}/messages/{msg_id}",
            headers=headers, json_body=payload,
        )
    except Exception as err:
        print(f"ERROR al actualizar encuesta de control: {err}")


class AnuncioATCModal(discord.ui.Modal, title="Anuncio rápido a ATC"):
    texto = discord.ui.TextInput(
        label="Mensaje",
        style=discord.TextStyle.paragraph,
        placeholder="Escribe el anuncio tal como quieres que se vea…",
        max_length=1000,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not DISCORD_CHANNEL_ATC:
            await interaction.response.send_message("El canal ATC no está configurado.", ephemeral=True)
            return
        contenido = f"{str(self.texto)}\n\n-# Publicado por {interaction.user.mention}"
        payload = {
            "flags": 32768,
            "allowed_mentions": {"parse": []},
            "components": [
                {"type": 17, "accent_color": BRAND_SKY_NAVY, "components": [{"type": 10, "content": contenido}]}
            ],
        }
        try:
            await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATC), payload)
        except Exception as err:
            await interaction.response.send_message(f"No pude publicar el anuncio: {err}", ephemeral=True)
            return
        await interaction.response.send_message("Anuncio publicado en el canal ATC.", ephemeral=True)


class CerrarPosicionSelect(discord.ui.Select):
    def __init__(self):
        opciones = [
            discord.SelectOption(
                label=f"{op.get('airport') or '----'}_{op.get('positionType') or '---'}",
                description=f"Controla: {op.get('controllerName') or op.get('ownerId') or '—'}"[:100],
            )
            for op in _tabla_atc_ultimos_activos[:25]  # límite de Discord para un Select
        ]
        super().__init__(placeholder="Posición a cerrar", options=opciones)

    async def callback(self, interaction: discord.Interaction):
        clave = self.values[0]
        nuevos = [
            op for op in _tabla_atc_ultimos_activos
            if f"{op.get('airport') or '----'}_{op.get('positionType') or '---'}" != clave
        ]
        try:
            await _repostear_tabla_atc(nuevos)
        except Exception as err:
            await interaction.response.send_message(f"No pude actualizar la tabla: {err}", ephemeral=True)
            return
        await interaction.response.send_message(
            f"Posición `{clave}` cerrada en la tabla de Discord. Esto solo corrige lo que se ve aquí — "
            "no cierra la posición del lado de la web.",
            ephemeral=True,
        )


class CerrarPosicionView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(CerrarPosicionSelect())


class PanelATCView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="¿Quieren control? (encuesta)", style=discord.ButtonStyle.primary)
    async def encuesta_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not DISCORD_CHANNEL_ATC:
            await interaction.response.send_message("El canal ATC no está configurado.", ephemeral=True)
            return
        payload = _construir_payload_encuesta_control()
        try:
            await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATC), payload)
        except Exception as err:
            await interaction.response.send_message(f"No pude publicar la encuesta: {err}", ephemeral=True)
            return
        await interaction.response.send_message("Encuesta publicada en el canal ATC.", ephemeral=True)

    @discord.ui.button(label="Cerrar posición a la fuerza", style=discord.ButtonStyle.danger)
    async def cerrar_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _tabla_atc_ultimos_activos:
            await interaction.response.send_message("No hay ninguna posición registrada para cerrar ahora mismo.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Elige la posición a cerrar:", view=CerrarPosicionView(), ephemeral=True,
        )

    @discord.ui.button(label="Anuncio rápido a ATC", style=discord.ButtonStyle.secondary)
    async def anuncio_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AnuncioATCModal())


# Bloque A8: /panel-atc retirado — sus 3 funciones (encuesta "¿quieren
# control?", anuncio rápido, cerrar encuesta) quedan cubiertas por el panel
# privado de cada posición (ATCDashboardView) más la tabla de controladores.
# Se deja la función intacta por si se quiere reactivar.
# @tree.command(name="panel-atc", description="Abre el panel de herramientas para Controladores de Tráfico Aéreo (rol ATC)")
@app_commands.default_permissions()
async def panel_atc(interaction: discord.Interaction):
    if not has_any_role(interaction.user, ATC_ORDER + [ATC_ROLE_ID]):
        await interaction.response.send_message(
            "Este comando es exclusivo para Controladores de Tráfico Aéreo.", ephemeral=True
        )
        return
    await interaction.response.send_message("Panel de ATC — elige una acción:", view=PanelATCView(), ephemeral=True)


# ─── ATIS (Components V2, mismo formato ICAO estándar de antes) ───────────
def _construir_payload_atis(e: dict):
    ap = (e.get("airport") or "----").upper()
    ident = e.get("ident") or "A"
    at_ms = e.get("at")
    if at_ms:
        dt = datetime.datetime.fromtimestamp(at_ms / 1000, tz=datetime.timezone.utc)
        time_str = dt.strftime("%H%MZ")
    else:
        time_str = "----Z"
    dep = e.get("depRwy") or e.get("runway") or "---"
    arr = e.get("arrRwy") or dep
    lineas = [
        f"{ap} ATIS INFO {ident} TIME {time_str}",
        f"DEP RWY {dep} / ARR RWY {arr}",
        f"WIND {e.get('wind') or '---/--'} VIS {e.get('visibility') or 'CAVOK'} {e.get('clouds') or 'SKC'}",
        f"QNH {e.get('qnh') or '----'}",
        f"TRANSITION LEVEL {e.get('trl') or '----'}",
    ]
    if e.get("remarks"):
        lineas.append(e["remarks"])
    lineas.append(f"ACKNOWLEDGE RECEIPT OF INFORMATION {ident} ON INITIAL CONTACT")
    lineas.append(f"END OF INFORMATION {ident}")
    texto = "```\n" + "\n".join(lineas) + "\n```"
    return {
        "flags": 32768,
        "allowed_mentions": {"parse": []},
        "components": [
            {
                "type": 17, "accent_color": BRAND_BEACON_AMBER,
                "components": [
                    {"type": 10, "content": f"# {E['microfono']} ATIS {ap}"},
                    {"type": 14, "divider": True, "spacing": 1},
                    {"type": 10, "content": texto},
                ],
            },
        ],
    }


# Bloque A8: /atis ya no es un comando independiente — la misma lógica de
# publicación (_construir_payload_atis) ahora se dispara desde el botón
# "ATIS" del panel privado de la posición (ATISModal1/ATISModal2, más
# arriba). Se deja la función intacta por si se quiere reactivar como
# comando suelto en algún momento.
# @tree.command(name="atis", description="Publica un ATIS (información meteorológica y de pista) en el canal correspondiente")
@app_commands.describe(
    aeropuerto="ICAO del aeródromo (ej. SBGR)", ident="Letra de información (ej. A, B, C)",
    pista_salida="Pista de salida (ej. 09L)", pista_llegada="Pista de llegada (déjalo vacío si es la misma)",
    viento="Viento en formato dirección/velocidad (ej. 250/12)", visibilidad="Ej. CAVOK o 8000m",
    nubes="Ej. SKC, FEW020", qnh="Presión QNH (ej. 1013)", nivel_transicion="Nivel de transición (ej. FL60)",
    observaciones="Cualquier información adicional (opcional)",
)
async def atis(
    interaction: discord.Interaction, aeropuerto: str, ident: str,
    pista_salida: str = None, pista_llegada: str = None, viento: str = None,
    visibilidad: str = None, nubes: str = None, qnh: str = None,
    nivel_transicion: str = None, observaciones: str = None,
):
    if not (has_any_role(interaction.user, ATC_ORDER) or has_any_role(interaction.user, LIDERAZGO_ORDER)):
        await interaction.response.send_message("Este comando es solo para controladores (rol ATC).", ephemeral=True)
        return
    if not DISCORD_CHANNEL_ATIS:
        await interaction.response.send_message("No hay un canal de ATIS configurado (DISCORD_CHANNEL_ATIS).", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    payload = _construir_payload_atis({
        "airport": aeropuerto, "ident": ident,
        "at": int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000),
        "depRwy": pista_salida, "arrRwy": pista_llegada,
        "wind": viento, "visibility": visibilidad, "clouds": nubes,
        "qnh": qnh, "trl": nivel_transicion, "remarks": observaciones,
    })
    try:
        await _publicar_payload_crudo(int(DISCORD_CHANNEL_ATIS), payload)
    except Exception as err:
        await interaction.followup.send(f"No pude publicar el ATIS: {err}", ephemeral=True)
        return
    await interaction.followup.send(f"ATIS {aeropuerto.upper()} {ident} publicado. ✅", ephemeral=True)


COMANDOS_PUBLICAR = {
    "!publicar-verificacion": ARCHIVO_MENSAJE,
    "!publicar-guia": ARCHIVO_GUIA,
    "!publicar-guia-bloxlink": ARCHIVO_GUIA_BLOXLINK,
    "!publicar-guia-vuelo": ARCHIVO_GUIA_VUELO,
    "!publicar-guia-atis": ARCHIVO_GUIA_ATIS,
}


# ─── Autorización de plan de vuelo (comando oculto, sin slash — a propósito
# no aparece en ningún listado de comandos ni en las guías) ────────────────
# "!autorizar" arma la captura del canal: el PRÓXIMO mensaje que llegue ahí
# se interpreta como el plan de vuelo a autorizar y se desarma solo (no
# queda escuchando todo lo que se hable en el canal). Prioriza el plan REAL
# ya presentado en la web (si el autor tiene uno activo); si no hay, intenta
# interpretar el propio texto del mensaje. El resultado se manda por MD al
# piloto, nunca al canal — no queda rastro público de que esto existe.
AUTORIZAR_ARMADO: set[int] = set()  # IDs de canal armados en este momento

_RE_ICAO = re.compile(r"\b([A-Z]{4})\b")
_RE_CALLSIGN = re.compile(r"\b([A-Z]{2,4}\d{1,4}[A-Z]?)\b")
_RE_NIVEL = re.compile(r"\bFL ?(\d{2,3})\b", re.IGNORECASE)


def _puede_autorizar(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    return ATC_ROLE_ID in {r.id for r in member.roles}


def _parsear_plan_de_texto(texto: str):
    """Interpretación libre de un plan pegado a mano — sin formato fijo,
    busca lo mínimo indispensable (reglas de vuelo + dos aeródromos ICAO).
    Sin eso no hay forma responsable de armar una autorización."""
    mayus = texto.upper()
    if "IFR" in mayus:
        reglas = "IFR"
    elif "VFR" in mayus:
        reglas = "VFR"
    else:
        return None

    aerodromos = _RE_ICAO.findall(mayus)
    if len(aerodromos) < 2:
        return None

    m_callsign = _RE_CALLSIGN.search(mayus)
    m_nivel = _RE_NIVEL.search(mayus)
    return {
        "callsign": m_callsign.group(1) if m_callsign else "AERONAVE",
        "flightRules": reglas,
        "departure": aerodromos[0],
        "destination": aerodromos[1],
        "route": "",
        "level": m_nivel.group(1) and f"FL{m_nivel.group(1)}" or "",
        "squawk": "",
    }


def _squawk_de(plan: dict) -> str:
    squawk = (plan.get("squawk") or "").strip()
    if squawk:
        return squawk
    # Código transponder de relleno (1000-7777, primer dígito 1-7 como en la
    # vida real) — solo para tener algo que leer; control real lo confirma.
    return "".join(str(random.randint(0 if i else 1, 7)) for i in range(4))


def _construir_autorizacion(plan: dict, fuente: str) -> str:
    callsign = plan.get("callsign") or "AERONAVE"
    reglas = (plan.get("flightRules") or "IFR").upper()
    salida = plan.get("departure") or "----"
    destino = plan.get("destination") or "----"
    ruta = (plan.get("route") or "").strip()
    nivel = (plan.get("level") or "").strip()
    squawk = _squawk_de(plan)

    if reglas == "VFR":
        lineas = [
            f"**{callsign}**, vuelo VFR {salida} → {destino} aprobado.",
            "Manténgase fuera de espacio aéreo controlado salvo autorización expresa.",
            f"Squawk **{squawk}**.",
        ]
        if nivel:
            lineas.append(f"Nivel sugerido {nivel}, sujeto a condiciones VMC.")
    else:
        via = f" vía {ruta}" if ruta else ""
        lineas = [f"**{callsign}**, autorizado a {destino}{via}."]
        if nivel:
            lineas.append(f"Ascienda y mantenga {nivel}.")
        lineas.append(f"Squawk **{squawk}**.")

    origen = "tu plan de vuelo activo (/vuelo)" if fuente == "bot" else "tu último mensaje en el canal"
    lineas.append("")
    lineas.append(f"-# Generado a partir de {origen} — confirma los datos con control antes de rodar.")
    return "\n".join(lineas)


async def _procesar_autorizacion(message: discord.Message):
    autor = message.author
    plan, fuente = None, None

    try:
        fila = await atc_core.latest_active_flight_for_owner(db, str(autor.id))
        if fila:
            plan, fuente = _flight_row_to_op(fila), "bot"
    except Exception as err:
        print(f"Aviso: no pude consultar el plan de vuelo activo para autorizar: {err}")

    if plan is None:
        plan = _parsear_plan_de_texto(message.content)
        fuente = "mensaje"

    try:
        if plan is None:
            await autor.send(
                "No pude armar una autorización — no tienes un plan de vuelo activo (/vuelo), "
                "y tu último mensaje tampoco tenía los datos mínimos (indicativo, salida, destino "
                "y si es IFR o VFR)."
            )
        else:
            await autor.send(_construir_autorizacion(plan, fuente))
    except discord.Forbidden:
        print(f"Aviso: no pude mandarle la autorización por MD a {autor} (tiene los MD cerrados).")


# ─── Contador (juego de conteo, estilo countingbot.com) ───────────────────
# Cada mensaje válido tiene que ser el número siguiente al actual, y nadie
# puede postear dos veces seguidas — si se rompe cualquiera de las dos
# reglas, la cuenta se reinicia a 0 (igual que countingbot.com). El estado
# se guarda en disco para sobrevivir un reinicio normal del proceso.
_estado_conteo = _leer_json("conteo.json", {"actual": 0, "ultimo_usuario_id": None})

# Operadores permitidos en fórmulas del canal de conteo (ej. "40+2", "6*7",
# "100/4"). Nada de eval() — solo un subconjunto fijo de nodos aritméticos
# de ast, así no hay forma de ejecutar código arbitrario.
_CONTEO_OPERADORES = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _evaluar_formula_conteo(texto: str):
    """Evalúa una fórmula aritmética simple de forma segura. Devuelve un
    int si el resultado es un número entero, o None si el texto no es una
    fórmula válida (o el resultado no da un entero exacto)."""
    if len(texto) > 100:
        return None  # fórmulas razonables no necesitan ser tan largas

    def _nodo(n):
        if isinstance(n, ast.Expression):
            return _nodo(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _CONTEO_OPERADORES:
            izq, der = _nodo(n.left), _nodo(n.right)
            if isinstance(n.op, ast.Pow) and abs(der) > 20:
                raise ValueError("exponente demasiado grande")
            return _CONTEO_OPERADORES[type(n.op)](izq, der)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _CONTEO_OPERADORES:
            return _CONTEO_OPERADORES[type(n.op)](_nodo(n.operand))
        raise ValueError("nodo no permitido")

    try:
        arbol = ast.parse(texto, mode="eval")
        resultado = _nodo(arbol)
    except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None

    if not isinstance(resultado, (int, float)) or resultado != int(resultado):
        return None
    return int(resultado)


async def _procesar_mensaje_conteo(message: discord.Message):
    texto = message.content.strip()
    numero = _evaluar_formula_conteo(texto)
    if numero is None:
        return  # no es un número ni una fórmula válida — se ignora, no rompe la cuenta
    esperado = _estado_conteo["actual"] + 1
    mismo_usuario = _estado_conteo["ultimo_usuario_id"] == message.author.id

    if numero != esperado or mismo_usuario:
        if mismo_usuario and numero == esperado:
            motivo = "no puedes contar dos veces seguidas"
        else:
            motivo = f"seguía el **{esperado}**"
        try:
            await message.add_reaction("❌")
        except discord.HTTPException:
            pass
        await message.channel.send(
            f"{message.author.mention} rompió la cuenta en **{numero}** — {motivo}. Se reinicia desde **0**."
        )
        _estado_conteo["actual"] = 0
        _estado_conteo["ultimo_usuario_id"] = None
        _guardar_json("conteo.json", _estado_conteo)
        return

    try:
        await message.add_reaction("✅")
    except discord.HTTPException:
        pass
    _estado_conteo["actual"] = numero
    _estado_conteo["ultimo_usuario_id"] = message.author.id
    _guardar_json("conteo.json", _estado_conteo)


_TABLA_ATC_REPOST_COOLDOWN = 3  # segundos — evita golpear el rate limit de Discord si el canal está activo
_tabla_atc_ultimo_repost_ts = 0.0


# ---------------------------------------------------------------------------
# "!help" interactivo — reemplaza a la vieja lista estática Y al viejo
# "!funciones" (retirado como comando aparte). Todo el contenido técnico
# que antes solo veían los administradores en "!funciones" ahora vive en la
# sección "admin" de este mismo menú, visible solo para quien tenga permiso
# de administrador — la opción ni siquiera aparece en el selector para el
# resto (y además se revalida el permiso en el backend en cada callback,
# nunca se confía solo en ocultar la opción).
# ---------------------------------------------------------------------------

AYUDA_SECCIONES = {
    "general": {
        "etiqueta": "General",
        "emoji": "🧭",
        "titulo": "Ayuda — General",
        "descripcion": "Comandos disponibles para todos los miembros verificados.",
        "campos": [
            (
                "Identidad y progreso",
                "**/apodo** — recalcula tu apodo (usa tu nombre real de Roblox vía Bloxlink si está configurado)\n"
                "**/academia** — menú con tu progreso, tus certificados, y (si corresponde) la cola de evaluaciones o ascender\n"
                "**/advertencias** — consulta tu propio historial de moderación",
            ),
            (
                "Información en vivo",
                "**/servidor** — vuelos, controladores y verificados en línea en este momento\n"
                "**/rankings** — clasificación por minutos volados, minutos controlados y actividad combinada\n"
                "**/certificado-verificar** — comprueba la autenticidad de un certificado por su código",
            ),
        ],
    },
    "vuelo_atc": {
        "etiqueta": "Vuelos y ATC",
        "emoji": "✈️",
        "titulo": "Ayuda — Vuelos y control aéreo",
        "descripcion": "Cómo presentar un plan de vuelo o abrir una posición de control.",
        "campos": [
            (
                "/vuelo",
                f"Solo funciona en <#{DISCORD_CHANNEL_VUELO_CMD}>. Presenta un plan de vuelo (todos los campos son "
                "obligatorios salvo Ruta). El plan se publica como mensaje de texto en el canal de vuelos, y recibes "
                "un panel privado por mensaje directo para editar, cambiar el squawk, retirar o finalizar el plan. "
                "Se cierra solo tras 14 minutos de inactividad (con 1 minuto de gracia y la opción de extenderlo 5 "
                "minutos más).",
            ),
            (
                "/atc",
                f"Solo funciona en <#{DISCORD_CHANNEL_ATC_CMD}>. Abre una posición de control: crea el canal de voz "
                "correspondiente con el estado fijo \"ATC: tu usuario\" y te da un panel privado por mensaje directo "
                "para publicar un anuncio, cerrar la posición (ahora o de forma programada) y emitir el ATIS. La "
                "posición se cierra sola si el canal de voz queda vacío 10 minutos.",
            ),
        ],
    },
    "academia": {
        "etiqueta": "Academia",
        "emoji": "🎓",
        "titulo": "Ayuda — Academia",
        "descripcion": "Clases agendadas, solicitudes de sesión y evaluaciones.",
        "campos": [
            (
                "Clases programadas",
                "El panel de sesiones agendadas se actualiza solo y agrupa las clases por categoría. El botón "
                "\"Solicitar sesión\" abre un selector de rating: la solicitud pasa a un instructor disponible, que "
                "la reclama y confirma contigo antes de crear la sesión y el canal de voz.",
            ),
            (
                "Sesiones en vivo",
                "Cuando llega la hora agendada, se publica el anuncio de la clase en curso con botones para unirte, "
                "salir o ver la lista de alumnos inscritos, respetando siempre el cupo máximo.",
            ),
            (
                "Instructores y Staff",
                "Desde `/academia` se accede además al panel de instructor (atrasar, adelantar, iniciar, cancelar, "
                "cerrar o anunciar tus propias sesiones) y a la cola de evaluaciones, con una rúbrica de 10 "
                "criterios por rango. Al aprobar, se entrega un certificado con código de verificación por "
                "mensaje directo.",
            ),
        ],
    },
    "moderacion": {
        "etiqueta": "Moderación",
        "emoji": "🛡️",
        "titulo": "Ayuda — Moderación",
        "descripcion": "Herramientas de Staff e Instructores para mantener el orden del servidor.",
        "campos": [
            (
                "Comandos",
                "**/advertir**, **/timeout**, **/kick**, **/ban** registran un caso numerado y notifican al usuario "
                "por mensaje directo con un botón para apelar la sanción (salvo que tenga los mensajes directos "
                "cerrados).\n**/purgar** — borra entre 1 y 100 mensajes del canal actual.",
            ),
            (
                "Dashboard de Staff",
                "`!staff` publica el panel permanente de Staff (Director y Subdirector Ejecutivo): sancionar por "
                "selección de usuario, consultar historial y revocar casos activos, todo desde un único mensaje.",
            ),
        ],
    },
    "soporte": {
        "etiqueta": "Soporte y comunidad",
        "emoji": "🎫",
        "titulo": "Ayuda — Soporte y comunidad",
        "descripcion": "Tickets, el juego de conteo y la Foto de la Semana.",
        "campos": [
            (
                "Tickets de soporte",
                "El panel de soporte abre un canal privado numerado para cada solicitud, siempre con el Staff "
                "correspondiente incluido.",
            ),
            (
                "Conteo",
                "Escribe el número siguiente (o una operación matemática simple que dé ese resultado) sin repetir "
                "usuario consecutivo. Un error reinicia el conteo desde 0.",
            ),
            (
                "Foto de la Semana",
                "Reacciona con ⭐ a una foto para nominarla. Cada semana se abre una votación formal entre las "
                "nominadas y se anuncia a la ganadora.",
            ),
        ],
    },
    "admin": {
        "etiqueta": "Administración",
        "emoji": "🔧",
        "titulo": "Ayuda — Administración (solo administradores)",
        "descripcion": "Referencia técnica completa: comandos ocultos, automatizaciones y comandos retirados.",
        "campos": [
            (
                "Comandos de texto administrativos",
                "**!staff** — publica el Dashboard de Staff permanente\n"
                "**!panel-soporte** — publica el panel fijo de tickets de soporte\n"
                "**!publicar-verificacion / !publicar-guia / !publicar-guia-bloxlink / !publicar-guia-vuelo / "
                "!publicar-guia-atis** — publican los JSON fijos de payloads/ (se autoborran)\n"
                "**!autorizar** — arma/desarma la captura de autorización de vuelo en el canal actual; oculto a "
                "propósito, solo administradores o rol ATC",
            ),
            (
                "Slash — Instructores/Staff (ocultos del selector para todos los demás)",
                "**/apodo-miembro**, **/advertir**, **/timeout**, **/kick**, **/ban**, **/purgar**, **/eco** "
                "(modal de texto largo que el bot publica en un canal, por mensaje directo, o en el mismo canal), "
                "**/certificado-revocar** (revoca un certificado por su código; queda registrado como REVOCADO, "
                "nunca se borra ni se reutiliza el código)",
            ),
            (
                "Automatizaciones",
                "Verificación y bienvenida al unirse; apodo con prefijo automático según jerarquía de roles; rol "
                "base ATC/FLT agregado o retirado solo según el rango operativo; reconciliación de posiciones ATC "
                "activas al reiniciar el bot; tabla \"Controladores en línea\" reenviada al final del canal tras "
                "cada mensaje nuevo (con cooldown); cierre automático de posiciones ATC y planes de vuelo "
                "inactivos; loop de sesiones de Academia cada 60 segundos.",
            ),
            (
                "Retirados (funcionalidad absorbida por paneles permanentes)",
                "~~/vuelo-cerrar~~ ~~/atc-cerrar~~ ~~/atis~~ ~~/panel-atc~~ (paneles privados de /vuelo y /atc) — "
                "~~/reportar~~ (botón Apelar) ~~/panel-moderacion~~ (Dashboard de Staff) ~~/borrar-mensajes~~ "
                "(ahora /purgar) ~~/anunciar~~ (ahora /eco de nuevo) — ~~!funciones~~ (integrado en esta sección) — "
                "el código de todos queda comentado, no borrado, por si se reactivan.",
            ),
            (
                "Nota de seguridad",
                "Los comandos sensibles (purgar, advertir, timeout, kick, ban, apodo-miembro, eco) están ocultos "
                "por defecto: un administrador debe habilitarlos por rol desde Integraciones. Esta sección del "
                "menú valida el permiso de administrador en el backend, no solo al construir el selector.",
            ),
        ],
    },
}


def _embed_ayuda(seccion_id: str, es_admin: bool, semilla=None) -> discord.Embed:
    seccion = AYUDA_SECCIONES.get(seccion_id) or AYUDA_SECCIONES["general"]
    if seccion_id == "admin" and not es_admin:
        seccion = AYUDA_SECCIONES["general"]
    embed = discord.Embed(title=seccion["titulo"], description=seccion["descripcion"], color=BRAND_SKY_NAVY)
    for nombre, valor in seccion["campos"]:
        embed.add_field(name=nombre, value=valor, inline=False)
    embed.set_footer(text="Usa el menú de abajo para ver las demás categorías.")
    # La sección "academia" queda afuera de la imagen de identidad
    # institucional — Academia tiene su propia identidad separada (ver
    # BRANCH_BANNERS). El resto del menú sí la lleva.
    if seccion_id != "academia":
        aplicar_imagen_formal_determinista(embed, semilla)
    return embed


class AyudaSelect(discord.ui.Select):
    def __init__(self, autor_id: int, es_admin: bool, semilla):
        self.autor_id = autor_id
        self.es_admin = es_admin
        self.semilla = semilla
        opciones = [
            discord.SelectOption(label=datos["etiqueta"], value=clave, emoji=datos["emoji"])
            for clave, datos in AYUDA_SECCIONES.items()
            if clave != "admin" or es_admin
        ]
        super().__init__(placeholder="Elige una categoría…", options=opciones, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        seccion_id = self.values[0]
        # Revalidación server-side: no alcanza con que la opción "admin" no
        # aparezca en el selector de quien no es administrador — se vuelve a
        # comprobar el permiso real de quien interactúa antes de mostrarla.
        es_admin_ahora = (
            isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
        )
        if seccion_id == "admin" and not es_admin_ahora:
            await interaction.response.send_message("Esta sección es solo para administradores.", ephemeral=True)
            return
        await interaction.response.edit_message(embed=_embed_ayuda(seccion_id, es_admin_ahora, self.semilla))


class AyudaView(discord.ui.View):
    def __init__(self, autor_id: int, es_admin: bool):
        super().__init__(timeout=300)
        self.autor_id = autor_id
        # Semilla estable para toda la vida de este menú — así la sección
        # "vuelo_atc"/"moderacion"/etc. no "cambia" de imagen cada vez que
        # el mismo usuario navega de una categoría a otra con el select.
        self.semilla = f"ayuda:{autor_id}:{id(self)}"
        self.add_item(AyudaSelect(autor_id, es_admin, self.semilla))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.autor_id:
            await interaction.response.send_message(
                "Solo quien pidió esta ayuda puede usar este menú — escribe !help para tu propia copia.",
                ephemeral=True,
            )
            return False
        return True


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Canales de ejecución exclusiva (Bloque A): /vuelo y /atc son los
    # ÚNICOS mensajes permitidos en sus canales designados. Los slash
    # commands llegan como Interaction, no generan un discord.Message aquí —
    # esto solo agarra texto escrito a mano.
    for canal_id, nombre_comando in (
        (DISCORD_CHANNEL_VUELO_CMD, "vuelo"), (DISCORD_CHANNEL_ATC_CMD, "atc"),
    ):
        if message.channel.id == int(canal_id):
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound):
                pass
            try:
                await message.channel.send(
                    f"{message.author.mention} No está permitido enviar mensajes en este canal. "
                    f"Este canal está destinado exclusivamente a la ejecución de /{nombre_comando}.",
                    delete_after=10,
                )
            except discord.Forbidden:
                pass
            return

    # Canal ATC: cualquier mensaje nuevo dispara un repost de la tabla de
    # controladores en línea, para que quede siempre como el último mensaje
    # del canal (mismo mecanismo que usa la web cuando cambia una posición).
    # Con cooldown corto: postear en cadena por cada mensaje de una charla
    # activa satura el rate limit de Discord para ese canal, y eso era lo que
    # hacía fallar el delete y dejaba la tabla vieja duplicada.
    global _tabla_atc_ultimo_repost_ts
    if (
        DISCORD_CHANNEL_ATC
        and message.channel.id == int(DISCORD_CHANNEL_ATC)
        and asyncio.get_running_loop().time() - _tabla_atc_ultimo_repost_ts > _TABLA_ATC_REPOST_COOLDOWN
    ):
        _tabla_atc_ultimo_repost_ts = asyncio.get_running_loop().time()
        try:
            await _repostear_tabla_atc(_tabla_atc_ultimos_activos)
        except Exception as err:
            print(f"ERROR al reenviar la tabla de ATC Online tras un mensaje nuevo: {err}")

    # Canal de conteo (estilo countingbot.com): hay que postear el número
    # siguiente al actual, y nadie puede contar dos veces seguidas. Si se
    # rompe la cuenta, vuelve a 0.
    if message.channel.id == int(DISCORD_CHANNEL_CONTEO):
        await _procesar_mensaje_conteo(message)
        return

    comando = message.content.strip()

    # "!help" — guía interactiva por categorías (Bloque de idioma/UX):
    # reemplaza a la lista estática anterior Y al viejo "!funciones" (que se
    # retiró como comando aparte) — todo el contenido técnico que antes solo
    # veían los administradores ahora vive acá, en la sección "Administración",
    # visible únicamente para quien tenga permiso de administrador.
    if comando.lower() == "!help":
        es_admin = message.author.guild_permissions.administrator
        vista = AyudaView(autor_id=message.author.id, es_admin=es_admin)
        embed_ayuda = _embed_ayuda("general", es_admin, vista.semilla)
        archivo_ayuda = _archivo_imagen_formal(_nombre_imagen_formal_determinista(vista.semilla))
        if archivo_ayuda:
            await message.channel.send(embed=embed_ayuda, view=vista, file=archivo_ayuda)
        else:
            await message.channel.send(embed=embed_ayuda, view=vista)
        return

    # "!panel-soporte" — Bloque B6: publica el panel fijo de tickets en este
    # canal. Antes era un slash command; pasa a comando de texto para estar
    # en línea con el resto de los paneles/guías fijas del bot.
    if comando.lower() == "!panel-soporte":
        if not has_any_role(message.author, LIDERAZGO_ORDER):
            await message.reply("Este comando es solo para Staff.", delete_after=8)
            return
        embed_soporte, archivo_soporte = await _embed_panel_soporte()
        if archivo_soporte:
            await message.channel.send(embed=embed_soporte, view=TicketPanelView(), file=archivo_soporte)
        else:
            await message.channel.send(embed=embed_soporte, view=TicketPanelView())
        await message.delete()
        return

    # "!staff" — Bloque B5: publica el Dashboard de Staff permanente en este canal.
    if comando.lower() == "!staff":
        if not _puede_usar_dashboard_staff(message.author):
            await message.reply("Este comando es solo para Director Ejecutivo y Subdirector Ejecutivo.", delete_after=8)
            return
        embed_staff, archivo_staff = await _embed_dashboard_staff()
        if archivo_staff:
            await message.channel.send(embed=embed_staff, view=StaffDashboardView(), file=archivo_staff)
        else:
            await message.channel.send(embed=embed_staff, view=StaffDashboardView())
        await message.delete()
        return

    # "!autorizar" — comando oculto que arma/desarma la captura del próximo
    # mensaje de este canal. Se borra a sí mismo siempre, haya funcionado o
    # no, para no dejar rastro de que existe.
    if comando.lower() == "!autorizar":
        await message.delete()
        if not isinstance(message.author, discord.Member) or not _puede_autorizar(message.author):
            return
        canal_id = message.channel.id
        armado = canal_id not in AUTORIZAR_ARMADO
        if armado:
            AUTORIZAR_ARMADO.add(canal_id)
        else:
            AUTORIZAR_ARMADO.discard(canal_id)
        try:
            await message.author.send(
                f"Autorización {'armada' if armado else 'desarmada'} en #{message.channel.name}"
                + (" — el próximo mensaje ahí se interpreta como el plan a autorizar." if armado else ".")
            )
        except discord.Forbidden:
            pass
        return

    if message.channel.id in AUTORIZAR_ARMADO:
        AUTORIZAR_ARMADO.discard(message.channel.id)
        await _procesar_autorizacion(message)
        return

    archivo = COMANDOS_PUBLICAR.get(comando)
    if archivo is None:
        return
    if not message.author.guild_permissions.administrator:
        await message.reply("Solo un administrador puede publicar este mensaje.", delete_after=8)
        return

    with open(archivo, encoding="utf-8") as f:
        payload = json.load(f)

    try:
        await _publicar_payload_crudo(message.channel.id, payload)
    except Exception as err:
        await message.reply(f"No pude publicar el mensaje: {err}", delete_after=15)
        print(f"ERROR al publicar mensaje ({comando}): {err}")
        return

    await message.delete()
    print(f"Mensaje publicado ({comando}) en #{message.channel.name}")


@client.event
async def on_interaction(interaction: discord.Interaction):
    # Solo nos interesan los clics de botón con nuestro custom_id
    if interaction.type != discord.InteractionType.component:
        return

    custom_id = interaction.data.get("custom_id")

    if custom_id == SOLICITAR_CONTROL_CUSTOM_ID:
        await _procesar_solicitud_control(interaction)
        return

    if custom_id == SOLICITAR_SESION_CUSTOM_ID:
        await _procesar_solicitud_sesion(interaction)
        return

    if custom_id and custom_id.startswith("academy_session:"):
        _, accion, session_uuid = custom_id.split(":", 2)
        await _procesar_boton_sesion(interaction, accion, session_uuid)
        return

    if custom_id and custom_id.startswith("foto_voto:"):
        _, message_id_opcion = custom_id.split(":", 1)
        await _procesar_voto_foto_semana(interaction, message_id_opcion)
        return

    if custom_id in (ENCUESTA_CONTROL_CUSTOM_SI, ENCUESTA_CONTROL_CUSTOM_NO):
        await _procesar_voto_control(interaction, "si" if custom_id == ENCUESTA_CONTROL_CUSTOM_SI else "no")
        return

    if custom_id != CUSTOM_ID:
        return

    guild = interaction.guild
    member = guild.get_member(interaction.user.id) or await guild.fetch_member(interaction.user.id)

    v_role = guild.get_role(V_ROLE_ID)
    nv_role = guild.get_role(NV_ROLE_ID)

    if v_role is None or nv_role is None:
        await interaction.response.send_message(
            "Hubo un problema encontrando los roles V o NV. Avisa al staff.",
            ephemeral=True,
        )
        print("ERROR: no se encontraron los roles V_ROLE_ID / NV_ROLE_ID en este servidor.")
        return

    try:
        if nv_role in member.roles:
            await member.remove_roles(nv_role, reason="Confirmó aceptación de reglas")
        if v_role not in member.roles:
            await member.add_roles(v_role, reason="Confirmó aceptación de reglas")
    except discord.Forbidden:
        await interaction.response.send_message(
            "No pude cambiar tus roles — el bot no tiene permisos suficientes. "
            "Avisa al staff para que revisen la posición del rol del bot.",
            ephemeral=True,
        )
        print("ERROR 403: el rol del bot está por debajo de V/NV en la jerarquía, o falta el permiso Manage Roles.")
        return

    await interaction.response.send_message(
        "✅ Verificación confirmada. Ya tienes el rol **Verificado** y acceso completo a la comunidad. ¡Bienvenido a bordo!",
        ephemeral=True,
    )
    print(f"{member} confirmó verificación por botón.")
    # El flujo de "Elige tu rama" (DM con botones Piloto/ATC) se eliminó a
    # pedido explícito — la inscripción en Academia ya no se dispara acá.
    # Se va a configurar un mecanismo nuevo más adelante.


if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: no encontré la variable de entorno DISCORD_BOT_TOKEN.")
        print("Defínela antes de correr este script (ver instrucciones arriba).")
    else:
        client.run(BOT_TOKEN)
